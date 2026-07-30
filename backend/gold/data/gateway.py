import asyncio
import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from loguru import logger

from backend.gold.core.models import GoldBarData
from backend.gold.data.storage import GoldDataStore
from backend.gold.data.quality import DataQualityChecker
from backend.rate_limiter import RateLimiterGroup


class GoldDataGateway:
    """黄金数据网关 — AkShare(SHFE) → 直连新浪(SHFE) → yFinance(COMEX) 三级降级"""

    _req_cache: dict = {}       # ponytail: per-param dedup cache, 5s TTL
    _CACHE_TTL: float = 5.0    # seconds
    _last_refresh: dict = {}    # (symbol, period) → timestamp, 30s cooldown

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = str(Path(__file__).parent.parent.parent.parent / "data" / "backend" / "gold" / "gold.db")
        self.db_path = db_path
        self.store = GoldDataStore(db_path)
        self._rate_limiter_group = RateLimiterGroup(max_concurrent=2, min_interval=1.0)

    async def get_bars(self, symbol: str = "AU0", period: str = "d",
                       start: str = None, end: str = None,
                       limit: int = None, refresh: bool = False,
                       skip_quality_check: bool = False) -> list[GoldBarData]:
        """获取K线数据 — 优先SQLite，数据过期或refresh=True时从AkShare刷新"""
        # ponytail: cache_key 不含 limit/refresh，同 symbol/period/start/end 共享一次外部请求
        # 页面加载时 /bars(limit=200) + /market-data(limit=300) + /analysis(limit=500)
        # + /strategy-comparison(limit=120) 4路并发只触发1次外部获取
        cache_key = (symbol, period, start, end)
        now = time.monotonic()
        cached = self._req_cache.get(cache_key)
        if cached and (now - cached['t']) < self._CACHE_TTL:
            bars = cached['v']
            return self._slice_result(bars, limit)

        today = datetime.now().strftime("%Y-%m-%d")
        now_naive = datetime.now()

        # 非强制刷新时先查SQLite缓存
        if not refresh:
            bars = self.store.get_bars(symbol, period, start, end, limit)
            if bars:
                latest = bars[-1].datetime
                latest_naive = latest.replace(tzinfo=None) if latest.tzinfo else latest
                age_days = (now_naive - latest_naive).days
                if (period == "d" and age_days <= 1) or period != "d":
                    if not skip_quality_check:
                        qc = DataQualityChecker()
                        report = qc.check(bars)
                        if not report.passed:
                            logger.warning(f"缓存数据质量问题: {report.summary}")
                    return bars

        # 缓存miss或过期，从外部源获取
        # ponytail: refresh 冷却 — 日线60s内不重复拉取，分钟线30s
        if refresh:
            cool_down = 60 if period == "d" else 30
            last = self._last_refresh.get((symbol, period), 0)
            if now - last < cool_down:
                bars = self.store.get_bars(symbol, period, start, end, None)
                if bars:
                    logger.debug(f"refresh冷却中({now-last:.0f}s)，复用SQLite")
                    result = self._slice_result(bars, limit)
                    self._req_cache[cache_key] = {'v': bars, 't': time.monotonic()}
                    return result
        fetch_start = start or "2025-01-01"
        fetch_end = end or today
        # 三级降级: AkShare → 直连新浪 → yFinance COMEX
        # 每级拿完检查新鲜度，过期就继续 fallback
        for source_name, fetch_fn in [
            ("AkShare SHFE", lambda: self._fetch_from_akshare(symbol, period, fetch_start, fetch_end)),
            ("Sina直连", lambda: self._fetch_from_sina_direct(symbol, period, fetch_start, fetch_end)),
            ("yFinance COMEX", lambda: self._fetch_from_yfinance(fetch_start, fetch_end, period, output_symbol="GC=F")),
        ]:
            bars = await fetch_fn()
            if bars:
                latest_dt = bars[-1].datetime
                latest_naive = latest_dt.replace(tzinfo=None) if latest_dt.tzinfo else latest_dt
                age_days = (now_naive - latest_naive).days if latest_dt else 999
                # AkShare SHFE 已过期 → 继续试下个源（数据只到2024年）
                if source_name == "AkShare SHFE" and period == "d" and age_days > 3:
                    self.store.save_bars(bars, period)  # 存了当历史参考
                    logger.info(f"AkShare SHFE 数据已过期 ({age_days}天前)，尝试下个源")
                    bars = None
                    continue
                if bars:
                    logger.info(f"{source_name} 获取数据: {len(bars)}条 (最新 {latest_dt})")
                    break

        if bars:
            self.store.save_bars(bars, period)
            self._last_refresh[(symbol, period)] = time.monotonic()
            if len(self._last_refresh) > 16:
                self._last_refresh.clear()

        # 并发竞争检查：另一个请求可能已经完成了fetch并写入SQLite
        if not bars:
            bars = self.store.get_bars(symbol, period, start, end, None)
            if bars:
                logger.info(f"从并发写入的SQLite读取: {len(bars)}条")

        # 重新从存储查询（含旧数据+新数据，不分 limit 以便缓存复用）
        full_result = self.store.get_bars(symbol, period, start, end, limit=None)
        result = self._slice_result(full_result, limit)
        self._req_cache[cache_key] = {'v': full_result if full_result else bars, 't': time.monotonic()}
        if len(self._req_cache) > 128:  # ponytail: 容量上限
            self._req_cache.clear()
        return result if result else bars

    @staticmethod
    def _slice_result(bars: list, limit: int = None) -> list:
        """按 limit 截取尾部（ASC 顺序，最新在末尾）"""
        if bars and limit and len(bars) > limit:
            return bars[-limit:]
        return bars

    async def _fetch_from_akshare(self, symbol: str, period: str,
                                   start: str = None, end: str = None) -> list[GoldBarData]:
        """从AkShare获取SHFE黄金数据

        优先级: 日线→ futures_zh_daily_sina | 分钟线→ futures_zh_minute_sina
        """
        def _sync():
            import akshare as ak
            if period == "d":
                df = ak.futures_zh_daily_sina(symbol)
            else:
                period_map = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60"}
                ak_period = period_map.get(period, "5")
                df = ak.futures_zh_minute_sina(symbol=symbol, period=ak_period)
            return self._df_to_bars(df, symbol, period)
        try:
            async with self._rate_limiter_group.limit("https://akshare/api"):
                return await asyncio.to_thread(_sync) or []
        except Exception as e:
            logger.warning(f"AkShare数据获取失败: {symbol} {period}, {e}")
            return []

    async def _fetch_from_sina_direct(self, symbol: str, period: str,
                                       start: str = None, end: str = None) -> list[GoldBarData]:
        """备选1: 直连新浪期货API（AkShare不可用时）"""
        try:
            import requests
        except ImportError:
            return []

        def _sync():
            if period == "d":
                url = f"https://stock.finance.sina.com.cn/futures/api/json_v2.php/IndexService.getInnerFuturesDailyKLine?symbol={symbol}"
            else:
                period_map = {"1": "1", "5": "5", "15": "15", "30": "30", "60": "60"}
                ak_period = period_map.get(period, "5")
                url = f"https://stock.finance.sina.com.cn/futures/api/json_v2.php/IndexService.getInnerFuturesMinKLine?symbol={symbol}&type={ak_period}"

            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            resp.encoding = "gbk"
            rows = resp.json()
            if not rows:
                return None

            import pandas as pd
            df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
            df = df[(df["date"] >= (start or "2000")) & (df["date"] <= (end or "2030"))]
            df = df.astype({"open": float, "high": float, "low": float, "close": float, "volume": float})
            df.columns = ["日期", "开盘价", "最高价", "最低价", "收盘价", "成交量"]
            return self._df_to_bars(df, symbol, period)

        try:
            async with self._rate_limiter_group.limit("https://stock.finance.sina.com.cn/api"):
                return await asyncio.to_thread(_sync) or []
        except Exception as e:
            logger.warning(f"Sina直连获取失败: {e}")
            return []

    async def _fetch_from_yfinance(self, start: str, end: str,
                                    period: str = "d",
                                    output_symbol: str = "GC=F") -> list[GoldBarData]:
        """备选2: yFinance COMEX黄金（GC=F, 美元/盎司, 仅供参考）"""
        def _sync():
            import yfinance as yf
            ticker = yf.Ticker("GC=F")
            return ticker.history(start=start, end=end)

        try:
            async with self._rate_limiter_group.limit("https://query1.finance.yahoo.com/api"):
                df = await asyncio.to_thread(_sync)
            if df is None or df.empty:
                return []
            bars = []
            for idx, row in df.iterrows():
                bars.append(GoldBarData(
                    symbol=output_symbol, exchange="COMEX", period=period,
                    datetime=idx.to_pydatetime(),
                    open=float(row["Open"]), high=float(row["High"]),
                    low=float(row["Low"]), close=float(row["Close"]),
                    volume=float(row.get("Volume", 0)),
                ))
            return bars
        except Exception as e:
            logger.warning(f"yFinance数据获取失败: {e}")
            return []

    def _df_to_bars(self, df: pd.DataFrame, symbol: str,
                     period: str) -> list[GoldBarData]:
        """AkShare DataFrame → GoldBarData列表"""
        if df is None or df.empty:
            return []

        bars = []
        for _, row in df.iterrows():
            dt = row.get("日期") or row.get("时间") or row.get("datetime") or row.get("date")
            if isinstance(dt, str):
                dt = pd.to_datetime(dt)
            elif hasattr(dt, 'to_pydatetime'):
                dt = dt.to_pydatetime()
            if dt is None:
                continue

            bar = GoldBarData(
                symbol=symbol, exchange="SHFE", period=period,
                datetime=dt,
                open=float(row.get("开盘价", row.get("open", 0))),
                high=float(row.get("最高价", row.get("high", 0))),
                low=float(row.get("最低价", row.get("low", 0))),
                close=float(row.get("收盘价", row.get("close", 0))),
                volume=float(row.get("成交量", row.get("volume", 0))),
                turnover=float(row.get("成交额", row.get("turnover", 0))),
                open_interest=float(row.get("持仓量", row.get("open_interest", 0))),
            )
            bars.append(bar)
        return bars

    async def get_gold_etf_price(self, code: str = "518880") -> dict:
        """获取黄金ETF实时价格 — 复用现有market_data.py"""
        from backend.market_data import market_data_service
        return await market_data_service.get_etf_price(code)

    async def get_macro_data(self, start: str = "2024-01-01", end: str = None) -> pd.DataFrame:
        """获取宏观指标数据（DXY, VIX, US10Y）用于ML预测

        返回每日DataFrame，含 date, DXY_value, VIX_value, US10Y_value 列
        """
        cached = self._req_cache.get(("_macro_", start, end))
        if cached and (time.monotonic() - cached['t']) < self._CACHE_TTL:
            return cached['v']

        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not installed, skip macro data fetch")
            return pd.DataFrame()

        end = end or datetime.now().strftime("%Y-%m-%d")
        indicators = {
            "DXY_value": "DX-Y.NYB",
            "VIX_value": "^VIX",
            "US10Y_value": "^TNX",
        }

        def _sync():
            dfs = {}
            for col, ticker in indicators.items():
                try:
                    t = yf.Ticker(ticker)
                    hist = t.history(start=start, end=end)
                    if not hist.empty:
                        dfs[col] = hist["Close"].rename(col)
                except Exception as e:
                    logger.warning(f"Failed to fetch {ticker}: {e}")
            if not dfs:
                return pd.DataFrame()
            result = pd.concat(dfs, axis=1).reset_index()
            result["date"] = result["Date"].dt.strftime("%Y-%m-%d") if hasattr(result["Date"], "dt") else result["Date"]
            return result[["date"] + list(indicators.keys())]

        async with self._rate_limiter_group.limit("https://query1.finance.yahoo.com/api"):
            result = await asyncio.to_thread(_sync)
        self._req_cache[("_macro_", start, end)] = {'v': result, 't': time.monotonic()}
        if len(self._req_cache) > 128:
            self._req_cache.clear()
        return result

    async def get_cot_data(self, symbol: str = "088691") -> pd.DataFrame:
        """
        获取 CFTC COT 持仓报告（黄金期货）

        COT 数据每周五发布（下周二可获取），包含商业/非商业持仓分类。
        非商业（投机）净多单 = 投机多 - 投机空 → 对金价有领先指示。

        Args:
            symbol: CFTC 市场代码，088691=黄金期货

        Returns:
            DataFrame: date, spec_long, spec_short, comm_long, comm_short, total_oi
        """
        def _sync():
            import requests
            import pandas as pd

            try:
                # 使用 Quantsuz 的 COT 数据源（免费，无需 API key）
                url = f"https://data.quantsuz.com/api/v1/cftc/{symbol}"
                resp = requests.get(url, timeout=10)
                if resp.status_code != 200:
                    # fallback: 尝试 alternative source
                    url = f"https://www.cftc.gov/dea/futures/past/pa{int(symbol)}_past.txt"
                    resp = requests.get(url, timeout=10)
                    if resp.status_code != 200:
                        logger.warning(f"COT data fetch failed for {symbol}")
                        return pd.DataFrame()
                    lines = resp.text.strip().split("\n")
                    if len(lines) < 4:
                        return pd.DataFrame()
                    records = []
                    for line in lines[3:]:
                        parts = line.strip().split(",")
                        if len(parts) < 20:
                            continue
                        try:
                            records.append({
                                "date": parts[0].strip(),
                                "spec_long": int(parts[6]),
                                "spec_short": int(parts[7]),
                                "comm_long": int(parts[10]),
                                "comm_short": int(parts[11]),
                                "total_oi": int(parts[1]),
                            })
                        except (ValueError, IndexError):
                            continue
                    return pd.DataFrame(records)

                data = resp.json()
                if "data" in data:
                    return pd.DataFrame(data["data"])
                return pd.DataFrame()

            except Exception as e:
                logger.warning(f"COT data error: {e}")
                return pd.DataFrame()

        return await asyncio.to_thread(_sync)

    async def get_training_data(self, symbol: str = 'GC',
                                 lookback_days: int = 2520) -> pd.DataFrame:
        """获取ML预测训练数据 — 复用现有data_sync.py"""
        from backend.data_sync import get_gold_training_data
        df = get_gold_training_data(symbol, lookback_days=lookback_days)

        if df.empty:
            return df

        # 合并COT持仓数据（如果有）
        try:
            cot = await self.get_cot_data()
            if not cot.empty:
                cot["date"] = pd.to_datetime(cot["date"]).dt.strftime("%Y-%m-%d")
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                # 计算COT因子
                cot["spec_net"] = cot["spec_long"] - cot["spec_short"]
                cot["spec_net_ratio"] = cot["spec_net"] / (cot["total_oi"] + 1)
                cot["spec_long_ratio"] = cot["spec_long"] / (cot["total_oi"] + 1)
                cot_cols = ["date", "spec_net", "spec_net_ratio", "spec_long_ratio"]
                df = df.merge(cot[cot_cols], on="date", how="left")
                # 向前填充（COT 每周发布一次）
                for c in cot_cols:
                    if c != "date" and c in df.columns:
                        df[c] = df[c].ffill().bfill().fillna(0)
                logger.debug(f"合并 COT 数据: {cot[cot_cols].dropna().shape[0]} 条记录")
        except Exception as e:
            logger.debug(f"COT 数据合并失败: {e}")

        return df
        return get_gold_training_data(symbol, lookback_days)
