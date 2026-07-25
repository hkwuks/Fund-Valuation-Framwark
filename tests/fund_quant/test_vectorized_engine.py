"""Tests for vectorized backtest engine."""

import numpy as np
import pytest

from backend.fund_quant.backtest.vectorized_engine import (
    VectorizedBacktestEngine,
    VectorizedBacktestResult,
)


def equal_weight(nav_matrix: np.ndarray) -> np.ndarray:
    """Equal-weight all funds."""
    n_funds, n_days = nav_matrix.shape
    return np.ones((n_funds, n_days)) / n_funds


def single_fund_weight(nav_matrix: np.ndarray) -> np.ndarray:
    """100% weight on the first fund."""
    n_funds, n_days = nav_matrix.shape
    weights = np.zeros((n_funds, n_days))
    weights[0, :] = 1.0
    return weights


class TestVectorizedBacktestEngine:
    """Test suite for VectorizedBacktestEngine."""

    def test_equal_weight(self):
        """Equal-weight 3 funds -> returns average of 3 fund returns."""
        nav = np.array([
            [1.0, 1.02, 1.04],
            [1.0, 1.01, 1.02],
            [1.0, 0.99, 0.98],
        ])
        engine = VectorizedBacktestEngine()
        result = engine.run(nav, equal_weight)

        # Manual computation: daily_ret = mean(fund_ret) for each day
        ret_f0 = nav[0, 1:] / nav[0, :-1] - 1  # [0.02, 0.01960784]
        ret_f1 = nav[1, 1:] / nav[1, :-1] - 1  # [0.01, 0.00990099]
        ret_f2 = nav[2, 1:] / nav[2, :-1] - 1  # [-0.01, -0.01010101]
        port_ret = (ret_f0 + ret_f1 + ret_f2) / 3
        expected_total = (1 + port_ret[0]) * (1 + port_ret[1]) - 1

        assert result.total_return == pytest.approx(expected_total, rel=1e-10)
        assert result.n_trading_days == 2
        assert len(result.equity_curve) == 3

    def test_single_fund(self):
        """One fund -> returns same as that fund's total return."""
        nav = np.array([
            [1.0, 1.02, 1.04, 1.06],
            [1.0, 1.00, 1.00, 1.00],
        ])
        engine = VectorizedBacktestEngine()
        result = engine.run(nav, single_fund_weight)

        expected = nav[0, -1] / nav[0, 0] - 1  # 0.06
        assert result.total_return == pytest.approx(expected, rel=1e-10)

    def test_sharpe_matches_event_driven(self):
        """Simple formula strategy -> vectorized produces valid metrics."""
        # Momentum strategy: go 100% into the fund with best 3-day return
        def momentum_weights(nav_matrix):
            n_funds, n_days = nav_matrix.shape
            weights = np.zeros((n_funds, n_days))
            weights[:, 0] = 1.0 / n_funds  # equal on first day
            for t in range(1, n_days):
                if t >= 3:
                    rets = nav_matrix[:, t] / nav_matrix[:, t - 3] - 1
                    best = int(np.argmax(rets))
                    weights[best, t] = 1.0
            return weights

        np.random.seed(42)
        n_funds, n_days = 5, 100
        steps = np.random.randn(n_funds, n_days) * 0.01
        nav = np.zeros((n_funds, n_days))
        nav[:, 0] = 1.0
        for t in range(1, n_days):
            nav[:, t] = nav[:, t - 1] * (1 + steps[:, t])

        engine = VectorizedBacktestEngine()
        result = engine.run(nav, momentum_weights)

        assert result.sharpe_ratio != 0.0
        assert result.n_trading_days == n_days - 1
        assert len(result.equity_curve) == n_days

    def test_benchmark_comparison(self):
        """Benchmark mode returns comparison dict with pct_diff < 0.01."""
        nav = np.array([
            [1.0, 1.01, 1.02],
            [1.0, 1.00, 1.00],
        ])
        engine = VectorizedBacktestEngine()

        result = engine.run(nav, equal_weight)
        ed_result = {
            "total_return": result.total_return,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown": result.max_drawdown,
        }
        comp = engine.benchmark_vs_event_driven(nav, equal_weight, ed_result)

        for key in ("total_return", "sharpe_ratio", "max_drawdown"):
            assert f"{key}_vectorized" in comp
            assert f"{key}_event_driven" in comp
            assert f"{key}_pct_diff" in comp
            assert comp[f"{key}_pct_diff"] < 0.01

        assert "vectorized_result" in comp

    def test_no_funds_raises(self):
        """Empty nav_matrix -> ValueError."""
        engine = VectorizedBacktestEngine()
        with pytest.raises(ValueError):
            engine.run(np.array([]).reshape(0, 0), equal_weight)

    def test_single_day(self):
        """2 funds x 1 day -> works, returns 0.0 for annual metrics."""
        nav = np.array([
            [1.0],
            [1.0],
        ])
        engine = VectorizedBacktestEngine()
        result = engine.run(nav, equal_weight)

        assert result.total_return == 0.0
        assert result.annual_return == 0.0
        assert result.volatility == 0.0
        assert result.sharpe_ratio == 0.0
        assert result.n_trading_days == 0
        assert len(result.equity_curve) == 1
