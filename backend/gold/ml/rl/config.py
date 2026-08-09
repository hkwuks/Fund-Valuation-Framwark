"""RL配置 — 奖励/环境/智能体超参数"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RewardConfig:
    """奖励函数配置"""
    reward_scale: float = 1e-6       # PnL缩放
    cost_penalty: float = 1.5         # 交易成本惩罚系数
    dd_penalty_start: float = 0.05    # 回撤惩罚起始点
    dd_penalty_steep: float = 0.5     # 回撤惩罚斜率（低回撤区）
    dd_penalty_steep2: float = 1.0    # 回撤惩罚斜率（高回撤区）
    dd_terminate: float = 0.15        # 回撤终止点
    sharpe_bonus_scale: float = 0.01  # Sharpe bonus缩放
    freq_penalty: float = 0.2         # 频繁交易惩罚


@dataclass
class EnvConfig:
    """交易环境配置"""
    initial_capital: float = 1_000_000
    multiplier: int = 1000
    margin_rate: float = 0.08
    commission_per_lot: float = 10.0
    slippage_per_lot: float = 20.0
    max_position: int = 10
    window_size: int = 30
    reward: RewardConfig = field(default_factory=RewardConfig)


@dataclass
class AgentConfig:
    """PPO智能体配置"""
    lr: float = 3e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_epsilon: float = 0.2
    entropy_coef: float = 0.01
    value_coef: float = 0.5
    max_grad_norm: float = 0.5
    n_epochs: int = 10
    batch_size: int = 64
    hidden_dim: int = 256