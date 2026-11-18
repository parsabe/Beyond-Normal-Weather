import unittest

import numpy as np

from beyond_normal.benchmark import drought_onsets


class FlashDroughtTests(unittest.TestCase):
    def test_only_complete_spells_of_at_least_three_days_have_onsets(self):
        values = np.array([0.1, 0.1, 0.1, 0.5, 0.1, 0.1, 0.5, 0.05, 0.1, 0.08, 0.3, 0.1, 0.1, 0.1])
        np.testing.assert_allclose(drought_onsets(values, 0.2), [0.45])
