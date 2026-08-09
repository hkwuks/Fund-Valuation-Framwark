"""测试多周期特征"""
import numpy as np
import pandas as pd
from backend.gold.ml.multi_timeframe import MultiTimeframeFeatureEngineer


def _make_df(n=200, prefix=""):
    closes = 4000 + np.cumsum(np.random.randn(n) * 5)
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="D"),
        "close": closes, "open": closes*0.999, "high": closes*1.005,
        "low": closes*0.995, "volume": np.random.randint(1000, 10000, n),
    })
    return df


class TestMultiTimeframeFeatureEngineer:
    def test_daily_only(self):
        df = _make_df(200)
        mfe = MultiTimeframeFeatureEngineer()
        result = mfe.extract_features(df)
        assert result is not None
        assert len(result) > 0
        names = mfe.get_feature_names()
        assert all(n.startswith("daily_") for n in names)

    def test_daily_and_4h(self):
        daily = _make_df(100)
        f4h = _make_df(400)
        mfe = MultiTimeframeFeatureEngineer()
        result = mfe.extract_features(daily, df_4h=f4h)
        names = mfe.get_feature_names()
        daily_names = [n for n in names if n.startswith("daily_")]
        tf4h_names = [n for n in names if n.startswith("tf_4h_")]
        assert len(daily_names) > 0
        assert len(tf4h_names) > 0

    def test_all_three(self):
        daily = _make_df(100)
        f4h = _make_df(400)
        f1h = _make_df(1600)
        mfe = MultiTimeframeFeatureEngineer()
        result = mfe.extract_features(daily, df_4h=f4h, df_1h=f1h)
        names = mfe.get_feature_names()
        assert any(n.startswith("daily_") for n in names)
        assert any(n.startswith("tf_4h_") for n in names)
        assert any(n.startswith("tf_1h_") for n in names)