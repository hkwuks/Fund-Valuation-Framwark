"""RL专用的Walk-Forward验证框架"""
import os
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
import pandas as pd
from loguru import logger

from .env import GoldTradingEnv
from .agent import PPOAgent, PPORolloutBuffer
from ..features import FeatureEngineer


# 模型保存目录
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "backend", "gold", "rl_models")


def _ensure_model_dir():
    os.makedirs(MODEL_DIR, exist_ok=True)
    return MODEL_DIR


def _bars_to_dataframe(bars: list) -> pd.DataFrame:
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

    df_feat["tick_count"] = np.random.poisson(500, len(df_feat))
    df_feat["buy_ratio"] = 0.5 + 0.1 * np.tanh(df_feat.get("returns", pd.Series([0] * len(df_feat))) * 10)
    df_feat["vol_imbalance"] = df_feat.get("obv", pd.Series([0] * len(df_feat))).diff().fillna(0)
    df_feat["vol_imbalance"] = np.tanh(df_feat["vol_imbalance"] / 1e6)
    df_feat["spread"] = (df_feat["high"] - df_feat["low"]) / (df_feat["close"] + 1e-8) * 0.01
    for col in ["DXY_change", "US10Y_change", "VIX_value", "gold_dxy_ratio"]:
        if col not in df_feat.columns:
            df_feat[col] = 0.0

    df_feat = df_feat.replace([np.inf, -np.inf], np.nan).fillna(0)
    return df_feat


@dataclass
class FoldResult:
    """单个fold的验证结果"""
    fold_index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_bars: int
    test_bars: int
    oos_return_pct: float
    oos_sharpe: float
    oos_max_dd_pct: float
    oos_win_rate: float
    oos_trades: int
    oos_volatility: float
    training_loss: list = field(default_factory=list)
    model_path: str = ""


class RLWalkForwardValidator:
    """
    RL专用的Walk-Forward验证

    时间线分割:
    |---- Train ----|--Embargo--|--Test--|
                     ^ 去掉embargo天

    - 每个fold独立训练PPO，在OOS测试集上评估
    - 严格禁止数据前视（embargo + purging）
    - 输出每个fold的OOS Sharp、Return、DD
    - 最终输出跨fold的统计量（均值、标准差、最差case）
    """

    def __init__(
        self,
        n_splits: int = 5,
        train_ratio: float = 0.7,
        embargo_days: int = 20,
        rl_train_kwargs: dict = None,
    ):
        self.n_splits = n_splits
        self.train_ratio = train_ratio
        self.embargo_days = embargo_days
        self.rl_train_kwargs = rl_train_kwargs or {
            "n_iterations": 30,
            "n_steps": 1024,
        }
        self.fold_results: list[FoldResult] = []
        self._df: Optional[pd.DataFrame] = None

    def validate_from_bars(self, bars: list) -> dict:
        """从GoldBarData列表执行Walk-Forward验证"""
        df = _bars_to_dataframe(bars)
        return self.validate(df)

    def validate(self, df: pd.DataFrame) -> dict:
        """执行Walk-Forward验证，返回完整结果"""
        self._df = df
        self.fold_results.clear()

        n = len(df)
        if n < 200:
            return {"error": f"数据不足: {n} 根 (最少 200)"}

        # 每个fold的测试窗口大小
        total_test_bars = int((1 - self.train_ratio) * n)
        test_window = max(total_test_bars // self.n_splits, 20)
        test_start_base = n - total_test_bars

        folds = []
        for i in range(self.n_splits):
            test_start = test_start_base + i * test_window
            test_end = min(test_start + test_window, n)
            if test_end - test_start < 20:
                break

            # embargo: 训练集尾部去掉embargo_days天，防止数据前视
            train_end = test_start - self.embargo_days
            if train_end < 100:
                continue

            result = self._run_fold(i, df, train_end, test_start, test_end)
            if result is not None:
                folds.append(result)

        if not folds:
            return {"error": "无有效fold"}

        self.fold_results = folds
        return self._aggregate(folds)

    def _run_fold(
        self,
        fold_index: int,
        df: pd.DataFrame,
        train_end: int,
        test_start: int,
        test_end: int,
    ) -> Optional[FoldResult]:
        """运行单个fold: 训练PPO -> 在OOS测试集上评估"""
        train_df = df.iloc[:train_end].reset_index(drop=True)
        test_df = df.iloc[test_start:test_end].reset_index(drop=True)

        if len(train_df) < 50 or len(test_df) < 50:
            logger.warning(f"[RLWF] Fold {fold_index}: 数据不足, 跳过")
            return None

        logger.info(
            f"[RLWF] Fold {fold_index}: train={len(train_df)} bars, "
            f"test={len(test_df)} bars"
        )

        # ---- 1. 创建训练环境 + 智能体 ----
        env = GoldTradingEnv(train_df)
        agent = PPOAgent(
            obs_dim=env.obs_dim,
            n_actions=env.n_actions,
            model_dir=_ensure_model_dir(),
        )

        # ---- 2. 训练 ----
        kwargs = self.rl_train_kwargs
        history = agent.train_on_env(
            env,
            n_steps=kwargs.get("n_steps", 1024),
            n_iterations=kwargs.get("n_iterations", 30),
            eval_interval=0,
            save_interval=0,
        )

        # ---- 3. 保存模型 ----
        model_path = os.path.join(
            _ensure_model_dir(), f"rlwf_fold_{fold_index}.pt"
        )
        agent.save(model_path, {"fold": fold_index, "train_bars": len(train_df)})

        # ---- 4. OOS测试集评估 ----
        test_env = GoldTradingEnv(test_df)
        obs = test_env.reset()
        done = False
        while not done:
            action, _, _ = agent.get_action(obs, deterministic=True)
            obs, _, done, _ = test_env.step(action)
        test_metrics = test_env.get_metrics()

        # 提取训练loss曲线
        losses = [it.get("train_loss", 0) for it in history.get("iterations", [])]

        return FoldResult(
            fold_index=fold_index,
            train_start=0,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            train_bars=len(train_df),
            test_bars=len(test_df),
            oos_return_pct=test_metrics["total_return_pct"],
            oos_sharpe=test_metrics["sharpe_ratio"],
            oos_max_dd_pct=test_metrics["max_drawdown_pct"],
            oos_win_rate=test_metrics["win_rate_pct"],
            oos_trades=test_metrics["total_trades"],
            oos_volatility=test_metrics["volatility"],
            training_loss=losses,
            model_path=model_path,
        )

    def summary(self) -> dict:
        """返回汇总统计：平均OOS Sharpe、标准差、最差折"""
        if not self.fold_results:
            return {"error": "No fold results"}
        return self._aggregate(self.fold_results)

    @staticmethod
    def _aggregate(folds: list[FoldResult]) -> dict:
        """聚合所有fold结果"""
        returns = [f.oos_return_pct for f in folds]
        sharpes = [f.oos_sharpe for f in folds]
        dds = [f.oos_max_dd_pct for f in folds]
        win_rates = [f.oos_win_rate for f in folds]

        return {
            "method": "rl_walk_forward",
            "n_splits": len(folds),
            "oos_avg_return_pct": round(float(np.mean(returns)), 2),
            "oos_std_return_pct": round(float(np.std(returns)), 2),
            "oos_min_return_pct": round(float(min(returns)), 2),
            "oos_max_return_pct": round(float(max(returns)), 2),
            "oos_avg_sharpe": round(float(np.mean(sharpes)), 2),
            "oos_std_sharpe": round(float(np.std(sharpes)), 2),
            "oos_min_sharpe": round(float(min(sharpes)), 2),
            "oos_avg_max_dd_pct": round(float(np.mean(dds)), 2),
            "oos_worst_dd_pct": round(float(min(dds)), 2),
            "oos_avg_win_rate": round(float(np.mean(win_rates)), 1),
            "positive_return_ratio": round(
                sum(1 for r in returns if r > 0) / len(returns) * 100, 1
            ),
            "folds": [
                {
                    "fold": f.fold_index,
                    "train_bars": f.train_bars,
                    "test_bars": f.test_bars,
                    "oos_return_pct": f.oos_return_pct,
                    "oos_sharpe": f.oos_sharpe,
                    "oos_max_dd_pct": f.oos_max_dd_pct,
                    "oos_win_rate": f.oos_win_rate,
                    "oos_trades": f.oos_trades,
                    "oos_volatility": f.oos_volatility,
                    "model_path": f.model_path,
                }
                for f in folds
            ],
        }