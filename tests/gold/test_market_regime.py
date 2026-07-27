"""测试市场状态检测"""
import numpy as np
import pandas as pd
from backend.gold.ml.market_regime import MarketRegimeDetector


def _make_trend_df(n=200):
    """上涨趋势数据"""
    closes = 4000 + np.linspace(0, 200, n) + np.random.randn(n) * 10
    return pd.DataFrame({
        "close": closes, "high": closes*1.01, "low": closes*0.99,
        "volume": np.random.randint(1000, 10000, n),
    })


def _make_ranging_df(n=200):
    """震荡数据"""
    closes = 4000 + np.random.randn(n) * 20
    return pd.DataFrame({
        "close": closes, "high": closes*1.01, "low": closes*0.99,
        "volume": np.random.randint(1000, 10000, n),
    })


class TestMarketRegimeDetector:
    def test_detect_trend(self):
        df = _make_trend_df(300)
        d = MarketRegimeDetector()
        regime = d.detect(df)
        assert regime in ("trending", "ranging", "volatile")

    def test_detect_window(self):
        df = _make_trend_df(300)
        d = MarketRegimeDetector()
        regimes = d.detect_window(df)
        assert len(regimes) == len(df)
        assert all(r in ("trending", "ranging", "volatile") for r in regimes)

    def test_regime_stats(self):
        df = _make_trend_df(300)
        returns = df["close"].pct_change()
        d = MarketRegimeDetector()
        stats = d.regime_stats(df, returns)
        for regime in ("trending", "ranging", "volatile"):
            assert regime in stats
            assert "sharpe" in stats[regime]
            assert "n" in stats[regime]

    def test_empty_df(self):
        df = pd.DataFrame({"close": [4000]})
        d = MarketRegimeDetector()
        regime = d.detect(df)
        assert regime == "ranging"