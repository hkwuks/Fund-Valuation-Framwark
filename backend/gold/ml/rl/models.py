"""强化学习神经网络模型"""
import math
from typing import Optional
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class ResidualBlock(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(dim, dim), nn.LayerNorm(dim), nn.ReLU(),
            nn.Linear(dim, dim), nn.LayerNorm(dim),
        )

    def forward(self, x):
        return F.relu(x + self.net(x))


class FeatureNet(nn.Module):
    """状态特征提取器 — MLP + Residual"""
    def __init__(self, obs_dim: int, hidden_dim: int = 256):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(obs_dim, hidden_dim),
            nn.LayerNorm(hidden_dim), nn.ReLU(),
            ResidualBlock(hidden_dim),
            ResidualBlock(hidden_dim),
            nn.Linear(hidden_dim, hidden_dim),
            nn.LayerNorm(hidden_dim), nn.ReLU(),
        )

    def forward(self, x):
        return self.net(x)


class Actor(nn.Module):
    """策略网络 — 输出动作概率和熵"""
    def __init__(self, feature_dim: int, n_actions: int, hidden_dim: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(feature_dim, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim), nn.ReLU(),
        )
        self.mean = nn.Linear(hidden_dim, n_actions)
        self.log_std = nn.Parameter(torch.zeros(n_actions))

        # 初始化
        nn.init.orthogonal_(self.mean.weight, gain=0.01)
        nn.init.constant_(self.mean.bias, 0.0)

    def forward(self, features):
        x = self.net(features)
        action_logits = self.mean(x)
        return action_logits

    def get_distribution(self, features):
        logits = self.forward(features)
        probs = F.softmax(logits, dim=-1)
        dist = torch.distributions.Categorical(probs)
        return dist

    def sample_action(self, features, deterministic=False):
        dist = self.get_distribution(features)
        if deterministic:
            action = dist.probs.argmax(dim=-1)
        else:
            action = dist.sample()
        log_prob = dist.log_prob(action)
        entropy = dist.entropy()
        return action, log_prob, entropy


class ContinuousActor(nn.Module):
    """连续动作策略 — Beta分布，输出[-1, 1]"""
    def __init__(self, feature_dim: int, hidden_dim: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(feature_dim, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim), nn.ReLU(),
        )
        self.alpha_head = nn.Linear(hidden_dim, 1)
        self.beta_head = nn.Linear(hidden_dim, 1)

    def forward(self, features):
        x = self.net(features)
        alpha = F.softplus(self.alpha_head(x)) + 1.0
        beta = F.softplus(self.beta_head(x)) + 1.0
        return alpha, beta

    def get_distribution(self, features):
        alpha, beta = self.forward(features)
        return torch.distributions.Beta(alpha, beta)

    def sample_action(self, features, deterministic=False):
        dist = self.get_distribution(features)
        if deterministic:
            action = dist.mean
        else:
            action = dist.sample()
        # [0,1] → [-1,1]
        mapped = 2.0 * action - 1.0
        log_prob = dist.log_prob(action).squeeze(-1) - torch.log(torch.tensor(2.0, device=action.device))
        entropy = dist.entropy().squeeze(-1)
        return mapped, log_prob, entropy


class Critic(nn.Module):
    """价值网络 — 输出状态价值V(s)"""
    def __init__(self, feature_dim: int, hidden_dim: int = 128):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(feature_dim, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim), nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, features):
        return self.net(features).squeeze(-1)


class ActorCritic(nn.Module):
    """Actor-Critic网络 — 支持离散/连续动作"""
    def __init__(self, obs_dim: int, n_actions: int, hidden_dim: int = 256, action_space: str = "discrete"):
        super().__init__()
        self.action_space = action_space
        self.feature_net = FeatureNet(obs_dim, hidden_dim)
        if action_space == "continuous":
            self.actor = ContinuousActor(hidden_dim, hidden_dim // 2)
        else:
            self.actor = Actor(hidden_dim, n_actions, hidden_dim // 2)
        self.critic = Critic(hidden_dim, hidden_dim // 2)

    def forward(self, obs):
        features = self.feature_net(obs)
        value = self.critic(features)
        dist = self.actor.get_distribution(features)
        return value, dist

    def act(self, obs, deterministic=False):
        features = self.feature_net(obs)
        action, log_prob, entropy = self.actor.sample_action(features, deterministic)
        value = self.critic(features)
        return action, log_prob, entropy, value

    def evaluate(self, obs, actions):
        features = self.feature_net(obs)
        dist = self.actor.get_distribution(features)
        log_probs = dist.log_prob(actions)
        entropy = dist.entropy()
        values = self.critic(features)
        return values, log_probs, entropy
