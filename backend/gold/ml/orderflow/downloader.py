"""Dukascopy XAU/USD 历史Tick数据下载器（直接下载 .bi5 + 解码）"""
import os, struct, lzma, urllib.request, urllib.error, glob
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
import numpy as np
from loguru import logger

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "data", "backend", "gold", "xau_tick")
TICK_DIR = os.path.join(DATA_DIR, "raw")
CSV_DIR = os.path.join(DATA_DIR, "csv")


def ensure_dirs():
    for d in [DATA_DIR, TICK_DIR, CSV_DIR]:
        os.makedirs(d, exist_ok=True)


def _bi5_url(dt: datetime) -> str:
    """Dukascopy 的 month 从 0 开始"""
    return (f"https://datafeed.dukascopy.com/datafeed/XAUUSD/"
            f"{dt.year}/{dt.month - 1:02d}/{dt.day:02d}/{dt.hour:02d}h_ticks.bi5")


def _bi5_path(dt: datetime) -> str:
    return os.path.join(TICK_DIR, f"XAUUSD_{dt.strftime('%Y%m%d_%H')}.bi5")


def _csv_path(date: str) -> str:
    return os.path.join(CSV_DIR, f"XAUUSD_{date}.csv")


def download_hour(dt: datetime, retries: int = 2) -> bool:
    """下载单个小时 .bi5 文件"""
    path = _bi5_path(dt)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return True
    url = _bi5_url(dt)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            if len(data) > 0:
                with open(path, "wb") as f:
                    f.write(data)
                return True
            return False
        except urllib.request.HTTPError as e:
            if e.code == 404:
                return False  # 无数据，快速跳过
            if attempt < retries - 1:
                continue
        except Exception:
            if attempt < retries - 1:
                continue
    return False


def _market_hours(dt: datetime) -> list[int]:
    """XAU/USD 交易时段: 周日 21:00 ~ 周五 22:00 UTC"""
    wd = dt.weekday()
    if wd == 5:  # 周六
        return []
    if wd == 6:  # 周日
        return list(range(21, 24))
    if wd == 4:  # 周五
        return list(range(0, 22))
    return list(range(24))


def decode_bi5(path: str) -> list[dict]:
    """解码 .bi5 文件为 tick 记录列表

    .bi5 格式: 每条 20 字节, struct.unpack('!IIIff')
    - 4 bytes: 时间戳 (当天毫秒数)
    - 4 bytes: ask 价 (int, 除以 1000 得实际价格 — 黄金3位小数)
    - 4 bytes: bid 价 (int)
    - 4 bytes: ask 量 (float32, 乘以 1000000 得实际量)
    - 4 bytes: bid 量 (float32)
    """
    try:
        with lzma.open(path) as f:
            raw = f.read()
    except Exception:
        return []

    if len(raw) < 20:
        return []

    records = []
    for i in range(0, len(raw), 20):
        chunk = raw[i:i + 20]
        if len(chunk) < 20:
            break
        ts, ask_int, bid_int, ask_vol_f, bid_vol_f = struct.unpack("!IIIff", chunk)

        ask = ask_int / 1000
        bid = bid_int / 1000
        ask_vol = round(ask_vol_f * 1_000_000)
        bid_vol = round(bid_vol_f * 1_000_000)

        if ask == 0 or bid == 0:
            continue

        records.append({
            "ts": ts,
            "bid": bid,
            "bid_vol": bid_vol,
            "ask": ask,
            "ask_vol": ask_vol,
        })
    return records


def download_date(date_str: str, workers: int = 8) -> bool:
    """下载一天的数据，返回是否成功"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    csv_path = _csv_path(date_str)
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        return True

    hours = _market_hours(dt)
    if not hours:
        return False

    hour_dts = [dt + timedelta(hours=h) for h in hours]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_hour, h): h for h in hour_dts}
        for f in as_completed(futures):
            pass

    # 解码并合并
    all_records = []
    for h in hour_dts:
        path = _bi5_path(h)
        records = decode_bi5(path)
        # 时间戳 = 小时内的毫秒数
        for r in records:
            t = h.replace(minute=0, second=0, microsecond=0) + timedelta(milliseconds=r["ts"])
            r["datetime"] = t
            r["spread"] = round((r["ask"] - r["bid"]) * 10000, 2)  # 以基点计
            all_records.append(r)

    if not all_records:
        logger.warning(f"No data for {date_str}")
        return False

    df = pd.DataFrame(all_records)
    df = df.sort_values("datetime").reset_index(drop=True)
    df.to_csv(csv_path, index=False)
    logger.info(f"Saved {len(df)} ticks for {date_str}")
    return True


def list_available_dates() -> list[str]:
    files = sorted(glob.glob(os.path.join(CSV_DIR, "XAUUSD_*.csv")))
    return [os.path.basename(f).replace("XAUUSD_", "").replace(".csv", "") for f in files]


def load_date(date_str: str) -> pd.DataFrame:
    path = _csv_path(date_str)
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=["datetime"])


def load_range(start: str, end: str) -> pd.DataFrame:
    dfs = []
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    for i in range((e - s).days + 1):
        d = (s + timedelta(days=i)).strftime("%Y-%m-%d")
        df = load_date(d)
        if not df.empty:
            dfs.append(df)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


if __name__ == "__main__":
    ensure_dirs()
    # 测试下载 1 天
    ok = download_date("2026-07-01")
    print(f"Download OK: {ok}")
    df = load_date("2026-07-01")
    print(f"Ticks: {len(df)}")
    print(df.head(3))