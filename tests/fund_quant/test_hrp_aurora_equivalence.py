"""HRP legacy -> Aurora migration characterization tests."""
import sys
sys.path.insert(0, "backend")

import numpy as np
import pytest

from backend.fund_quant.strategy.allocation.hrp import HRPStrategy
from backend.fund_quant.adapter import AuroraHRP


@pytest.fixture(scope="module")
def nav_series():
    rng = np.random.default_rng(17)
    series = {}
    for code, sigma in (("A", 0.004), ("B", 0.02), ("C", 0.01)):
        value, values = 1.0, []
        for _ in range(300):
            value *= 1 + rng.normal(0.0002, sigma)
            values.append(value)
        series[code] = values
    return series


def _legacy(series, codes, params=None):
    return HRPStrategy(params=params or {}).optimize(codes, nav_series=series)["weights"]


def _aurora(series, codes, params=None):
    strategy = AuroraHRP()
    if params:
        strategy.params.update(params)
    return strategy._compute_weights(series, codes)


class TestAuroraHRPEquivalence:
    def test_weights_match_legacy(self, nav_series):
        codes = ["A", "B", "C"]
        expected = _legacy(nav_series, codes)
        actual = _aurora(nav_series, codes)
        assert set(actual) == set(expected)
        for code in codes:
            assert actual[code] == pytest.approx(expected[code], abs=1e-4)

    def test_single_fund_matches_legacy(self, nav_series):
        assert _aurora(nav_series, ["A"]) == {"A": 1.0}

    def test_insufficient_data_uses_original_codes(self):
        series = {"A": [1.0] * 20, "B": [1.0] * 20}
        assert _aurora(series, ["A", "B"]) == {"A": 0.5, "B": 0.5}

    def test_parameters_match_legacy(self, nav_series):
        params = {"linkage_method": "average", "min_weight": 0.1, "max_weight": 0.6}
        expected = _legacy(nav_series, ["A", "B", "C"], params)
        actual = _aurora(nav_series, ["A", "B", "C"], params)
        assert actual == pytest.approx(expected, abs=1e-4)

    def test_aurora_path_does_not_access_legacy_optimizer(self, nav_series, monkeypatch):
        def fail(*_args, **_kwargs):
            raise AssertionError("Aurora HRP must not call legacy optimizer")

        monkeypatch.setattr(HRPStrategy, "optimize", fail)
        weights = _aurora(nav_series, ["A", "B", "C"])
        assert abs(sum(weights.values()) - 1.0) < 1e-4

    def test_aurora_path_does_not_access_db(self, nav_series, monkeypatch):
        def fail(*_args, **_kwargs):
            raise AssertionError("Aurora HRP must not access DB")

        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", fail)
        weights = _aurora(nav_series, ["A", "B", "C"])
        assert abs(sum(weights.values()) - 1.0) < 1e-4
