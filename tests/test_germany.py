import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from beyond_normal.data import sha256, write_csv, write_json
from germany.data import FIELDS, parse_site
from germany.dispatch import DispatchProblem, Plant
from germany.forecast import make_samples, weakest_three_days


def plant_fixture():
    return Plant({"plant": {"days_of_storage": 2, "terminal_reserve_days": 1, "kwh_per_kg": 50,
                            "renewable_eur_per_mwh": 40, "import_eur_per_kg": 11, "transfer_eur_per_kg": 0.1,
                            "unmet_eur_per_kg": 100, "reserve_gap_eur_per_kg": 5, "cvar_weight": 20, "cvar_level": 0.9},
                  "zone": [{"name": name, "contract_share": 1, "electrolyzer_mw": 1, "delivery_kg": 10,
                            "import_limit_kg": 10} for name in ["A", "B"]],
                  "link": [{"zones": ["A", "B"], "capacity_kg": 5}]})


class GermanyTests(unittest.TestCase):
    def test_real_dispatch_respects_available_production_and_link_limits(self):
        plant = plant_fixture()
        problem = DispatchProblem(plant, members=1, days=1)
        result = problem.realize(np.array([30., 0.]), np.zeros(2), np.zeros(2), np.zeros(2))
        np.testing.assert_allclose(result["production_kg"], [15, 0])
        np.testing.assert_allclose(result["unmet_kg"], [0, 5])
        np.testing.assert_allclose(result["transfers_kg"], [[5, 0]])
        np.testing.assert_allclose(result["reserve_kg"], [0, 0])

    def test_import_bookings_are_shared_and_respected_on_realization(self):
        plant = plant_fixture()
        planner = DispatchProblem(plant, members=2, days=2)
        supply = np.array([[[0, 0], [0, 0]], [[25, 25], [25, 25]]])
        planned = planner.solve(supply, np.zeros(2), plant.import_limit, plant.reserve)
        booked = planned[planner.imported[:, 0]]
        np.testing.assert_allclose(booked[0], booked[1])
        recourse = DispatchProblem(plant, members=1, days=1)
        actual = recourse.realize(np.zeros(2), np.zeros(2), booked[0], plant.reserve)
        np.testing.assert_allclose(actual["import_kg"], booked[0])

    def test_weak_supply_scores_consecutive_days(self):
        values = np.array([[[0, 0], [9, 3], [0, 0], [0, 0], [0, 3]]])
        np.testing.assert_allclose(weakest_three_days(values), [1])

    def test_weather_aggregation_uses_local_hours_and_excludes_evening(self):
        day = pd.Timestamp("2025-10-26")
        hours = pd.date_range("2025-10-25T22:00", "2025-10-26T22:00", freq="h", tz="UTC")
        local = hours.tz_convert("Europe/Berlin")
        signal = np.where(local.hour <= 18, 2.0, 100.0)
        payload = {"hourly": {"time": list(hours.strftime("%Y-%m-%dT%H:%M")), "wind": signal.tolist()}}
        self.assertAlmostEqual(parse_site(payload, ["wind"], pd.DatetimeIndex([day])).iloc[0, 0], 2.0)

    def test_issue_alignment_uses_only_matching_forecast_leads_and_past_generation(self):
        dates = pd.date_range("2023-12-27", "2024-01-16")
        values = np.arange(1, len(dates) + 1, dtype=float)
        generation = pd.DataFrame({"date": dates, "A_wind": values, "A_solar": values * 2})
        weather = pd.DataFrame({"date": dates, **{f"A_{f}_previous_day{lead}": values + lead * 100
                                                 for f in FIELDS for lead in range(1, 4)}})
        config = {"periods": {"train": ["2024-01-01", "2024-01-05"],
                              "calibration": ["2024-01-06", "2024-01-10"], "test": ["2024-01-11", "2024-01-16"]},
                  "zone": [{"name": "A"}], "experiment": {"generation_history_days": 3, "horizon_days": 3}}
        with TemporaryDirectory() as directory:
            for name, frame in (("smard_daily", generation), ("ifs_previous_runs", weather)):
                path = Path(directory) / f"{name}.csv"
                write_csv(frame, path)
                write_json({"sha256": sha256(path)}, path.with_suffix(".json"))
            samples, scale = make_samples(Path(directory), config)
        train = samples["train"]
        self.assertEqual(train.dates[-1], pd.Timestamp("2024-01-03"))
        np.testing.assert_allclose(train.features[0, :2] * scale[0], [4, 8])
        np.testing.assert_allclose(train.features[0, 8:14], [106, 106, 207, 207, 308, 308])
        np.testing.assert_allclose(train.target[0, :, 0, 0], [6, 7, 8])
