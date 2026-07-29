"""RL训练管线 — 数据准备 + 训练 + 信号生成"""
import os
import json
import asyncio
from datetime import datetime
from typing import Optional
import pandas as pd
import numpy as np
import torch
from loguru import logger

from .env import GoldTradingEnv
from .agent import PPOAgent
from ..features import FeatureEngineer
from .walk_forward import RLWalkForwardValidator, FoldResult


# 模型保存目录
# 模型保存目录 — 从 `backend/gold/ml/rl/` 到 `data/backend/gold/rl_models/`
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "backend", "gold", "rl_models")


def ensure_model_dir():
    os.makedirs(MODEL_DIR, exist_ok=True)
    return MODEL_DIR


def bars_to_dataframe(bars: list) -> pd.DataFrame:
    """K线列表 → 训练DataFrame（含特征工程）"""
    rows = []
    for b in bars:
        rows.append({
            "date": b.datetime.strftime("%Y-%m-%d") if hasattr(b.datetime, "strftime") else str(b.datetime),
            "open": float(b.open),
            "high": float(b.high),
            "low": float(b.low),
            "close": float(b.close),
            "volume": float(b.volume),
        })
    df = pd.DataFrame(rows)

    fe = FeatureEngineer()
    try:
        df_feat = fe.create_technical_features(df)
        df_feat["date"] = df["date"]
    except Exception as e:
        logger.warning(f"Feature engineering failed: {e}")
        df_feat = df

    df_feat = df_feat.replace([np.inf, -np.inf], np.nan).fillna(0)
    return df_feat


class RLTrainer:
    """RL训练器 — 整合数据、环境、智能体"""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.agent: Optional[PPOAgent] = None
        self.env: Optional[GoldTradingEnv] = None
        self.training_history = {}
        self.model_path = ""

    def prepare_env(self, bars: list, env_config: Optional[dict] = None):
        """从K线数据创建交易环境"""
        df = bars_to_dataframe(bars)
        ec = {**self.config.get("env", {}), **(env_config or {})}
        self.env = GoldTradingEnv(df, **ec)
        logger.info(f"[RLTrainer] Env created: {len(df)} bars, obs_dim={self.env.obs_dim}")
        return self.env

    def init_agent(self, agent_config: Optional[dict] = None):
        """初始化PPO智能体"""
        ac = {
            "obs_dim": self.env.obs_dim if self.env else 30,
            "n_actions": self.env.n_actions if self.env else 12,
            "model_dir": ensure_model_dir(),
            **(self.config.get("agent", {})),
            **(agent_config or {}),
        }
        self.agent = PPOAgent(**ac)
        logger.info(f"[RLTrainer] Agent initialized: device={self.agent.device}")
        return self.agent

    def train(self, bars: list, n_iterations: int = 50, n_steps: int = 1024) -> dict:
        """完整训练管线"""
        self.prepare_env(bars)
        self.init_agent()

        history = self.agent.train_on_env(
            self.env,
            n_steps=n_steps,
            n_iterations=n_iterations,
            eval_interval=5,
            save_interval=10,
        )
        self.training_history = history
        return history

    def generate_signal(self, bars: list) -> dict:
        """用训练好的模型生成交易信号"""
        if self.agent is None:
            raise ValueError("No trained agent. Load a model first.")

        df = bars_to_dataframe(bars)
        if len(df) < 30:
            return {"signal": None, "reason": "insufficient_data"}

        env = GoldTradingEnv(df, **self.config.get("env", {}))
        obs = env.reset()
        current_price = df.iloc[-1]["close"]

        # 滚动推理
        last_action = 0
        action_probs = None
        for i in range(len(df) - 1):
            action, _, _ = self.agent.get_action(obs)
            with torch.no_grad():
                obs_t = torch.tensor(obs, dtype=torch.float32, device=self.agent.device).unsqueeze(0)
                dist = self.agent.model.actor.get_distribution(self.agent.model.feature_net(obs_t))
                action_probs = torch.softmax(dist.logits, dim=-1).cpu().numpy()[0]
            obs, _, done, _ = env.step(action)
            last_action = action
            if done:
                break

        # 判断信号方向
        target_pos = env._action_to_position(last_action)
        confidence = 0.0
        direction = "hold"
        reason = ""

        if action_probs is not None:
            # 置信度 = 前4个(做多) vs 中间4个(做空) vs 最后4个(平仓)的概率和
            long_prob = float(action_probs[1:5].sum()) if len(action_probs) > 5 else 0.0
            short_prob = float(action_probs[7:11].sum()) if len(action_probs) > 11 else 0.0
            hold_prob = float(action_probs[[0, 6]].sum()) if len(action_probs) > 6 else 0.3
            confidence = max(long_prob, short_prob, hold_prob)
        else:
            long_prob, short_prob, hold_prob = 0.3, 0.3, 0.4

        if target_pos > 0 and long_prob > 0.5:
            direction = "long"
            reason = f"RL PPO模型触发做多(置信度{long_prob:.1%})"
        elif target_pos < 0 and short_prob > 0.5:
            direction = "short"
            reason = f"RL PPO模型触发做空(置信度{short_prob:.1%})"
        else:
            direction = "hold"
            reason = "RL PPO模型建议观望"

        # 风控参数
        stop_loss = None
        take_profit = None
        if direction != "hold":
            atr = df.get("atr_14", pd.Series([current_price * 0.01] * len(df))).iloc[-1]
            stop_loss = round(current_price - atr * 2 / 1000, 2) if direction == "long" else round(current_price + atr * 2 / 1000, 2)

        result = {
            "signal": {
                "direction": direction,
                "price": round(current_price, 2),
                "confidence": round(min(confidence, 1.0), 4),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "reason": reason,
                "position": target_pos,
                "long_prob": round(long_prob, 4),
                "short_prob": round(short_prob, 4),
                "hold_prob": round(hold_prob, 4),
            },
            "metrics": env.get_metrics(),
        }
        return result

    def load_model(self, path: str):
        """加载训练好的模型"""
        self.init_agent()
        metrics = self.agent.load(path)
        self.model_path = path
        return metrics

    def list_models(self) -> list[dict]:
        """列出所有已训练的模型"""
        model_dir = ensure_model_dir()
        models = []
        if os.path.isdir(model_dir):
            for f in sorted(os.listdir(model_dir)):
                if f.startswith("ppo_") and f.endswith(".pt"):
                    full = os.path.join(model_dir, f)
                    size = os.path.getsize(full)
                    mtime = datetime.fromtimestamp(os.path.getmtime(full))
                    models.append({
                        "filename": f,
                        "path": full,
                        "size_bytes": size,
                        "modified": mtime.isoformat(),
                    })
        return models

    def train_async(self, bars: list, n_iterations: int = 50, n_steps: int = 1024) -> dict:
        """异步训练（在事件循环中运行）"""
        return asyncio.to_thread(self.train, bars, n_iterations, n_steps)
