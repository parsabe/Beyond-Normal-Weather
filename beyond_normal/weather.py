"""Trend-preserving marginal correction and conditional block rank resampling."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import FACTORS, WEATHER


def monthly_ranks(values, months):
    ranks = pd.DataFrame(values).groupby(np.asarray(months)).rank().to_numpy()
    counts = np.bincount(months, minlength=13)[months, None]
    return (ranks - 0.5) / counts


def observations_to_factors(reference, historical, target):
    """Apply additive quantile delta mapping on resource units, then map to power.

    Monthly seasonal climatologies are compared across unpaired time series.
    Hourly ERA5 was converted to daily capacity factors upstream.
    """
    target_values = target[WEATHER].to_numpy()
    months = target.month.to_numpy()
    percentile = monthly_ranks(target_values, months)
    factors = np.empty_like(target_values)
    probabilities = np.linspace(0, 1, 201)

    for month in range(1, 13):
        where = months == month
        obs = reference.loc[reference.month == month]
        archive = historical.loc[historical.month == month, WEATHER].to_numpy()
        for column, (variable, capacity) in enumerate(zip(WEATHER, FACTORS)):
            observed_quantiles = np.quantile(obs[variable], probabilities)
            model_quantiles = np.quantile(archive[:, column], probabilities)
            delta = np.interp(percentile[where, column], probabilities, observed_quantiles - model_quantiles)
            corrected = np.maximum(target_values[where, column] + delta, 0.0)
            capacity_quantiles = np.quantile(obs[capacity], probabilities)
            values, starts, counts = np.unique(observed_quantiles, return_index=True, return_counts=True)
            capacity_at_value = np.add.reduceat(capacity_quantiles, starts) / counts
            factors[where, column] = np.interp(corrected, values, capacity_at_value)

    return factors


def reorder_margins(values, scores, months):
    """Use rank scores to reorder the same values within each calendar month."""
    reordered = np.empty_like(values)
    for month in np.unique(months):
        indices = np.flatnonzero(months == month)
        for column in range(values.shape[1]):
            order = np.argsort(scores[indices, column], kind="stable")
            reordered[indices[order], column] = np.sort(values[indices, column])
    return reordered


@dataclass(frozen=True)
class Blocks:
    fraction: float
    days: int


def block_ensemble(values, months, reference, settings, members, seed):
    """Resample paired reference rank blocks near modelled weather conditions."""
    if settings.fraction == 0:
        return values[None, :, :]
    rng = np.random.default_rng(seed)
    scores = monthly_ranks(values, months)
    reference_months = reference.month.to_numpy()
    reference_scores = monthly_ranks(reference[FACTORS], reference_months)
    output = np.empty((members, len(values), values.shape[1]))
    length = settings.days
    possible = np.arange(len(reference_scores) - length + 1)
    candidates = {month: possible[reference_months[possible] == month] for month in range(1, 13)}
    offsets = np.arange(length)
    reference_means = np.lib.stride_tricks.sliding_window_view(reference_scores, length, axis=0).mean(axis=-1)
    window = min(20, min(map(len, candidates.values())))

    # Start analog blocks at a similar modelled rank and block mean for both resources.
    neighbors = np.empty((len(scores), window), dtype=int)
    for start, score in enumerate(scores):
        eligible = candidates[months[start]]
        mean = scores[start : start + length].mean(axis=0)
        error = ((reference_scores[eligible] - score) ** 2).sum(axis=1)
        error += ((reference_means[eligible] - mean) ** 2).sum(axis=1)
        nearest = np.argpartition(error, window - 1)[:window]
        neighbors[start] = eligible[nearest]

    for member in range(members):
        mixed = scores.copy()
        starts = np.arange(rng.integers(0, length), len(scores), length)
        for start in starts[rng.random(len(starts)) < settings.fraction]:
            count = min(length, len(scores) - start)
            source = rng.choice(neighbors[start])
            mixed[start : start + count] = reference_scores[source + offsets[:count]]
        output[member] = reorder_margins(values, mixed, months)
    return output


def sequence_controls(values, years, months, members, seed):
    """Two counterfactual perturbations, both conserving per-month-year values."""
    rng = np.random.default_rng(seed)
    groups = [np.flatnonzero(years * 100 + months == group) for group in np.unique(years * 100 + months)]
    shuffle = np.repeat(values[None, :, :], members, axis=0)
    shift = shuffle.copy()
    for member in range(members):
        for index in groups:
            shuffle[member, index] = values[rng.permutation(index)]
            shift[member, index, 1] = np.roll(values[index, 1], rng.integers(3, len(index) - 2))
    return {"shuffle_paired_days": shuffle, "shift_solar": shift}
