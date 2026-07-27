"""测试RL Walk-Forward验证"""
import pytest
import numpy as np
import pandas as pd
from backend.gold.ml.rl.walk_forward import RLWalkForwardValidator, FoldResult


def _make_synthetic_df(n=500):
    """生成模拟K线DataFrame"""
    np.random.seed(42)
    closes = 4000 + np.cumsum(np.random.randn(n) * 10)
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "open": closes * 0.999,
        "high": closes * 1.005,
        "low": closes * 0.995,
        "close": closes,
        "volume": np.random.randint(1000, 10000, n),
    })
    for col in ["rsi_14", "macd_diff", "atr_ratio", "bb_position", "volume_ma_ratio", "hv_ratio"]:
        df[col] = np.random.randn(n) * 0.1
    for col in ["tick_count", "buy_ratio", "vol_imbalance", "spread"]:
        df[col] = np.random.randn(n) * 0.1
    for col in ["DXY_change", "US10Y_change", "VIX_value", "gold_dxy_ratio"]:
        df[col] = np.random.randn(n) * 0.01
    return df


class TestRLWalkForwardValidator:
    def test_init(self):
        v = RLWalkForwardValidator(n_splits=3, train_ratio=0.7, embargo_days=10)
        assert v.n_splits == 3
        assert v.train_ratio == 0.7
        assert v.embargo_days == 10

    def test_insufficient_data(self):
        df = _make_synthetic_df(50)
        v = RLWalkForwardValidator(n_splits=3, train_ratio=0.7)
        result = v.validate(df)
        assert "error" in result

    def test_summary_before_validate(self):
        v = RLWalkForwardValidator(n_splits=3)
        s = v.summary()
        assert "error" in s

    def test_fold_result_dataclass(self):
        fr = FoldResult(
            fold_index=0, train_start=0, train_end=100,
            test_start=200, test_end=300,
            train_bars=100, test_bars=100,
            oos_return_pct=5.0, oos_sharpe=1.5, oos_max_dd_pct=-10.0,
            oos_win_rate=60.0, oos_trades=10, oos_volatility=15.0,
        )
        assert fr.fold_index == 0
        assert fr.oos_sharpe == 1.5
        assert fr.training_loss == []

    @pytest.mark.slow
    def test_validate_structure(self):
        df = _make_synthetic_df(500)
        v = RLWalkForwardValidator(
            n_splits=2, train_ratio=0.7, embargo_days=5,
            rl_train_kwargs={"n_iterations": 1, "n_steps": 64},
        )
        result = v.validate(df)
        assert "error" not in result, result.get("error", "")
        assert "n_splits" in result
        assert "oos_avg_sharpe" in result
        assert "folds" in result

    @pytest.mark.slow
    def test_summary_after_validate(self):
        df = _make_synthetic_df(500)
        v = RLWalkForwardValidator(
            n_splits=2, train_ratio=0.7, embargo_days=5,
            rl_train_kwargs={"n_iterations": 1, "n_steps": 64},
        )
        v.validate(df)
        s = v.summary()
        assert "oos_avg_sharpe" in s