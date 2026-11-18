"""Daily energy-to-hydrogen conversion and finite storage mass balance."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Plant:
    wind_mw: float
    solar_mw: float
    electrolyzer_mw: float
    kwh_per_kg: float
    delivery_kg_per_day: float

    def __post_init__(self):
        values = [self.wind_mw, self.solar_mw, self.electrolyzer_mw, self.kwh_per_kg, self.delivery_kg_per_day]
        if not np.isfinite(values).all() or min(values) <= 0:
            raise ValueError("Plant capacities, energy use and delivery must be finite and positive")

    def production(self, sequence):
        power_mw = sequence[..., 0] * self.wind_mw + sequence[..., 1] * self.solar_mw
        return np.minimum(power_mw, self.electrolyzer_mw) * 24_000 / self.kwh_per_kg


def dispatch(sequence, plant, storage_days, warmup_days):
    """Return daily unmet kg for each weather trajectory and storage capacity.

    Start empty and repeat the first year once before scoring. Serve demand
    before storing surplus. Storage carries across the scored calendar years.
    """
    production = plant.production(sequence)
    demand = plant.delivery_kg_per_day
    capacities = demand * np.asarray(storage_days)
    inventory = np.zeros((len(production), len(storage_days)))
    unmet = np.empty((len(production), production.shape[1], len(storage_days)))
    for amount in production[:, :warmup_days].T:
        inventory = np.clip(inventory + amount[:, None] - demand, 0, capacities)
    for day, amount in enumerate(production.T):
        balance = inventory + amount[:, None] - demand
        unmet[:, day, :] = np.maximum(-balance, 0)
        inventory = np.clip(balance, 0, capacities)
    return unmet


def longest_runs(mask):
    current = np.zeros(mask.shape[::2], dtype=int)
    longest = current.copy()
    for day in np.moveaxis(mask, 1, 0):
        current = (current + 1) * day
        longest = np.maximum(longest, current)
    return longest


def annual_outcomes(sequence, years, plant, storage_days):
    calendar_years, ends = np.unique(years, return_counts=True)
    unmet = dispatch(sequence, plant, storage_days, warmup_days=int(ends[0]))
    days = np.flatnonzero(np.diff(years)) + 1
    fraction = []
    shortfall_days = []
    longest = []
    for year in np.split(unmet, days, axis=1):
        shortfall = year > 1e-9
        fraction.append(year.mean(axis=1) / plant.delivery_kg_per_day)
        shortfall_days.append(shortfall.mean(axis=1))
        longest.append(longest_runs(shortfall))
    return calendar_years, {
        "unmet_fraction": np.stack(fraction, axis=1),
        "shortfall_day_fraction": np.stack(shortfall_days, axis=1),
        "longest_shortfall_days": np.stack(longest, axis=1),
    }


def crps(ensemble, reference):
    """Empirical CRPS for a sample on axis zero, scored against unpaired years."""
    sorted_values = np.sort(ensemble, axis=0)
    count = len(sorted_values)
    ranks = (2 * np.arange(count) + 1 - count)[:, None]
    double_spread = (ranks * sorted_values).sum(axis=0) / count**2
    return np.abs(ensemble[:, None, :] - reference[None, :, :]).mean(axis=0) - double_spread
