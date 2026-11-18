"""Calibrate, verify and compare hydrogen risk under identical weather margins."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .data import FACTORS, WEATHER, load_config, sha256, write_csv, write_json
from .plant import Plant, annual_outcomes, crps
from .weather import Blocks, block_ensemble, observations_to_factors, sequence_controls


RAW = Blocks(0.0, 1)
FIXED = Blocks(1.0, 7)
LOSS = "unmet_fraction"


def daily_file(path, site, columns):
    manifest = json.loads(path.with_suffix(".json").read_text())
    if manifest["site"] != site or manifest["sha256"] != sha256(path):
        raise ValueError(f"Site or checksum mismatch for {path}. Re-run the data fetch.")
    frame = pd.read_csv(path, dtype={"date": str})
    calendar = frame.date.str.extract(r"^(\d{4})-(\d{2})-\d{2}$").astype(int)
    frame["year"], frame["month"] = calendar[0], calendar[1]
    if not frame.date.is_monotonic_increasing or frame.date.duplicated().any():
        raise ValueError(f"Out-of-order or duplicate dates: {path}")
    if not np.isfinite(frame[columns].to_numpy()).all():
        raise ValueError(f"Missing or invalid inputs: {path}")
    return frame


def period(frame, bounds):
    selected = frame.loc[frame.year.between(*bounds)].reset_index(drop=True)
    if sorted(selected.year.unique()) != list(range(bounds[0], bounds[1] + 1)):
        raise ValueError(f"Missing years in configured period {bounds}")
    return selected


def value_scores(annual, reference):
    return crps(np.concatenate(annual, axis=0), reference)


def annual_table(model, scenario, method, years, capacities, outcomes):
    shape = outcomes[LOSS].shape
    return pd.DataFrame(
        {
            "model": model,
            "scenario": scenario,
            "method": method,
            "weather_member": np.repeat(np.arange(shape[0]), shape[1] * shape[2]),
            "year": np.tile(np.repeat(years, shape[2]), shape[0]),
            "storage_days": np.tile(capacities, shape[0] * shape[1]),
            **{key: data.ravel() for key, data in outcomes.items()},
        }
    )


def summarize(annual, threshold):
    return (
        annual.groupby(["scenario", "model", "method", "storage_days"], as_index=False)
        .agg(
            mean_unmet_fraction=(LOSS, "mean"),
            median_unmet_fraction=(LOSS, "median"),
            p90_unmet_fraction=(LOSS, lambda x: x.quantile(0.9)),
            fraction_years_exceeding_target=(LOSS, lambda x: (x > threshold).mean()),
            mean_shortfall_day_fraction=("shortfall_day_fraction", "mean"),
            mean_longest_shortfall_days=("longest_shortfall_days", "mean"),
        )
    )


def plot_results(scores, summary, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6), layout="constrained")
    palette = {"gcm_chronology": "#46556b", "fixed_blocks": "#b96335", "storage_calibrated": "#11785e"}
    labels = {"gcm_chronology": "Climate model order", "fixed_blocks": "Fixed 7-day blocks",
              "storage_calibrated": "Storage-calibrated"}
    for method, color in palette.items():
        rows = scores.loc[(scores.method == method) & (scores.period == "test") & (scores.model == "pooled")]
        axes[0].plot(rows.storage_days, 100 * rows.crps, marker="o", color=color, label=labels[method])
    axes[0].set(title="Historical test performance", xlabel="Storage (days of delivery)",
                ylabel="CRPS for annual unmet demand (percentage points)")
    axes[0].legend(frameon=False)

    scenarios = {"ssp245": ("SSP2-4.5", "#356caf"), "ssp585": ("SSP5-8.5", "#b96335")}
    for scenario, rows in summary.groupby("scenario"):
        label, color = scenarios[scenario]
        baseline = rows.loc[rows.method == "gcm_chronology"]
        spread = baseline.groupby("storage_days").mean_unmet_fraction.agg(["min", "mean", "max"])
        calibrated = rows.loc[rows.method == "storage_calibrated"].groupby("storage_days").mean_unmet_fraction.mean()
        axes[1].plot(spread.index, 100 * spread["mean"], marker="o", color=color, label=f"{label} · model order")
        axes[1].plot(calibrated.index, 100 * calibrated, linestyle="--", color=color, label=f"{label} · calibrated")
        axes[1].fill_between(spread.index, 100 * spread["min"], 100 * spread["max"], color=color, alpha=0.10)
    axes[1].set(title="Future mean annual unmet demand", xlabel="Storage (days of delivery)",
                ylabel="Unmet hydrogen delivery (%)")
    axes[1].legend(title="Shading spans baseline models", frameon=False)
    for axis in axes:
        axis.set_xticks(sorted(scores.storage_days.unique()))
        axis.grid(axis="y", alpha=0.2)
        axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def run(config, catalog, data, output, config_path):
    site = config["site"]
    periods = config["periods"]
    experiment = config["experiment"]
    plant_config = config["plant"]
    storage = plant_config["storage_days"]
    withheld = plant_config["withheld_storage_days"]
    target = plant_config["target_unmet_fraction"]
    if (sorted(set(storage)) != storage or min(storage) < 0 or withheld not in storage
            or len(storage) < 2 or not 0 < target < 1):
        raise ValueError("Provide ordered unique nonnegative storage sizes, a withheld size and a target in (0, 1)")
    plant = Plant(**{key: plant_config[key] for key in Plant.__dataclass_fields__})
    if (experiment["ensemble_members"] < 1 or not experiment["block_days"] or not experiment["replacement_fractions"]
            or any(days < 2 or days > 28 for days in experiment["block_days"])
            or any(not 0 < fraction <= 1 for fraction in experiment["replacement_fractions"])):
        raise ValueError("Provide at least one member, block widths in [2, 28] and fractions in (0, 1]")
    era_path = data / "era5_daily.csv"
    era5 = daily_file(era_path, site, WEATHER + FACTORS)
    reference = period(era5, periods["train"])
    files = sorted(pd.read_csv(catalog).groupby(["source_id", "experiment_id"]).groups)
    models = sorted({model for model, scenario in files if scenario == "historical"})
    historical = {model: daily_file(data / f"{model}_historical.csv", site, WEATHER) for model in models}
    train = {model: period(frame, periods["train"]) for model, frame in historical.items()}
    members = experiment["ensemble_members"]
    seed = experiment["seed"]
    fit_storage = np.asarray([size != withheld for size in storage])
    search = {RAW, FIXED} | {
        Blocks(fraction, days) for fraction in experiment["replacement_fractions"] for days in experiment["block_days"]
    }
    search = sorted(search, key=lambda s: (s.fraction, s.days))

    def evaluate(values, frame, settings):
        ensemble = block_ensemble(values, frame.month.to_numpy(), reference, settings, members, seed)
        return annual_outcomes(ensemble, frame.year.to_numpy(), plant, storage)

    observed = {}
    for partition in ("calibration", "test"):
        subset = period(era5, periods[partition])
        years, outcomes = annual_outcomes(subset[FACTORS].to_numpy()[None], subset.year.to_numpy(), plant, storage)
        observed[partition] = (years, outcomes)

    calibration = {
        model: period(frame, periods["calibration"]) for model, frame in historical.items()
    }
    mapped_cal = {model: observations_to_factors(reference, train[model], frame) for model, frame in calibration.items()}
    candidates = {}
    tuning = []
    calibration_ref = observed["calibration"][1][LOSS][0]
    for settings in search:
        samples = {
            model: evaluate(mapped_cal[model], frame, settings)[1][LOSS].reshape(-1, len(storage))
            for model, frame in calibration.items()
        }
        score = float(value_scores(list(samples.values()), calibration_ref)[:, fit_storage].mean())
        candidates[settings] = samples
        tuning.append({"replacement_fraction": settings.fraction, "block_days": settings.days, "crps": score})
        print(f"Fit {settings.fraction:g} / {settings.days} days: CRPS {score:.6f}", flush=True)
    best = min(zip(search, tuning), key=lambda row: row[1]["crps"])[0]
    methods = {"gcm_chronology": RAW, "fixed_blocks": FIXED, "storage_calibrated": best}
    print(f"Selected replacement fraction {best.fraction:g} with {best.days}-day blocks", flush=True)

    score_rows = []
    test_scores = {}
    annual = []
    for partition in ("calibration", "test"):
        ref_years, ref_outcomes = observed[partition]
        if partition == "test":
            annual.append(annual_table("ERA5", "historical_test", "reference", ref_years, storage, ref_outcomes))
        for method, settings in methods.items():
            samples = {}
            for model, frame in historical.items():
                if partition == "calibration":
                    samples[model] = candidates[settings][model]
                else:
                    subset = period(frame, periods[partition])
                    values = observations_to_factors(reference, train[model], subset)
                    years, outcomes = evaluate(values, subset, settings)
                    samples[model] = outcomes[LOSS].reshape(-1, len(storage))
                    annual.append(annual_table(model, "historical_test", method, years, storage, outcomes))
            ref_loss = ref_outcomes[LOSS][0]
            pooled = value_scores(list(samples.values()), ref_loss)
            if partition == "test":
                test_scores[method] = pooled
            scores = {model: crps(sample, ref_loss) for model, sample in samples.items()}
            scores["pooled"] = pooled
            for model, values in scores.items():
                for i, size in enumerate(storage):
                    score_rows.append({"period": partition, "model": model, "method": method,
                                       "storage_days": size, "withheld_storage_size": bool(size == withheld),
                                       "crps": values[:, i].mean()})

    score_difference = test_scores["storage_calibrated"] - test_scores["gcm_chronology"]
    rng = np.random.default_rng(seed)
    n_years = len(score_difference)
    starts = rng.integers(n_years, size=(2000, (n_years + 1) // 2))
    indices = ((starts[:, :, None] + np.arange(2)) % n_years).reshape(len(starts), -1)[:, :n_years]
    intervals = np.quantile(score_difference[indices].mean(axis=1), [0.025, 0.975], axis=0)
    differences = pd.DataFrame({"storage_days": storage, "crps_difference": score_difference.mean(axis=0),
                                "bootstrap_p025": intervals[0], "bootstrap_p975": intervals[1]})

    for model, scenario in files:
        if scenario == "historical":
            continue
        if model not in train:
            raise ValueError(f"Missing historical archive for {model}")
        frame = period(daily_file(data / f"{model}_{scenario}.csv", site, WEATHER), periods["future"])
        values = observations_to_factors(reference, train[model], frame)
        years = frame.year.to_numpy()
        for method, settings in methods.items():
            labels, outcomes = evaluate(values, frame, settings)
            annual.append(annual_table(model, scenario, method, labels, storage, outcomes))
        for method, trajectories in sequence_controls(values, years, frame.month.to_numpy(), members, seed).items():
            labels, outcomes = annual_outcomes(trajectories, years, plant, storage)
            annual.append(annual_table(model, scenario, method, labels, storage, outcomes))
        print(f"Simulated {model} {scenario}", flush=True)

    output.mkdir(parents=True, exist_ok=True)
    annual = pd.concat(annual, ignore_index=True)
    scores = pd.DataFrame(score_rows)
    future = annual.loc[annual.scenario != "historical_test"]
    summary = summarize(future, target)
    write_csv(annual, output / "annual_outcomes.csv")
    write_csv(summary, output / "future_summary.csv")
    write_csv(scores, output / "historical_scores.csv")
    write_csv(pd.DataFrame(tuning).sort_values("crps", kind="stable"), output / "calibration_grid.csv")
    write_csv(differences, output / "test_crps_differences.csv")
    smallest = summary.loc[summary.mean_unmet_fraction <= target].groupby(["scenario", "model", "method"]).storage_days.min()
    capacities = summary[["scenario", "model", "method"]].drop_duplicates().join(smallest.rename("minimum_tested_storage_days"),
                                                                                  on=["scenario", "model", "method"])
    write_csv(capacities, output / "storage_targets.csv")
    plot_results(scores, summary, output / "comparison.png")
    input_hashes = {era_path.name: sha256(era_path)}
    input_hashes.update({f"{model}_{scenario}.csv": sha256(data / f"{model}_{scenario}.csv") for model, scenario in files})
    write_json({"config": config, "config_sha256": sha256(config_path), "catalog_sha256": sha256(catalog),
                "input_sha256": input_hashes, "python_packages": {key: version(key) for key in ("numpy", "pandas", "matplotlib")},
                "selected_fraction": best.fraction, "selected_block_days": best.days}, output / "run_manifest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("study.toml"))
    parser.add_argument("--catalog", type=Path, default=Path("data/source/cmip6_stores.csv"))
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--output", type=Path, default=Path("results"))
    arguments = parser.parse_args()
    run(load_config(arguments.config), arguments.catalog, arguments.data, arguments.output, arguments.config)


if __name__ == "__main__":
    main()
