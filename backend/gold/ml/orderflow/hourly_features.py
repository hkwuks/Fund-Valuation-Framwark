"""XAU/USD tick → 1小时 bar 特征 + COMEX 对齐"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from loguru import logger

from .features import resample_features, add_microstructure_features
from .downloader import load_date, list_available_dates


def build_hourly_xau_features(dates: list[str] = None) -> pd.DataFrame:
    """从所有日期的 tick 数据构建连续的小时级 XAU/USD 特征

    Returns:
        DataFrame: 每行1小时, chron-indexed, 含 microstructure 特征
    """
    if dates is None:
        dates = list_available_dates()

    all_hourly = []
    for d in dates:
        df = load_date(d)
        if df.empty or len(df) < 1000:
            continue
        hourly = resample_features(df, "1h")
        if hourly.empty:
            continue
        hourly["date_str"] = d
        all_hourly.append(hourly)

    if not all_hourly:
        raise ValueError("No hourly data generated")

    result = pd.concat(all_hourly).sort_index()
    # 去重（相邻日期的边界可能重叠）
    result = result[~result.index.duplicated(keep="last")]
    return result


def add_microstructure_features_v2(df: pd.DataFrame, windows: list[int] = None) -> pd.DataFrame:
    """在小时级特征上添加衍生特征（v2: 更稳定的计算）"""
    if windows is None:
        windows = [4, 8, 24]  # 4h, 8h, 24h

    df = df.copy()

    for w in windows:
        # 价格动量
        df[f"ret_{w}h"] = df["mid_close"].pct_change(w) * 100

        # 成交量变化
        total_vol = df["bid_volume"] + df["ask_volume"]
        df[f"vol_change_{w}h"] = total_vol.pct_change(w) * 100

        # 不平衡趋势
        df[f"imbalance_mean_{w}h"] = df["volume_imbalance"].rolling(w).mean()
        df[f"imbalance_std_{w}h"] = df["volume_imbalance"].rolling(w).std()

        # 价差趋势
        df[f"spread_mean_{w}h"] = df["spread_mean"].rolling(w).mean()
        df[f"spread_std_{w}h"] = df["spread_mean"].rolling(w).std()

        # 波动率
        df[f"volatility_{w}h"] = df["mid_return"].rolling(w).std() * 100

        # VPIN
        signed_vol = df["volume_imbalance"] * total_vol
        df[f"vpin_{w}h"] = signed_vol.rolling(w).sum() / (total_vol.rolling(w).sum() + 1e-10)

        # 价格位置
        roll_max = df["mid_close"].rolling(w).max()
        roll_min = df["mid_close"].rolling(w).min()
        df[f"price_position_{w}h"] = ((df["mid_close"] - roll_min) / (roll_max - roll_min + 1e-10)) * 100

        # Tick 强度变化
        df[f"tick_intensity_{w}h"] = df["tick_count"].rolling(w).mean()

    return df.replace([np.inf, -np.inf], np.nan).fillna(0)


def load_comex_hourly() -> pd.DataFrame:
    """从 yfinance 下载 COMEX 黄金期货小时数据

    Returns:
        DataFrame: 每小时 OHLCV, index=datetime UTC
    """
    import yfinance as yf
    gc = yf.download("GC=F", period="2y", interval="1h", progress=False)
    if gc.empty:
        raise ValueError("No COMEX hourly data")
    # Flatten MultiIndex columns
    gc.columns = [c[0].lower() for c in gc.columns]
    gc.index = gc.index.tz_localize(None)  # 去掉时区信息
    gc = gc.sort_index()
    return gc


def align_xau_comex(
    xau_hourly: pd.DataFrame,
    comex_hourly: pd.DataFrame,
) -> pd.DataFrame:
    """对齐 XAU/USD 小时特征与 COMEX 小时数据

    XAU/USD 用当前小时的 microstructure 特征预测 COMEX 下一小时收益
    对齐策略: XAU_T → COMEX_{T+1}
    """
    xau = xau_hourly.copy()
    comex = comex_hourly.copy()

    # 确保无时区
    xau.index = xau.index.tz_localize(None) if hasattr(xau.index, 'tz') else xau.index
    comex.index = comex.index.tz_localize(None) if hasattr(comex.index, 'tz') else comex.index

    # 确保 XAU 是整点小时索引
    xau = xau[~xau.index.duplicated(keep="last")]
    comex = comex[~comex.index.duplicated(keep="last")]

    # COMEX 收益: 用当前 close 算下一小时收益
    comex = comex.copy()
    comex["comex_return"] = comex["close"].pct_change().shift(-1)  # T 时刻的 close 预测 T+1 的收益

    # 对齐: XAU 当前小时特征 → COMEX 当前小时（用 COMEX close 算 next hour return）
    # 只保留双方都有数据的行
    common_idx = xau.index.intersection(comex.index)
    xau_aligned = xau.loc[common_idx]
    comex_aligned = comex.loc[common_idx]

    # 去掉 COMEX 中与 XAU 重复的列
    xau_cols = set(xau_aligned.columns)
    comex_cols = {c for c in comex_aligned.columns if c not in xau_cols or c == "comex_return"}
    comex_keep = comex_aligned[list(comex_cols)]

    combined = comex_keep.join(xau_aligned, how="inner")

    return combined.dropna(subset=["comex_return"])


def prepare_hourly_rl_data(
    xau_hourly: pd.DataFrame,
    comex_hourly: pd.DataFrame,
    window_size: int = 48,
) -> pd.DataFrame:
    """为 RL 训练准备小时级数据

    1. 添加 microstructure 衍生特征
    2. 对齐 XAU → COMEX
    3. 添加技术指标
    """
    from ..features import FeatureEngineer

    # 衍生特征
    xau_feat = add_microstructure_features_v2(xau_hourly)

    # 对齐
    combined = align_xau_comex(xau_feat, comex_hourly)

    if len(combined) < window_size + 100:
        raise ValueError(f"Too few aligned rows: {len(combined)}")

    # 技术指标（仅用 COMEX 价格）
    fe = FeatureEngineer()
    tech = fe.create_technical_features(
        combined[["open", "high", "low", "close", "volume"]].rename(
            columns={"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
        )
    )

    # 选择关键技术指标
    for col in ["rsi_14", "macd_diff", "atr_ratio", "bb_position", "volume_ma_ratio"]:
        combined[col] = tech[col].values if col in tech.columns else 0.0

    return combined.replace([np.inf, -np.inf], np.nan).fillna(0)