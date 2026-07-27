"""RL策略可解释性 — Feature Permutation Importance + 动作归因 + 交易案例"""
from typing import Optional
import numpy as np
from loguru import logger


class RLExplainer:
    """RL策略可解释性

    方法：
    1. Feature Permutation Importance — 打乱每个特征，观察收益变化
    2. Action Probability Decomposition — 各特征对动作概率的贡献
    3. 状态对比分析 — 典型交易案例的特征归因
    """

    def __init__(self, agent, env):
        self.agent = agent
        self.env = env

    # ── 1. Feature Permutation Importance ──────────────────────

    def feature_importance(self, obs: np.ndarray, n_permutations: int = 30) -> dict:
        """返回特征重要性排序

        Permutation Importance方法：
        1. 对观测状态做N次评估，记录基准reward
        2. 逐个打乱特征的列，再次评估，reward下降越多 = 特征越重要
        3. 返回归一化的重要性分数
        """
        n_features = obs.shape[-1]
        # 基准表现
        base_rewards = self._rollout_episode(obs, n_permutations)
        base_mean = float(np.mean(base_rewards))

        importances = {}
        for i in range(n_features):
            perm_rewards = []
            for _ in range(n_permutations):
                obs_perm = obs.copy()
                np.random.shuffle(obs_perm[..., i])
                r = self._rollout_episode(obs_perm, 1)
                perm_rewards.append(r[0])
            drop = base_mean - float(np.mean(perm_rewards))
            importances[f"feat_{i}"] = drop

        # 归一化
        values = np.array(list(importances.values()))
        total = values.sum()
        if total > 1e-10:
            for k in importances:
                importances[k] = float(importances[k] / total)

        # 按重要性降序
        importances = dict(sorted(importances.items(), key=lambda x: x[1], reverse=True))
        return importances

    def _rollout_episode(self, obs: np.ndarray, n_steps: int) -> list:
        """用给定obs作为初始状态，rollout n_steps步收集reward"""
        rewards = []
        for _ in range(n_steps):
            action, _, _ = self.agent.get_action(obs, deterministic=True)
            obs, reward, done, _ = self.env.step(action)
            rewards.append(reward)
            if done:
                break
        return rewards

    # ── 2. Action Probability Decomposition ────────────────────

    def explain_action(self, obs: np.ndarray, action: int) -> dict:
        """解释单个动作

        对每个特征维度，将其置零/均值，观测动作概率的变化
        """
        import torch
        obs_t = torch.tensor(obs, dtype=torch.float32, device=self.agent.device).unsqueeze(0)
        base_probs = self._get_action_probs(obs_t)

        feature_contribs = {}
        n_features = obs.shape[-1]
        for i in range(n_features):
            obs_perturbed = obs.copy()
            obs_perturbed[..., i] = 0.0
            with torch.no_grad():
                pt = torch.tensor(obs_perturbed, dtype=torch.float32, device=self.agent.device).unsqueeze(0)
                perturbed_probs = self._get_action_probs(pt)
            # 动作概率变化
            delta = float(base_probs[action] - perturbed_probs[action])
            feature_contribs[f"feat_{i}"] = round(delta, 6)

        # 按贡献绝对值降序
        feature_contribs = dict(
            sorted(feature_contribs.items(), key=lambda x: abs(x[1]), reverse=True)
        )

        return {
            "action": int(action),
            "position": self.env._action_to_position(action),
            "base_probs": base_probs.tolist(),
            "feature_contributions": feature_contribs,
        }

    def _get_action_probs(self, obs_t) -> np.ndarray:
        import torch
        with torch.no_grad():
            features = self.agent.model.feature_net(obs_t)
            dist = self.agent.model.actor.get_distribution(features)
            return dist.probs.cpu().numpy()[0]

    # ── 3. Trade Examples ──────────────────────────────────────

    def trade_examples(self, history: list, n_examples: int = 3) -> list[dict]:
        """找出典型交易案例

        history: list of TradeRecord from env.trades (支持dict或object)
        """
        def _get(t, attr, default=None):
            return getattr(t, attr, None) if not isinstance(t, dict) else t.get(attr, default)

        entries = [t for t in history if _get(t, "action", 0) not in (0, 6)]
        if not entries:
            return []

        entries.sort(key=lambda t: abs(_get(t, "pnl", 0) or 0), reverse=True)
        examples = []
        for t in entries[:n_examples]:
            examples.append({
                "step": _get(t, "step", 0),
                "action": _get(t, "action", 0),
                "position": _get(t, "position", 0),
                "price": _get(t, "price", 0),
                "pnl": _get(t, "pnl", 0),
                "cumulative_pnl": _get(t, "cumulative_pnl", 0),
                "reward_breakdown": _get(t, "reward_breakdown", {}),
                "reason": _get(t, "reason", ""),
            })
        return examples