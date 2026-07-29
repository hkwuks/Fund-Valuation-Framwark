"""市场状态检测 — 趋势/震荡/高波动三分类"""
import numpy as np
import pandas as pd
from loguru import logger


class MarketRegimeDetector:
    """
    市场状态检测 — 趋势/震荡/高波动三分类

    方法：
    - ATR/price 比率 → 高波动判定
    - ADX → 趋势强度
    - MA排列 → 趋势方向
    """

    def __init__(self, lookback: int = 20):
        self.lookback = lookback

    def detect(self, df: pd.DataFrame) -> str:
        """检测当前市场状态"""
        regimes = self.detect_window(df)
        if isinstance(regimes, pd.Series) and len(regimes) > 0:
            return regimes.iloc[-1]
        return "ranging"

    def detect_window(self, df: pd.DataFrame) -> pd.Series:
        """返回每个时间点的市场状态序列"""
        df = df.copy()
        if "close" not in df.columns:
            return pd.Series(["ranging"] * len(df))

        close = df["close"]
        high = df.get("high", close)
        low = df.get("low", close)
        volume = df.get("volume", pd.Series([0] * len(df)))

        # 1. ATR/price 比率
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(self.lookback).mean()
        atr_ratio = atr / (close + 1e-8)

        # 高波动阈值
        vol_threshold = atr_ratio.rolling(60).mean() + atr_ratio.rolling(60).std()
        is_volatile = atr_ratio > vol_threshold

        # 2. ADX
        atr14 = tr.rolling(14).mean()
        plus_dm = (high.diff() > low.diff().abs()) * high.diff().clip(0)
        minus_dm = (low.diff().abs() > high.diff()) * low.diff().abs().clip(0)
        plus_di = 100 * plus_dm.rolling(14).mean() / (atr14 + 1e-10)
        minus_di = 100 * minus_dm.rolling(14).mean() / (atr14 + 1e-10)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
        adx = dx.rolling(14).mean()

        # 3. MA排列
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        ma_trend = (ma20 > ma50).astype(int)

        # 综合判断
        regimes = pd.Series(["ranging"] * len(df), index=df.index)
        # 高波动优先
        regimes[is_volatile.fillna(False)] = "volatile"
        # 趋势（非高波动且ADX>25）
        trending = (adx > 25) & (~is_volatile.fillna(False))
        regimes[trending.fillna(False)] = "trending"

        return regimes

    def regime_stats(self, df: pd.DataFrame, returns: pd.Series = None) -> dict:
        """返回各状态下的Sharpe/波动率/胜率"""
        regimes = self.detect_window(df)
        if returns is None:
            returns = df["close"].pct_change()

        stats = {}
        for regime in ["trending", "ranging", "volatile"]:
            mask = regimes == regime
            r = returns[mask].dropna()
            if len(r) < 5:
                stats[regime] = {"n": int(mask.sum()), "sharpe": 0, "volatility": 0, "win_rate": 0}
                continue
            sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 1e-10 else 0
            stats[regime] = {
                "n": int(mask.sum()),
                "sharpe": round(float(sharpe), 3),
                "volatility": round(float(r.std() * np.sqrt(252) * 100), 2),
                "win_rate": round(float((r > 0).mean() * 100), 1),
                "mean_return": round(float(r.mean() * 100), 3),
            }

        return stats