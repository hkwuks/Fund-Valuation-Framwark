"""MaxDiversification（MDP）legacy → Aurora 迁移表征测试

AuroraMaxDiversification._compute_weights 已内联 legacy
MaxDiversificationStrategy.optimize 的 nav_series 路径。本文件在迁移前
快照 legacy 行为，迁移后持续锁定行为一致（等价重写，可任意改实现）。
"""
import sys
sys.path.insert(0, "backend")  # 暴露 core 包（与 test_aurora_etf_rotation 一致）
import numpy as np
import pytest

from backend.fund_quant.strategy.allocation.max_diversification import MaxDiversificationStrategy
from backend.fund_quant.adapter import AuroraMaxDiversification


@pytest.fixture(scope="module")
def nav_series():
    """确定性合成净值：A 低波动、B 高波动、C 中波动"""
    rng = np.random.default_rng(7)
    series = {}
    for code, sigma in (("A", 0.004), ("B", 0.02), ("C", 0.010)):
        v, vals = 1.0, []
        for _ in range(300):
            v *= 1 + rng.normal(0.0002, sigma)
            vals.append(v)
        series[code] = vals
    return series


def _legacy(nav_series, codes, params=None):
    return MaxDiversificationStrategy(params=params or {}).optimize(
        fund_codes=codes, nav_series=nav_series
    )["weights"]


def _aurora(nav_series, codes, params=None):
    s = AuroraMaxDiversification()
    if params:
        s.params.update(params)
    return s._compute_weights(nav_series, codes)


class TestAuroraMaxDiversificationEquivalence:
    def test_weights_match_legacy(self, nav_series):
        codes = ["A", "B", "C"]
        legacy_w = _legacy(nav_series, codes)
        aurora_w = _aurora(nav_series, codes)
        assert set(aurora_w) == set(legacy_w)
        for code in codes:
            assert aurora_w[code] == pytest.approx(legacy_w[code], abs=1e-4)

    def test_weights_sum_to_one(self, nav_series):
        w = _aurora(nav_series, ["A", "B", "C"])
        assert abs(sum(w.values()) - 1.0) < 1e-4

    def test_insufficient_data_equal_weight(self):
        """<2 只有效序列 → 等权回退（legacy insufficient_data 路径）"""
        series = {"A": [1.0] * 20, "B": [1.0] * 20}
        legacy_w = _legacy(series, ["A", "B"])
        aurora_w = _aurora(series, ["A", "B"])
        assert aurora_w == pytest.approx(legacy_w) == {"A": 0.5, "B": 0.5}

    def test_single_fund(self, nav_series):
        legacy_w = _legacy(nav_series, ["A"])
        aurora_w = _aurora(nav_series, ["A"])
        assert aurora_w == pytest.approx(legacy_w) == {"A": 1.0}

    def test_no_db_access_in_aurora_path(self, nav_series, monkeypatch):
        """Aurora 路径只消费传入净值，绝不触碰 DB"""
        import backend.fund_quant.adapter as adapter
        from backend.fund_quant.strategy import allocation as _  # noqa: F401

        def fail(*_a, **_k):
            raise AssertionError("Aurora MDP 不得访问 DB")

        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", fail)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_histories", fail)
        w = _aurora(nav_series, ["A", "B", "C"])
        assert w and abs(sum(w.values()) - 1.0) < 1e-4

    def test_max_weight_param_respected(self, nav_series):
        """max_weight 参数生效：所有权重 ≤ max_weight"""
        w = _aurora(nav_series, ["A", "B", "C"], params={"max_weight": 0.5})
        assert all(v <= 0.5 + 1e-4 for v in w.values())
