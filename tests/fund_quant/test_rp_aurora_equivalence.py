"""Risk Parity legacy -> Aurora migration characterization tests."""
import sys
sys.path.insert(0, "backend")

import numpy as np
import pytest

from backend.fund_quant.strategy.allocation.risk_parity import RiskParityStrategy
from backend.fund_quant.adapter import AuroraRiskParity, AuroraDynamicRiskParity


@pytest.fixture(scope="module")
def nav_series():
    rng = np.random.default_rng(23)
    series = {}
    for code, sigma in (("A", 0.004), ("B", 0.02), ("C", 0.01)):
        value, values = 1.0, []
        for _ in range(300):
            value *= 1 + rng.normal(0.0002, sigma)
            values.append(value)
        series[code] = values
    return series


@pytest.fixture(autouse=True)
def no_bond_meta(monkeypatch):
    """合成序列无债券分类：隔离 DB，保证等价性确定性。"""
    monkeypatch.setattr("backend.fund_quant.data.storage.get_fund_meta", lambda code: None)


def _legacy(series, codes, params=None):
    return RiskParityStrategy(params=params or {}).optimize(codes, nav_series=series)["weights"]


def _aurora(series, codes, params=None):
    s = AuroraRiskParity()
    if params:
        s.params.update(params)
    return s._compute_weights(series, codes)


class TestAuroraRiskParityEquivalence:
    def test_weights_match_legacy(self, nav_series):
        codes = ["A", "B", "C"]
        expected = _legacy(nav_series, codes)
        actual = _aurora(nav_series, codes)
        assert set(actual) == set(expected)
        for code in codes:
            assert actual[code] == pytest.approx(expected[code], abs=1e-4)

    def test_weights_sum_to_one(self, nav_series):
        weights = _aurora(nav_series, ["A", "B", "C"])
        assert abs(sum(weights.values()) - 1.0) < 1e-3

    def test_single_fund(self, nav_series):
        assert _aurora(nav_series, ["A"]) == {"A": 1.0}

    def test_insufficient_data_equal_weight(self):
        series = {"A": [1.0] * 20, "B": [1.0] * 20}
        assert _aurora(series, ["A", "B"]) == {"A": 0.5, "B": 0.5}

    def test_params_match_legacy(self, nav_series):
        params = {"min_weight": 0.1, "max_weight": 0.6}
        expected = _legacy(nav_series, ["A", "B", "C"], params)
        actual = _aurora(nav_series, ["A", "B", "C"], params)
        assert actual == pytest.approx(expected, abs=1e-4)

    def test_bond_vol_multiplier_matches_legacy(self, nav_series, monkeypatch):
        """get_fund_meta 返回 bond -> 双方一致放大。"""
        meta = {code: {"fund_type": "bond"} for code in nav_series}
        monkeypatch.setattr(
            "backend.fund_quant.data.storage.get_fund_meta", lambda code: meta.get(code)
        )
        params = {"bond_vol_multiplier": "auto"}
        expected = _legacy(nav_series, ["A", "B", "C"], params)
        actual = _aurora(nav_series, ["A", "B", "C"], params)
        assert actual == pytest.approx(expected, abs=1e-4)

    def test_no_legacy_optimizer_call(self, nav_series, monkeypatch):
        def fail(*_args, **_kwargs):
            raise AssertionError("Aurora RP must not call legacy optimize")

        monkeypatch.setattr(RiskParityStrategy, "optimize", fail)
        weights = _aurora(nav_series, ["A", "B", "C"])
        assert weights and abs(sum(weights.values()) - 1.0) < 1e-3


class TestAuroraDynamicRiskParityMigration:
    def test_dynamic_matches_legacy_truncated(self, nav_series):
        """动态版 = legacy 对截断窗口求解（window_months 生效）。"""
        s = AuroraDynamicRiskParity()
        codes = ["A", "B", "C"]
        n = s.params.get("window_months", 12) * 21
        truncated = {c: values[-n:] for c, values in nav_series.items() if len(values) >= 60}
        expected = _legacy(truncated, codes)
        actual = s._compute_weights(nav_series, codes)
        assert actual == pytest.approx(expected, abs=1e-4)

    def test_dynamic_does_not_read_nav_db(self, nav_series, monkeypatch):
        """动态路径绝不回退 get_nav_history（防前视）。"""
        def fail(*_args, **_kwargs):
            raise AssertionError("Dynamic RP must not read get_nav_history")

        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", fail)
        s = AuroraDynamicRiskParity()
        weights = s._compute_weights(nav_series, ["A", "B", "C"])
        assert weights

    def test_dynamic_short_window_returns_empty(self):
        s = AuroraDynamicRiskParity()
        series = {f"F{i}": list(range(50, 50 + 59)) for i in range(3)}
        assert s._compute_weights(series, list(series)) == {}
