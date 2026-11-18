"""Compare projected flash-drought frequency with Lin et al. (2026) Figure 2."""

import argparse
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

from .data import WEATHER, load_config, read_url, sha256, write_csv, write_json
from .run import RAW, daily_file, period
from .weather import Blocks, block_ensemble, observations_to_factors


PAPER = "https://doi.org/10.1038/s41558-026-02753-3"
SUPPORTING_FILES = {
    "lin-2026-source-data.zip": "https://static-content.springer.com/esm/art%3A10.1038%2Fs41558-026-02753-3/MediaObjects/41558_2026_2753_MOESM2_ESM.zip",
    "lin-2026-supplement.pdf": "https://static-content.springer.com/esm/art%3A10.1038%2Fs41558-026-02753-3/MediaObjects/41558_2026_2753_MOESM1_ESM.pdf",
    "lin-2026-zenodo.json": "https://zenodo.org/api/records/21865561",
}
WORKBOOK = "Source Data/Source_Data_Fig.2.xlsx"
HISTORICAL = (1985, 2014)
FUTURE = (2045, 2074)
SCENARIO = "ssp245"


def fetch_references(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name, url in SUPPORTING_FILES.items():
        path = directory / name
        temporary = path.with_suffix(".part")
        temporary.write_bytes(read_url(url))
        temporary.replace(path)
        print(f"Downloaded {path}", flush=True)
    write_json({name: {"url": url, "sha256": sha256(directory / name)} for name, url in SUPPORTING_FILES.items()},
               directory / "lin-2026-downloads.json")


def drought_onsets(values, low):
    """Day-before depletion for complete spells below low for at least three days."""
    flags = values < low
    transitions = np.diff(np.r_[False, flags, False].astype(int))
    begins = np.flatnonzero(transitions == 1)
    ends = np.flatnonzero(transitions == -1)
    qualified = begins[(ends - begins >= 3) & (begins > 0) & (ends < len(values))]
    return values[qualified - 1] - values[qualified]


def paired_frequency(historical, future):
    low = np.quantile(historical, 0.1)
    historical_onsets = drought_onsets(historical, low)
    if len(historical_onsets) == 0:
        return 0, np.nan
    onset = np.quantile(historical_onsets, 0.9)
    return (historical_onsets > onset).sum(), (drought_onsets(future, low) > onset).sum()


def source_cell(archive, site):
    with ZipFile(archive) as zipped, zipped.open(WORKBOOK) as workbook:
        grid = pd.read_excel(workbook, sheet_name="Fig.2")
    grid = grid.dropna(subset=["CFw_ssp245_CR_mean", "CFs_ssp245_CR_mean"])
    lat1 = np.radians(site["latitude"])
    lat2 = np.radians(grid.Latitude)
    delta_lon = np.radians(grid.Longitude - site["longitude"])
    distance = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(delta_lon / 2) ** 2
    return grid.loc[distance.idxmin()]


def compare(config_path, catalog, data, future_data, manifest_path, source_archive, output):
    config = load_config(config_path)
    manifest = json.loads(manifest_path.read_text())
    if manifest["config_sha256"] != sha256(config_path) or manifest["catalog_sha256"] != sha256(catalog):
        raise ValueError("The benchmark must use the configuration and catalog that produced the frozen fit")
    site = config["site"]
    inputs = {"era5_daily.csv": data / "era5_daily.csv"}
    records = pd.read_csv(catalog)
    models = sorted(set(records.loc[records.experiment_id == SCENARIO, "source_id"]))
    inputs.update({f"{model}_historical.csv": data / f"{model}_historical.csv" for model in models})
    for filename, path in inputs.items():
        if manifest["input_sha256"][filename] != sha256(path):
            raise ValueError(f"The frozen fit used a different input: {path}")
    era5 = daily_file(inputs["era5_daily.csv"], site, WEATHER)
    reference = period(era5, config["periods"]["train"])
    selected = Blocks(manifest["selected_fraction"], manifest["selected_block_days"])
    methods = {"gcm_chronology": RAW, "storage_calibrated": selected}
    members = config["experiment"]["ensemble_members"]
    seed = config["experiment"]["seed"]
    published = source_cell(source_archive, site)
    published_change = {"wind": published.CFw_ssp245_CR_mean, "solar": published.CFs_ssp245_CR_mean}
    grid_cell = {"latitude": float(published.Latitude), "longitude": float(published.Longitude)}
    rows = []
    for model in models:
        baseline = daily_file(inputs[f"{model}_historical.csv"], site, WEATHER)
        train = period(baseline, config["periods"]["train"])
        historical = period(baseline, HISTORICAL)
        latest = future_data / f"{model}_{SCENARIO}.csv"
        future = period(daily_file(latest, site, WEATHER), FUTURE)
        inputs[str(latest)] = latest
        past_values = observations_to_factors(reference, train, historical)
        future_values = observations_to_factors(reference, train, future)
        for name, setting in methods.items():
            past = block_ensemble(past_values, historical.month.to_numpy(), reference, setting, members, seed)
            future_ensemble = block_ensemble(future_values, future.month.to_numpy(), reference, setting, members, seed + 1)
            for column, resource in enumerate(("wind", "solar")):
                counts = np.array([paired_frequency(h[:, column], f[:, column]) for h, f in zip(past, future_ensemble)])
                valid = np.isfinite(counts[:, 1]) & (counts[:, 0] > 0)
                past_count, future_count = counts[valid].mean(axis=0) if valid.any() else (np.nan, np.nan)
                rows.append({"model": model, "method": name, "resource": resource,
                             "historical_events_per_30_years": past_count, "future_events_per_30_years": future_count,
                             "valid_trajectories": valid.sum(), "total_trajectories": len(counts),
                             "change_percent": 100 * (future_count / past_count - 1),
                             "published_nearby_change_percent": published_change[resource]})
        print(f"Compared {model} with the published map at {grid_cell}", flush=True)

    output.mkdir(parents=True, exist_ok=True)
    result = pd.DataFrame(rows)
    write_csv(result, output / "flash_drought_model_comparison.csv")
    summary = result.groupby(["method", "resource"], as_index=False).agg(
        study_mean_change_percent=("change_percent", "mean"),
        study_min_change_percent=("change_percent", "min"),
        study_max_change_percent=("change_percent", "max"),
        published_nearby_change_percent=("published_nearby_change_percent", "first"),
    )
    write_csv(summary, output / "flash_drought_summary.csv")
    write_json({"paper": PAPER, "workbook": WORKBOOK, "source_archive_sha256": sha256(source_archive),
                "source_cell": grid_cell, "site": site, "historical_period": HISTORICAL, "future_period": FUTURE,
                "scenario": SCENARIO, "fit_manifest_sha256": sha256(manifest_path),
                "input_sha256": {str(key): sha256(path) for key, path in inputs.items()}},
               output / "flash_drought_manifest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("study.toml"))
    parser.add_argument("--catalog", type=Path, default=Path("data/source/cmip6_stores.csv"))
    parser.add_argument("--data", type=Path, default=Path("data/raw"))
    parser.add_argument("--future-data", type=Path, default=Path("data/raw/lin-2026"))
    parser.add_argument("--references", type=Path, default=Path("data/reference_downloads"))
    parser.add_argument("--fit", type=Path, default=Path("results/run_manifest.json"))
    parser.add_argument("--output", type=Path, default=Path("results"))
    parser.add_argument("--download", action="store_true", help="Download the publisher files and exit")
    args = parser.parse_args()
    if args.download:
        fetch_references(args.references)
    else:
        compare(args.config, args.catalog, args.data, args.future_data, args.fit, args.references / "lin-2026-source-data.zip", args.output)


if __name__ == "__main__":
    main()
