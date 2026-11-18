import unittest

import numpy as np
import pandas as pd

from beyond_normal.data import wind_capacity_factor
from beyond_normal.plant import Plant, crps, dispatch
from beyond_normal.weather import Blocks, block_ensemble, observations_to_factors, sequence_controls


class PipelineTests(unittest.TestCase):
    def test_turbine_thresholds(self):
        np.testing.assert_allclose(wind_capacity_factor(np.array([1, 3, 12, 20, 25, 30])), [0, 0, 1, 1, 0, 0])

    def test_dispatch_carries_storage_through_a_shortfall(self):
        plant = Plant(wind_mw=1, solar_mw=1, electrolyzer_mw=1, kwh_per_kg=24, delivery_kg_per_day=500)
        sequence = np.array([[[1, 0], [0, 0], [0, 0], [1, 1]]], dtype=float)
        unmet = dispatch(sequence, plant, [0, 1, 2], warmup_days=0)
        np.testing.assert_allclose(unmet[0], [[0, 0, 0], [500, 0, 0], [500, 500, 500], [0, 0, 0]])

    def test_ensemble_crps_matches_pairwise_definition(self):
        forecasts = np.array([[0.1, 0.2], [0.2, 0.4], [0.5, 0.1]])
        observed = np.array([[0.2, 0.3], [0.6, 0.1]])
        truth = np.abs(forecasts[:, None] - observed[None]).mean(axis=0)
        truth -= 0.5 * np.abs(forecasts[:, None] - forecasts[None]).mean(axis=(0, 1))
        np.testing.assert_allclose(crps(forecasts, observed), truth)

    def test_analog_reordering_preserves_every_monthly_marginal(self):
        months = pd.date_range("2001-01-01", "2002-12-31").month.to_numpy()
        rng = np.random.default_rng(4)
        values = rng.random((len(months), 2))
        reference = pd.DataFrame({"month": months, "wind_cf": rng.random(len(months)), "solar_cf": rng.random(len(months))})
        ensemble = block_ensemble(values, months, reference, Blocks(0.5, 7), 3, seed=1)
        self.assertFalse(np.array_equal(ensemble[0], values))
        for member in ensemble:
            for month in range(1, 13):
                mask = months == month
                np.testing.assert_array_equal(np.sort(member[mask], axis=0), np.sort(values[mask], axis=0))

    def test_shuffle_keeps_pairs_and_solar_shift_keeps_wind_dates(self):
        values = np.column_stack([np.arange(31), np.arange(31) + 100])
        controls = sequence_controls(values, np.full(31, 2000), np.ones(31, dtype=int), 2, seed=10)
        np.testing.assert_array_equal(controls["shuffle_paired_days"][..., 1] - controls["shuffle_paired_days"][..., 0],
                                      np.full((2, 31), 100))
        for member in controls["shift_solar"]:
            np.testing.assert_array_equal(member[:, 0], values[:, 0])
            np.testing.assert_array_equal(np.sort(member[:, 1]), values[:, 1])

    def test_additive_quantile_correction_recovers_reference_for_a_constant_bias(self):
        months = np.repeat(np.arange(1, 13), 200)
        levels = np.tile(np.linspace(0, 1, 200), 12)
        reference = pd.DataFrame({"month": months, "wind10_m_s": 2 + 3 * levels, "solar_w_m2": 10 + 100 * levels,
                                  "wind_cf": levels * 0.6, "solar_cf": levels * 0.3})
        biased = reference.assign(wind10_m_s=reference.wind10_m_s + 3, solar_w_m2=reference.solar_w_m2 - 5)
        corrected = observations_to_factors(reference, biased, biased)
        np.testing.assert_allclose(corrected, reference[["wind_cf", "solar_cf"]], atol=1e-9)


if __name__ == "__main__":
    unittest.main()
