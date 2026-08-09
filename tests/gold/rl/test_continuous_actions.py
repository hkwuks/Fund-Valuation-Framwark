"""测试连续动作空间"""
import pytest
import torch
import numpy as np
from backend.gold.ml.rl.models import ContinuousActor, ActorCritic
from backend.gold.ml.rl.env import GoldTradingEnv


def _make_df(n=200):
    np.random.seed(42)
    closes = 4000 + np.cumsum(np.random.randn(n) * 5)
    df = __import__("pandas").DataFrame({
        "close": closes, "open": closes*0.999, "high": closes*1.005,
        "low": closes*0.995, "volume": np.random.randint(1000, 10000, n),
    })
    for col in ["rsi_14", "macd_diff", "atr_ratio", "bb_position", "volume_ma_ratio", "hv_ratio"]:
        df[col] = np.random.randn(n) * 0.1
    for col in ["tick_count", "buy_ratio", "vol_imbalance", "spread"]:
        df[col] = np.random.randn(n) * 0.1
    for col in ["DXY_change", "US10Y_change", "VIX_value", "gold_dxy_ratio"]:
        df[col] = np.random.randn(n) * 0.01
    return df


class TestContinuousActor:
    def test_output_shape(self):
        actor = ContinuousActor(feature_dim=64, hidden_dim=32)
        features = torch.randn(4, 64)
        action, log_prob, entropy = actor.sample_action(features)
        assert action.shape == (4, 1)
        assert log_prob.shape == (4,)
        assert entropy.shape == (4,)

    def test_action_range(self):
        actor = ContinuousActor(feature_dim=16, hidden_dim=16)
        for _ in range(50):
            f = torch.randn(1, 16)
            action, _, _ = actor.sample_action(f)
            a = action.item()
            assert -1.0 <= a <= 1.0, f"Action {a} out of [-1, 1]"

    def test_deterministic_stable(self):
        actor = ContinuousActor(feature_dim=16, hidden_dim=16)
        f = torch.randn(1, 16)
        a1, _, _ = actor.sample_action(f, deterministic=True)
        a2, _, _ = actor.sample_action(f, deterministic=True)
        assert abs(a1.item() - a2.item()) < 1e-6


class TestContinuousEnv:
    def test_action_mapping(self):
        env = GoldTradingEnv(_make_df(), action_space="continuous")
        obs = env.reset()

        # 满仓多
        pos = env._action_to_position(1.0)
        assert pos == env.max_position

        # 满仓空
        pos = env._action_to_position(-1.0)
        assert pos == -env.max_position

        # 空仓
        pos = env._action_to_position(0.0)
        assert pos == 0

        # 半仓多
        pos = env._action_to_position(0.5)
        assert pos == env.max_position // 2

    def test_continuous_step(self):
        env = GoldTradingEnv(_make_df(), action_space="continuous")
        obs = env.reset()
        for _ in range(10):
            obs, reward, done, info = env.step(0.0)
            if done:
                break
        assert "position" in info

    def test_discrete_unchanged(self):
        env = GoldTradingEnv(_make_df(), action_space="discrete")
        obs = env.reset()
        pos = env._action_to_position(4)
        assert pos == 10  # 满仓多

    def test_actor_critic_continuous(self):
        model = ActorCritic(obs_dim=30, n_actions=12, hidden_dim=64, action_space="continuous")
        obs = torch.randn(1, 30)
        action, log_prob, entropy, value = model.act(obs)
        assert action.shape == (1, 1)
        assert value.shape == (1,)