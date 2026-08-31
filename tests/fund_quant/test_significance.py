"""统计显著性检验测试 — SignificanceTester"""

import sys

sys.path.insert(0, "backend/..")

import numpy as np
import pytest

from backend.fund_quant.backtest.significance import SignificanceReport, SignificanceTester


class TestSignificanceTester:
    def test_significant_on_positive_returns(self):
        """强正漂移收益率 -> p < 0.05, is_significant=True"""
        rng = np.random.RandomState(42)
        base = np.full(500, 0.001)
        noise = rng.normal(0, 0.0001, 500)
        returns = base + noise  # strong positive drift

        report = SignificanceTester().test(returns, n_bootstrap=1000, seed=42)

        assert report.is_significant is True
        assert report.p_value < 0.05
        assert report.sharpe > 0

    def test_not_significant_on_random(self):
        """随机正态收益率（均值=0）-> p >= 0.05"""
        rng = np.random.RandomState(42)
        returns = rng.normal(0, 0.01, 500)

        report = SignificanceTester().test(returns, n_bootstrap=1000, seed=42)

        assert report.p_value >= 0.05
        assert report.is_significant is False

    def test_ci_contains_zero_for_random(self):
        """随机收益率 -> 95% CI 包含 0"""
        rng = np.random.RandomState(42)
        returns = rng.normal(0, 0.01, 500)

        report = SignificanceTester().test(returns, n_bootstrap=1000, seed=42)

        assert report.ci_lower <= 0 <= report.ci_upper

    def test_reproducible_seed(self):
        """相同种子 -> 相同 p_value"""
        rng = np.random.RandomState(42)
        returns = rng.normal(0.0005, 0.01, 500)

        r1 = SignificanceTester().test(returns, n_bootstrap=500, seed=42)
        r2 = SignificanceTester().test(returns, n_bootstrap=500, seed=42)

        assert r1.p_value == r2.p_value
        assert r1.sharpe == r2.sharpe
        assert r1.ci_lower == r2.ci_lower
        assert r1.ci_upper == r2.ci_upper

    def test_n_bootstrap_respected(self):
        """n_bootstrap=500 -> 500 次迭代"""
        returns = np.array([0.001] * 500)
        report = SignificanceTester().test(returns, n_bootstrap=500, seed=42)

        assert report.n_bootstrap == 500

    def test_empty_raises(self):
        """空数组 -> ValueError"""
        with pytest.raises(ValueError, match="至少需要 2 个样本"):
            SignificanceTester().test(np.array([]), n_bootstrap=100, seed=42)

    def test_low_volatility_handling(self):
        """恒定收益率 -> sharpe=0.0, p=1.0"""
        returns = np.array([0.001, 0.001])
        report = SignificanceTester().test(returns, n_bootstrap=100, seed=42)

        assert report.sharpe == 0.0
        assert report.p_value == 1.0


class TestSparseSampleProtection:
    """稀疏窗口收益保护 — 样本太少时不应给出虚假显著性"""

    def test_sparse_returns_marked_insufficient(self):
        """20 个样本 -> insufficient=True, p_value=1.0, 不显著"""
        rng = np.random.RandomState(42)
        returns = 0.005 + rng.normal(0, 0.001, 20)  # 强正漂移但样本太少
        report = SignificanceTester().test(returns, n_bootstrap=500, seed=42)

        assert report.insufficient is True
        assert report.n_returns == 20
        assert report.p_value == 1.0
        assert report.is_significant is False
        assert report.insufficiency_reason != ""

    def test_sparse_returns_dont_report_significant(self):
        """即便是强正收益，样本不足时也必须不显著（防止误导）"""
        returns = np.full(15, 0.01) + np.random.RandomState(1).normal(0, 0.001, 15)
        report = SignificanceTester().test(returns, n_bootstrap=500, seed=1)
        assert report.insufficient is True
        assert report.is_significant is False
        assert report.is_significant_adjusted is False

    def test_adequate_returns_not_insufficient(self):
        """>= 阈值样本 -> insufficient=False"""
        rng = np.random.RandomState(42)
        returns = rng.normal(0, 0.01, 100)
        report = SignificanceTester().test(returns, n_bootstrap=200, seed=42)
        assert report.insufficient is False
        assert report.insufficiency_reason == ""
        assert report.n_returns == 100

    def test_returns_report_exposes_sparse_metadata(self):
        """报告字段完整暴露稀疏元数据"""
        rng = np.random.RandomState(0)
        report = SignificanceTester().test(rng.normal(0.01, 0.001, 10), n_bootstrap=100)
        d = report.__dict__
        for key in ("n_returns", "insufficient", "insufficiency_reason",
                    "adjusted_p_value", "multiple_comparison",
                    "is_significant_adjusted", "method_notes"):
            assert key in d, f"缺少字段: {key}"


class TestMultipleComparison:
    """多重比较校正 — 避免多策略/多窗口同时检验的虚假显著性"""

    def test_bonferroni_adjusts_p_value(self):
        """n_comparisons=10 -> p_value 放大 10 倍"""
        rng = np.random.RandomState(42)
        returns = 0.0005 + rng.normal(0, 0.01, 500)
        report = SignificanceTester().test(returns, n_bootstrap=1000, seed=42,
                                           n_comparisons=10)
        assert report.adjusted_p_value == pytest.approx(min(report.p_value * 10, 1.0))
        assert report.multiple_comparison == "bonferroni"
        assert report.n_comparisons == 10

    def test_multi_comparison_can_flip_significance(self):
        """校正后显著性可能翻转: 单次显著但不满足 Bonferroni"""
        rng = np.random.RandomState(7)
        # 构造一个在 0.05 附近但不到 0.005 的 p-value
        returns = rng.normal(0.0015, 0.01, 500)
        single = SignificanceTester().test(returns, n_bootstrap=1000, seed=7)
        many = SignificanceTester().test(returns, n_bootstrap=1000, seed=7,
                                         n_comparisons=10)
        assert single.adjusted_p_value == single.p_value
        assert many.adjusted_p_value >= single.p_value
        if single.is_significant:
            assert many.is_significant_adjusted <= single.is_significant

    def test_holm_familywise(self):
        """holm 校正 n_comparisons>1 时产生校正 p 值"""
        rng = np.random.RandomState(42)
        returns = rng.normal(0.0005, 0.01, 500)
        report = SignificanceTester().test(returns, n_bootstrap=500, seed=42,
                                           n_comparisons=5, method="holm")
        assert report.adjusted_p_value == pytest.approx(min(report.p_value * 5, 1.0))
        assert report.multiple_comparison == "holm"

    def test_single_comparison_no_correction(self):
        """n_comparisons=1 -> 不校正, multiple_comparison='none'"""
        rng = np.random.RandomState(42)
        returns = rng.normal(0.0005, 0.01, 500)
        report = SignificanceTester().test(returns, n_bootstrap=500, seed=42)
        assert report.multiple_comparison == "none"
        assert report.adjusted_p_value == report.p_value
        assert report.is_significant_adjusted == report.is_significant

    def test_invalid_comparison_count_raises(self):
        with pytest.raises(ValueError, match="n_comparisons"):
            SignificanceTester().test(np.random.RandomState(0).normal(0, 1, 50),
                                      n_comparisons=0)

    def test_invalid_method_raises(self):
        with pytest.raises(ValueError, match="校正方法"):
            SignificanceTester().test(np.random.RandomState(0).normal(0, 1, 50),
                                      n_comparisons=3, method="bogus")
