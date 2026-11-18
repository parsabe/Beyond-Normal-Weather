"""Download German daily generation and archived weather predictors."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tomllib
from urllib.error import HTTPError
from urllib.parse import urlencode

import numpy as np
import pandas as pd

from beyond_normal.data import read_url, sha256, write_csv, write_json


SMARD = "https://www.smard.de/app/chart_data"
PREVIOUS_RUNS = "https://previous-runs-api.open-meteo.com/v1/forecast"
METEO_MODEL = "ecmwf_ifs025"
TIMEZONE = "Europe/Berlin"
TECHNOLOGIES = ("wind", "solar")
FIELDS = ("wind_speed_10m", "shortwave_radiation")


def read_config(path):
    with Path(path).open("rb") as stream:
        config = tomllib.load(stream)
    bounds = [tuple(map(pd.Timestamp, config["periods"][period])) for period in ("train", "calibration", "test")]
    if any(first >= last for first, last in bounds) or any(before[1] >= after[0] for before, after in zip(bounds, bounds[1:])):
        raise ValueError("Training, calibration and test need ordered, disjoint periods")
    horizon = config["experiment"]["horizon_days"]
    if not 2 <= horizon <= 7:
        raise ValueError("Use a horizon between two and seven days for this archive")
    if config["experiment"]["generation_history_days"] < 3 or config["experiment"]["scenarios"] < 2:
        raise ValueError("Use at least three history days and two forecast scenarios")
    return config


def data_dates(config):
    train_start = config["periods"]["train"][0]
    first = min(pd.Timestamp(train_start), pd.Timestamp(config["experiment"].get("data_start", train_start)))
    first -= pd.Timedelta(days=config["experiment"]["generation_history_days"] + 2)
    last = pd.Timestamp(config["periods"]["test"][1])
    return pd.date_range(first, last, freq="D")


def cached_json(url, cache):
    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    path = cache / f"{key}.json"
    if not path.exists():
        write_json(json.loads(read_url(url)), path)
    return json.loads(path.read_text()), {"url": url, "file": path.name, "sha256": sha256(path)}


def smard_series(region, code, dates, cache, allow_missing=False):
    endpoint = f"{SMARD}/{code}/{region}"
    url = f"{endpoint}/index_day.json"
    try:
        index, manifest = cached_json(url, cache)
    except HTTPError as error:
        if error.code == 404 and allow_missing:
            return pd.Series(0.0, index=dates), [{"url": url, "status": 404, "assumed_mwh": 0.0}]
        raise
    records = [manifest]
    blocks = []
    for timestamp in index["timestamps"]:
        year = pd.Timestamp(timestamp, unit="ms", tz="UTC").tz_convert(TIMEZONE).year
        if dates[0].year <= year <= dates[-1].year:
            url = f"{endpoint}/{code}_{region}_day_{timestamp}.json"
            block, record = cached_json(url, cache)
            blocks.extend(block["series"])
            records.append(record)
    frame = pd.DataFrame(blocks, columns=["timestamp", "mwh"]).dropna(subset=["timestamp"])
    days = pd.to_datetime(frame.timestamp, unit="ms", utc=True).dt.tz_convert(TIMEZONE).dt.tz_localize(None).dt.normalize()
    values = pd.Series(pd.to_numeric(frame.mwh).to_numpy(), index=days)
    if values.index.has_duplicates:
        raise ValueError(f"SMARD {region}/{code} has duplicate dates")
    values = values.reindex(dates)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError(f"SMARD {region}/{code} has missing or invalid days: {list(values.index[~np.isfinite(values)][:5])}")
    return values, records


def fetch_generation(config, output, cache):
    dates = data_dates(config)
    columns = {}
    records = []

    def retrieve(zone):
        region = zone["name"]
        wind, wind_meta = smard_series(region, 4067, dates, cache)
        solar, solar_meta = smard_series(region, 4068, dates, cache)
        offshore_meta = []
        if zone["offshore"]:
            offshore, offshore_meta = smard_series(region, 1225, dates, cache, zone["offshore"] == "if_reported")
            wind = wind + offshore
        print(f"SMARD {region}: {len(dates)} local days", flush=True)
        return region, wind, solar, wind_meta + solar_meta + offshore_meta

    with ThreadPoolExecutor(max_workers=4) as workers:
        for region, wind, solar, downloads in workers.map(retrieve, config["zone"]):
            columns[f"{region}_wind"] = wind
            columns[f"{region}_solar"] = solar
            records.extend(downloads)
    frame = pd.DataFrame(columns).rename_axis("date").reset_index()
    path = output / "smard_daily.csv"
    write_csv(frame, path)
    write_json({"source": "Bundesnetzagentur | SMARD.de", "unit": "MWh per local calendar day", "time_zone": TIMEZONE,
                "requests": records, "sha256": sha256(path)}, path.with_suffix(".json"))


def parse_site(site, fields, dates):
    hourly = pd.DataFrame(site["hourly"])
    timestamp = pd.to_datetime(hourly.pop("time"), utc=True).dt.tz_convert(TIMEZONE)
    # The last retained valid hour is 18:00 local. Even on 25-hour days,
    # subtracting the encoded lead leaves at least five hours before issue.
    hour = timestamp.dt.hour <= 18
    local_day = timestamp.dt.tz_localize(None).dt.normalize()
    series = hourly.loc[hour, fields].astype(float)
    means = series.groupby(local_day.loc[hour]).mean().reindex(dates)
    counts = series.groupby(local_day.loc[hour]).count().reindex(dates)
    return means.where(counts >= 17)


def fetch_weather(config, output, cache):
    dates = data_dates(config)
    horizon = config["experiment"]["horizon_days"]
    fields = [f"{field}_previous_day{lead}" for lead in range(1, horizon + 1) for field in FIELDS]
    providers = config["zone"]
    points = [point for zone in providers for point in zone["locations"]]
    sections = []
    records = []
    # Day ranges in the API are UTC. Include the preceding day for local midnight.
    for _, block_days in pd.Series(dates, index=dates).groupby(dates.to_period("M")):
        block = pd.DatetimeIndex(block_days)
        params = dict(latitude=",".join(str(p[0]) for p in points), longitude=",".join(str(p[1]) for p in points),
                      start_date=(block[0] - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), end_date=block[-1].strftime("%Y-%m-%d"),
                      hourly=",".join(fields), models=METEO_MODEL, wind_speed_unit="ms", timezone="GMT")
        sites, record = cached_json(f"{PREVIOUS_RUNS}?{urlencode(params)}", cache)
        if not isinstance(sites, list) or len(sites) != len(points):
            raise ValueError("Archived forecast did not return all requested sites")
        columns = {}
        offset = 0
        for zone in providers:
            count = len(zone["locations"])
            local = []
            for site, point in zip(sites[offset:offset + count], points[offset:offset + count]):
                if np.max(np.abs(np.array([site["latitude"], site["longitude"]]) - point)) > 0.6:
                    raise ValueError("Unexpected coordinate in archived forecast response")
                local.append(parse_site(site, fields, block))
            values = np.stack([site.to_numpy() for site in local]).mean(axis=0)
            columns.update({f"{zone['name']}_{key}": values[:, col] for col, key in enumerate(fields)})
            offset += count
        frame = pd.DataFrame(columns, index=block)
        sections.append(frame)
        records.append(record)
        print(f"IFS archive {block[0].date()} to {block[-1].date()}: {frame.notna().to_numpy().mean():.1%} present", flush=True)
    values = pd.concat(sections).rename_axis("date").reset_index()
    path = output / "ifs_previous_runs.csv"
    write_csv(values, path)
    write_json({"source": "Open-Meteo Previous Runs API, ECMWF IFS 0.25 degree", "model": METEO_MODEL,
                "sample_points": [dict(name=zone["name"], locations=zone["locations"]) for zone in providers],
                "local_hours": [0, 18], "time_zone": TIMEZONE, "requests": records, "sha256": sha256(path)}, path.with_suffix(".json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=["generation", "weather", "all"])
    parser.add_argument("--config", type=Path, default=Path("germany/study.toml"))
    parser.add_argument("--output", type=Path, default=Path("data/raw/germany"))
    args = parser.parse_args()
    config = read_config(args.config)
    cache = args.output / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    if args.source in {"generation", "all"}:
        fetch_generation(config, args.output, cache)
    if args.source in {"weather", "all"}:
        fetch_weather(config, args.output, cache)


if __name__ == "__main__":
    main()
