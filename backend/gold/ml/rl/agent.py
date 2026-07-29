"""PPO Agent — 黄金期货强化学习交易智能体"""
import os
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from loguru import logger

from .models import ActorCritic
from .models_lstm import LSTMActorCritic
from .env import GoldTradingEnv


# Optional TensorBoard
try:
    from torch.utils.tensorboard import SummaryWriter
except ImportError:
    SummaryWriter = None


@dataclass
class PPORolloutBuffer:
    obs: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    rewards: list = field(default_factory=list)
    dones: list = field(default_factory=list)
    values: list = field(default_factory=list)
    log_probs: list = field(default_factory=list)

    def clear(self):
        self.obs.clear()
        self.actions.clear()
        self.rewards.clear()
        self.dones.clear()
        self.values.clear()
        self.log_probs.clear()

    def to_tensors(self, device):
        return (
            torch.tensor(np.array(self.obs), dtype=torch.float32, device=device),
            torch.tensor(np.array(self.actions), dtype=torch.long, device=device),
            torch.tensor(np.array(self.rewards), dtype=torch.float32, device=device),
            torch.tensor(np.array(self.dones), dtype=torch.float32, device=device),
            torch.tensor(np.array(self.values), dtype=torch.float32, device=device),
            torch.tensor(np.array(self.log_probs), dtype=torch.float32, device=device),
        )


class PPOAgent:
    """PPO Agent for gold futures trading

    PPO-Clip with GAE, 适用于GoldTradingEnv的离散动作空间
    """

    def __init__(
        self,
        obs_dim: int,
        n_actions: int,
        lr: float = 3e-4,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_epsilon: float = 0.2,
        entropy_coef: float = 0.01,
        value_coef: float = 0.5,
        max_grad_norm: float = 0.5,
        n_epochs: int = 10,
        batch_size: int = 64,
        hidden_dim: int = 256,
        device: str = "auto",
        model_dir: str = "",
        model_type: str = "mlp",
        history_len: int = 30,
        action_space: str = "discrete",
        adaptive_lr: bool = True,
    ):
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.clip_epsilon = clip_epsilon
        self.entropy_coef = entropy_coef
        self.value_coef = value_coef
        self.max_grad_norm = max_grad_norm
        self.n_epochs = n_epochs
        self.batch_size = batch_size

        self.device = device if device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
        self.model_dir = model_dir
        self.action_space = action_space
        logger.info(f"[PPO] Using device: {self.device}")

        if model_type == "lstm":
            self.model = LSTMActorCritic(obs_dim, n_actions, hidden_dim, history_len, action_space=action_space).to(self.device)
        else:
            self.model = ActorCritic(obs_dim, n_actions, hidden_dim, action_space=action_space).to(self.device)
        self.model_type = model_type
        self.optimizer = optim.Adam(self.model.parameters(), lr=lr)
        self.scheduler = optim.lr_scheduler.CosineAnnealingLR(self.optimizer, T_max=100, eta_min=1e-6)

        self.buffer = PPORolloutBuffer()
        self.training_step = 0
        self.best_reward = -float("inf")
        self.writer: Optional[SummaryWriter] = None
        self.entropy_history: list[float] = []

        # 自适应学习率
        self.adaptive_lr = adaptive_lr
        self.adaptive_lr_min = 1e-6
        self.adaptive_lr_max = 1e-3
        self.entropy_low_threshold = 0.5
        self.entropy_high_threshold = 2.0

    def get_action(self, obs: np.ndarray, deterministic: bool = False) -> tuple[int, float, float]:
        """推理：给定状态返回动作"""
        with torch.no_grad():
            obs_t = torch.tensor(obs, dtype=torch.float32, device=self.device).unsqueeze(0)
            action, log_prob, entropy, value = self.model.act(obs_t, deterministic)
        return int(action.item()), float(log_prob.item()), float(value.item())

    def reset_history(self):
        """重置LSTM模型的历史状态（新episode时调用）"""
        if self.model_type == "lstm":
            self.model.reset_history()

    def store_transition(self, obs, action, reward, done, value, log_prob):
        """存储经验"""
        self.buffer.obs.append(obs)
        self.buffer.actions.append(action)
        self.buffer.rewards.append(reward)
        self.buffer.dones.append(done)
        self.buffer.values.append(value)
        self.buffer.log_probs.append(log_prob)

    def train(self) -> dict:
        """PPO训练更新，返回loss信息"""
        if len(self.buffer.obs) < self.batch_size:
            return {"error": "not enough samples"}

        obs, actions, rewards, dones, values, old_log_probs = self.buffer.to_tensors(self.device)

        # GAE 计算优势
        advantages = self._compute_gae(rewards, values, dones)
        returns = advantages + values
        advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        # 多epoch训练
        dataset_size = len(obs)
        indices = np.arange(dataset_size)
        losses = []

        for _ in range(self.n_epochs):
            np.random.shuffle(indices)
            for start in range(0, dataset_size, self.batch_size):
                end = start + self.batch_size
                batch_idx = indices[start:end]

                batch_obs = obs[batch_idx]
                batch_actions = actions[batch_idx]
                batch_returns = returns[batch_idx]
                batch_adv = advantages[batch_idx]
                batch_old_log_probs = old_log_probs[batch_idx]

                values_pred, log_probs, entropy = self.model.evaluate(batch_obs, batch_actions)

                # PPO-clip
                ratio = torch.exp(log_probs - batch_old_log_probs)
                surr1 = ratio * batch_adv
                surr2 = torch.clamp(ratio, 1 - self.clip_epsilon, 1 + self.clip_epsilon) * batch_adv
                policy_loss = -torch.min(surr1, surr2).mean()

                # Value loss
                value_loss = nn.MSELoss()(values_pred, batch_returns)

                # Entropy bonus
                entropy_loss = -entropy.mean()

                loss = policy_loss + self.value_coef * value_loss + self.entropy_coef * entropy_loss

                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), self.max_grad_norm)
                self.optimizer.step()

                losses.append({
                    "loss": loss.item(),
                    "policy_loss": policy_loss.item(),
                    "value_loss": value_loss.item(),
                    "entropy": entropy.mean().item(),
                    "approx_kl": (0.5 * (ratio - 1).pow(2).mean().item()),
                })

        self.buffer.clear()
        self.scheduler.step()

        if not losses:
            return {"error": "no training steps executed"}

        avg_loss = {k: np.mean([l[k] for l in losses]) for k in losses[0]}

        # 自适应学习率调整
        avg_entropy = avg_loss.get("entropy", 0)
        self.entropy_history.append(avg_entropy)
        if self.adaptive_lr:
            self._adjust_lr(avg_entropy)

        return avg_loss

    def _adjust_lr(self, avg_entropy: float):
        """根据策略熵值自适应调整学习率"""
        current_lr = self.optimizer.param_groups[0]["lr"]
        if avg_entropy < self.entropy_low_threshold:
            new_lr = min(current_lr * 1.5, self.adaptive_lr_max)
        elif avg_entropy > self.entropy_high_threshold:
            new_lr = max(current_lr * 0.5, self.adaptive_lr_min)
        else:
            return
        for pg in self.optimizer.param_groups:
            pg["lr"] = new_lr
        logger.debug(f"[PPO] Adaptive LR: {current_lr:.3e} -> {new_lr:.3e} (entropy={avg_entropy:.3f})")

    def _compute_gae(self, rewards, values, dones):
        """GAE-Lambda 优势估计"""
        advantages = torch.zeros_like(rewards)
        gae = 0
        for t in reversed(range(len(rewards) - 1)):
            delta = rewards[t] + self.gamma * values[t + 1] * (1 - dones[t]) - values[t]
            gae = delta + self.gamma * self.gae_lambda * (1 - dones[t]) * gae
            advantages[t] = gae
        return advantages

    def save(self, path: str, metrics: dict = None):
        """保存模型"""
        torch.save({
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "training_step": self.training_step,
            "best_reward": self.best_reward,
            "metrics": metrics or {},
        }, path)
        logger.info(f"[PPO] Model saved to {path}")

    def load(self, path: str):
        """加载模型"""
        checkpoint = torch.load(path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        self.training_step = checkpoint.get("training_step", 0)
        self.best_reward = checkpoint.get("best_reward", -float("inf"))
        logger.info(f"[PPO] Model loaded from {path}, step={self.training_step}")
        return checkpoint.get("metrics", {})

    def train_on_env(
        self,
        env: GoldTradingEnv,
        n_steps: int = 2048,
        n_iterations: int = 100,
        eval_interval: int = 10,
        save_interval: int = 20,
        progress_callback=None,
    ) -> dict:
        """在环境中训练PPO智能体

        Args:
            env: 交易环境
            n_steps: 每次迭代收集步数
            n_iterations: 迭代次数
            eval_interval: 评估间隔
            save_interval: 保存间隔
            progress_callback: 进度回调(locals_dict) → None

        Returns:
            training_history: dict
        """
        logger.info(f"[PPO] Starting training: {n_iterations} iters × {n_steps} steps")
        history = {
            "iterations": [],
            "best_reward": -float("inf"),
            "total_steps": 0,
        }

        for iteration in range(1, n_iterations + 1):
            obs = env.reset()
            ep_rewards = []
            ep_info = {}

            # 收集轨迹
            for step in range(n_steps):
                action, log_prob, value = self.get_action(obs)
                next_obs, reward, done, info = env.step(action)
                self.store_transition(obs, action, reward, done, value, log_prob)
                ep_rewards.append(reward)
                obs = next_obs

                if done:
                    self.reset_history()
                    ep_info = info
                    obs = env.reset()

            # PPO更新
            train_stats = self.train()
            self.training_step += 1

            total_reward = sum(ep_rewards)
            metrics = env.get_metrics()
            avg_return = metrics["total_return_pct"]
            sharpe = metrics["sharpe_ratio"]

            iter_data = {
                "iteration": iteration,
                "total_reward": round(total_reward, 4),
                "avg_return_pct": avg_return,
                "sharpe": sharpe,
                "win_rate": metrics["win_rate_pct"],
                "max_dd": metrics["max_drawdown_pct"],
                "train_loss": round(train_stats.get("loss", 0), 6),
                "entropy": round(train_stats.get("entropy", 0), 4),
                "policy_loss": round(train_stats.get("policy_loss", 0), 6),
                "value_loss": round(train_stats.get("value_loss", 0), 6),
            }
            history["iterations"].append(iter_data)

            if total_reward > history["best_reward"]:
                history["best_reward"] = total_reward
                self.best_reward = total_reward

            # 评估
            if eval_interval > 0 and (iteration % eval_interval == 0 or iteration == 1):
                eval_result = self.evaluate(env, n_episodes=2)
                iter_data["eval_return"] = round(eval_result["avg_return"], 2)
                iter_data["eval_sharpe"] = round(eval_result["sharpe"], 3)
                logger.info(
                    f"[PPO] iter={iteration}/{n_iterations} "
                    f"reward={total_reward:.4f} return={avg_return:.1f}% "
                    f"sharpe={sharpe:.3f} loss={train_stats.get('loss',0):.6f} "
                    f"eval_return={eval_result['avg_return']:.1f}%"
                )

            # 保存
            if save_interval and iteration % save_interval == 0 and self.model_dir:
                self.save(
                    os.path.join(self.model_dir, f"ppo_iter_{iteration}.pt"),
                    {"iteration": iteration, **iter_data},
                )

            if progress_callback:
                progress_callback(locals())

        history["total_steps"] = self.training_step * n_steps
        logger.info(f"[PPO] Training complete: best_reward={history['best_reward']:.4f}")
        return history

    def evaluate(self, env: GoldTradingEnv, n_episodes: int = 3) -> dict:
        """评估智能体性能"""
        returns = []
        shrapes = []
        trades = []
        for _ in range(n_episodes):
            obs = env.reset()
            done = False
            while not done:
                action, _, _ = self.get_action(obs, deterministic=True)
                obs, _, done, _ = env.step(action)
            m = env.get_metrics()
            returns.append(m["total_return_pct"])
            shrapes.append(m["sharpe_ratio"])
            trades.append(m["total_trades"])
        return {
            "avg_return": np.mean(returns),
            "std_return": np.std(returns),
            "sharpe": np.mean(shrapes),
            "avg_trades": int(np.mean(trades)),
            "n_episodes": n_episodes,
        }
