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
