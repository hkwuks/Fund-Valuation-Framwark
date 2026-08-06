"""XAU/USD 订单流微观结构特征提取"""
import numpy as np
import pandas as pd
from loguru import logger


def resample_features(df: pd.DataFrame, freq: str = "5min") -> pd.DataFrame:
    """从原始Tick数据聚合为指定频率的微观结构特征

    Args:
        df: tick数据，列 [datetime, bid, ask, bid_vol, ask_vol]
        freq: 聚合频率，如 '5min', '15min', '1h'

    Returns:
        DataFrame: 每行一个时间窗口的微观结构特征
    """
    df = df.set_index("datetime").sort_index()

    # 价格
    ask_ohlc = df["ask"].resample(freq).ohlc()
    bid_ohlc = df["bid"].resample(freq).ohlc()

    # 成交量
    bid_vol = df["bid_vol"].resample(freq).sum()
    ask_vol = df["ask_vol"].resample(freq).sum()
    tick_count = df["bid"].resample(freq).count()

    # 价差
    spread = df["spread"].resample(freq).mean()
    spread_min = df["spread"].resample(freq).min()
    spread_max = df["spread"].resample(freq).max()

    # 中间价
    mid = (df["bid"] + df["ask"]) / 2
    mid_ohlc = mid.resample(freq).ohlc()

    # 构造结果
    result = pd.DataFrame(index=ask_ohlc.index)
    result["ask_open"] = ask_ohlc["open"]
    result["ask_high"] = ask_ohlc["high"]
    result["ask_low"] = ask_ohlc["low"]
    result["ask_close"] = ask_ohlc["close"]
    result["bid_open"] = bid_ohlc["open"]
    result["bid_high"] = bid_ohlc["high"]
    result["bid_low"] = bid_ohlc["low"]
    result["bid_close"] = bid_ohlc["close"]
    result["mid_open"] = mid_ohlc["open"]
    result["mid_close"] = mid_ohlc["close"]

    # 成交量
    result["bid_volume"] = bid_vol
    result["ask_volume"] = ask_vol
    result["volume_imbalance"] = (bid_vol - ask_vol) / (bid_vol + ask_vol + 1e-10)
    result["tick_count"] = tick_count

    # 价差特征
    result["spread_mean"] = spread
    result["spread_min"] = spread_min
    result["spread_max"] = spread_max

    # 价格变化
    result["mid_return"] = result["mid_close"].pct_change()

    # Tick 强度 (成交量/频次)
    result["avg_tick_volume"] = (bid_vol + ask_vol) / (tick_count + 1e-10)

    return result.dropna()


def add_microstructure_features(df: pd.DataFrame, windows: list[int] = None) -> pd.DataFrame:
    """在聚合特征上添加微观结构衍生特征

    Args:
        df: resample_features 的输出
        windows: 滚动窗口列表

    Returns:
        DataFrame: 包含衍生特征的完整特征集
    """
    if windows is None:
        windows = [5, 10, 20]

    df = df.copy()

    for w in windows:
        # 价格动量
        df[f"ret_{w}"] = df["mid_close"].pct_change(w) * 100

        # 成交量变化
        total_vol = df["bid_volume"] + df["ask_volume"]
        df[f"vol_change_{w}"] = total_vol.pct_change(w) * 100

        # 不平衡趋势
        df[f"imbalance_mean_{w}"] = df["volume_imbalance"].rolling(w).mean()
        df[f"imbalance_std_{w}"] = df["volume_imbalance"].rolling(w).std()

        # 价差趋势
        df[f"spread_mean_{w}"] = df["spread_mean"].rolling(w).mean()
        df[f"spread_std_{w}"] = df["spread_mean"].rolling(w).std()

        # 波动率 (基于mid return)
        df[f"volatility_{w}"] = df["mid_return"].rolling(w).std() * 100

        # Tick 强度
        df[f"tick_intensity_{w}"] = df["tick_count"].rolling(w).mean()

        # 成交压力 (VPIN-like)
        signed_vol = df["volume_imbalance"] * total_vol
        df[f"vpin_{w}"] = signed_vol.rolling(w).sum() / (total_vol.rolling(w).sum() + 1e-10)

        # 价格位置
        roll_max = df["mid_close"].rolling(w).max()
        roll_min = df["mid_close"].rolling(w).min()
        df[f"price_position_{w}"] = ((df["mid_close"] - roll_min) / (roll_max - roll_min + 1e-10)) * 100

    return df.replace([np.inf, -np.inf], np.nan).fillna(0)


def compute_overnight_features(df_day: pd.DataFrame, df_next_day: pd.DataFrame) -> pd.Series:
    """计算 XAU/USD 隔夜特征（SGE 收盘 15:00 CST → 次日开盘 9:00 CST）

    SGE 交易时间: 9:00-15:00 CST = 1:00-7:00 UTC
    隔夜时段: 7:00 UTC (SGE 收盘) → 次日 1:00 UTC (SGE 开盘)
    使用 tick 数据:
      - 当天 7:00-23:59 UTC（收盘后）
      - 次日 0:00-0:59 UTC（开盘前）

    Returns:
        Series: 隔夜微观结构特征
    """
    mid = (df_day["bid"] + df_day["ask"]) / 2
    mid_next = (df_next_day["bid"] + df_next_day["ask"]) / 2

    # 收盘后时段: 7:00-23:59 UTC
    after_close = df_day[df_day["datetime"].dt.hour >= 7].copy()
    mid_ac = (after_close["bid"] + after_close["ask"]) / 2

    # 开盘前时段: 0:00-0:59 UTC
    before_open = df_next_day[df_next_day["datetime"].dt.hour < 1].copy()
    mid_bo = (before_open["bid"] + before_open["ask"]) / 2

    # 合并隔夜数据
    overnight = pd.concat([after_close, before_open]).sort_values("datetime")
    mid_on = (overnight["bid"] + overnight["ask"]) / 2

    if len(overnight) < 10:
        return pd.Series({
            k: 0.0 for k in [
                "on_return", "on_volatility", "on_volume_imbalance", "on_tick_count",
                "on_spread_avg", "on_range", "pm_return", "pm_volatility",
            ]
        })

    # 隔夜价格变化
    on_ret = (mid_on.iloc[-1] / mid_on.iloc[0] - 1) * 100 if len(mid_on) > 1 else 0.0
    # 开盘前最后1小时（0:00-1:00 UTC）
    pm_ret = (mid_bo.iloc[-1] / mid_bo.iloc[0] - 1) * 100 if len(mid_bo) > 1 else 0.0

    bid_vol = overnight["bid_vol"].sum()
    ask_vol = overnight["ask_vol"].sum()
    total_vol = bid_vol + ask_vol

    features = {
        "on_return": on_ret,
        "on_volatility": mid_on.pct_change().std() * 100 if len(mid_on) > 5 else 0.0,
        "on_volume_imbalance": (bid_vol - ask_vol) / (total_vol + 1e-10),
        "on_tick_count": len(overnight),
        "on_spread_avg": (overnight["ask"] - overnight["bid"]).mean() / mid_on.mean() * 10000,
        "on_range": (mid_on.max() - mid_on.min()) / mid_on.mean() * 100,
        "pm_return": pm_ret,
        "pm_volatility": mid_bo.pct_change().std() * 100 if len(mid_bo) > 5 else 0.0,
    }
    return pd.Series(features)


def compute_overnight_for_all_dates(daily_tick_data: dict[tuple[str, str], pd.DataFrame],
                                     dates: list[str]) -> pd.DataFrame:
    """为所有日期计算隔夜特征

    Args:
        daily_tick_data: {(date, next_date): df} 缓存
        dates: 排序后的日期列表

    Returns:
        DataFrame: 每行一个交易日的隔夜特征
    """
    rows = []
    for i in range(len(dates) - 1):
        d = dates[i]
        nd = dates[i + 1]
        df_day = daily_tick_data.get(d)
        df_next = daily_tick_data.get(nd)
        if df_day is None or df_next is None or df_day.empty or df_next.empty:
            continue
        feats = compute_overnight_features(df_day, df_next)
        feats["date"] = nd  # 隔夜特征归属到开盘日
        rows.append(feats)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def compute_daily_orderflow_features(df: pd.DataFrame) -> pd.Series:
    """从日频 tick 数据计算每日微观结构特征摘要

    用于作为 RL 的输入特征

    Returns:
        Series: 当日微观结构特征
    """
    # 当日价格
    mid = (df["bid"] + df["ask"]) / 2
    bid_vol = df["bid_vol"].sum()
    ask_vol = df["ask_vol"].sum()
    total_vol = bid_vol + ask_vol

    features = {
        "mid_open": mid.iloc[0],
        "mid_close": mid.iloc[-1],
        "mid_high": mid.max(),
        "mid_low": mid.min(),
        "mid_return": (mid.iloc[-1] / mid.iloc[0] - 1) * 100,
        "total_volume": total_vol,
        "tick_count": len(df),
        "volume_imbalance": (bid_vol - ask_vol) / (total_vol + 1e-10),
        "avg_spread_bps": (df["ask"] - df["bid"]).mean() / mid.mean() * 10000,
        "avg_tick_volume": total_vol / len(df),
        "day_volatility": mid.pct_change().std() * 100,
        "max_spread": (df["ask"] - df["bid"]).max(),
        "min_spread": (df["ask"] - df["bid"]).min(),
    }

    # 按小时统计
    hourly_vol = df.set_index("datetime").resample("1h").apply(
        lambda g: g["bid_vol"].sum() + g["ask_vol"].sum()
    )
    if not hourly_vol.empty and len(hourly_vol.dropna()) > 1:
        features["hourly_vol_std"] = float(hourly_vol.std())
        features["peak_hour_ratio"] = float(hourly_vol.max() / (hourly_vol.mean() + 1e-10))
    else:
        features["hourly_vol_std"] = 0.0
        features["peak_hour_ratio"] = 1.0

    return pd.Series(features)


def prepare_features_for_prediction(daily_features: pd.DataFrame, windows: list[int] = None) -> pd.DataFrame:
    """为预测模型准备特征（含滞后特征）

    Args:
        daily_features: compute_daily_orderflow_features 的输出（按天拼接）
        windows: 滞后窗口

    Returns:
        DataFrame: 用于监督学习的特征矩阵
    """
    if windows is None:
        windows = [1, 3, 5, 10]

    df = daily_features.copy()

    # 关键特征的滞后值
    for col in ["mid_return", "volume_imbalance", "avg_spread_bps", "day_volatility",
                "total_volume", "tick_count", "avg_tick_volume"]:
        if col in df.columns:
            for w in windows:
                df[f"{col}_lag_{w}"] = df[col].shift(w)
                # 差分
                df[f"{col}_diff_{w}"] = df[col].diff(w)

    return df.replace([np.inf, -np.inf], np.nan).fillna(0)