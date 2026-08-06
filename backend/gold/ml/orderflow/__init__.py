"""Dukascopy XAU/USD 历史Tick数据下载器"""
import subprocess, os, glob
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "backend", "gold", "xau_tick")


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_DIR


def _tick_csv_path(date: str) -> str:
    return os.path.join(ensure_data_dir(), f"XAUUSD_{date}.csv")


def download_tick(start_date: str, end_date: str, overwrite: bool = False):
    """下载 XAU/USD tick 数据，使用 duka CLI

    Args:
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        overwrite: 是否覆盖已有文件
    """
    from datetime import datetime as dt
    s = dt.strptime(start_date, "%Y-%m-%d")
    e = dt.strptime(end_date, "%Y-%m-%d")
    days = (e - s).days
    if days < 0:
        raise ValueError("end_date must be after start_date")

    # 逐日下载，避免一次性拉太多
    for i in range(days + 1):
        d = s + timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        out = _tick_csv_path(date_str)
        if not overwrite and os.path.exists(out) and os.path.getsize(out) > 0:
            logger.debug(f"Skip existing: {out}")
            continue

        try:
            result = subprocess.run(
                ["python3", "-m", "duka.main", "XAUUSD", "-d", date_str, "-f", ensure_data_dir()],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                # duka 输出 CSV 文件名可预测，检查
                csv_path = os.path.join(ensure_data_dir(), f"XAUUSD_{date_str.replace('-', '')}.csv")
                if os.path.exists(csv_path):
                    os.rename(csv_path, os.path.join(ensure_data_dir(), f"XAUUSD_{date_str}.csv"))
                logger.info(f"Downloaded XAUUSD {date_str}")
            else:
                logger.warning(f"Failed {date_str}: {result.stderr.strip()[:200]}")
        except Exception as e:
            logger.warning(f"Error downloading {date_str}: {e}")


def list_available_dates() -> list[str]:
    """列出已下载的日期"""
    pattern = os.path.join(ensure_data_dir(), "XAUUSD_*.csv")
    files = sorted(glob.glob(pattern))
    dates = []
    for f in files:
        name = os.path.basename(f)
        date_part = name.replace("XAUUSD_", "").replace(".csv", "")
        dates.append(date_part)
    return dates


def load_tick_csv(date: str) -> "pd.DataFrame":
    """加载单日tick数据"""
    import pandas as pd
    path = _tick_csv_path(date)
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


if __name__ == "__main__":
    logger.info(f"Data dir: {ensure_data_dir()}")
    avail = list_available_dates()
    logger.info(f"Available: {len(avail)} days")