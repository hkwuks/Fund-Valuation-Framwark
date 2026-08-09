"""LSTM时序编码 + Actor-Critic模型"""
from collections import deque
import torch
import torch.nn as nn
import torch.nn.functional as F

from .models import ContinuousActor


class LSTMActorCritic(nn.Module):
    """
    LSTM时序编码 + Actor-Critic

    架构:
    - LSTM(obs_dim, hidden_dim, num_layers=2, batch_first=True) → 取最后一步输出
    - LayerNorm
    - Actor: 2层MLP → 离散(Categorical) 或 连续(Beta)
    - Critic: 2层MLP → 1 (V值)

    act() / evaluate() 接口与ActorCritic完全一致，保持PPOAgent兼容
    """

    def __init__(self, obs_dim: int, n_actions: int, hidden_dim: int = 256, history_len: int = 30, action_space: str = "discrete"):
        super().__init__()
        self.obs_dim = obs_dim
        self.history_len = history_len
        self.action_space = action_space

        # LSTM编码器
        self.lstm = nn.LSTM(
            input_size=obs_dim,
            hidden_size=hidden_dim,
            num_layers=2,
            batch_first=True,
            dropout=0.1,
        )
        self.ln = nn.LayerNorm(hidden_dim)

        # Actor
        if action_space == "continuous":
            self.actor = ContinuousActor(hidden_dim, hidden_dim // 2)
        else:
            self.actor_net = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Linear(hidden_dim // 2, hidden_dim // 2),
                nn.ReLU(),
            )
            self.actor_head = nn.Linear(hidden_dim // 2, n_actions)
            nn.init.orthogonal_(self.actor_head.weight, gain=0.01)
            nn.init.constant_(self.actor_head.bias, 0.0)

        # Critic
        self.critic_net = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, hidden_dim // 2),
            nn.ReLU(),
            nn.Linear(hidden_dim // 2, 1),
        )

        # 历史窗口
        self._obs_history = deque(maxlen=history_len)

    def _build_sequence(self, obs: torch.Tensor) -> torch.Tensor:
        """把当前obs加入历史，返回 (batch, history_len, obs_dim)"""
        self._obs_history.append(obs.detach().cpu())
        seq_list = list(self._obs_history)
        # 填充到history_len
        while len(seq_list) < self.history_len:
            seq_list.insert(0, seq_list[0] if seq_list else obs.detach().cpu())
        seq = torch.stack(seq_list, dim=1)  # (batch, seq_len, obs_dim)
        return seq.to(obs.device)

    def reset_history(self):
        """重置历史（新episode时调用）"""
        self._obs_history.clear()

    def forward(self, obs):
        seq = self._build_sequence(obs)
        lstm_out, _ = self.lstm(seq)
        features = self.ln(lstm_out[:, -1, :])  # 取最后一步
        value = self.critic_net(features).squeeze(-1)
        dist = self._get_action_distribution(features)
        return value, dist

    def _get_action_distribution(self, features):
        if self.action_space == "continuous":
            return self.actor.get_distribution(features)
        x = self.actor_net(features)
        logits = self.actor_head(x)
        probs = F.softmax(logits, dim=-1)
        return torch.distributions.Categorical(probs)

    def act(self, obs, deterministic=False):
        seq = self._build_sequence(obs)
        lstm_out, _ = self.lstm(seq)
        features = self.ln(lstm_out[:, -1, :])

        if self.action_space == "continuous":
            action, log_prob, entropy = self.actor.sample_action(features, deterministic)
        else:
            x = self.actor_net(features)
            logits = self.actor_head(x)
            probs = F.softmax(logits, dim=-1)
            dist = torch.distributions.Categorical(probs)
            if deterministic:
                action = dist.probs.argmax(dim=-1)
            else:
                action = dist.sample()
            log_prob = dist.log_prob(action)
            entropy = dist.entropy()

        value = self.critic_net(features).squeeze(-1)
        return action, log_prob, entropy, value

    def evaluate(self, obs, actions):
        seq = self._build_sequence(obs)
        lstm_out, _ = self.lstm(seq)
        features = self.ln(lstm_out[:, -1, :])

        if self.action_space == "continuous":
            dist = self.actor.get_distribution(features)
        else:
            x = self.actor_net(features)
            logits = self.actor_head(x)
            probs = F.softmax(logits, dim=-1)
            dist = torch.distributions.Categorical(probs)
        log_probs = dist.log_prob(actions)
        entropy = dist.entropy()

        values = self.critic_net(features).squeeze(-1)
        return values, log_probs, entropy