"""订单流信号生成器（非 ML，直接计算微观结构信号）

从订单流特征中提取信号，直接作为 RL 输入。

日频信号（基于日间 XAU/USD 特征预测 SGE 日收益）:
  - XAU 昨日波动率 z-score: w=-0.50 (r=-0.32)
  - XAU 昨日收益率 z-score: w=+0.40 (r=+0.20)
  - XAU 昨日价差 z-score:  w=+0.15
  - 成交量不平衡:          w=+0.10

隔夜信号（基于 XAU/USD 隔夜特征预测 SGE 日内收益）:
  - 隔夜 XAU 收益:  w=+0.50 (直接决定 SGE 开盘价)
  - 隔夜波动率:    w=-0.30
  - 价差:          w=+0.10
  - 成交量不平衡:  w=+0.10
"""
import os, joblib
import numpy as np
import pandas as pd
from loguru import logger

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "backend", "gold", "orderflow_models")


def ensure_dir():
    os.makedirs(MODEL_DIR, exist_ok=True)
    return MODEL_DIR


def compute_flow_signals(daily_features: pd.DataFrame) -> pd.DataFrame:
    """从日频订单流特征计算 RL 输入信号"""
    df = daily_features.copy()
    w = 20

    xau_ret = df.get("mid_return", 0) / 100
    vol = df.get("day_volatility", 0) / 100
    spread = df.get("avg_spread_bps", 0)
    vi = df.get("volume_imbalance", 0)

    xau_z = (xau_ret - xau_ret.rolling(w).mean()) / (xau_ret.rolling(w).std() + 1e-10)
    vol_z = (vol - vol.rolling(w).mean()) / (vol.rolling(w).std() + 1e-10)
    spread_z = (spread - spread.rolling(w).mean()) / (spread.rolling(w).std() + 1e-10)

    raw = xau_z.shift(1) * 0.40 - vol_z.shift(1) * 0.50 + spread_z.shift(1) * 0.15 + vi.shift(1) * 0.10

    df["flow_signal"] = (raw.clip(-2, 2) + 2) / 4
    df["flow_signal"] = df["flow_signal"].fillna(0.5)
    df["flow_confidence"] = (raw.abs() / 2).clip(0, 1).fillna(0.0)

    return df[["flow_signal", "flow_confidence"]]


def compute_overnight_signals(overnight_features: pd.DataFrame) -> pd.DataFrame:
    """从隔夜 XAU/USD 特征计算 RL 输入信号

    隔夜特征比日频特征预测力强得多:
      - on_return（隔夜 XAU 收益）直接决定 SGE 开盘价
      - 日内 SGE 走势主要受开盘价 + 日内 XAU 影响
    """
    df = overnight_features.copy()
    w = 20

    on_ret = df.get("on_return", 0) / 100
    on_vol = df.get("on_volatility", 0) / 100
    on_spread = df.get("on_spread_avg", 0)
    on_vi = df.get("on_volume_imbalance", 0)
    pm_ret = df.get("pm_return", 0) / 100

    on_ret_z = (on_ret - on_ret.rolling(w).mean()) / (on_ret.rolling(w).std() + 1e-10)
    on_vol_z = (on_vol - on_vol.rolling(w).mean()) / (on_vol.rolling(w).std() + 1e-10)
    on_spread_z = (on_spread - on_spread.rolling(w).mean()) / (on_spread.rolling(w).std() + 1e-10)
    pm_ret_z = (pm_ret - pm_ret.rolling(w).mean()) / (pm_ret.rolling(w).std() + 1e-10)

    # 隔夜收益是主导信号，直接决定 SGE 开盘方向
    raw = on_ret_z * 0.50 - on_vol_z * 0.30 + on_spread_z * 0.10 + on_vi * 0.10 + pm_ret_z * 0.15

    df["flow_signal"] = (raw.clip(-2, 2) + 2) / 4
    df["flow_signal"] = df["flow_signal"].fillna(0.5)
    df["flow_confidence"] = (raw.abs() / 2).clip(0, 1).fillna(0.0)

    return df[["flow_signal", "flow_confidence"]]


class OrderFlowSignals:
    """订单流信号生成器（无 ML，纯统计信号）"""

    def compute(self, daily_features: pd.DataFrame) -> pd.DataFrame:
        return compute_flow_signals(daily_features)

    def save(self, path: str = None):
        if path is None:
            path = os.path.join(ensure_dir(), "flow_signal_params.joblib")
        joblib.dump({"method": "statistical_signals", "version": 1}, path)
        logger.info(f"[FlowSignals] saved to {path}")

    def load(self, path: str):
        logger.info(f"[FlowSignals] loaded from {path}")