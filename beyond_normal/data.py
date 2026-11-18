"""Acquire and normalize point inputs for the daily research experiment."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import time
import tomllib
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd


WEATHER = ["wind10_m_s", "solar_w_m2"]
FACTORS = ["wind_cf", "solar_cf"]
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
ERA5_FIELDS = ["wind_speed_10m", "wind_speed_100m", "shortwave_radiation"]


def load_config(path):
    with Path(path).open("rb") as file:
        config = tomllib.load(file)
    periods = config["periods"]
    for name, (start, end) in periods.items():
        if start > end:
            raise ValueError(f"Invalid {name} period: {start}–{end}")
    for previous, next_period in zip(["train", "calibration", "test"], ["calibration", "test", "future"]):
        if periods[previous][1] >= periods[next_period][0]:
            raise ValueError(f"{previous} and {next_period} overlap or are out of order")
    if periods["test"][1] > 2014 or periods["future"][0] < 2015:
        raise ValueError("CMIP6 historical data end in 2014 and SSP runs start in 2015")
    return config


def read_url(url):
    for attempt in range(3):
        try:
            with urlopen(url, timeout=90) as response:
                return response.read()
        except HTTPError as error:
            if error.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                raise
        except (TimeoutError, URLError):
            if attempt == 2:
                raise
        time.sleep(3 * (attempt + 1))


def write_csv(frame, path):
    temporary = path.with_suffix(".csv.part")
    frame.to_csv(temporary, index=False, float_format="%.8g")
    temporary.replace(path)


def write_json(value, path):
    temporary = path.with_suffix(".json.part")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def wind_capacity_factor(wind_m_s):
    """Idealized turbine with 3/12/25 m/s cut-in, rated and cut-out speeds."""
    cubic = np.clip((wind_m_s**3 - 3.0**3) / (12.0**3 - 3.0**3), 0.0, 1.0)
    return np.where(wind_m_s < 25.0, cubic, 0.0)


def era5_daily(payload):
    units = payload["hourly_units"]
    expected = {"wind_speed_10m": "m/s", "wind_speed_100m": "m/s", "shortwave_radiation": "W/m²"}
    if any(units[field] != unit for field, unit in expected.items()):
        raise ValueError(f"Unexpected ERA5 units: {units}")
    hourly = pd.DataFrame(payload["hourly"]).set_index("time")
    hourly.index = pd.to_datetime(hourly.index)
    if len(hourly) == 0 or not np.isfinite(hourly[ERA5_FIELDS].to_numpy(dtype=float)).all():
        raise ValueError("ERA5 response has missing hourly weather")
    if not hourly.index.equals(pd.date_range(hourly.index.min(), hourly.index.max(), freq="h")):
        raise ValueError("ERA5 hourly timestamps are duplicated, missing or out of order")
    if not hourly.resample("D").size().eq(24).all():
        raise ValueError("Expected 24 ERA5 values per UTC day")
    power = pd.DataFrame(
        {
            "wind10_m_s": hourly.wind_speed_10m,
            "solar_w_m2": hourly.shortwave_radiation.clip(lower=0),
            "wind_cf": wind_capacity_factor(hourly.wind_speed_100m.to_numpy()),
            "solar_cf": (hourly.shortwave_radiation * 0.85 / 1000.0).clip(0, 1),
        },
        index=hourly.index,
    )
    return power.resample("D").mean().rename_axis("date").reset_index()


def fetch_era5(config, output):
    start = config["periods"]["train"][0]
    end = config["periods"]["test"][1]
    site = config["site"]
    frames = []
    requests = []
    cache = output / "open-meteo"
    cache.mkdir(exist_ok=True)
    for first in range(start, end + 1, 3):
        last = min(first + 2, end)
        query = urlencode(
            dict(latitude=site["latitude"], longitude=site["longitude"], start_date=f"{first}-01-01",
                 end_date=f"{last}-12-31", hourly=",".join(ERA5_FIELDS), models="era5",
                 wind_speed_unit="ms", timezone="GMT", cell_selection="nearest")
        )
        url = f"{ARCHIVE}?{query}"
        request_id = hashlib.sha256(url.encode()).hexdigest()[:12]
        path = cache / f"{first}-{last}-{request_id}.json"
        if path.exists():
            payload = json.loads(path.read_bytes())
        else:
            payload = json.loads(read_url(url))
            write_json(payload, path)
        daily = era5_daily(payload)
        expected = pd.date_range(f"{first}-01-01", f"{last}-12-31")
        if not daily.date.equals(pd.Series(expected, name="date")):
            raise ValueError(f"Unexpected ERA5 coverage in {first}–{last}")
        frames.append(daily)
        requests.append({"url": url, "sha256": sha256(path), "cache_file": str(path.relative_to(output)),
                         "response_latitude": payload["latitude"], "response_longitude": payload["longitude"]})
        print(f"ERA5 {first}–{last}: {len(daily)} days", flush=True)
    path = output / "era5_daily.csv"
    write_csv(pd.concat(frames, ignore_index=True), path)
    write_json({"site": site, "requests": requests, "sha256": sha256(path)}, output / "era5_daily.json")


def cmip6_point(row, site, years):
    import gcsfs
    import xarray as xr

    variable = row.variable_id
    expected = {"sfcWind": {"m s-1", "m/s"}, "rsds": {"W m-2", "W/m2", "W m^-2"}}
    filesystem = gcsfs.GCSFileSystem(token="anon", access="read_only")
    mapper = filesystem.get_mapper(row.zstore)
    with xr.open_zarr(mapper, consolidated=True, chunks=None, decode_times=xr.coders.CFDatetimeCoder(use_cftime=True)) as dataset:
        data = dataset[variable]
        if data.attrs.get("units") not in expected[variable]:
            raise ValueError(f"Unexpected units for {row.zstore}: {data.attrs.get('units')}")
        if data.lat.ndim != 1 or data.lon.ndim != 1:
            raise ValueError(f"Expected rectilinear grid: {row.zstore}")
        lat = int(np.abs(data.lat.values - site["latitude"]).argmin())
        distance = np.abs((data.lon.values - site["longitude"] + 180) % 360 - 180)
        lon = int(distance.argmin())
        selected = data.isel(lat=lat, lon=lon).sel(time=slice(f"{years[0]}-01", f"{years[1]}-12")).load()
        times = selected.time.values
        if len(times) == 0 or times[0].year != years[0] or times[-1].year != years[1]:
            raise ValueError(f"Incomplete calendar coverage: {row.zstore}")
        if (times[0].month, times[0].day) != (1, 1) or times[-1].month != 12 or times[-1].day != times[-1].daysinmonth:
            raise ValueError(f"Data do not cover whole years: {row.zstore}")
        if any((end - start).days != 1 for start, end in zip(times[:-1], times[1:])):
            raise ValueError(f"CMIP6 dates are duplicated or missing: {row.zstore}")
        metadata = dict(zstore=row.zstore, version=str(row.version), member=row.member_id, grid=row.grid_label,
                        calendar=times[0].calendar, latitude=float(selected.lat), longitude=float(selected.lon),
                        units=data.attrs["units"])
        values = np.asarray(selected.values, dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"Nonfinite weather in {row.zstore}")
        if values.min() < -1e-3:
            raise ValueError(f"Negative values in {row.zstore}")
        return [t.strftime("%Y-%m-%d") for t in times], np.clip(values, 0, None), metadata


def fetch_cmip6(config, catalog, output, workers, experiments=None):
    records = pd.read_csv(catalog, dtype={"version": str})
    if experiments is not None:
        records = records.loc[records.experiment_id.isin(experiments)]
        if set(records.experiment_id) != set(experiments):
            raise ValueError("The source catalog lacks a requested experiment")
    groups = list(records.groupby(["source_id", "experiment_id"], sort=True))

    def download(group):
        (model, experiment), rows = group
        site = config["site"]
        periods = config["periods"]
        years = (periods["train"][0], periods["test"][1]) if experiment == "historical" else periods["future"]
        path = output / f"{model}_{experiment}.csv"
        manifest = path.with_suffix(".json")
        expected_stores = sorted(rows.zstore)
        if path.is_file() and manifest.is_file():
            saved = json.loads(manifest.read_text())
            if (saved["site"] == site and saved["years"] == list(years) and saved["sha256"] == sha256(path)
                    and sorted(field["zstore"] for field in saved["variables"].values()) == expected_stores):
                print(f"CMIP6 {model} {experiment}: verified cache", flush=True)
                return
        if sorted(rows.variable_id) != ["rsds", "sfcWind"] or rows.member_id.nunique() != 1 or rows.grid_label.nunique() != 1:
            raise ValueError(f"Expected a matched wind/solar run for {model} {experiment}")
        fields = {row.variable_id: cmip6_point(row, site, years) for row in rows.itertuples()}
        wind_dates, wind, wind_metadata = fields["sfcWind"]
        solar_dates, solar, solar_metadata = fields["rsds"]
        if wind_dates != solar_dates or any(wind_metadata[k] != solar_metadata[k] for k in ("latitude", "longitude", "calendar")):
            raise ValueError(f"Mismatched CMIP6 time or grid: {model} {experiment}")
        frame = pd.DataFrame({"date": wind_dates, "wind10_m_s": wind, "solar_w_m2": solar})
        write_csv(frame, path)
        write_json({"site": site, "years": years, "source": model, "experiment": experiment,
                    "variables": {"sfcWind": wind_metadata, "rsds": solar_metadata}, "sha256": sha256(path)}, manifest)
        print(f"CMIP6 {model} {experiment}: {len(frame)} days", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(download, groups))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=["era5", "cmip6", "all"])
    parser.add_argument("--config", type=Path, default=Path("study.toml"))
    parser.add_argument("--catalog", type=Path, default=Path("data/source/cmip6_stores.csv"))
    parser.add_argument("--output", type=Path, default=Path("data/raw"))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--future-years", type=int, nargs=2, metavar=("START", "END"))
    parser.add_argument("--experiments", choices=["historical", "ssp245", "ssp585"], nargs="+")
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)
    config = load_config(arguments.config)
    if arguments.future_years is not None:
        first, last = arguments.future_years
        if not 2015 <= first <= last:
            parser.error("Future-years need an ordered range starting in 2015 or later")
        config["periods"]["future"] = arguments.future_years
    if arguments.source in {"era5", "all"}:
        fetch_era5(config, arguments.output)
    if arguments.source in {"cmip6", "all"}:
        fetch_cmip6(config, arguments.catalog, arguments.output, arguments.workers, arguments.experiments)


if __name__ == "__main__":
    main()
