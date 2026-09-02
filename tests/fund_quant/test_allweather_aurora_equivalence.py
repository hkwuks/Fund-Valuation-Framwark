"""All Weather legacy -> Aurora migration characterization tests."""
import sys
sys.path.insert(0, "backend")

import numpy as np
import pytest

from backend.fund_quant.strategy.allocation.all_weather import AllWeatherStrategy
from backend.fund_quant.adapter import AllWeatherAurora


@pytest.fixture(scope="module")
def nav_series():
    rng = np.random.default_rng(29)
    series = {}
    for code, sigma in (
        ("510300", 0.01), ("511520", 0.002), ("518880", 0.008), ("513100", 0.012)
    ):
        value, values = 1.0, []
        for _ in range(300):
            value *= 1 + rng.normal(0.0002, sigma)
            values.append(value)
        series[code] = values
    return series


def _legacy(params=None, codes=None, series=None):
    merged = {"mode": "fixed"}
    if params:
        merged.update(params)
    return AllWeatherStrategy(params=merged).optimize(
        fund_codes=codes, nav_series=series
    )["weights"]


def _aurora(params=None, codes=None, series=None):
    s = AllWeatherAurora()
    s.params.update({"mode": "fixed"})
    if params:
        s.params.update(params)
    return s._compute_weights(series, codes)


class TestAllWeatherAuroraEquivalence:
    def test_fixed_template_matches_legacy(self):
        """fixed 模式不传池 → 资产模板权重"""
        expected = _legacy()
        actual = _aurora()
        assert actual == pytest.approx(expected, abs=1e-4)
        assert abs(sum(actual.values()) - 1.0) < 1e-4

    def test_fixed_custom_codes_match_legacy(self):
        """fixed 模式传入自定义基金池（含模板外代码）→ 与 legacy optimize(codes) 等价"""
        codes = ["510300", "513100", "518880", "CUSTOM"]
        expected = _legacy(codes=codes)
        actual = _aurora(codes=codes)
        assert set(actual) == set(codes)
        assert actual == pytest.approx(expected, abs=1e-4)

    def test_risk_parity_matches_legacy(self, nav_series):
        """risk_parity 模式 → 与 legacy optimize(codes, nav_series) 等价（无 DB）"""
        codes = list(nav_series)
        params = {"mode": "risk_parity"}
        expected = _legacy(params=params, codes=codes, series=nav_series)
        actual = _aurora(params=params, codes=codes, series=nav_series)
        assert set(actual) == set(expected)
        for code in codes:
            assert actual[code] == pytest.approx(expected[code], abs=1e-4)

    def test_risk_parity_insufficient_data_falls_back_to_fixed(self):
        """有效资产 < 2 → 回退固定权重（legacy 同款）"""
        short = {"510300": [1.0] * 10, "511520": [1.0] * 10}
        codes = ["510300", "511520"]
        params = {"mode": "risk_parity"}
        expected = _legacy(params=params, codes=codes, series=short)
        actual = _aurora(params=params, codes=codes, series=short)
        assert actual == pytest.approx(expected, abs=1e-4)

    def test_leverage_matches_legacy(self, nav_series):
        codes = list(nav_series)
        params = {"mode": "risk_parity", "leverage": 2.0}
        expected = _legacy(params=params, codes=codes, series=nav_series)
        actual = _aurora(params=params, codes=codes, series=nav_series)
        assert actual == pytest.approx(expected, abs=1e-4)

    def test_no_legacy_optimize_call(self, nav_series, monkeypatch):
        def fail(*_args, **_kwargs):
            raise AssertionError("Aurora AllWeather must not call legacy optimize")

        monkeypatch.setattr(AllWeatherStrategy, "optimize", fail)
        weights = _aurora(params={"mode": "risk_parity"}, codes=list(nav_series), series=nav_series)
        assert weights

    def test_no_db_in_nav_series_path(self, nav_series, monkeypatch):
        """risk_parity 提供 nav_series 时绝不读 DB（防前视）"""
        def fail(*_args, **_kwargs):
            raise AssertionError("AllWeather must not read get_nav_history")

        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", fail)
        weights = _aurora(
            params={"mode": "risk_parity"},
            codes=list(nav_series), series=nav_series,
        )
        assert weights and abs(sum(weights.values()) - 1.0) < 1e-3
