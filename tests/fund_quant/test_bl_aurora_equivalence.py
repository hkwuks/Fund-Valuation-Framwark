"""Black-Litterman / BL 四象限 legacy -> Aurora migration characterization tests."""
import sys
sys.path.insert(0, "backend")

import numpy as np
import pytest

from backend.fund_quant.strategy.allocation.black_litterman import BlackLittermanStrategy
from backend.fund_quant.strategy.allocation.bl_quadrant import BlackLittermanQuadrant
from backend.fund_quant.adapter import AuroraBlackLitterman, AuroraBlQuadrant


@pytest.fixture(scope="module")
def nav_series():
    rng = np.random.default_rng(31)
    series = {}
    for code, sigma in (("A", 0.004), ("B", 0.02), ("C", 0.01), ("D", 0.008)):
        value, values = 1.0, []
        for _ in range(300):
            value *= 1 + rng.normal(0.0002, sigma)
            values.append(value)
        series[code] = values
    return series


@pytest.fixture(scope="module")
def quadrant_series():
    """使用 QUADRANT_MAP 真实代码：growth/deflation/neutral/inflation 全覆盖。"""
    rng = np.random.default_rng(37)
    codes = ["015740", "006961", "001961", "000218"]
    series = {}
    for code, sigma in zip(codes, (0.01, 0.002, 0.004, 0.008)):
        value, values = 1.0, []
        for _ in range(300):
            value *= 1 + rng.normal(0.0002, sigma)
            values.append(value)
        series[code] = values
    return series


def _bl_legacy(series, codes, params=None):
    return BlackLittermanStrategy(params=params or {}).optimize(codes, nav_series=series)["weights"]


def _bl_aurora(series, codes, params=None):
    s = AuroraBlackLitterman()
    if params:
        s.params.update(params)
    return s._compute_weights(series, codes)


def _quad_legacy(series, codes, params=None):
    return BlackLittermanQuadrant(params=params or {}).optimize(codes, nav_series=series)["weights"]


def _quad_aurora(series, codes, params=None):
    s = AuroraBlQuadrant()
    if params:
        s.params.update(params)
    return s._compute_weights(series, codes)


class TestAuroraBlackLittermanEquivalence:
    def test_no_views_equal_weight(self, nav_series):
        codes = ["A", "B", "C", "D"]
        expected = _bl_legacy(nav_series, codes)
        actual = _bl_aurora(nav_series, codes)
        assert actual == pytest.approx(expected, abs=1e-4)
        assert abs(sum(actual.values()) - 1.0) < 1e-4

    def test_user_views_match_legacy(self, nav_series):
        codes = ["A", "B", "C", "D"]
        params = {"views": [{"fund_long": "A", "fund_short": "B",
                             "excess_return": 0.03, "confidence": "high"}]}
        expected = _bl_legacy(nav_series, codes, params)
        actual = _bl_aurora(nav_series, codes, params)
        assert set(actual) == set(codes)
        for code in codes:
            assert actual[code] == pytest.approx(expected[code], abs=1e-4)

    def test_single_fund(self, nav_series):
        assert _bl_aurora(nav_series, ["A"]) == {"A": 1.0}

    def test_insufficient_data_equal_weight(self):
        series = {"A": [1.0] * 20, "B": [1.0] * 20}
        assert _bl_aurora(series, ["A", "B"]) == {"A": 0.5, "B": 0.5}

    def test_no_legacy_optimize_call(self, nav_series, monkeypatch):
        def fail(*_args, **_kwargs):
            raise AssertionError("Aurora BL must not call legacy optimize")

        monkeypatch.setattr(BlackLittermanStrategy, "optimize", fail)
        weights = _bl_aurora(nav_series, ["A", "B", "C", "D"])
        assert weights

    def test_no_db_in_nav_series_path(self, nav_series, monkeypatch):
        def fail(*_args, **_kwargs):
            raise AssertionError("BL must not read get_nav_history")

        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", fail)
        weights = _bl_aurora(nav_series, ["A", "B", "C", "D"])
        assert weights


class TestAuroraBlQuadrantEquivalence:
    def test_weights_match_legacy(self, quadrant_series):
        codes = list(quadrant_series)
        expected = _quad_legacy(quadrant_series, codes)
        actual = _quad_aurora(quadrant_series, codes)
        assert set(actual) == set(codes)
        for code in codes:
            assert actual[code] == pytest.approx(expected[code], abs=1e-4)

    def test_single_fund_returns_empty(self, quadrant_series):
        """适配器守卫：<2 只基金不进入优化器（_compute_weights 返回 {}）。"""
        code = list(quadrant_series)[0]
        assert _quad_aurora(quadrant_series, [code]) == {}

    def test_no_legacy_optimize_call(self, quadrant_series, monkeypatch):
        def fail(*_args, **_kwargs):
            raise AssertionError("Aurora BL Quadrant must not call legacy optimize")

        monkeypatch.setattr(BlackLittermanQuadrant, "optimize", fail)
        weights = _quad_aurora(quadrant_series, list(quadrant_series))
        assert weights

    def test_no_db_in_nav_series_path(self, quadrant_series, monkeypatch):
        def fail(*_args, **_kwargs):
            raise AssertionError("BL Quadrant must not read get_nav_history")

        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", fail)
        weights = _quad_aurora(quadrant_series, list(quadrant_series))
        assert weights
