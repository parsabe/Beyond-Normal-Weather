"""Train joint neural and linear forecasts from date-aligned public inputs."""

from dataclasses import dataclass
import json

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from beyond_normal.data import sha256

from .data import FIELDS, TECHNOLOGIES


def verified_frame(path):
    metadata = json.loads(path.with_suffix(".json").read_text())
    if sha256(path) != metadata["sha256"]:
        raise ValueError(f"Mismatched input checksum: {path}")
    frame = pd.read_csv(path, parse_dates=["date"], index_col="date")
    if not frame.index.equals(pd.date_range(frame.index[0], frame.index[-1], freq="D")):
        raise ValueError(f"Expected consecutive unique days in {path}")
    return frame


@dataclass(frozen=True)
class Samples:
    dates: pd.DatetimeIndex
    features: np.ndarray
    target: np.ndarray
    persistence: np.ndarray
    missing_weather: np.ndarray


def make_samples(data, config):
    history = config["experiment"]["generation_history_days"]
    horizon = config["experiment"]["horizon_days"]
    regions = [zone["name"] for zone in config["zone"]]
    columns = [f"{region}_{tech}" for region in regions for tech in TECHNOLOGIES]
    generation = verified_frame(data / "smard_daily.csv")[columns]
    weather = verified_frame(data / "ifs_previous_runs.csv").reindex(generation.index)
    if not np.isfinite(generation).all().all() or (generation < 0).any().any():
        raise ValueError("Generation values must be finite nonnegative MWh")
    baseline = generation.loc[slice(*config["periods"]["train"])]
    scale = np.maximum(baseline.mean().to_numpy(), 1.0).reshape(len(regions), len(TECHNOLOGIES))
    weather_blocks = np.stack([
        weather[[f"{region}_{field}_previous_day{lead}" for region in regions for field in FIELDS]].to_numpy()
        for lead in range(1, horizon + 1)
    ], axis=1)
    generation_values = generation.to_numpy().reshape(-1, len(regions), len(TECHNOLOGIES))
    samples = {}
    for period, (first, last) in config["periods"].items():
        first = pd.Timestamp(first)
        last = pd.Timestamp(last) - pd.Timedelta(days=horizon - 1)
        issue_dates = pd.date_range(first, last)
        ix = generation.index.get_indexer(issue_dates)
        if len(ix) == 0 or (ix < history + 1).any() or ix[-1] + horizon > len(generation):
            raise ValueError(f"Incomplete generation coverage for {period}")
        leads = ix[:, None] + np.arange(horizon)
        # A day-2 history cutoff leaves a full day for daily SMARD totals to arrive.
        lag = generation_values[ix[:, None] - 2 - np.arange(history)] / scale
        weather_at_issue = weather_blocks[leads, np.arange(horizon)[None, :], :]
        angle = 2 * np.pi * generation.index.day_of_year.to_numpy()[leads] / 365.2425
        features = np.column_stack([
            lag[:, :3].reshape(len(ix), -1), lag.mean(axis=1).reshape(len(ix), -1),
            weather_at_issue.reshape(len(ix), -1), np.sin(angle), np.cos(angle),
        ])
        samples[period] = Samples(issue_dates, features, generation_values[leads],
                                  np.repeat(generation_values[ix - 2, None], horizon, axis=1),
                                  ~np.isfinite(weather_at_issue).all(axis=(1, 2)))
    if np.any(np.isfinite(samples["train"].features).sum(axis=0) == 0):
        raise ValueError("At least one forecast feature is unavailable throughout training")
    return samples, scale


def weakest_three_days(availability):
    """Minimum three-day average per trajectory, aggregating supply over regions."""
    country = availability.sum(axis=-1)
    width = min(country.shape[-1], 3)
    return np.lib.stride_tricks.sliding_window_view(country, width, axis=-1).mean(axis=-1).min(axis=-1)


def train_models(samples, scale, plant, seed):
    training = samples["train"]
    target = (training.target / scale).reshape(len(training.dates), -1)
    weak_supply = weakest_three_days(plant.availability(training.target))
    cutoff = float(np.quantile(weak_supply, 0.2))

    models = {"ridge": make_pipeline(SimpleImputer(), StandardScaler(), Ridge(alpha=30.0))}
    for name in ("neural", "reserve_neural"):
        models[name] = make_pipeline(
            SimpleImputer(), StandardScaler(),
            MLPRegressor(hidden_layer_sizes=(64, 32), alpha=0.05, learning_rate_init=0.001,
                         max_iter=1200, n_iter_no_change=35, tol=1e-4, random_state=seed),
        )
    for name, model in models.items():
        options = {}
        if name == "reserve_neural":
            options["mlpregressor__sample_weight"] = np.where(weak_supply <= cutoff, 3.0, 1.0)
        model.fit(training.features, target, **options)
        fitted = model.steps[-1][1]
        iterations = getattr(fitted, "n_iter_", None)
        suffix = f", {iterations} optimization iterations" if iterations is not None else ""
        print(f"Trained {name}: {len(training.dates)} forecast origins{suffix}", flush=True)
    return models, cutoff


def point_forecasts(models, samples, scale):
    forecasts = {"persistence": samples.persistence}
    for name, model in models.items():
        prediction = model.predict(samples.features).reshape(samples.target.shape)
        forecasts[name] = np.maximum(prediction, 0.0) * scale
    return forecasts


def forecast_ensembles(models, samples, scale, plant, members, seed):
    calibration = samples["calibration"]
    test = samples["test"]
    cal_point = point_forecasts(models, calibration, scale)
    test_point = point_forecasts(models, test, scale)
    ensembles = {}
    for method, prediction in test_point.items():
        rng = np.random.default_rng(seed)
        residuals = calibration.target - cal_point[method]
        cal_supply = weakest_three_days(plant.availability(cal_point[method]))
        test_supply = weakest_three_days(plant.availability(prediction))
        cal_day = calibration.dates.day_of_year.to_numpy()
        test_day = test.dates.day_of_year.to_numpy()
        scale_supply = max(float(np.std(cal_supply)), 1.0)
        delta = np.abs(test_day[:, None] - cal_day[None, :])
        seasonal = np.minimum(delta, 366 - delta) / 120.0
        distance = np.abs(test_supply[:, None] - cal_supply[None, :]) / scale_supply + seasonal
        near_count = min(60, len(residuals))
        if members > near_count:
            raise ValueError("Number of scenarios exceeds the calibration neighborhood")
        nearest = np.argsort(distance, axis=1, kind="stable")[:, :near_count]
        paths = np.stack([residuals[rng.choice(group, size=members, replace=False)] for group in nearest])
        ensembles[method] = np.maximum(prediction[:, None] + paths, 0.0)
    return test_point, ensembles


def forecast_scores(point, ensembles, samples, scale, plant, event_cutoff):
    truth = samples.target
    event = weakest_three_days(plant.availability(truth)) <= event_cutoff
    rows = []
    for method, predictions in point.items():
        scenarios = ensembles[method]
        probabilities = (weakest_three_days(plant.availability(scenarios)) <= event_cutoff).mean(axis=1)
        country_forecast = predictions.sum(axis=(2, 3))
        country_truth = truth.sum(axis=(2, 3))
        country_scenarios = scenarios.sum(axis=(3, 4))
        crps = np.abs(country_scenarios - country_truth[:, None]).mean(axis=1)
        crps -= 0.5 * np.abs(country_scenarios[:, :, None] - country_scenarios[:, None, :]).mean(axis=(1, 2))
        normalized = np.abs(predictions - truth) / scale
        for i in range(truth.shape[1]):
            rows.append({"method": method, "lead_day": i + 1,
                         "country_mae_gwh": np.abs(country_forecast[:, i] - country_truth[:, i]).mean() / 1000.0,
                         "country_crps_gwh": crps[:, i].mean() / 1000.0,
                         "regional_relative_mae": normalized[:, i].mean(),
                         "low_supply_brier_across_horizon": ((probabilities - event) ** 2).mean(),
                         "low_supply_reference_rate": event.mean()})
    return pd.DataFrame(rows)
