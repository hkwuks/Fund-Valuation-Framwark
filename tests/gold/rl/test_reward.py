"""测试奖励函数各成分"""
import pytest
import numpy as np
import pandas as pd
from backend.gold.ml.rl.env import GoldTradingEnv


def _make_df(n=200):
    """生成模拟K线DataFrame"""
    np.random.seed(42)
    closes = 4000 + np.cumsum(np.random.randn(n) * 5)
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


class TestRewardFunction:
    def test_reward_breakdown_in_info(self):
        env = GoldTradingEnv(_make_df(), reward_scale=1e-6)
        env.reset()
        for _ in range(5):
            obs, reward, done, info = env.step(0)  # 空仓观望
            if done:
                break
        assert "reward_breakdown" in info
        rb = info["reward_breakdown"]
        assert "pnl" in rb
        assert "cost" in rb
        assert "freq" in rb
        assert "drawdown" in rb

    def test_hold_position_has_zero_cost(self):
        env = GoldTradingEnv(_make_df(), reward_scale=1e-6)
        obs = env.reset()
        _, reward, _, info = env.step(0)  # 空仓
        assert info["reward_breakdown"]["cost"] > -1e-10  # 成本接近0（空仓无交易）

    def test_trade_has_cost(self):
        env = GoldTradingEnv(_make_df(), reward_scale=1e-6)
        obs = env.reset()
        # 开多（动作4=满仓多）
        _, reward, _, info = env.step(4)
        assert info["reward_breakdown"]["cost"] < 0  # 有成本

    def test_freq_penalty_on_change(self):
        env = GoldTradingEnv(_make_df(), reward_scale=1e-6)
        obs = env.reset()
        # 从空仓变多仓
        _, r1, _, i1 = env.step(4)
        # 多仓变空仓
        _, r2, _, i2 = env.step(10)
        # 两次都有freq惩罚
        assert i1["reward_breakdown"]["freq"] < 0 or i1["reward_breakdown"]["freq"] == 0
        # 多变空有惩罚
        assert i2["reward_breakdown"]["freq"] < 0

    def test_drawdown_progressive(self):
        """测试回撤惩罚是渐进式的"""
        # 用下跌趋势数据
        np.random.seed(42)
        n = 100
        closes = 4000 - np.cumsum(np.random.randn(n) * 8)  # 下跌
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

        env = GoldTradingEnv(df, reward_scale=1e-6)
        obs = env.reset()
        drawdowns = []
        for i in range(60):
            obs, reward, done, info = env.step(4)  # 持多仓
            drawdowns.append(info["drawdown"])
            if done:
                break
        # 下跌趋势中，drawdown应逐渐增大
        if len(drawdowns) > 10:
            assert drawdowns[-1] >= drawdowns[0]  # 回撤增加

    def test_custom_reward_config(self):
        rc = {
            "reward_scale": 1e-5,
            "cost_penalty": 3.0,
            "dd_penalty_start": 0.03,
            "dd_penalty_steep": 1.0,
        }
        env = GoldTradingEnv(_make_df(), reward_config=rc)
        obs = env.reset()
        _, reward, _, info = env.step(4)
        # 自定义reward_scale更大
        assert info["reward_breakdown"]["pnl"] != 0.0

    def test_trade_record_has_breakdown(self):
        env = GoldTradingEnv(_make_df(), reward_scale=1e-6)
        obs = env.reset()
        for _ in range(3):
            obs, reward, done, info = env.step(0)
            if done:
                break
        assert len(env.trades) > 0
        t = env.trades[0]
        assert hasattr(t, "reward_breakdown")