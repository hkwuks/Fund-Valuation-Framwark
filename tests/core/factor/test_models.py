"""FactorEngine 数据模型测试"""

import sys; sys.path.insert(0, 'backend/..')
from datetime import date
from backend.core.factor.models import (
    FactorMeta, FactorSnapshot, FactorEvaluationReport,
    ICSnapshot, GroupReturnResult, FamaMacBethResult, EvalCache,
)


class TestFactorMeta:
    def test_frozen(self):
        meta = FactorMeta(name="test", display_name="测试", category="risk",
                          domain="fund", description="测试因子", direction=1)
        import pytest
        with pytest.raises(AttributeError):
            meta.name = "changed"

    def test_defaults(self):
        meta = FactorMeta(name="a", display_name="A", category="c",
                          domain="d", description="d", direction=1)
        assert meta.min_history_days == 60
        assert meta.params == {}

    def test_factor_meta_fund_types_default(self):
        """fund_types 默认空列表"""
        m = FactorMeta(name="test", display_name="test", category="a",
                       domain="fund", direction=1, description="test")
        assert m.fund_types == []

    def test_factor_meta_fund_types_set(self):
        """fund_types 可以指定"""
        m = FactorMeta(name="test", display_name="test", category="a",
                       domain="fund", direction=1, description="test",
                       fund_types=["equity", "qdii"])
        assert m.fund_types == ["equity", "qdii"]


class TestICSnapshot:
    def test_defaults(self):
        ic = ICSnapshot()
        assert ic.ic == 0.0
        assert ic.sign_accuracy == 0.5

    def test_perfect_correlation(self):
        ic = ICSnapshot(ic=1.0, rank_ic=1.0, n=100, p_value=0.0)
        assert ic.ic == 1.0


class TestGroupReturnResult:
    def test_defaults(self):
        gr = GroupReturnResult()
        assert len(gr.group_means) == 5

    def test_monotonic(self):
        gr = GroupReturnResult(
            group_means=[-0.02, -0.01, 0.01, 0.03, 0.05],
            monotonicity_score=1.0,
            long_short_spread=0.07,
        )
        assert gr.monotonicity_score == 1.0


class TestFamaMacBethResult:
    def test_significant(self):
        fm = FamaMacBethResult(beta_mean=0.05, t_stat=2.5, p_value=0.01)
        assert fm.p_value < 0.05

    def test_not_significant(self):
        fm = FamaMacBethResult(beta_mean=0.01, t_stat=0.5, p_value=0.62)
        assert fm.p_value > 0.05


class TestFactorEvaluationReport:
    def test_default_verdict(self):
        report = FactorEvaluationReport()
        assert report.verdict == "noise"

    def test_strong_factor(self):
        report = FactorEvaluationReport(
            factor_name="test", rank_ic_mean=0.10, ic_ir=0.9,
            long_short_t_stat=3.5, monotonicity_score=0.9,
            factor_turnover=0.15, verdict="strong",
        )
        assert report.verdict == "strong"


class TestEvalCache:
    def test_set_and_get(self):
        cache = EvalCache(ttl_seconds=3600)
        report = FactorEvaluationReport(factor_name="test")
        key = cache._make_key("test", ["A", "B"], date(2023, 1, 1), date(2023, 12, 31))
        cache.set(key, report)
        assert cache.get(key) is not None

    def test_miss(self):
        cache = EvalCache()
        assert cache.get("nonexistent") is None

    def test_invalidate(self):
        cache = EvalCache()
        r = FactorEvaluationReport(factor_name="sharpe")
        k = cache._make_key("sharpe", ["A"], date(2023, 1, 1), date(2023, 12, 31))
        cache.set(k, r)
        cache.invalidate("sharpe")
        assert cache.get(k) is None
