"""测试LSTM Actor-Critic模型"""
import pytest
import torch
import numpy as np
from backend.gold.ml.rl.models_lstm import LSTMActorCritic


class TestLSTMActorCritic:
    def test_init(self):
        model = LSTMActorCritic(obs_dim=30, n_actions=12, hidden_dim=64, history_len=20)
        assert model.obs_dim == 30
        assert model.history_len == 20
        # LSTM: input=30, hidden=64, layers=2
        assert model.lstm.input_size == 30
        assert model.lstm.hidden_size == 64
        assert model.lstm.num_layers == 2
        # Actor head: 64//2=32 → 12
        assert model.actor_head.in_features == 32
        assert model.actor_head.out_features == 12
        # Critic: 64//2=32 → 1
        assert model.critic_net[-1].out_features == 1

    def test_act_output_shape(self):
        model = LSTMActorCritic(obs_dim=30, n_actions=12, hidden_dim=64, history_len=20)
        obs = torch.randn(1, 30)
        action, log_prob, entropy, value = model.act(obs)
        assert action.shape == (1,)
        assert log_prob.shape == (1,)
        assert entropy.shape == (1,)
        assert value.shape == (1,)

    def test_evaluate_output_shape(self):
        model = LSTMActorCritic(obs_dim=30, n_actions=12, hidden_dim=64, history_len=20)
        obs = torch.randn(1, 30)
        actions = torch.tensor([3])
        values, log_probs, entropy = model.evaluate(obs, actions)
        assert values.shape == (1,)
        assert log_probs.shape == (1,)
        assert entropy.shape == (1,)

    def test_history_window(self):
        model = LSTMActorCritic(obs_dim=4, n_actions=3, hidden_dim=16, history_len=10)
        assert len(model._obs_history) == 0

        for _ in range(5):
            obs = torch.randn(1, 4)
            model.act(obs)
        assert len(model._obs_history) == 5  # 5个obs，没到10上限

        for _ in range(10):
            obs = torch.randn(1, 4)
            model.act(obs)
        assert len(model._obs_history) == 10  # 达到上限

    def test_reset_history(self):
        model = LSTMActorCritic(obs_dim=4, n_actions=3, hidden_dim=16, history_len=10)
        for _ in range(5):
            model.act(torch.randn(1, 4))
        assert len(model._obs_history) == 5
        model.reset_history()
        assert len(model._obs_history) == 0

    def test_batch_act(self):
        model = LSTMActorCritic(obs_dim=30, n_actions=12, hidden_dim=64, history_len=20)
        # batch_size=4
        for _ in range(10):
            obs = torch.randn(4, 30)
            action, log_prob, entropy, value = model.act(obs)
            assert action.shape == (4,)
            assert value.shape == (4,)
        model.reset_history()

    def test_deterministic_vs_stochastic(self):
        model = LSTMActorCritic(obs_dim=4, n_actions=3, hidden_dim=16, history_len=10)
        obs = torch.randn(1, 4)
        # deterministic: 应返回相同动作
        a1, _, _, _ = model.act(obs, deterministic=True)
        model.reset_history()
        a2, _, _, _ = model.act(obs, deterministic=True)
        assert a1.item() == a2.item()