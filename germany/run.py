"""Train and backtest Germany-wide hydrogen reserve recommendations."""

import argparse
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from beyond_normal.data import sha256, write_csv, write_json

from .data import read_config
from .dispatch import DispatchProblem, Plant
from .forecast import forecast_ensembles, forecast_scores, make_samples, train_models


def replay(plant, dates, actual, forecast, persistence, import_fraction, method, condition):
    planner = None if forecast is None else DispatchProblem(plant, forecast.shape[1], forecast.shape[2])
    recourse = DispatchProblem(plant, members=1, days=1)
    imports = plant.import_limit * import_fraction
    stock = plant.reserve.copy()
    daily = []
    regional = []
    transfers = []
    for ix, date in enumerate(dates):
        if planner is None:
            target = plant.reserve
            orders = np.clip(plant.demand + target - stock - persistence[ix], 0, imports)
        else:
            orders, target = planner.recommended_actions(forecast[ix], stock, imports)
        result = recourse.realize(actual[ix], stock, orders, target)
        opening = stock
        stock = result["reserve_kg"]
        flow = result["transfers_kg"]
        lost = result["unmet_kg"]
        production = result["production_kg"]
        inbound = np.zeros(len(plant.zones))
        outbound = np.zeros(len(plant.zones))
        for (left, right), (forward, reverse) in zip(plant.links, flow):
            inbound[left] += reverse
            inbound[right] += forward
            outbound[left] += forward
            outbound[right] += reverse
        np.testing.assert_allclose(opening + production + orders + inbound - outbound - plant.demand + lost,
                                   stock, atol=1e-6, rtol=0)
        cash, penalized = plant.cost(production, orders, flow, lost)
        keys = {"condition": condition, "method": method, "date": date.date().isoformat()}
        daily.append({**keys, "unmet_kg": lost.sum(), "demand_kg": plant.demand.sum(), "production_kg": production.sum(),
                      "import_kg": orders.sum(), "transfer_kg": flow.sum(), "reserve_kg": stock.sum(),
                      "cash_eur": cash, "penalized_eur": penalized})
        for z, name in enumerate(plant.zones):
            regional.append({**keys, "zone": name, "available_kg": actual[ix, z], "demand_kg": plant.demand[z],
                             "opening_reserve_kg": opening[z], "reserve_target_kg": target[z],
                             "reserve_kg": stock[z], "production_kg": production[z],
                             "unmet_kg": lost[z], "import_order_kg": orders[z],
                             "inbound_kg": inbound[z], "outbound_kg": outbound[z]})
        for (left, right), (forward, reverse) in zip(plant.links, flow):
            transfers.append({**keys, "zone_a": plant.zones[left], "zone_b": plant.zones[right],
                              "a_to_b_kg": forward, "b_to_a_kg": reverse})
    return daily, regional, transfers


def operational_scores(daily):
    rows = []
    for (condition, method), frame in daily.groupby(["condition", "method"]):
        ordered = frame.sort_values("date")
        worst = ordered.unmet_kg.rolling(7, min_periods=1).sum().max()
        rows.append({"condition": condition, "method": method, "days": len(frame),
                     "demand_kg": frame.demand_kg.sum(), "unmet_kg": frame.unmet_kg.sum(),
                     "unmet_percent": 100 * frame.unmet_kg.sum() / frame.demand_kg.sum(),
                     "shortage_days": (frame.unmet_kg > 1e-6).sum(), "worst_seven_day_unmet_kg": worst,
                     "import_kg": frame.import_kg.sum(), "transfer_kg": frame.transfer_kg.sum(),
                     "cash_eur": frame.cash_eur.sum(), "penalized_eur": frame.penalized_eur.sum()})
    return pd.DataFrame(rows)


def plot(forecast, operations, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    styles = {"persistence": ("Persistence", "#748296"), "ridge": ("Ridge", "#2c69a8"),
              "neural": ("Neural", "#14806b"), "reserve_neural": ("Reserve-weighted neural", "#bc5b2e"),
              "fixed_reserve": ("Fixed reserve", "#b7a23a")}
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), layout="constrained")
    for method, group in forecast.groupby("method"):
        label, color = styles[method]
        axes[0].plot(group.lead_day, group.country_crps_gwh, marker="o", color=color, label=label)
    axes[0].set(xlabel="Lead day", ylabel="CRPS (GWh/day)", title="Germany-wide generation forecast")
    stress = operations.loc[operations.condition == "no_imports"].sort_values("unmet_percent")
    axes[1].barh([styles[name][0] for name in stress.method], stress.unmet_percent,
                 color=[styles[name][1] for name in stress.method])
    axes[1].invert_yaxis()
    axes[1].set(xlabel="Unmet hydrogen demand (%)", title="Modeled import interruption")
    axes[0].legend(frameon=False, fontsize=8)
    for axis in axes:
        axis.spines[["top", "right"]].set_visible(False)
    fig.savefig(output, dpi=160)
    plt.close(fig)


def run(config_path, data, output):
    config = read_config(config_path)
    plant = Plant(config)
    experiment = config["experiment"]
    samples, scale = make_samples(data, config)
    for period, sample in samples.items():
        print(f"{period}: {len(sample.dates)} origins, {sample.missing_weather.sum()} with forecast gaps", flush=True)
    models, cutoff = train_models(samples, scale, plant, experiment["seed"])
    point, ensembles = forecast_ensembles(models, samples, scale, plant, experiment["scenarios"], experiment["seed"])
    test = samples["test"]
    scores = forecast_scores(point, ensembles, test, scale, plant, cutoff)
    actual = plant.availability(test.target[:, 0])
    persistence = plant.availability(test.persistence[:, 0])
    availability = {method: plant.availability(values) for method, values in ensembles.items()}
    daily, regional, transfers = [], [], []
    for condition, limit in (("imports_available", 1.0), ("no_imports", 0.0)):
        for method, values in {"fixed_reserve": None, **availability}.items():
            day, zone, flow = replay(plant, test.dates, actual, values, persistence, limit, method, condition)
            daily.extend(day)
            regional.extend(zone)
            transfers.extend(flow)
            print(f"Replayed {condition}/{method}: {len(day)} days", flush=True)
    daily = pd.DataFrame(daily)
    operations = operational_scores(daily)
    output.mkdir(parents=True, exist_ok=True)
    write_csv(scores, output / "forecast_scores.csv")
    write_csv(operations, output / "operations_summary.csv")
    write_csv(daily, output / "daily_operations.csv")
    write_csv(pd.DataFrame(regional), output / "regional_decisions.csv")
    write_csv(pd.DataFrame(transfers), output / "regional_transfers.csv")
    plot(scores, operations, output / "backtest.png")
    training = {}
    for name in ("neural", "reserve_neural"):
        network = models[name].named_steps["mlpregressor"]
        training[name] = {"iterations": network.n_iter_, "maximum_iterations": network.max_iter,
                          "training_loss": network.loss_, "random_seed": network.random_state}
    write_json({"study": config, "config_sha256": sha256(config_path), "low_supply_cutoff_kg_per_day": cutoff,
                "inputs": {filename: sha256(data / filename) for filename in ("smard_daily.csv", "ifs_previous_runs.csv")},
                "samples": {name: {"first_issue": sample.dates[0].date().isoformat(),
                                   "last_issue": sample.dates[-1].date().isoformat(), "origins": len(sample.dates),
                                   "with_weather_gaps": int(sample.missing_weather.sum())}
                            for name, sample in samples.items()}, "training": training,
                "packages": {name: version(name) for name in ("numpy", "pandas", "scipy", "scikit-learn", "matplotlib")}},
               output / "run_manifest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("germany/study.toml"))
    parser.add_argument("--data", type=Path, default=Path("data/raw/germany"))
    parser.add_argument("--output", type=Path, default=Path("results/germany"))
    args = parser.parse_args()
    with threadpool_limits(limits=1):
        run(args.config, args.data, args.output)


if __name__ == "__main__":
    main()
