"""多周期特征工程 — 日/4h/1h"""
import pandas as pd
import numpy as np
from backend.gold.ml.features import FeatureEngineer


class MultiTimeframeFeatureEngineer:
    """多周期特征工程 — 从日/4h/1h提取特征，对齐拼接"""

    def __init__(self):
        self.fe = FeatureEngineer()
        self._feature_names: list[str] = []

    def extract_features(self, df_daily: pd.DataFrame,
                         df_4h: pd.DataFrame = None,
                         df_1h: pd.DataFrame = None) -> pd.DataFrame:
        """每个周期独立计算指标，按前缀拼接对齐到日频"""
        all_feats = []

        # 日频
        daily_feats = self.fe.create_technical_features(df_daily)
        daily_feats = daily_feats.select_dtypes(include=[np.number])
        daily_feats = daily_feats.add_prefix("daily_")
        all_feats.append(daily_feats)

        # 4h — 取每日最后一个值
        if df_4h is not None and len(df_4h) > 0:
            f4h = self.fe.create_technical_features(df_4h)
            f4h = f4h.select_dtypes(include=[np.number])
            f4h = f4h.add_prefix("tf_4h_")
            f4h = self._resample_to_daily(f4h, df_4h)
            all_feats.append(f4h)

        # 1h — 同上
        if df_1h is not None and len(df_1h) > 0:
            f1h = self.fe.create_technical_features(df_1h)
            f1h = f1h.select_dtypes(include=[np.number])
            f1h = f1h.add_prefix("tf_1h_")
            f1h = self._resample_to_daily(f1h, df_1h)
            all_feats.append(f1h)

        combined = pd.concat(all_feats, axis=1)
        self._feature_names = list(combined.columns)
        return combined

    def get_feature_names(self) -> list[str]:
        return self._feature_names.copy()

    @staticmethod
    def _resample_to_daily(features: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
        """从快周期DataFrame取每日最后一个值，对齐日频索引"""
        if "date" in raw.columns:
            dates = pd.to_datetime(raw["date"])
        else:
            dates = features.index
        if isinstance(dates, pd.Series):
            daily_dates = dates.dt.strftime("%Y-%m-%d")
        else:
            daily_dates = dates.strftime("%Y-%m-%d")
        daily_idx = pd.DatetimeIndex(daily_dates)
        last_of_day = features.groupby(daily_idx).last()
        last_of_day.index = last_of_day.index.strftime("%Y-%m-%d")
        return last_of_day