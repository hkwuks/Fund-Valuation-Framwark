"""Monte Carlo 模拟测试"""

import sys

sys.path.insert(0, "backend/..")

import numpy as np
import pytest

from backend.fund_quant.backtest.monte_carlo import MonteCarloEngine, MonteCarloReport


class TestMonteCarloEngine:
    def test_random_data_sharpe_ci_contains_zero(self):
        """随机正态收益率（均值约0）-> Sharpe 置信区间应包含 0"""
        rng = np.random.RandomState(42)
        daily_returns = rng.normal(0, 0.01, 500).tolist()
        report = MonteCarloEngine().run(
            daily_returns, n_simulations=200, n_periods=252
        )
        assert report.sharpe_ratio["p5"] < 0 < report.sharpe_ratio["p95"]

    def test_positive_returns_positive_ratio_high(self):
        """全部正收益率 -> positive_ratio 应为 100%"""
        daily_returns = [0.001] * 500
        report = MonteCarloEngine().run(
            daily_returns, n_simulations=100, n_periods=252
        )
        assert report.return_pct["positive_ratio"] == 100.0

    def test_seed_reproducibility(self):
        """相同种子 -> 相同结果"""
        daily_returns = (
            np.random.RandomState(99).normal(0.0005, 0.01, 500).tolist()
        )
        r1 = MonteCarloEngine().run(
            daily_returns, n_simulations=100, n_periods=252, seed=42
        )
        r2 = MonteCarloEngine().run(
            daily_returns, n_simulations=100, n_periods=252, seed=42
        )
        assert r1.return_pct["mean"] == r2.return_pct["mean"]
        assert r1.sharpe_ratio["mean"] == r2.sharpe_ratio["mean"]
        assert r1.max_drawdown_pct["mean"] == r2.max_drawdown_pct["mean"]
        assert r1.ulcer_index["mean"] == r2.ulcer_index["mean"]
        assert r1.probability_of_loss == r2.probability_of_loss

    def test_n_simulations_respected(self):
        """n_simulations=10 -> 10 条路径"""
        daily_returns = [0.001] * 500
        report = MonteCarloEngine().run(
            daily_returns, n_simulations=10, n_periods=50
        )
        assert report.n_simulations == 10

    def test_empty_returns_raises(self):
        """空序列应抛出 ValueError"""
        with pytest.raises(ValueError, match="daily_returns 为空"):
            MonteCarloEngine().run([], n_simulations=10, n_periods=50)

    def test_ulcer_index_in_report(self):
        """验证 ulcer_index 被正确计算并返回"""
        daily_returns = [0.001] * 500
        report = MonteCarloEngine().run(
            daily_returns, n_simulations=50, n_periods=100
        )
        assert "mean" in report.ulcer_index
        assert "p5" in report.ulcer_index
        assert "p95" in report.ulcer_index
        # 恒定正收益 -> ulcer 应非常接近 0
        assert report.ulcer_index["mean"] < 1.0
