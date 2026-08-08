"""
估值数据源适配层

按接口类型拆分为独立类，每类内置兜底机制，对外暴露统一 get_signal() 接口。

数据源类：
1. BondYieldAPI          — 债券基金 → 中国10年国债收益率分位
2. DomesticIndexPEAPI    — 国内指数 → 乐咕PE → 中证官网历史PE → 中证短周期PE
3. OverseasIndexPEAPI    — 海外指数 → multpl标普PE → 乐咕恒生PE → 标普PE近似 → 价格分位
4. GoldPriceAPI          — 黄金基金 → COMEX金价分位

统一入口 ValuationSignalService 按基金类型分派 + 兜底。
"""

import re
import numpy as np
from datetime import datetime
from hashlib import md5
from typing import Dict, Optional

import yfinance as yf

from backend.market_data import GLOBAL_INDEX_MAPPING
from backend.ttl_cache import TtlCache
from loguru import logger

# 海外指数 → yfinance ETF 代码（价格分位兜底 + 当前PE）
OVERSEAS_ETF_MAP = {
    "sp500": "SPY",
    "nasdaq100": "QQQ",
    "nasdaq": "QQQ",
    "dax": "EWG",
    "hsi": "EWH",
    "sp_china_a_dividend": "515450.SS",  # 标普A股大盘红利低波50 ETF
}

# 债券指数 → 用10年国债收益率作为估值指标
BOND_INDEX_CODES = {
    "CBA00101", "CBA00201", "CBA00401", "CBA00501", "CBA00601",
    "H11070", "H11071", "H11072", "bond_index",
}

# 乐咕支持的历史PE数据（12个宽基指数，5000+行）
PE_LG_SUPPORTED = {"沪深300", "中证500", "上证50", "中证1000", "中证800",
                   "上证180", "深证红利", "深证100", "中证100",
                   "上证红利", "上证380", "创业板50"}

# 近10年窗口（一年约250个交易日 / 120个月）
LOOKBACK_YEARS = 10
LOOKBACK_DAYS = LOOKBACK_YEARS * 250
LOOKBACK_MONTHS = LOOKBACK_YEARS * 12


def compute_signal(percentile: Optional[float]) -> Dict[str, str]:
    """根据估值分位生成定投信号"""
    if percentile is None:
        return {"signal": "unknown", "action": "无信号", "source": "无数据"}
    if percentile < 20:
        return {"signal": "深度低估", "action": "加倍定投", "source": "低估区间"}
    if percentile < 40:
        return {"signal": "低估", "action": "正常定投", "source": "低估区间"}
    if percentile < 60:
        return {"signal": "合理", "action": "持有/暂停定投", "source": "合理区间"}
    if percentile < 80:
        return {"signal": "偏高", "action": "逐步止盈", "source": "偏高区间"}
    return {"signal": "高估", "action": "分批卖出", "source": "高估区间"}


class BondYieldAPI:
    """债券基金估值 — 使用中国10年国债收益率历史分位

    兜底：数据获取失败返回空字典（信号为无数据）。
    """

    def __init__(self):
        self._cache = TtlCache(default_ttl=300, maxsize=8)  # 5分钟缓存

    def get_signal(self) -> Dict:
        """获取债券估值信号"""
        cached = self._cache.get("bond_yield")
        if cached is not None:
            return cached
        try:
            from akshare import bond_zh_us_rate
            df = bond_zh_us_rate()
            col = "中国国债收益率10年"
            if df is None or col not in df.columns:
                return {}
            series = df[[col]].dropna()
            if len(series) < 20:
                return {}
            recent_series = series.tail(LOOKBACK_DAYS)
            latest = float(recent_series[col].iloc[-1])
            vals = recent_series[col].astype(float).values
            # 收益率越低 → 债券越贵（估值越高），用反分位
            yield_pct = (vals < latest).mean() * 100
            bond_expensive_pct = 100 - yield_pct
            result = {
                "index_code": "CN10Y",
                "index_name": "中国10年国债",
                "pe_value": round(latest, 3),
                "pe_percentile": round(float(bond_expensive_pct), 1),
                "pb_value": None,
                "pb_percentile": None,
                "source": "bond_yield",
            }
            self._cache.set("bond_yield", result)
            return result
        except Exception as e:
            logger.warning(f"获取债券收益率分位失败: {e}")
            return {}


class DomesticIndexPEAPI:
    """国内指数PE估值 — 统一近10年窗口

    兜底链：乐咕历史PE → 中证官网历史PE（含滚动市盈率）→ 中证短周期PE。
    """

    # 指数代码 → 中文名
    PE_INDEX_MAP = {
        "000300": "沪深300", "000905": "中证500", "000016": "上证50",
        "000852": "中证1000", "000906": "中证800", "000688": "科创50",
        "399006": "创业板指", "399673": "创业板50",
        "000001": "上证指数", "399001": "深证成指",
        "000922": "上证红利", "399986": "中证银行",
        "399967": "中证军工", "399997": "中证白酒",
        "399975": "中证证券", "399808": "中证新能源",
        "000932": "中证消费", "000933": "中证医药",
        "931632": "中证黄金股", "399989": "中证医疗",
        "000934": "中证信息", "399005": "中小板指", "399303": "国证2000",
    }

    def __init__(self):
        self._cache = TtlCache(default_ttl=300, maxsize=32)

    def get_signal(self, index_code: str) -> Dict:
        """获取国内指数PE估值信号"""
        cache_key = f"pe_{index_code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        pe_name = self.PE_INDEX_MAP.get(index_code)
        if not pe_name:
            return {}
        result = self._fetch_legulegu(pe_name, index_code) or \
                 self._fetch_csindex_hist(index_code, pe_name) or \
                 self._fetch_csindex_short(index_code, pe_name)
        if result:
            self._cache.set(cache_key, result)
        return result

    @staticmethod
    def _fetch_legulegu(pe_name: str, index_code: str) -> Dict:
        """Source 1: 乐咕历史PE（宽基指数，5000+行）"""
        try:
            from akshare import stock_index_pe_lg
            df = stock_index_pe_lg(symbol=pe_name)
            if df is None or len(df) < 20:
                return {}
            all_pe = df["滚动市盈率"].dropna().values
            recent_pe = all_pe[-LOOKBACK_DAYS:]
            if len(recent_pe) < 20:
                return {}
            latest_pe = float(recent_pe[-1])
            percentile = (recent_pe < latest_pe).mean() * 100
            return {
                "index_code": index_code,
                "index_name": pe_name,
                "pe_value": round(latest_pe, 2),
                "pe_percentile": round(float(percentile), 1),
                "pb_value": None,
                "pb_percentile": None,
                "source": "pe_lg",
            }
        except Exception as e:
            logger.debug(f"乐咕PE获取失败 {pe_name}: {e}")
            return {}

    @staticmethod
    def _fetch_csindex_hist(index_code: str, pe_name: str) -> Dict:
        """Source 2: 中证指数官网历史PE（行业指数，含滚动市盈率）"""
        try:
            from akshare import stock_zh_index_hist_csindex
            start_date = f"{datetime.now().year - LOOKBACK_YEARS}0101"
            end_date = datetime.now().strftime("%Y%m%d")
            df = stock_zh_index_hist_csindex(
                symbol=index_code, start_date=start_date, end_date=end_date
            )
            if df is None or len(df) < 20 or "滚动市盈率" not in df.columns:
                return {}
            pe_series = df["滚动市盈率"].dropna()
            if len(pe_series) < 20:
                return {}
            latest_pe = float(pe_series.iloc[-1])
            percentile = (pe_series.values < latest_pe).mean() * 100
            return {
                "index_code": index_code,
                "index_name": pe_name,
                "pe_value": round(latest_pe, 2),
                "pe_percentile": round(float(percentile), 1),
                "pb_value": None,
                "pb_percentile": None,
                "source": "pe_csindex",
            }
        except Exception as e:
            logger.debug(f"中证官网历史PE获取失败 {index_code}: {e}")
            return {}

    @staticmethod
    def _fetch_csindex_short(index_code: str, pe_name: str) -> Dict:
        """Source 3: 中证短周期PE（仅近20日，兜底）"""
        try:
            from akshare import stock_zh_index_value_csindex
            df = stock_zh_index_value_csindex(symbol=index_code)
            if df is None or len(df) < 5:
                return {}
            latest_pe = float(df["市盈率1"].iloc[0])
            all_pe = df["市盈率1"].dropna().values
            percentile = (all_pe < latest_pe).mean() * 100
            return {
                "index_code": index_code,
                "index_name": pe_name,
                "pe_value": round(latest_pe, 2),
                "pe_percentile": round(float(percentile), 1),
                "pb_value": float(df["市盈率2"].iloc[0]) if "市盈率2" in df.columns else None,
                "pb_percentile": None,
                "source": "pe_csindex_short",
            }
        except Exception as e:
            logger.debug(f"中证短周期PE获取失败 {index_code}: {e}")
            return {}


class OverseasIndexPEAPI:
    """海外指数PE估值

    兜底链：
    1. multpl 真实PE（标普500，月度回1871年）
    2. 乐咕真实PE（恒生指数，月度回1973年）
    3. 标普500 PE 近似（纳指/DAX，全球权益估值联动）
    4. yfinance 价格分位（最终兜底）
    """

    def __init__(self):
        self._cache = TtlCache(default_ttl=300, maxsize=16)

    def get_signal(self, index_code: str) -> Dict:
        """获取海外指数估值信号"""
        cache_key = f"overseas_{index_code}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        result = {}
        # 兜底链
        if index_code == "sp500":
            result = self._fetch_multpl() or {}
        if not result and index_code == "hsi":
            result = self._fetch_legulegu_hsi() or {}
        if not result and index_code in ("nasdaq", "nasdaq100", "dax"):
            sp500_data = self._fetch_multpl()
            if sp500_data:
                result = {
                    "index_code": index_code,
                    "index_name": GLOBAL_INDEX_MAPPING.get(index_code, {}).get("name", index_code),
                    "pe_value": sp500_data.get("pe_value"),
                    "pe_percentile": sp500_data.get("pe_percentile"),
                    "pb_value": None,
                    "pb_percentile": None,
                    "source": "sp500_pe_proxy",  # 以标普500PE近似
                }
        if not result:
            result = self._fetch_price_fallback(index_code)
        if result:
            self._cache.set(cache_key, result)
        return result

    @staticmethod
    def _fetch_multpl() -> Dict:
        """Source 1: multpl.com 标普500真实历史PE（近10年120个月）"""
        try:
            import requests
            import html as html_lib
            resp = requests.get(
                "https://www.multpl.com/s-p-500-pe-ratio/table/by-month",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            if resp.status_code != 200:
                return {}
            trs = re.findall(r"<tr[^>]*>(.*?)</tr>", resp.text, re.S)
            vals = []
            for tr in trs[1:]:
                cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
                if len(cells) >= 2:
                    v = html_lib.unescape(re.sub(r"<[^>]+>", "", cells[1])).replace("†", "").strip()
                    try:
                        vals.append(float(v))
                    except ValueError:
                        pass
            if len(vals) < 120:
                return {}
            cur = vals[0]
            recent = vals[:LOOKBACK_MONTHS]
            pct = sum(1 for v in recent if v < cur) / len(recent) * 100
            return {
                "index_code": "sp500",
                "index_name": "标普500",
                "pe_value": round(cur, 1),
                "pe_percentile": round(float(pct), 1),
                "pb_value": None,
                "pb_percentile": None,
                "source": "sp500_pe",
            }
        except Exception as e:
            logger.warning(f"获取标普500 PE 分位失败: {e}")
            return {}

    @staticmethod
    def _fetch_legulegu_hsi() -> Dict:
        """Source 2: 乐咕恒生指数真实历史PE（月度回1973年）"""
        try:
            from akshare.stock_feature.stock_a_indicator import get_cookie_csrf
            import requests as _req
            token = md5(datetime.now().date().isoformat().encode()).hexdigest()
            resp = _req.get(
                "https://legulegu.com/api/stockdata/hs",
                params={"token": token, "indexCode": "HSI"},
                **get_cookie_csrf(url="https://legulegu.com/stockdata/market/hk/dv/hsi"),
                timeout=20,
            )
            if resp.status_code != 200:
                return {}
            data = resp.json()
            if not isinstance(data, list) or not data or "pe" not in data[0]:
                return {}
            recent = [
                x["pe"] for x in data
                if x.get("pe") and x.get("date", "") >= f"{datetime.now().year - LOOKBACK_YEARS}-01-01"
            ]
            if len(recent) < 24:
                return {}
            cur = float(recent[-1])
            pct = sum(1 for v in recent if v < cur) / len(recent) * 100
            return {
                "index_code": "hsi",
                "index_name": "恒生指数",
                "pe_value": round(cur, 1),
                "pe_percentile": round(float(pct), 1),
                "pb_value": None,
                "pb_percentile": None,
                "source": "hsi_pe",
            }
        except Exception as e:
            logger.warning(f"获取恒生指数 PE 分位失败: {e}")
            return {}

    def _fetch_price_fallback(self, index_code: str) -> Dict:
        """Source 4: yfinance 价格分位（最终兜底，附当前PE）"""
        etf = OVERSEAS_ETF_MAP.get(index_code)
        if not etf:
            return {}
        try:
            data = yf.download(etf, period="10y", progress=False, auto_adjust=True)
            if data is None or len(data) < 50:
                return {}
            close = data["Close"].squeeze() if hasattr(data, "squeeze") else data["Close"]
            close = close.dropna()
            if len(close) < 50:
                return {}
            latest = float(close.iloc[-1])
            price_pct = (close < latest).mean() * 100
            pe_value = None
            try:
                info = yf.Ticker(etf).info
                pe_value = info.get("trailingPE")
            except Exception:
                pass
            return {
                "index_code": index_code,
                "index_name": GLOBAL_INDEX_MAPPING.get(index_code, {}).get("name", etf),
                "pe_value": round(float(pe_value), 2) if pe_value else None,
                "pe_percentile": round(float(price_pct), 1),
                "pb_value": None,
                "pb_percentile": None,
                "source": "overseas_price",
            }
        except Exception as e:
            logger.warning(f"获取海外指数 {index_code} 价格分位失败: {e}")
            return {}


class GoldPriceAPI:
    """黄金基金估值 — 用COMEX金价历史分位（金价本身无PE）"""

    def __init__(self):
        self._cache = TtlCache(default_ttl=300, maxsize=4)

    def get_signal(self, index_code: str = "au") -> Dict:
        """获取黄金估值信号"""
        cached = self._cache.get("gold_price")
        if cached is not None:
            return cached
        try:
            data = yf.download("GC=F", period="10y", progress=False, auto_adjust=True)
            if data is None or len(data) < 50:
                return {}
            close = data["Close"].squeeze() if hasattr(data, "squeeze") else data["Close"]
            close = close.dropna()
            if len(close) < 50:
                return {}
            latest = float(close.iloc[-1])
            price_pct = (close < latest).mean() * 100
            result = {
                "index_code": index_code,
                "index_name": "COMEX黄金",
                "pe_value": round(latest, 2),
                "pe_percentile": round(float(price_pct), 1),
                "pb_value": None,
                "pb_percentile": None,
                "source": "gold_price",
            }
            self._cache.set("gold_price", result)
            return result
        except Exception as e:
            logger.warning(f"获取黄金价格分位失败: {e}")
            return {}


class ValuationSignalService:
    """估值信号统一入口 — 按基金类型分派数据源 + 兜底"""

    def __init__(self):
        self.bond_api = BondYieldAPI()
        self.domestic_api = DomesticIndexPEAPI()
        self.overseas_api = OverseasIndexPEAPI()
        self.gold_api = GoldPriceAPI()

    def get_signal(self, tracking_index: Optional[str], fund_type: str = "") -> Dict:
        """
        根据跟踪指数和基金类型返回估值数据 + 定投信号

        Returns:
            dict: {index_code, index_name, pe_value, pe_percentile, ...,
                   signal, signal_action, signal_source}
        """
        pe_data: Dict = {}

        # 1. 债券基金 → 10年国债收益率分位
        is_bond = any(kw in (fund_type or "") for kw in ["债券", "固收", "偏债"]) or (
            tracking_index in BOND_INDEX_CODES
        )
        # 2. 海外指数
        is_overseas = tracking_index in OVERSEAS_ETF_MAP
        # 3. 黄金
        is_gold = tracking_index == "au"

        if is_bond:
            pe_data = self.bond_api.get_signal()
        elif is_overseas and tracking_index:
            pe_data = self.overseas_api.get_signal(tracking_index)
        elif tracking_index == "au":
            pe_data = self.gold_api.get_signal()
        elif tracking_index:
            pe_data = self.domestic_api.get_signal(tracking_index)
            # 黄金股PE失败时回退到金价分位
            if not pe_data and tracking_index == "931632":
                gold_data = self.gold_api.get_signal()
                if gold_data:
                    gold_data["index_code"] = "931632"
                    gold_data["index_name"] = "中证黄金股(参考金价)"
                    gold_data["source"] = "gold_price"
                    pe_data = gold_data

        if not pe_data:
            return {}

        # 生成定投信号
        signal = compute_signal(pe_data.get("pe_percentile"))
        pe_data["signal"] = signal["signal"]
        pe_data["signal_action"] = signal["action"]
        return pe_data


valuation_signal_service = ValuationSignalService()
