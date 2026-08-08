"""
市场数据服务 - 重构版

按数据需求类型组织 API 类：
1. StockPriceAPI - A 股/港股/美股股票价格
2. FundPriceAPI - 基金价格/净值数据
3. FundHoldingsAPI - 基金持仓数据
4. IndexPriceAPI - 国内指数价格
5. GlobalIndexAPI - 海外指数价格
6. ETFPriceAPI - ETF 实时价格
"""

import asyncio
import aiohttp
import json
import re
import time
import random
from pathlib import Path
from datetime import datetime
from functools import partial
from typing import Dict, List, Optional, Any
from loguru import logger
import akshare as ak
import yfinance as yf

from backend.models import (
    MarketData,
    FundData,
    Holding,
    MarketType,
    AssetType,
)
from backend.rate_limiter import RateLimiter, RateLimiterGroup
from backend.ttl_cache import TtlCache

logger.add(str(Path(__file__).parent.parent / "logs" / "market_data.log"), encoding="utf-8", rotation="10 MB")

# ==================== 常量定义 ====================

INDEX_MAPPING = {
    # A 股主要指数
    "000001": {"name": "上证指数", "code": "sh000001"},
    "000016": {"name": "上证 50", "code": "sh000016"},
    "000300": {"name": "沪深 300", "code": "sh000300"},
    "000905": {"name": "中证 500", "code": "sh000905"},
    "000852": {"name": "中证 1000", "code": "sh000852"},
    "399001": {"name": "深证成指", "code": "sz399001"},
    "399006": {"name": "创业板指", "code": "sz399006"},
    "399673": {"name": "创业板 50", "code": "sz399673"},
    "000688": {"name": "科创 50", "code": "sh000688"},
    "399005": {"name": "中小板指", "code": "sz399005"},
    "399303": {"name": "国证 2000", "code": "sz399303"},
    # 行业/主题指数
    "399997": {"name": "中证白酒", "code": "sz399997"},
    "399808": {"name": "中证新能源", "code": "sz399808"},
    "399967": {"name": "中证军工", "code": "sz399967"},
    "399986": {"name": "中证银行", "code": "sz399986"},
    "399975": {"name": "中证证券", "code": "sz399975"},
    "000932": {"name": "中证消费", "code": "sh000932"},
    "000933": {"name": "中证医药", "code": "sh000933"},
    "000922": {"name": "中证红利", "code": "sh000922"},
    "399989": {"name": "中证医疗", "code": "sz399989"},
    "931632": {"name": "中证黄金股", "code": "sh931632"},
    "931152": {"name": "创新药", "code": "sh931152"},
    "000906": {"name": "中证 800", "code": "sh000906"},
    "000991": {"name": "全指医药", "code": "sh000991"},
    # 港股相关指数
    "hshk_dividend": {"name": "恒生港股通高股息", "code": "hkHSIHKD", "sina": "hkHSIHKD"},
    "hsi": {"name": "恒生指数", "code": "hkHSI", "sina": "hkHSI"},
    "hshkdividend": {"name": "恒生港股通高股息", "code": "hkHSIHKD", "sina": "hkHSIHKD"},
    "sphklowvol": {"name": "标普港股通低波红利", "code": "hkSPHKLV", "sina": "hkSPHKLV"},
    "sp_hkconnect": {"name": "标普港股通低波红利", "code": "hkSPHKLV", "sina": "hkSPHKLV"},
    "hk_tech": {"name": "中证港股通科技", "code": "hk_hktech"},
    # 富时指数
    "ftse_cashflow": {"name": "富时中国 A50", "code": "hkFTAI", "sina": "hkFTAI"},
    "ftai": {"name": "富时中国 A50", "code": "hkFTAI", "sina": "hkFTAI"},
    # 标普中国 A 股红利低波指数
    "sp_china_a_dividend": {"name": "标普中国 A 股大盘红利低波 50", "code": "sp_china_a_dividend"},
    # 债券指数 - 中债系列
    "bond_index": {"name": "中债综合指数", "code": "CBA00101", "type": "bond", "source": "chinabond"},
    "CBA00101": {"name": "中债综合指数", "code": "CBA00101", "type": "bond", "source": "chinabond"},
    "CBA00201": {"name": "中债总指数", "code": "CBA00201", "type": "bond", "source": "chinabond"},
    "CBA00401": {"name": "中债国债总指数", "code": "CBA00401", "type": "bond", "source": "chinabond"},
    "CBA00501": {"name": "中债政策性金融债指数", "code": "CBA00501", "type": "bond", "source": "chinabond"},
    "CBA00601": {"name": "中债企业债总指数", "code": "CBA00601", "type": "bond", "source": "chinabond"},
    "中债综合指数": {"name": "中债综合指数", "code": "CBA00101", "type": "bond", "source": "chinabond"},
    "中债总指数": {"name": "中债总指数", "code": "CBA00201", "type": "bond", "source": "chinabond"},
    # 债券指数 - 中证系列
    "csi_bond": {"name": "中证国债指数", "code": "H11070", "type": "bond", "source": "csi"},
    "H11070": {"name": "中证国债指数", "code": "H11070", "type": "bond", "source": "csi"},
    "H11071": {"name": "中证金融债指数", "code": "H11071", "type": "bond", "source": "csi"},
    "H11072": {"name": "中证企业债指数", "code": "H11072", "type": "bond", "source": "csi"},
    "csi_dividend": {"name": "中证红利", "code": "sh000922"},
}

GLOBAL_INDEX_MAPPING = {
    # 美股指数 - yfinance 使用 ^ 前缀
    "nasdaq": {"name": "纳斯达克指数", "code": "^IXIC", "secid": "100.NDX", "sina": "gb_$nasdaq", "qq": "us.IXIC", "yf": "^IXIC", "investing": "14958"},
    "nasdaq100": {"name": "纳斯达克 100", "code": "^NDX", "secid": "100.NDX", "sina": "gb_$nasdaq", "qq": "us.IXIC", "yf": "^NDX", "investing": "20"},
    "sp500": {"name": "标普 500", "code": "^SPX", "secid": "100.SPX", "sina": "gb_$spx", "qq": "us.INX", "yf": "^SPX", "investing": "166"},
    "dji": {"name": "道琼斯", "code": "^DJI", "secid": "100.DJIA", "sina": "gb_$dji", "qq": "us.DJI", "yf": "^DJI", "investing": "169"},
    # 港股指数
    "hsi": {"name": "恒生指数", "code": "HSI", "secid": "100.HSI", "sina": "hkHSI", "qq": "hkHSI", "yf": "^HSI", "investing": "179"},
    "hshk_dividend": {"name": "恒生港股通高股息", "code": "HSIHKD", "secid": "100.HSIHKD", "sina": "hkHSIHKD", "qq": "hkHSIHKD", "yf": "^HSIHKD"},
    "sp_hkconnect": {"name": "标普港股通低波红利", "code": "SPHKLV", "secid": "100.SPHKLV", "sina": "hkSPHKLV", "qq": "hkSPHKLV", "yf": "^SPHKLV"},
    # 港股通科技指数
    "hstech": {"name": "恒生港股通科技", "code": "HSTECH", "secid": "100.HSTECH", "sina": "hkHSTECH", "qq": "hkHSTECH", "yf": "^HSTECH"},
    # 日本指数
    "nikkei": {"name": "日经 225", "code": "N225", "secid": "100.N225", "sina": "gb_$n225", "qq": "us.N225", "yf": "^N225", "investing": "178"},
    # 黄金/商品
    "au": {"name": "黄金现货", "code": "GC=F", "secid": "100.XAU", "sina": "gb_$xau", "qq": "us.GC", "yf": "GC=F", "investing": "68"},
    # 英国 FTSE 指数
    "ftse_cashflow": {"name": "富时中国 A50", "code": "FTAI", "secid": "100.FTAI", "sina": "hkFTAI", "qq": "hk.FTAI", "yf": "^FTAI"},
    "a50": {"name": "富时中国 A50", "code": "FTAI", "secid": "100.FTAI", "sina": "hkFTAI", "qq": "hk.FTAI", "yf": "^FTAI"},
    # 德国 DAX
    "dax": {"name": "德国 DAX", "code": "^GDAXI", "secid": "100.GDAXI", "sina": "gb_$dax", "qq": "de.GDAXI", "yf": "^GDAXI", "investing": "172"},
    # 英国富 100
    "ftse100": {"name": "英国富时 100", "code": "^FTSE", "secid": "100.UKX", "sina": "gb_$ftse", "qq": "uk.FTSE", "yf": "^FTSE", "investing": "27"},
    # 法国 CAC40
    "cac40": {"name": "法国 CAC40", "code": "^FCHI", "secid": "100.PX1", "sina": "gb_$px1", "qq": "fr.PX1", "yf": "^FCHI", "investing": "167"},
    # 韩国 Kospi
    "kospi": {"name": "韩国综合股价", "code": "^KS11", "secid": "100.KS11", "sina": "gb_$ks11", "qq": "kr.KS11", "yf": "^KS11", "investing": "39496"},
    # 台湾加权
    "twii": {"name": "台湾加权", "code": "^TWII", "secid": "100.TWII", "sina": "gb_$twii", "qq": "tw.TWII", "yf": "^TWII", "investing": "153"},
    # 印度 Nifty 50
    "nifty50": {"name": "印度 Nifty 50", "code": "^NSEI", "secid": "100.NSEI", "sina": "gb_$nsei", "qq": "in.NSEI", "yf": "^NSEI", "investing": "17940"},
}

# ETF 代码到跟踪指数的映射（用于自动发现）
# 格式：ETF 代码 -> 指数名称关键字列表
ETF_TRACKING_INDEX = {
    # 港股 ETF
    "513210": ["恒生红利", "恒生港股通高股息", "港股通高股息"],
    "513130": ["恒生红利", "恒生港股通高股息", "港股通高股息"],
    "159369": ["国证港股通创新药", "港股通创新药", "创新药"],
    "513860": ["中证港股通 50", "港股通 50"],
    "513010": ["恒生科技", "港股通科技"],
    "513330": ["恒生互联网", "港股通互联网"],
    "513050": ["中概互联", "中证海外中国互联网 50"],
    "513370": ["标普港股通低波红利", "港股通低波红利"],
    # A 股 ETF
    "510300": ["沪深 300"],
    "510500": ["中证 500"],
    "510050": ["上证 50"],
    "588000": ["科创 50"],
    "159915": ["创业板"],
    "512170": ["中证医疗", "医疗"],
    "159992": ["中证医疗", "医疗"],
    "519908": ["白酒", "消费"],
}


# ==================== 工具函数 ====================
def find_etf_by_index_name(index_name: str) -> Optional[str]:
    """
    根据指数名称查找跟踪该指数的 ETF 代码

    Args:
        index_name: 指数名称

    Returns:
        Optional[str]: ETF 代码
    """
    if not index_name:
        return None

    index_name_lower = index_name.lower()

    for etf_code, keywords in ETF_TRACKING_INDEX.items():
        for keyword in keywords:
            if keyword.lower() in index_name_lower:
                return etf_code

    return None
    
def determine_market_type(fund_code: str, fund_name: str = "", fund_type: str = "") -> MarketType:
    """判断基金是场内基金还是场外基金"""
    if not fund_code:
        return MarketType.UNKNOWN

    code_prefix = fund_code[:2]
    code_prefix3 = fund_code[:3] if len(fund_code) >= 3 else ""

    if fund_name:
        name_upper = fund_name.upper()
        if "联接" in fund_name:
            return MarketType.OFF_EXCHANGE
        if any(kw in name_upper for kw in ["ETF", "LOF", "封闭式"]):
            return MarketType.ON_EXCHANGE

    if fund_type:
        type_upper = fund_type.upper()
        if "联接" in fund_type:
            return MarketType.OFF_EXCHANGE
        if any(kw in type_upper for kw in ["ETF", "LOF", "封闭式"]):
            return MarketType.ON_EXCHANGE

    if code_prefix in ("15", "16", "18"):
        return MarketType.ON_EXCHANGE

    if code_prefix == "51":
        if code_prefix3 in ("510", "511", "512", "513", "515", "516", "517", "518"):
            return MarketType.ON_EXCHANGE
        else:
            return MarketType.OFF_EXCHANGE

    if code_prefix == "50":
        return MarketType.ON_EXCHANGE

    if code_prefix == "52":
        return MarketType.ON_EXCHANGE

    if fund_code.startswith("0"):
        return MarketType.OFF_EXCHANGE

    return MarketType.UNKNOWN


def extract_fund_type(fund_name: str) -> str:
    """从基金名称中提取基金类型"""
    fund_name_lower = fund_name.lower()

    if "etf" in fund_name_lower:
        return "ETF 基金"
    if "lof" in fund_name_lower:
        return "LOF 基金"
    if any(kw in fund_name for kw in ["指数", "沪深 300", "中证 500", "中证 1000", "创业板", "科创 50", "上证 50"]):
        return "指数型"
    if any(kw in fund_name for kw in ["股票", "消费行业", "医疗", "科技", "新能源"]):
        return "股票型"
    if any(kw in fund_name for kw in ["混合", "成长", "价值", "精选", "优势"]):
        return "混合型"
    if any(kw in fund_name for kw in ["债券", "纯债", "信用债", "利率债"]):
        return "债券型"
    if any(kw in fund_name for kw in ["货币", "现金"]):
        return "货币型"
    if any(kw in fund_name for kw in ["QDII", "纳斯达克", "标普", "恒生", "港股", "美股"]):
        return "QDII 基金"
    if any(kw in fund_name for kw in ["对冲", "绝对收益"]):
        return "对冲基金"
    if "fof" in fund_name_lower:
        return "FOF 基金"

    return "混合型"


# ==================== 股票价格 API ====================

class StockPriceAPI:
    """
    A 股/港股/美股股票价格 API

    数据源：
    1. 东方财富 Push API (最快)
    2. 新浪财经 API
    3. 腾讯财经 API
    4. AkShare (备份)
    """

    def __init__(self):
        self.session = None
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        self.current_user_agent = random.choice(self.user_agents)
        self._rate_limiter_group = RateLimiterGroup(max_concurrent=3, min_interval=0.3)

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            # 移除 cookie_jar 避免反爬问题，ssl=False 避免某些平台的 SSL 验证问题
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300, ssl=False)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def _close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    def _get_random_headers(self, referer: str = "https://finance.sina.com.cn/") -> Dict:
        """生成随机请求头，增强反爬机制"""
        # 扩展的 User-Agent 池（20 个不同版本）
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:123.0) Gecko/20100101 Firefox/123.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.2; rv:122.0) Gecko/20100101 Firefox/122.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
        ]
        self.current_user_agent = random.choice(self.user_agents)

        # 随机化的 Accept-Language
        accept_languages = [
            "zh-CN,zh;q=0.9,en;q=0.8",
            "zh-CN,zh;q=0.9",
            "zh-CN,zh-TW;q=0.9,zh;q=0.8,en;q=0.7",
            "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        ]

        return {
            "User-Agent": self.current_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": random.choice(accept_languages),
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": referer,
        }

    async def _request_with_retry(self, method: str, url: str, max_retries: int = 5, use_exponential_backoff: bool = True, **kwargs):
        """
        带重试机制的请求（增强反爬版）

        Args:
            method: HTTP 方法
            url: 请求 URL
            max_retries: 最大重试次数
            use_exponential_backoff: 是否使用指数退避
            **kwargs: 其他参数
        """
        for attempt in range(max_retries):
            try:
                session = await self._ensure_session()
                headers = kwargs.pop("headers", {})
                headers.update(self._get_random_headers())
                timeout = kwargs.pop("timeout", aiohttp.ClientTimeout(total=15.0))

                # 每次请求前随机等待 0.1-0.5 秒（避免固定间隔）
                if attempt > 0:
                    base_delay = 0.2 if use_exponential_backoff else 0.5
                    # 指数退避 + 随机抖动
                    delay = base_delay * (2 ** attempt) + random.uniform(0.1, 0.3)
                    await asyncio.sleep(delay)

                async with self._rate_limiter_group.limit(url):
                    async with session.request(method, url, headers=headers, timeout=timeout, **kwargs) as response:
                        if response.status == 200:
                            return await response.text()
                        elif response.status == 429:
                            # 请求过于频繁，延长等待时间
                            wait_time = 2.0 * (attempt + 1) + random.uniform(0.5, 1.0)
                            logger.debug(f"Request rate limited (429), waiting {wait_time:.1f}s")
                            await asyncio.sleep(wait_time)
                        elif response.status == 403:
                            # 可能被反爬拦截，更换 User-Agent 重试
                            logger.debug(f"Request forbidden (403), changing User-Agent")
                            self.current_user_agent = random.choice(self.user_agents)
                            wait_time = 1.0 * (attempt + 1)
                            await asyncio.sleep(wait_time)
                        else:
                            logger.debug(f"Request returned status {response.status}")
                            wait_time = 0.5 * (attempt + 1) + random.uniform(0.2, 0.5)
                            await asyncio.sleep(wait_time)

            except aiohttp.ClientResponseError as e:
                logger.debug(f"HTTP error (attempt {attempt + 1}/{max_retries}): {e.status} {e.message}")
                if e.status == 429:
                    await asyncio.sleep(2.0 * (attempt + 1))
                else:
                    await asyncio.sleep(0.5 * (attempt + 1) + random.uniform(0.2, 0.5))
            except aiohttp.ClientError as e:
                # 网络连接错误
                logger.debug(f"Network error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
                wait_time = 0.5 * (attempt + 1) + random.uniform(0.3, 0.7)
                await asyncio.sleep(wait_time)
            except asyncio.TimeoutError:
                logger.debug(f"Request timeout (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(1.0 * (attempt + 1))
            except Exception as e:
                logger.debug(f"Unexpected error (attempt {attempt + 1}/{max_retries}): {type(e).__name__} - {e}")
                await asyncio.sleep(0.5 * (attempt + 1))

        return None

    async def get_stock_price(self, code: str) -> Optional[MarketData]:
        """
        获取股票价格（支持 A 股/港股/美股）

        Args:
            code: 股票代码
                - A 股：600000, 000001, 300001
                - 港股：00700, 09988
                - 美股：AAPL, GOOGL

        Returns:
            Optional[MarketData]: 股票价格数据
        """
        # 判断市场类型
        is_hk = len(code) == 5 and code.isdigit()
        is_us = code.isalpha() and code.isupper() and len(code) <= 5

        # ===== 数据源 1: 新浪财经 API (优先级最高，最稳定) =====
        try:
            if is_hk:
                url = f"https://hq.sinajs.cn/list=rt_hk_{code}"
                referer = "https://quote.sina.com.cn/"
            elif is_us:
                url = f"https://hq.sinajs.cn/list=gb_{code.lower()}"
                referer = "https://finance.sina.com.cn/stock/usstock/"
            else:
                url = f"https://hq.sinajs.cn/list={'sh' if code.startswith('6') else 'sz'}{code}"
                referer = "https://finance.sina.com.cn/"

            content = await self._request_with_retry("GET", url, headers={"Referer": referer})
            if content:
                # 解析新浪格式：var hq_str_sz000001="平安银行，10.50,10.40,10.45,..."
                match = re.search(r'"([^"]+)"', content)
                if match:
                    parts = match.group(1).split(",")
                    if len(parts) >= 7:
                        name = parts[0]
                        previous_close = float(parts[2]) if parts[2] else 0
                        price = float(parts[3]) if parts[3] else 0
                        change = price - previous_close
                        change_percent = (change / previous_close * 100) if previous_close else 0

                        logger.debug(f"股票 {code} 数据获取成功（新浪）：price={price}, change={change_percent}%")
                        return MarketData(
                            code=code,
                            name=name,
                            price=price,
                            change=change,
                            change_percent=round(change_percent, 2),
                            timestamp=datetime.now(),
                        )
        except Exception as e:
            logger.debug(f"Sina stock API error: {e}")

        # ===== 数据源 2: 东方财富 Push API (备用) =====
        try:
            if is_us:
                secid = f"107.{code}"
            elif is_hk:
                secid = f"116.{code}"
            elif code.startswith("6"):
                secid = f"1.{code}"
            else:
                secid = f"0.{code}"

            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {"secid": secid, "fields": "f43,f44,f45,f46,f47,f48,f49,f14,f169,f170"}

            content = await self._request_with_retry("GET", url, params=params)
            if content:
                data = json.loads(content)
                if data.get("data"):
                    tick = data["data"]
                    price_divisor = 1000 if is_hk else 100
                    price = float(tick.get("f43", 0)) / price_divisor
                    change = float(tick.get("f169", 0)) / 100
                    change_percent = float(tick.get("f170", 0)) / 100

                    logger.debug(f"股票 {code} 数据获取成功（东方财富）：price={price}, change={change_percent}%")
                    return MarketData(
                        code=code,
                        name=tick.get("f14", code) or code,
                        price=price,
                        change=change,
                        change_percent=change_percent,
                        volume=float(tick.get("f47", 0)),
                        timestamp=datetime.now(),
                    )
        except Exception as e:
            logger.debug(f"EastMoney stock API error: {e}")

        # ===== 数据源 3: AkShare (备份) =====
        try:
            if not is_hk and not is_us:
                def _fetch_akshare_stock():
                    stock_df = ak.stock_zh_a_spot_em()
                    stock_data = stock_df[stock_df["代码"] == code]
                    if not stock_data.empty:
                        row = stock_data.iloc[0]
                        return {
                            'name': row["名称"],
                            'price': float(row["最新价"]),
                            'change': float(row["涨跌额"]),
                            'change_percent': float(row["涨跌幅"]),
                        }
                    return None

                # 使用 to_thread 运行同步代码，并设置超时
                result = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_akshare_stock),
                    timeout=15.0
                )

                if result:
                    return MarketData(
                        code=code,
                        name=result['name'],
                        price=result['price'],
                        change=result['change'],
                        change_percent=result['change_percent'],
                        timestamp=datetime.now(),
                    )
        except asyncio.TimeoutError:
            logger.debug(f"AkShare stock API timeout for {code}")
        except Exception as e:
            logger.debug(f"AkShare stock API error: {e}")

        return None

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._close_session()


# ==================== 基金价格 API ====================

class FundPriceAPI:
    """
    基金价格/净值数据 API

    数据源：
    1. 天天基金网详情页 (fund.eastmoney.com)
    2. 天天基金网 pingzhongdata API
    3. 东方财富 F10 详情页
    """

    def __init__(self):
        self.session = None
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        ]
        self.current_user_agent = random.choice(self.user_agents)
        self._rate_limiter_group = RateLimiterGroup(max_concurrent=3, min_interval=0.3)
        self._cache = TtlCache(default_ttl=60, maxsize=512)

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300)
            # 移除 cookie_jar 以避免反爬机制触发
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def _close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    def _get_random_headers(self):
        return {
            "User-Agent": self.current_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://fund.eastmoney.com/",
        }

    async def _request_with_retry(self, method: str, url: str, max_retries: int = 3, **kwargs):
        for attempt in range(max_retries):
            try:
                session = await self._ensure_session()
                headers = kwargs.pop("headers", {})
                headers.update(self._get_random_headers())
                timeout = kwargs.pop("timeout", aiohttp.ClientTimeout(total=15.0))

                async with self._rate_limiter_group.limit(url):
                    async with session.request(method, url, headers=headers, timeout=timeout, **kwargs) as response:
                        if response.status == 200:
                            return await response.text()
                        elif response.status in [429, 403]:
                            await asyncio.sleep(2 * (attempt + 1))
                        else:
                            await asyncio.sleep(1 * (attempt + 1))
            except Exception as e:
                logger.debug(f"Request failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(1 * (attempt + 1))
        return None

    async def get_fund_data(self, code: str) -> Optional[FundData]:
        """
        获取基金完整数据（包含价格和信息）

        Args:
            code: 基金代码

        Returns:
            Optional[FundData]: 基金数据
        """
        # 检查缓存
        cache_key = f"fund_data_{code}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        try:
            # ===== 数据源 1: 天天基金网详情页 =====
            url = f"https://fund.eastmoney.com/{code}.html"
            content = await self._request_with_retry("GET", url)

            if not content:
                return None

            # 提取基金名称
            # 优先级 1: 从 meta 标签获取标准名称
            name_match = re.search(r'<meta name="title" content="([^"]+)"', content)
            if not name_match:
                # 优先级 2: 从"基金名称："标签获取
                name_match = re.search(r"基金名称：([^<]+)", content)
            if not name_match:
                # 优先级 3: 从 title 标签获取（清理多余内容）
                name_match = re.search(r"<title>([^-]+?)-", content)

            fund_name = name_match.group(1).strip() if name_match else code

            # 清理可能残留的 HTML 标签和多余内容
            if fund_name != code:
                # 移除 HTML 标签
                fund_name = re.sub(r'<[^>]+>', '', fund_name)
                # 移除"基金净值_估值_行情走势"等多余内容
                fund_name = re.sub(r'\(?\d{6}\)?基金.*', '', fund_name)
                # 移除"天天基金网"等后缀
                fund_name = re.sub(r'—.*', '', fund_name)
                fund_name = fund_name.strip()

            # 提取单位净值和净值日期
            nav_match = re.search(
                r'单位净值\s*\(\d{4}-\d{2}-\d{2}\)\s*<span class="ui-font-middle ui-color-red ui-num">([\d.]+)</span>',
                content,
            )
            if not nav_match:
                nav_match = re.search(r'<span class="ui-font-middle ui-color-red ui-num">([\d.]+)</span>', content)

            nav_date_match = re.search(r"单位净值\s*\((\d{4}-\d{2}-\d{2})\)", content)
            if not nav_date_match:
                nav_date_match = re.search(r'<span class="pull-right">\((\d{4}-\d{2}-\d{2})\)</span>', content)

            nav = float(nav_match.group(1)) if nav_match else None
            nav_date = nav_date_match.group(1) if nav_date_match else None

            # 提取净值走势数据，用于获取昨日净值
            previous_nav = None
            trend_match = re.search(r"var Data_netWorthTrend = \[(.*?)\];", content, re.DOTALL)
            if trend_match:
                try:
                    nav_data = json.loads("[" + trend_match.group(1) + "]")
                    if nav_data:
                        sorted_nav_data = sorted(nav_data, key=lambda x: x.get("x", 0), reverse=True)
                        if not nav and sorted_nav_data:
                            nav = float(sorted_nav_data[0].get("y", 0))
                            nav_date = datetime.fromtimestamp(sorted_nav_data[0].get("x", 0) / 1000).strftime("%Y-%m-%d")
                        if len(sorted_nav_data) >= 2:
                            previous_nav = float(sorted_nav_data[1].get("y", 0))
                except Exception as e:
                    logger.debug(f"解析净值数据失败：{e}")

            # 如果无法从页面获取，尝试 pingzhongdata API
            if not nav:
                url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
                content = await self._request_with_retry("GET", url)
                if content:
                    nav_match = re.search(r"var Data_netWorthTrend = \[(.*?)\];", content, re.DOTALL)
                    if nav_match:
                        try:
                            nav_data = json.loads("[" + nav_match.group(1) + "]")
                            if nav_data:
                                sorted_nav_data = sorted(nav_data, key=lambda x: x.get("x", 0), reverse=True)
                                nav = float(sorted_nav_data[0].get("y", 0))
                                nav_date = datetime.fromtimestamp(sorted_nav_data[0].get("x", 0) / 1000).strftime("%Y-%m-%d")
                                if len(sorted_nav_data) >= 2:
                                    previous_nav = float(sorted_nav_data[1].get("y", 0))
                        except Exception as e:
                            logger.debug(f"解析 pingzhongdata 失败：{e}")

            # 检查是否成功获取到净值数据
            if nav is None and fund_name == code:
                # 基金名称等于代码，说明天天基金网没有收录该基金
                # 可能是香港互认基金(968开头)、私募基金或其他特殊基金
                # 尝试海外基金 API 获取名称和净值
                if code.startswith("968"):
                    overseas_data = await self._get_overseas_fund_data(code)
                    if overseas_data:
                        fund_data = overseas_data
                        self._cache.set(cache_key, fund_data)
                        return fund_data
                logger.warning(f"基金 {code} 在天天基金网没有收录，无法获取数据")
                return None

            # 获取基金详情信息
            fund_type = await self._get_fund_type(code)
            market_type = determine_market_type(code, fund_name, fund_type)

            # 计算涨跌幅
            change = 0.0
            change_percent = 0.0
            if nav and previous_nav and previous_nav > 0:
                change = nav - previous_nav
                change_percent = (change / previous_nav * 100)

            fund_data = FundData(
                fund_code=code,
                fund_name=fund_name,
                fund_type=fund_type or "混合型",
                nav=nav,
                nav_date=nav_date,
                previous_nav=previous_nav,
                establish_date=None,
                market_type=market_type,
                benchmark=None,
                tracking_index=None,
                price=nav,
                change=change,
                change_percent=change_percent,
                volume=0.0,
                timestamp=datetime.now(),
            )

            self._cache.set(cache_key, fund_data)
            return fund_data

        except Exception as e:
            logger.error(f"获取基金数据失败 {code}: {e}")
            return None

    async def _get_overseas_fund_data(self, code: str) -> Optional[FundData]:
        """968 开头香港互认基金数据 — 三级降级

        1. fundgz.1234567.com.cn  基金实时估值 API（名称+净值+估值）
        2. datacenter.eastmoney   数据中心 API（名称+类型）
        3. yfinance               Yahoo Finance 兜底
        """
        for source_name, fetch_fn in [
            ("fundgz实时估值", partial(self._overseas_source_fundgz, code)),
            ("datacenter API", partial(self._overseas_source_datacenter, code)),
            ("yfinance", partial(self._overseas_source_yfinance, code)),
        ]:
            try:
                result = await fetch_fn()
                if result:
                    logger.info(f"海外基金 {code} 数据来源: {source_name}")
                    return result
            except Exception as e:
                logger.debug(f"海外基金 {code} 来源 [{source_name}] 失败: {e}")
                continue

        logger.warning(f"海外基金 {code} 所有数据源均失败")
        return None

    async def _overseas_source_fundgz(self, code: str) -> Optional[FundData]:
        """数据源 1: fundgz.1234567.com.cn 海外基金实时估值 API"""
        url = f"https://fundgz.1234567.com.cn/js/{code}.js"
        content = await self._request_with_retry("GET", url)
        if not content:
            return None

        match = re.search(r'jsonpgz\((.+)\)', content)
        if not match:
            return None

        import json
        data = json.loads(match.group(1))
        fund_name = data.get("name", code)
        nav = float(data["dwjz"]) if "dwjz" in data else None
        estimated_nav = float(data["gsz"]) if "gsz" in data else None
        nav_date = data.get("jzrq")

        previous_nav = estimated_nav if estimated_nav else nav
        change = 0.0
        change_percent = 0.0
        if nav and previous_nav and previous_nav > 0:
            change = nav - previous_nav
            change_percent = (change / previous_nav * 100)

        return FundData(
            fund_code=code,
            fund_name=fund_name,
            fund_type="海外混合",
            nav=nav, nav_date=nav_date, previous_nav=previous_nav,
            establish_date=None, market_type=MarketType.UNKNOWN,
            benchmark=None, tracking_index=None,
            price=estimated_nav or nav, change=change,
            change_percent=change_percent, volume=0.0,
            timestamp=datetime.now(),
        )

    async def _overseas_source_datacenter(self, code: str) -> Optional[FundData]:
        """数据源 2: fundf10.eastmoney.com 基金概况页（名称+类型）"""
        url = f"https://fundf10.eastmoney.com/jbgk_{code}.html"
        content = await self._request_with_retry("GET", url)
        if not content:
            return None

        # 从 title 提取基金名称
        title_match = re.search(r'<title>(.+?)基金基本概况', content)
        name_from_title = title_match.group(1).strip() if title_match else None
        # 清理标题中的括号部分，如 "(968006)基金基本概况" → ""
        if name_from_title and re.match(r'^\(\d{6}\)$', name_from_title):
            name_from_title = None

        # 从 FundArchivesDatas API 尝试获取（部分海外基金可用）
        api_url = (f"https://api.fund.eastmoney.com/f10/FundArchivesDatas"
                   f"?type=jjgk&code={code}&topline=10")
        api_content = await self._request_with_retry("GET", api_url)
        fund_name = None
        fund_type = "海外混合"
        if api_content:
            try:
                api_data = json.loads(api_content)
                if api_data.get("Data"):
                    rows = api_data["Data"]
                    if rows and isinstance(rows, list) and len(rows) > 0:
                        row = rows[0]
                        fund_name = row.get("FUND_NAME") or row.get("fund_name")
                        fund_type = row.get("FUND_TYPE") or row.get("fund_type", "海外混合")
            except (json.JSONDecodeError, AttributeError, KeyError):
                pass

        if not fund_name:
            fund_name = name_from_title or code

        return FundData(
            fund_code=code,
            fund_name=fund_name,
            fund_type=fund_type,
            nav=None, nav_date=None, previous_nav=None,
            establish_date=None, market_type=MarketType.UNKNOWN,
            benchmark=None, tracking_index=None,
            price=None, change=0.0, change_percent=0.0,
            volume=0.0, timestamp=datetime.now(),
        )

    async def _overseas_source_yfinance(self, code: str) -> Optional[FundData]:
        """数据源 3: yfinance Yahoo Finance 兜底"""
        try:
            import yfinance as yf

            def _sync():
                ticker = yf.Ticker(f"{code}.HK")
                return ticker.info

            info = await asyncio.to_thread(_sync)
        except Exception:
            return None

        fund_name = info.get("shortName") or info.get("longName") or code
        price = info.get("regularMarketPrice") or info.get("previousClose")
        prev_close = info.get("previousClose") or price
        nav_date = datetime.now().strftime("%Y-%m-%d")

        change = 0.0
        change_percent = 0.0
        if price and prev_close and prev_close > 0:
            change = price - prev_close
            change_percent = (change / prev_close * 100)

        return FundData(
            fund_code=code,
            fund_name=fund_name,
            fund_type="海外混合",
            nav=price, nav_date=nav_date, previous_nav=prev_close,
            establish_date=None, market_type=MarketType.UNKNOWN,
            benchmark=None, tracking_index=None,
            price=price, change=change,
            change_percent=change_percent, volume=0.0,
            timestamp=datetime.now(),
        )

    async def _get_fund_type(self, code: str) -> Optional[str]:
        """获取基金类型"""
        try:
            url = f"https://fundf10.eastmoney.com/jbgk_{code}.html"
            content = await self._request_with_retry("GET", url)
            if content:
                type_match = re.search(r"基金类型.*?<td[^>]*>(.*?)</td>", content, re.DOTALL)
                if type_match:
                    return re.sub(r"<[^>]+>", "", type_match.group(1)).strip()
        except Exception as e:
            logger.debug(f"获取基金类型失败：{e}")
        return None

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._close_session()


# ==================== 基金持仓 API ====================

class FundHoldingsAPI:
    """
    基金持仓数据 API

    数据源：
    1. 东方财富基金详情页
    2. 东方财富 F10 详情页
    3. 天天基金网 API
    """

    def __init__(self):
        self.session = None
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        self.current_user_agent = random.choice(self.user_agents)
        self._rate_limiter_group = RateLimiterGroup(max_concurrent=3, min_interval=0.3)

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300)
            # 移除 cookie_jar 以避免反爬机制触发
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def _close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    def _get_random_headers(self):
        return {
            "User-Agent": self.current_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://fund.eastmoney.com/",
        }

    async def _request_with_retry(self, method: str, url: str, max_retries: int = 3, **kwargs):
        for attempt in range(max_retries):
            try:
                session = await self._ensure_session()
                headers = kwargs.pop("headers", {})
                headers.update(self._get_random_headers())
                timeout = kwargs.pop("timeout", aiohttp.ClientTimeout(total=10.0))

                async with self._rate_limiter_group.limit(url):
                    async with session.request(method, url, headers=headers, timeout=timeout, **kwargs) as response:
                        if response.status == 200:
                            return await response.text()
                        await asyncio.sleep(1 * (attempt + 1))
            except Exception as e:
                logger.debug(f"Request failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(1 * (attempt + 1))
        return None

    async def get_fund_holdings(self, fund_code: str) -> List[Holding]:
        """
        获取基金持仓（前 10 大股票）

        Args:
            fund_code: 基金代码

        Returns:
            List[Holding]: 持仓列表，按权重降序排列
        """
        try:
            headers = self._get_random_headers()

            # ===== 数据源 1: 天天基金网 pingzhongdata API (最可靠) =====
            holdings = await self._get_holdings_from_pingzhongdata(fund_code)
            if holdings:
                logger.debug(f"天天基金 pingzhongdata 获取 {fund_code} 持仓成功，共{len(holdings)}条")
                holdings.sort(key=lambda x: x.weight or 0, reverse=True)
                return holdings[:10]

            # ===== 数据源 2: 东方财富基金详情页 =====
            url = f"https://fund.eastmoney.com/{fund_code}.html"
            content = await self._request_with_retry("GET", url, headers=headers)

            if content:
                holdings = self._parse_holdings_from_detail_page(content)
                if holdings:
                    logger.debug(f"东方财富详情页获取 {fund_code} 持仓成功，共{len(holdings)}条")
                    holdings.sort(key=lambda x: x.weight or 0, reverse=True)
                    return holdings[:10]

            # ===== 数据源 3: 东方财富 F10 详情页 =====
            url = f"https://fundf10.eastmoney.com/ccmx_{fund_code}.html"
            content = await self._request_with_retry("GET", url, headers=headers)
            if content:
                holdings = self._parse_holdings_from_f10_page(content)
                if holdings:
                    logger.debug(f"东方财富 F10 获取 {fund_code} 持仓成功，共{len(holdings)}条")
                    holdings.sort(key=lambda x: x.weight or 0, reverse=True)
                    return holdings[:10]

            # ===== 数据源 4: 天天基金网 API =====
            holdings = await self._get_holdings_from_api(fund_code)
            if holdings:
                logger.debug(f"天天基金 API 获取 {fund_code} 持仓成功，共{len(holdings)}条")
                holdings.sort(key=lambda x: x.weight or 0, reverse=True)
                return holdings[:10]

            logger.warning(f"No holdings data for fund {fund_code}")
            return []

        except Exception as e:
            logger.error(f"获取基金持仓失败 {fund_code}: {e}")
            return []

    async def _get_holdings_from_pingzhongdata(self, fund_code: str) -> List[Holding]:
        """从天天基金网 pingzhongdata 获取持仓"""
        try:
            url = f"http://fundgz.1234567.com.cn/js/{fund_code}.js"
            headers = self._get_random_headers()
            headers["Referer"] = f"https://fund.eastmoney.com/{fund_code}.html"

            content = await self._request_with_retry("GET", url, headers=headers)
            if not content:
                return []

            # 解析 jsonp 格式：jsonpgz({"fundcode":"...","name":"...","stock": [...]})
            match = re.search(r"jsonpgz\((.*?)\);?", content, re.DOTALL)
            if not match:
                return []

            try:
                data = json.loads(match.group(1))
                holdings = []

                # 获取持仓股票数据
                if data.get("stock"):
                    for stock in data["stock"]:
                        try:
                            asset_code = stock.get("code", "")
                            asset_name = stock.get("name", "")
                            weight_str = stock.get("weight", "")

                            if weight_str and asset_code and asset_name:
                                weight = float(weight_str)
                                if weight > 0:
                                    holdings.append(Holding(
                                        asset_code=asset_code,
                                        asset_name=asset_name,
                                        asset_type=AssetType.STOCK,
                                        quantity=0,
                                        market_value=0,
                                        weight=weight,
                                        price=0,
                                    ))
                        except (ValueError, KeyError) as e:
                            logger.debug(f"解析 pingzhongdata 持仓失败：{e}")

                return holdings
            except json.JSONDecodeError:
                return []

        except Exception as e:
            logger.debug(f"从天天基金 pingzhongdata 获取持仓失败 {fund_code}: {e}")
            return []

    def _parse_holdings_from_detail_page(self, content: str) -> List[Holding]:
        """从详情页解析持仓"""
        holdings = []

        stock_table_match = re.search(
            r"<table[^>]*>\s*<tr>\s*<th[^>]*>股票名称</th>.*?</table>",
            content, re.DOTALL
        )
        if stock_table_match:
            table_html = stock_table_match.group(0)
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL)

            for row in rows[1:]:
                try:
                    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
                    if len(cells) < 2:
                        continue

                    name_match = re.search(r">([^<>]+)</a>", cells[0])
                    if not name_match:
                        continue
                    asset_name = name_match.group(1).strip()

                    # 尝试多种代码格式匹配
                    code_match = None
                    asset_code = None

                    # 1. 东方财富统一格式：href="//quote.eastmoney.com/unify/r/[市场代码].[股票代码]"
                    # 市场代码：0=A 股深市，1=A 股沪市，116=港股
                    code_match = re.search(
                        r'href="//quote\.eastmoney\.com/unify/r/(\d+)\.([0-9]+)"',
                        cells[0],
                    )
                    if code_match:
                        market_code = code_match.group(1)
                        stock_code = code_match.group(2)
                        # 港股代码（市场代码 116）通常是 5 位数
                        if market_code == "116":
                            asset_code = stock_code.zfill(5)  # 补齐 5 位
                        else:
                            asset_code = stock_code.zfill(6)  # 补齐 6 位（A 股）

                    # 2. 备用：直接匹配括号内的代码 (000001) 或 (00700)
                    if not asset_code:
                        code_match = re.search(r"\((\d{5,6})\)", cells[0])
                        if code_match:
                            asset_code = code_match.group(1).strip()

                    # 3. 备用：匹配 HK 开头的港股代码
                    if not asset_code:
                        code_match = re.search(r"\(HK(\d{5})\)", cells[0], re.IGNORECASE)
                        if code_match:
                            asset_code = code_match.group(1)

                    if not asset_code:
                        continue

                    weight_str = re.sub(r"<[^>]+>", "", cells[1]).strip()
                    if "%" in weight_str:
                        weight = float(weight_str.replace("%", ""))
                        if weight > 0:
                            holdings.append(Holding(
                                asset_code=asset_code,
                                asset_name=asset_name,
                                asset_type=AssetType.STOCK,
                                quantity=0,
                                market_value=0,
                                weight=weight,
                                price=0,
                            ))
                except Exception as e:
                    logger.debug(f"解析持仓行失败：{e}")

        return holdings

    def _parse_holdings_from_f10_page(self, content: str) -> List[Holding]:
        """从 F10 页面解析持仓"""
        holdings = []

        f10_table_match = re.search(
            r'<table[^>]*id="cctable"[^>]*>.*?<tbody>(.*?)</tbody>',
            content, re.DOTALL
        )
        if f10_table_match:
            table_html = f10_table_match.group(1)
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL)

            for row in rows:
                try:
                    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
                    if len(cells) < 4:
                        continue

                    asset_name = re.sub(r"<[^>]+>", "", cells[0]).strip()
                    asset_code = re.sub(r"<[^>]+>", "", cells[1]).strip()
                    weight_str = re.sub(r"<[^>]+>", "", cells[3]).strip()

                    if "%" in weight_str:
                        weight = float(weight_str.replace("%", ""))
                        if weight > 0:
                            holdings.append(Holding(
                                asset_code=asset_code,
                                asset_name=asset_name,
                                asset_type=AssetType.STOCK,
                                quantity=0,
                                market_value=0,
                                weight=weight,
                                price=0,
                            ))
                except Exception as e:
                    logger.debug(f"解析 F10 持仓行失败：{e}")

        return holdings

    async def _get_holdings_from_api(self, fund_code: str) -> List[Holding]:
        """从天天基金网 API 获取持仓"""
        try:
            url = "https://api.fund.eastmoney.com/f10/CCMX"
            params = {"fundcode": fund_code}
            headers = self._get_random_headers()
            headers["Accept"] = "application/json"

            content = await self._request_with_retry("GET", url, params=params, headers=headers)
            if not content:
                return []

            data = json.loads(content)
            holdings = []

            if data.get("data") and data["data"].get("data"):
                for stock in data["data"]["data"]:
                    try:
                        asset_code = stock.get("GPDM", "")
                        asset_name = stock.get("GPJC", "")
                        weight_str = stock.get("JZBL", "")

                        if weight_str:
                            if "%" in weight_str:
                                weight = float(weight_str.replace("%", "").strip())
                            else:
                                weight = float(weight_str)

                            if weight > 0:
                                holdings.append(Holding(
                                    asset_code=asset_code,
                                    asset_name=asset_name,
                                    asset_type=AssetType.STOCK,
                                    quantity=0,
                                    market_value=0,
                                    weight=weight,
                                    price=0,
                                ))
                    except (ValueError, KeyError) as e:
                        logger.debug(f"解析 API 持仓失败：{e}")

            return holdings

        except Exception as e:
            logger.error(f"从 API 获取持仓失败 {fund_code}: {e}")
            return []

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._close_session()


# ==================== 指数价格 API ====================

class IndexPriceAPI:
    """
    国内指数价格 API

    数据源：
    1. 腾讯财经 API (最快，最稳定)
    2. 新浪财经 API
    3. 东方财富 Push API
    4. AkShare (兜底)
    """

    def __init__(self):
        self.session = None
        # 扩展的 User-Agent 池
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        ]
        self.current_user_agent = random.choice(self.user_agents)
        self._rate_limiter_group = RateLimiterGroup(max_concurrent=3, min_interval=0.3)
        # ponytail: 30s TTL 避免高频重复请求触发反爬
        self._cache = TtlCache(default_ttl=30, maxsize=64)

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            # 移除 cookie_jar 避免反爬问题，ssl=False 避免某些平台的 SSL 验证问题
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300, ssl=False)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def _close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    def _get_random_headers(self, referer: str = "https://finance.qq.com/") -> Dict:
        """生成随机请求头，增强反爬机制"""
        self.current_user_agent = random.choice(self.user_agents)
        return {
            "User-Agent": self.current_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": referer,
        }

    async def _request_with_retry(self, method: str, url: str, max_retries: int = 5, use_exponential_backoff: bool = True, **kwargs):
        """带重试机制的请求（增强反爬版）"""
        for attempt in range(max_retries):
            try:
                session = await self._ensure_session()
                headers = kwargs.pop("headers", {})
                headers.update(self._get_random_headers())
                timeout = kwargs.pop("timeout", aiohttp.ClientTimeout(total=15.0))

                # 指数退避 + 随机抖动
                if attempt > 0:
                    base_delay = 0.3 if use_exponential_backoff else 0.5
                    delay = base_delay * (2 ** attempt) + random.uniform(0.1, 0.3)
                    await asyncio.sleep(delay)

                async with self._rate_limiter_group.limit(url):
                    async with session.request(method, url, headers=headers, timeout=timeout, **kwargs) as response:
                        if response.status == 200:
                            return await response.text()
                        elif response.status == 429:
                            wait_time = 2.0 * (attempt + 1) + random.uniform(0.5, 1.0)
                            await asyncio.sleep(wait_time)
                        elif response.status == 403:
                            self.current_user_agent = random.choice(self.user_agents)
                            wait_time = 1.0 * (attempt + 1)
                            await asyncio.sleep(wait_time)
                        else:
                            wait_time = 0.5 * (attempt + 1) + random.uniform(0.2, 0.5)
                            await asyncio.sleep(wait_time)

            except aiohttp.ClientResponseError as e:
                logger.debug(f"HTTP error (attempt {attempt + 1}/{max_retries}): {e.status}")
                await asyncio.sleep(0.5 * (attempt + 1) + random.uniform(0.2, 0.5))
            except aiohttp.ClientError as e:
                logger.debug(f"Network error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
                wait_time = 0.5 * (attempt + 1) + random.uniform(0.3, 0.7)
                await asyncio.sleep(wait_time)
            except asyncio.TimeoutError:
                logger.debug(f"Request timeout (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(1.0 * (attempt + 1))
            except Exception as e:
                logger.debug(f"Unexpected error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
                await asyncio.sleep(0.5 * (attempt + 1))

        return None

    async def get_index_price(self, code: str) -> Optional[MarketData]:
        """获取指数价格"""
        index_info = INDEX_MAPPING.get(code)
        if not index_info:
            return None

        # 债券指数不支持
        if index_info.get("type") == "bond":
            logger.debug(f"债券指数 {code} 不支持实时数据获取")
            return None

        # ponytail: 30s TTL 缓存避免高频重复请求
        cache_key = f"idx_{code}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        # ===== 数据源 1: 腾讯财经 API (最稳定) =====
        try:
            index_code = index_info["code"]
            # 腾讯 API 格式：sh000300, sz399006
            url = f"https://qt.gtimg.cn/q={index_code}"
            content = await self._request_with_retry("GET", url, timeout=aiohttp.ClientTimeout(total=5.0))
            if content:
                # 解析腾讯格式：v_sh000300="1~沪深 300~000300~4660.44~..."
                match = re.search(r'"([^"]+)"', content)
                if match:
                    parts = match.group(1).split("~")
                    if len(parts) >= 5:
                        # 腾讯格式详解：
                        # 0: 未知标识
                        # 1: 名称
                        # 2: 代码
                        # 3: 当前价
                        # 4: 昨收价
                        # 5: 开盘价
                        # ...
                        # 6: 涨跌幅 (百分比)
                        name = parts[1] if len(parts) > 1 else index_info["name"]
                        price = float(parts[3]) if len(parts) > 3 and parts[3] else 0
                        previous_close = float(parts[4]) if len(parts) > 4 and parts[4] else 0
                        change = price - previous_close
                        change_percent = (change / previous_close * 100) if previous_close else 0

                        logger.debug(f"腾讯指数 {code} 成功：price={price}, change_percent={change_percent}%")
                        md = MarketData(
                            code=code,
                            name=name,
                            price=price,
                            change=change,
                            change_percent=round(change_percent, 2),
                            timestamp=datetime.now(),
                        )
                        self._cache.set(cache_key, md)
                        return md
        except Exception as e:
            logger.debug(f"Tencent index API error: {e}")

        # ===== 数据源 2: 新浪财经 API (支持港股指数) =====
        try:
            sina_code = index_info.get("sina", index_info.get("code", ""))
            if sina_code:
                url = f"https://hq.sinajs.cn/list={sina_code}"
                content = await self._request_with_retry("GET", url, timeout=aiohttp.ClientTimeout(total=5.0))
                if content:
                    match = re.search(r'"([^"]+)"', content)
                    if match:
                        parts = match.group(1).split(",")
                        if len(parts) >= 7:
                            name = parts[0]
                            previous_close = float(parts[2]) if parts[2] else 0
                            price = float(parts[3]) if parts[3] else 0

                            # 港股指数只有 4 个字段
                            if len(parts) >= 5:
                                change_percent = float(parts[2]) if parts[2] else 0
                            else:
                                change_percent = ((price - previous_close) / previous_close * 100) if previous_close else 0

                            logger.debug(f"新浪指数 {code} 成功：price={price}, change_percent={change_percent}%")
                            md = MarketData(
                                code=code,
                                name=name,
                                price=price,
                                change=price - previous_close,
                                change_percent=change_percent,
                                timestamp=datetime.now(),
                            )
                            self._cache.set(cache_key, md)
                            return md
        except Exception as e:
            logger.debug(f"Sina index API error: {e}")

        # ===== 数据源 3: 东方财富 Push API (仅支持 A 股指数) =====
        # 港股指数不使用东方财富，直接跳过到数据源 4
        index_code = index_info["code"]
        if not index_code.startswith("hk"):
            try:
                if index_code.startswith("sh"):
                    secid = f"1.{index_code[2:]}"
                elif index_code.startswith("sz"):
                    secid = f"0.{index_code[2:]}"
                else:
                    secid = f"1.{index_code}"

                url = "https://push2.eastmoney.com/api/qt/stock/get"
                params = {"secid": secid, "fields": "f43,f44,f45,f46,f47,f48,f49,f14,f169,f170"}
                kwargs["headers"] = {"Referer": "https://quote.eastmoney.com/"}

                content = await self._request_with_retry("GET", url, params=params, timeout=aiohttp.ClientTimeout(total=5.0))
                if content:
                    data = json.loads(content)
                    if data.get("data"):
                        tick = data["data"]
                        price = float(tick.get("f43", 0)) / 100
                        open_price = float(tick.get("f46", 0)) / 100
                        change = price - open_price
                        change_percent = (change / open_price * 100) if open_price > 0 else 0

                        logger.debug(f"东方财富指数 {code} 成功：price={price}, change_percent={change_percent}%")
                        md = MarketData(
                            code=code,
                            name=tick.get("f14", code) or index_info["name"],
                            price=price,
                            change=change,
                            change_percent=change_percent,
                            volume=float(tick.get("f47", 0)),
                            timestamp=datetime.now(),
                        )
                        self._cache.set(cache_key, md)
                        return md
            except Exception as e:
                logger.debug(f"EastMoney index API error: {e}")

        # ===== 数据源 4: 使用 ETF 价格作为替代（针对无法直接获取的指数） =====
        # 对于无法通过常规数据源获取的指数（如某些港股指数），使用跟踪该指数的 ETF 价格作为替代
        # 这样可以避免硬编码每个指数的 ETF 对应关系

        etf_code = find_etf_by_index_name(index_info.get("name", ""))
        if etf_code:
            try:
                url = f"https://push2.eastmoney.com/api/qt/stock/get?secid=1.{etf_code}&fields=f43,f44,f45,f46,f47,f48,f49,f14,f169,f170"
                kwargs["headers"] = {"Referer": "https://quote.eastmoney.com/"}
                content = await self._request_with_retry("GET", url, timeout=aiohttp.ClientTimeout(total=5.0))
                if content:
                    data = json.loads(content)
                    if data.get("data"):
                        tick = data["data"]
                        # f43=当前价，f169=涨跌额，f170=涨跌幅（都需要除以 100）
                        price = float(tick.get("f43", 0)) / 100
                        change = float(tick.get("f169", 0)) / 100
                        change_percent = float(tick.get("f170", 0)) / 100

                        logger.info(f"指数 {code} 使用 ETF {etf_code} 价格作为替代：price={price}, change_percent={change_percent}%")
                        md = MarketData(
                            code=code,
                            name=f"{index_info['name']} ({etf_code} 替代)",
                            price=price,
                            change=change,
                            change_percent=round(change_percent, 2),
                            timestamp=datetime.now(),
                        )
                        self._cache.set(cache_key, md)
                        return md
            except Exception as e:
                logger.debug(f"使用 ETF 替代指数数据失败：{e}")

        return None

    async def get_index_realtime_data(self, index_code: str) -> Optional[Dict[str, Any]]:
        """获取指数实时数据（返回字典格式）"""
        market_data = await self.get_index_price(index_code)
        if market_data:
            return {
                "code": index_code,
                "name": market_data.name,
                "price": market_data.price,
                "change_percent": market_data.change_percent,
                "change": market_data.change,
            }
        return None

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._close_session()


# ==================== 债券指数 API ====================

class BondIndexAPI:
    """
    债券指数价格 API

    数据源：
    1. 中债估值中心 (chinabond.com.cn) - 官方权威数据
    2. 中证指数公司 (csindex.com.cn) - 中证债券指数
    3. 东方财富债券指数 API
    4. AkShare 债券指数接口

    支持的指数：
    - 中债综合指数 (CBA00101)
    - 中债总指数 (CBA00201)
    - 中债国债总指数 (CBA00401)
    - 中债政策性金融债指数 (CBA00501)
    - 中债企业债总指数 (CBA00601)
    - 中证国债指数 (H11070)
    - 中证金融债指数 (H11071)
    - 中证企业债指数 (H11072)
    """

    def __init__(self):
        self.session = None
        self._cache = TtlCache(default_ttl=60, maxsize=256)
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        ]
        self._rate_limiter_group = RateLimiterGroup(max_concurrent=3, min_interval=0.3)

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300, ssl=False)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def _close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    async def _request_with_retry(self, method: str, url: str, max_retries: int = 3, **kwargs):
        """带重试的请求"""
        for attempt in range(max_retries):
            try:
                session = await self._ensure_session()
                headers = kwargs.pop("headers", {})
                headers["User-Agent"] = random.choice(self.user_agents)
                timeout = kwargs.pop("timeout", aiohttp.ClientTimeout(total=10.0))

                async with self._rate_limiter_group.limit(url):
                    async with session.request(method, url, headers=headers, timeout=timeout, **kwargs) as response:
                        if response.status == 200:
                            return await response.text()
                        await asyncio.sleep(1 * (attempt + 1))
            except Exception as e:
                logger.debug(f"BondIndexAPI 请求失败 (尝试 {attempt + 1}/{max_retries}): {type(e).__name__}: {e}")
                await asyncio.sleep(1 * (attempt + 1))
        return None

    async def get_bond_index_data(self, index_code: str) -> Optional[Dict[str, Any]]:
        """
        获取债券指数数据

        Args:
            index_code: 指数代码 (如 CBA00101, H11070 等)

        Returns:
            Optional[Dict]: 指数数据，包含 code, name, price, change_percent 等
        """
        # 检查缓存
        cache_key = f"bond_index_{index_code}"
        cached_data = self._cache.get(cache_key)
        if cached_data:
            logger.debug(f"债券指数 {index_code} 使用缓存数据")
            return cached_data

        index_info = INDEX_MAPPING.get(index_code)
        if not index_info or index_info.get("type") != "bond":
            # 尝试从代码判断是否为债券指数
            if not (index_code.startswith("CBA") or index_code.startswith("H110")):
                return None
            index_info = {"name": "债券指数", "code": index_code, "type": "bond"}

        # ===== 数据源 1: 中债估值中心 API =====
        if index_info.get("source") == "chinabond" or index_code.startswith("CBA"):
            data = await self._get_chinabond_index_data(index_code)
            if data:
                self._cache.set(cache_key, data)
                return data

        # ===== 数据源 2: 中证指数公司 API =====
        if index_info.get("source") == "csi" or index_code.startswith("H110"):
            data = await self._get_csi_bond_index_data(index_code)
            if data:
                self._cache.set(cache_key, data)
                return data

        # ===== 数据源 3: 东方财富债券指数 API =====
        data = await self._get_eastmoney_bond_index_data(index_code)
        if data:
            self._cache.set(cache_key, data)
            return data

        # ===== 数据源 4: AkShare (最后备用) =====
        data = await self._get_akshare_bond_index_data(index_code)
        if data:
            self._cache.set(cache_key, data)
            return data

        logger.warning(f"债券指数 {index_code} 所有数据源均失败")
        return None

    async def _get_chinabond_index_data(self, index_code: str) -> Optional[Dict[str, Any]]:
        """
        从中债估值中心获取数据

        中债指数通过 yield.chinabond.com.cn 提供数据
        由于官方 API 需要授权，这里使用网页爬虫方式获取公开数据
        """
        # 获取指数信息
        index_info = INDEX_MAPPING.get(index_code, {"name": "中债指数", "code": index_code})

        try:
            # 中债估值中心的指数详情页面
            url = f"https://yield.chinabond.com.cn/cbweb-mn/yc/ycDetail?curveCode={index_code}&curveType=0"
            headers = {
                "User-Agent": random.choice(self.user_agents),
                "Referer": "https://yield.chinabond.com.cn/",
            }

            # 注意：中债网站可能需要 JavaScript 渲染，直接请求可能无法获取数据
            # 这里作为尝试，如果失败则使用其他数据源
            content = await self._request_with_retry("GET", url, headers=headers, max_retries=2)
            if content:
                # 尝试从 HTML 中提取指数数据
                # 中债格式通常包含指数值、涨跌幅等
                match = re.search(r"指数值 [\s\S]*?(\d+\.\d+)", content)
                if match:
                    index_value = float(match.group(1))
                    logger.debug(f"中债指数 {index_code} 成功：index_value={index_value}")
                    return {
                        "code": index_code,
                        "name": index_info.get("name", "中债指数"),
                        "price": index_value,
                        "change_percent": 0.0,  # 中债指数日内波动极小，近似为 0
                        "source": "chinabond",
                    }
        except Exception as e:
            logger.debug(f"中债估值中心 API error: {e}")

        # 如果实时数据获取失败，返回一个估算值
        # 债券指数日内波动通常小于 0.05%，可以用 0 近似
        logger.info(f"中债指数 {index_code} 使用估算数据（债券日内波动极小）")
        return {
            "code": index_code,
            "name": index_info.get("name", "中债指数"),
            "price": 0,  # 价格指数不需要
            "change_percent": 0.0,
            "source": "chinabond_estimate",
            "note": "债券指数日内波动极小，涨跌幅近似为 0",
        }

    async def _get_csi_bond_index_data(self, index_code: str) -> Optional[Dict[str, Any]]:
        """
        从中证指数公司获取债券指数数据

        中证指数公司官网：www.csindex.com.cn
        """
        # 获取指数信息
        index_info = INDEX_MAPPING.get(index_code, {"name": "中证债券指数", "code": index_code})

        try:
            # 中证指数公司 API
            url = f"https://www.csindex.com.cn/csindex-home/quote/index-detail/{index_code}"
            content = await self._request_with_retry("GET", url, max_retries=2)
            if content:
                # 解析中证指数数据
                match = re.search(r"最新价 [\s\S]*?(\d+\.\d+)", content)
                if match:
                    price = float(match.group(1))
                    logger.debug(f"中证债券指数 {index_code} 成功：price={price}")
                    return {
                        "code": index_code,
                        "name": index_info.get("name", "中证债券指数"),
                        "price": price,
                        "change_percent": 0.0,
                        "source": "csi",
                    }
        except Exception as e:
            logger.debug(f"中证指数公司 API error: {e}")

        return None

    async def _get_eastmoney_bond_index_data(self, index_code: str) -> Optional[Dict[str, Any]]:
        """从东方财富获取债券指数数据"""
        try:
            # 东方财富债券指数 API
            # 格式：md511.CBA00101
            secid = f"md5{index_code}"
            url = "https://push2.eastmoney.com/api/qt/stock/get"
            params = {
                "secid": secid,
                "fields": "f43,f44,f45,f46,f47,f48,f49,f14,f169,f170",
            }

            content = await self._request_with_retry("GET", url, params=params, max_retries=2)
            if content:
                data = json.loads(content)
                if data.get("data"):
                    tick = data["data"]
                    price = float(tick.get("f43", 0)) / 100
                    change_percent = float(tick.get("f170", 0)) / 100

                    logger.debug(f"东方财富债券指数 {index_code} 成功：price={price}, change_percent={change_percent}%")
                    return {
                        "code": index_code,
                        "name": tick.get("f14", "债券指数"),
                        "price": price,
                        "change_percent": round(change_percent, 4),
                        "source": "eastmoney",
                    }
        except Exception as e:
            logger.debug(f"东方财富债券指数 API error: {e}")

        return None

    async def _get_akshare_bond_index_data(self, index_code: str) -> Optional[Dict[str, Any]]:
        """从 AkShare 获取债券指数数据"""
        try:
            # AkShare 债券指数接口
            import akshare as ak

            # 中债综合指数
            if index_code in ["CBA00101", "bond_index", "中债综合指数"]:
                def _fetch_bond_index():
                    data = ak.bond_composite_index_cbond()
                    if data is not None and len(data) > 0:
                        return data.iloc[-1]
                    return None

                # 使用 to_thread 运行同步代码，并设置超时
                latest = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_bond_index),
                    timeout=15.0
                )

                if latest is not None:
                    logger.debug(f"AkShare 债券指数成功：data={latest}")
                    return {
                        "code": index_code,
                        "name": "中债综合指数",
                        "price": float(latest.get("指数值", 0)),
                        "change_percent": 0.0,  # AkShare 提供的是日频数据，无法计算日内涨跌
                        "source": "akshare",
                        "note": "AkShare 提供的是日频数据，非实时",
                    }
        except asyncio.TimeoutError:
            logger.debug(f"AkShare 债券指数 API timeout for {index_code}")
        except Exception as e:
            logger.debug(f"AkShare 债券指数 API error: {e}")

        return None


# ==================== 海外指数 API ====================

class GlobalIndexAPI:
    """
    海外指数价格 API

    数据源（按指数路由）：
    - US 指数: 腾讯API (qt.gtimg.cn) → yfinance
    - HK 指数: 新浪 (hq.sinajs.cn) → 腾讯API → yfinance
    - 欧/亚/其他: yfinance (Yahoo Finance)

    ponytail: 已移除失效的新浪美股(gb_$)、Investing.com、东方财富海外源
    """

    def __init__(self):
        self.session = None
        # 扩展的 User-Agent 池
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        ]
        self.current_user_agent = random.choice(self.user_agents)
        self._rate_limiter_group = RateLimiterGroup(max_concurrent=3, min_interval=0.3)

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300)
            # 移除 cookie_jar 以避免反爬机制触发
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def _close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    def _get_random_headers(self):
        """生成随机请求头，增强反爬机制"""
        self.current_user_agent = random.choice(self.user_agents)
        return {
            "User-Agent": self.current_user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": "https://quote.eastmoney.com/",
            "sec-ch-ua": '"Not A(Brand";v="8", "Chromium";v="121", "Microsoft Edge";v="121"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }

    async def _request_with_retry(self, method: str, url: str, max_retries: int = 2, use_exponential_backoff: bool = True, **kwargs):
        """带重试机制的请求（增强反爬版）"""
        for attempt in range(max_retries):
            try:
                session = await self._ensure_session()
                headers = kwargs.pop("headers", {})
                headers.update(self._get_random_headers())
                timeout = kwargs.pop("timeout", aiohttp.ClientTimeout(total=8.0))

                # 指数退避 + 随机抖动
                if attempt > 0:
                    base_delay = 0.5 if use_exponential_backoff else 0.5
                    delay = base_delay * (2 ** attempt) + random.uniform(0.1, 0.3)
                    await asyncio.sleep(delay)

                async with self._rate_limiter_group.limit(url):
                    async with session.request(method, url, headers=headers, timeout=timeout, **kwargs) as response:
                        if response.status == 200:
                            return await response.text()
                        elif response.status == 429:
                            wait_time = 2.0 * (attempt + 1) + random.uniform(0.5, 1.0)
                            await asyncio.sleep(wait_time)
                        elif response.status == 403:
                            self.current_user_agent = random.choice(self.user_agents)
                            wait_time = 1.0 * (attempt + 1)
                            await asyncio.sleep(wait_time)
                        else:
                            wait_time = 0.5 * (attempt + 1) + random.uniform(0.2, 0.5)
                            await asyncio.sleep(wait_time)

            except aiohttp.ClientResponseError as e:
                logger.debug(f"HTTP error (attempt {attempt + 1}/{max_retries}): {e.status}")
                await asyncio.sleep(0.5 * (attempt + 1) + random.uniform(0.2, 0.5))
            except aiohttp.ClientError as e:
                logger.debug(f"Network error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
                wait_time = 0.5 * (attempt + 1) + random.uniform(0.3, 0.7)
                await asyncio.sleep(wait_time)
            except asyncio.TimeoutError:
                logger.debug(f"Request timeout (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(1.0 * (attempt + 1))
            except Exception as e:
                logger.debug(f"Unexpected error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
                await asyncio.sleep(0.5 * (attempt + 1))

        return None

    async def get_global_index_realtime_data(self, index_code: str) -> Optional[Dict[str, Any]]:
        """
        获取海外指数实时数据

        Args:
            index_code: 指数代码 (nasdaq, sp500, hsi, nikkei 等)

        数据源优先级（按指数类型）:
        - 美股 (us.*): 腾讯API 快取 → yfinance
        - 港股 (hk*):  新浪HK → 腾讯HK → yfinance
        - 其他 (欧/亚/商品): yfinance 直取
        """
        index_info = GLOBAL_INDEX_MAPPING.get(index_code)
        if not index_info:
            return None

        qq_code = index_info.get("qq", "")
        sina_code = index_info.get("sina", "")
        yf_code = index_info.get("yf", index_info.get("code", ""))

        # ===== 美股: 腾讯API (us.*) 最快，<1s =====
        if qq_code and qq_code.startswith("us."):
            data = await self._get_tencent_index_data(qq_code, index_info["name"])
            if data:
                return data

        # ===== 港股: 新浪HK (延迟低，格式稳定) =====
        if sina_code and sina_code.startswith("hk"):
            data = await self._get_sina_hk_index_data(sina_code, index_info["name"])
            if data:
                return data

        # ===== 港股/其他: 腾讯HK (新浪兜底后) =====
        if qq_code and not qq_code.startswith("us."):
            data = await self._get_tencent_index_data(qq_code, index_info["name"])
            if data:
                return data

        # ===== 通用兜底: yfinance (2~5s, 覆盖所有指数) =====
        if yf_code:
            data = await self._get_yfinance_index_data(yf_code, index_info["name"])
            if data:
                return data

        logger.warning(f"海外指数 {index_code} 所有数据源均失败")

        # ===== 指数回退机制 =====
        fallback_map = {
            "hshk_dividend": "hsi",
            "sp_hkconnect": "hsi",
        }
        if index_code in fallback_map:
            fallback_code = fallback_map[index_code]
            logger.info(f"指数 {index_code} 尝试使用替代指数 {fallback_code}")
            fallback_data = await self.get_global_index_realtime_data(fallback_code)
            if fallback_data:
                fallback_data["code"] = index_code
                fallback_data["name"] = index_info["name"]
                fallback_data["fallback_from"] = fallback_code
                return fallback_data

        return None

    async def _get_tencent_index_data(self, qq_code: str, index_name: str) -> Optional[Dict[str, Any]]:
        """腾讯财经 API - 美股/港股指数，最快数据源"""
        try:
            url = f"https://qt.gtimg.cn/q={qq_code}"
            content = await self._request_with_retry("GET", url, max_retries=2, timeout=aiohttp.ClientTimeout(total=5.0))
            if not content:
                return None

            match = re.search(r'"([^"]+)"', content)
            if not match:
                return None

            parts = match.group(1).split("~")
            if len(parts) < 6:
                return None

            name = parts[1] if len(parts) > 1 else index_name
            price_str = parts[3] if len(parts) > 3 else ""
            prev_close_str = parts[4] if len(parts) > 4 else ""
            change_pct_str = parts[32] if len(parts) > 32 else ""

            if not price_str:
                return None

            price = float(price_str)
            previous_close = float(prev_close_str) if prev_close_str else 0
            change_percent = float(change_pct_str) if change_pct_str else 0
            change = price - previous_close if previous_close else 0

            return {
                "code": qq_code,
                "name": name,
                "price": price,
                "change_percent": round(change_percent, 2),
                "change": round(change, 2),
                "source": "tencent",
            }
        except Exception as e:
            logger.debug(f"Tencent index API error ({qq_code}): {e}")
        return None

    async def _get_sina_hk_index_data(self, sina_code: str, index_name: str) -> Optional[Dict[str, Any]]:
        """新浪财经港股指数 API"""
        try:
            url = f"https://hq.sinajs.cn/list={sina_code}"
            content = await self._request_with_retry("GET", url, max_retries=2, timeout=aiohttp.ClientTimeout(total=5.0))
            if not content:
                return None

            match = re.search(r'"([^"]+)"', content)
            if not match:
                return None

            parts = match.group(1).split(",")
            if len(parts) < 7:
                return None

            name = parts[0] or index_name
            previous_close = float(parts[2]) if parts[2] else 0
            price = float(parts[3]) if parts[3] else 0

            if price == 0:
                return None

            change = price - previous_close
            change_percent = (change / previous_close * 100) if previous_close else 0

            return {
                "code": sina_code,
                "name": name,
                "price": price,
                "change_percent": round(change_percent, 2),
                "change": round(change, 2),
                "source": "sina",
            }
        except Exception as e:
            logger.debug(f"Sina HK index API error ({sina_code}): {e}")
        return None

    async def _get_yfinance_index_data(self, yf_code: str, index_name: str) -> Optional[Dict[str, Any]]:
        """yfinance (Yahoo Finance) - 通用兜底"""
        try:
            def _fetch():
                ticker = yf.Ticker(yf_code)
                hist = ticker.history(period="1d")
                if not hist.empty:
                    price = float(hist["Close"].iloc[-1])
                    info = ticker.info
                    return price, info
                return None, None

            price, info = await asyncio.wait_for(
                asyncio.to_thread(_fetch),
                timeout=10.0
            )

            if price is not None and info is not None:
                prev_close = info.get("previousClose", price)
                change = price - prev_close
                change_percent = (change / prev_close * 100) if prev_close else 0

                return {
                    "code": yf_code,
                    "name": index_name,
                    "price": round(price, 2),
                    "change_percent": round(change_percent, 2),
                    "change": round(change, 2),
                    "source": "yfinance",
                }
        except asyncio.TimeoutError:
            logger.debug(f"yfinance {yf_code} 超时")
        except Exception as e:
            logger.debug(f"yfinance error ({yf_code}): {e}")
        return None

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._close_session()


# ==================== ETF 价格 API ====================

class ETFPriceAPI:
    """
    ETF 实时价格 API

    数据源优先级：
    1. 东方财富 Push API（最准确，支持最全）
    2. 新浪财经 API（备用）
    3. 腾讯财经 API（备用）
    4. AkShare 基金 ETF 实时行情（增强备用）

    缓存策略：
    - ETF 价格数据缓存 30 秒（避免频繁请求）
    - QDII ETF 缓存 60 秒（海外交易时段更新慢）
    - 非交易时段缓存 5 分钟
    """

    def __init__(self):
        self.session = None
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ]
        self.current_user_agent = random.choice(self.user_agents)
        self._rate_limiter_group = RateLimiterGroup(max_concurrent=3, min_interval=0.3)

        # 缓存相关
        self._cache = TtlCache(default_ttl=30, maxsize=512)

        # QDII ETF 列表（这些 ETF 投资海外市场，交易时间不同）
        self.qdii_etfs = {
            # 美股 ETF
            "513100", "513500", "513030", "513010", "513050", "513300",  # 纳指/标普/德国
            # 港股 ETF
            "513180", "513330", "513370", "513130", "513060", "513010",  # 恒生/港股通
            # 黄金/商品 ETF
            "518880", "159934", "159937", "159985",  # 黄金 ETF
            # 日本 ETF
            "513880", "513000",  # 日经 ETF
        }

        # 跨市场 ETF 列表（需要特殊处理）
        self.cross_market_etfs = {
            # 债券 ETF
            "511010", "511260", "511030",
            # 货币 ETF
            "511880", "511990",
        }

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            connector = aiohttp.TCPConnector(limit=10, limit_per_host=5, ttl_dns_cache=300, enable_cleanup_closed=True)
            # 移除 cookie_jar 以避免反爬机制触发
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def _close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    def _get_random_headers(self):
        return {
            "User-Agent": self.current_user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": "https://quote.eastmoney.com/",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

    @staticmethod
    def _get_cache_key(code: str) -> str:
        """生成缓存键"""
        return f"etf_{code}"

    def _is_trading_time(self) -> bool:
        """判断是否在 A 股交易时间内"""
        now = datetime.now()
        # 周末不交易
        if now.weekday() >= 5:
            return False
        # 交易时段：9:30-11:30, 13:00-15:00
        current_time = now.time()
        morning_start = datetime.strptime("09:30", "%H:%M").time()
        morning_end = datetime.strptime("11:30", "%H:%M").time()
        afternoon_start = datetime.strptime("13:00", "%H:%M").time()
        afternoon_end = datetime.strptime("15:00", "%H:%M").time()
        return (morning_start <= current_time <= morning_end or
                afternoon_start <= current_time <= afternoon_end)

    def _is_qdii_etf(self, code: str) -> bool:
        """判断是否为 QDII ETF"""
        return code in self.qdii_etfs

    async def _request_with_retry(self, method: str, url: str, max_retries: int = 3, **kwargs):
        """带重试机制的请求（增强反爬版）"""
        # 对于东方财富 API，如果失败，尝试交替使用不同的 secid 格式
        for attempt in range(max_retries):
            try:
                session = await self._ensure_session()
                headers = kwargs.pop("headers", {})
                headers.update(self._get_random_headers())
                timeout = kwargs.pop("timeout", aiohttp.ClientTimeout(total=10.0))

                async with self._rate_limiter_group.limit(url):
                    async with session.request(method, url, headers=headers, timeout=timeout, **kwargs) as response:
                        if response.status == 200:
                            return await response.text()
                        elif response.status == 429:
                            # 请求过于频繁，延长等待时间
                            wait_time = 2.0 * (attempt + 1) + random.uniform(0.5, 1.0)
                            await asyncio.sleep(wait_time)
                        elif response.status == 403:
                            # 切换 User-Agent
                            self.current_user_agent = random.choice(self.user_agents)
                            wait_time = 1.0 * (attempt + 1)
                            await asyncio.sleep(wait_time)
                        else:
                            await asyncio.sleep(0.5 * (attempt + 1))
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.debug(f"Request failed (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
                await asyncio.sleep(0.5 * (attempt + 1))
            except Exception as e:
                logger.debug(f"Request failed (attempt {attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(0.5 * (attempt + 1))
        return None

    async def get_etf_realtime_data(self, code: str) -> Optional[Dict[str, Any]]:
        """
        获取 ETF 实时数据

        数据源优先级：
        1. 腾讯财经 API（最稳定，最快）
        2. 新浪财经 API（备用）
        3. 东方财富 Push API（备用）
        4. AkShare 基金 ETF 实时行情（增强备用）

        缓存策略：
        - ETF 价格数据缓存 30 秒（避免频繁请求）
        - QDII ETF 缓存 60 秒（海外交易时段更新慢）
        - 非交易时段缓存 5 分钟

        Args:
            code: ETF 代码

        Returns:
            Optional[Dict]: ETF 数据
        """
        cache_key = self._get_cache_key(code)

        # 根据 ETF 类型和交易时间设置不同的缓存 TTL
        if self._is_qdii_etf(code):
            # QDII ETF 缓存时间较长（海外交易时段更新慢）
            ttl = 60 if self._is_trading_time() else 300
        elif self._is_trading_time():
            # 交易时段缓存 30 秒
            ttl = 30
        else:
            # 非交易时段缓存 5 分钟
            ttl = 300

        # 检查缓存
        cached_data = self._cache.get(cache_key)
        if cached_data:
            logger.debug(f"ETF {code} 使用缓存数据（TTL={ttl}s）")
            return cached_data

        # ===== 数据源 1: 腾讯财经 API (最稳定，且自带 IOPV 可算折溢价) =====
        try:
            # 腾讯 API 格式：sh510300, sz159915
            exchange = 'sh' if code.startswith('51') or code.startswith('50') else 'sz'
            url = f"https://qt.gtimg.cn/q={exchange}{code}"
            content = await self._request_with_retry("GET", url, timeout=aiohttp.ClientTimeout(total=5.0))
            if content:
                # 解析腾讯格式：v_sh510300="1~名称~代码~price~previous_close~..."
                # ETF 完整 88 字段格式中，位置 78 为 IOPV 实时估值（如无则跳过折溢价）
                match = re.search(r'"([^"]+)"', content)
                if match:
                    parts = match.group(1).split("~")
                    if len(parts) >= 5:
                        name = parts[1] if len(parts) > 1 else code
                        price = float(parts[3]) if len(parts) > 3 and parts[3] else 0
                        previous_close = float(parts[4]) if len(parts) > 4 and parts[4] else 0
                        change = price - previous_close
                        change_percent = (change / previous_close * 100) if previous_close else 0

                        # IOPV → 折溢价率（溢价为正 = 市价高于净值）
                        iopv = None
                        premium_percent = None
                        if len(parts) > 78 and parts[78]:
                            try:
                                iopv = float(parts[78])
                                if iopv > 0:
                                    premium_percent = round((price - iopv) / iopv * 100, 2)
                            except ValueError:
                                pass

                        result = {
                            "code": code,
                            "name": name,
                            "price": price,
                            "change": change,
                            "change_percent": round(change_percent, 2),
                            "previous_close": previous_close,
                            "iopv": iopv,
                            "premium_percent": premium_percent,
                        }
                        logger.debug(f"ETF {code} 数据获取成功（腾讯）：price={price}, change={change_percent}%")
                        self._cache.set(cache_key, result, ttl)
                        return result
        except Exception as e:
            logger.debug(f"Tencent ETF API error for {code}: {e}")

        # ===== 数据源 2: 东方财富 Push API =====
        try:
            # 修复 secid 构造逻辑：
            # 1xxxxx = 上交所证券 (510xxx, 511xxx, 512xxx, 513xxx, 515xxx, 516xxx, 517xxx, 518xxx, 50xxxx)
            # 0xxxxx = 深交所证券 (15xxxx, 16xxxx, 18xxxx)
            if code.startswith("51") or code.startswith("50"):
                secid = f"1.{code}"
            elif code.startswith("15") or code.startswith("16") or code.startswith("18"):
                secid = f"0.{code}"
            else:
                # 默认尝试上交所格式，失败时会自动尝试深交所
                secid = f"1.{code}"

            url = "https://push2.eastmoney.com/api/qt/stock/get"
            # fltt=2 时 f43 直接返回小数价格；f170=涨跌幅(%)，f169=涨跌额
            params = {"secid": secid, "fields": "f43,f44,f45,f46,f47,f48,f49,f14,f169,f170", "fltt": "2", "invt": "2"}

            content = await self._request_with_retry("GET", url, params=params, timeout=aiohttp.ClientTimeout(total=5.0))
            if content:
                data = json.loads(content)
                if data.get("data"):
                    tick = data["data"]
                    price = float(tick.get("f43", 0))
                    change_percent = float(tick.get("f170", 0))  # f170 = 涨跌幅(%)
                    change = float(tick.get("f169", 0))          # f169 = 涨跌额
                    previous_close = float(tick.get("f46", 0)) if tick.get("f46") else price - change

                    result = {
                        "code": code,
                        "name": tick.get("f14", code),
                        "price": price,
                        "change": change,
                        "change_percent": change_percent,
                        "volume": float(tick.get("f47", 0)),
                        "previous_close": previous_close,
                        "iopv": None,
                        "premium_percent": None,
                    }
                    logger.debug(f"ETF {code} 数据获取成功（东方财富）：price={price}, change={change_percent}%")
                    self._cache.set(cache_key, result, ttl)
                    return result
        except Exception as e:
            logger.debug(f"EastMoney ETF API error for {code}: {e}")

        # ===== 数据源 3: 新浪财经 API =====
        try:
            exchange = 'sh' if code.startswith('51') or code.startswith('50') else 'sz'
            url = f"https://hq.sinajs.cn/list={exchange}{code}"
            content = await self._request_with_retry("GET", url, timeout=aiohttp.ClientTimeout(total=5.0))
            if content:
                match = re.search(r'"([^"]+)"', content)
                if match:
                    parts = match.group(1).split(",")
                    if len(parts) >= 7:
                        name = parts[0]
                        previous_close = float(parts[2]) if parts[2] else 0
                        price = float(parts[3]) if parts[3] else 0
                        change = price - previous_close
                        change_percent = (change / previous_close * 100) if previous_close else 0

                        result = {
                            "code": code,
                            "name": name,
                            "price": price,
                            "change": change,
                            "change_percent": round(change_percent, 2),
                            "previous_close": previous_close,
                            "iopv": None,
                            "premium_percent": None,
                        }
                        logger.debug(f"ETF {code} 数据获取成功（新浪）：price={price}, change={change_percent}%")
                        self._cache.set(cache_key, result, ttl)
                        return result
        except Exception as e:
            logger.debug(f"Sina ETF API error for {code}: {e}")

        # ===== 数据源 4: AkShare 基金 ETF 实时行情 =====
        # 注意：AkShare 可能需要较长时间加载，作为最后的备用方案
        try:
            logger.debug(f"尝试使用 AkShare 获取 ETF {code} 数据...")

            def _fetch_akshare():
                df = ak.fund_etf_spot_em()
                if df is not None and len(df) > 0:
                    etf_row = df[df['代码'] == code]
                    if len(etf_row) > 0:
                        row = etf_row.iloc[0]
                        return {
                            'price': float(row.get('最新价', 0)),
                            'change_percent': float(row.get('涨跌幅', 0)),
                            'previous_close': float(row.get('昨收', 0)) if '昨收' in row else float(row.get('昨收价', 0)),
                            'name': row.get('名称', code),
                            'iopv': row.get('IOPV实时估值'),
                            'premium_percent': float(row.get('基金折价率', 0)) if row.get('基金折价率') not in (None, '-') else None,
                        }
                return None

            # 使用 to_thread 运行同步代码，并设置超时
            result = await asyncio.wait_for(
                asyncio.to_thread(_fetch_akshare),
                timeout=15.0
            )

            if result:
                logger.debug(f"ETF {code} 数据获取成功（AkShare）：price={result['price']}, change={result['change_percent']}%")
                result_data = {
                    "code": code,
                    "name": result['name'],
                    "price": result['price'],
                    "change": result['price'] * result['change_percent'] / 100 if result['change_percent'] else 0,
                    "change_percent": result['change_percent'],
                    "previous_close": result['previous_close'],
                    "iopv": result.get('iopv'),
                    "premium_percent": result.get('premium_percent'),
                }
                self._cache.set(cache_key, result_data, ttl)
                return result_data
        except asyncio.TimeoutError:
            logger.debug(f"AkShare ETF API timeout for {code}")
        except Exception as e:
            logger.debug(f"AkShare ETF API error for {code}: {e}")

        # 所有数据源都失败，缓存失败结果（短时间）
        logger.warning(f"ETF {code} 数据获取失败（所有数据源均不可用）")
        self._cache.set(cache_key, None, 10)  # 失败结果缓存 10 秒
        return None

    async def __aenter__(self):
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self._close_session()


# ==================== 宏观指标API (DXY/VIX/US10Y/TIPS) ====================

class MacroIndicatorAPI:
    """宏观指标API — 中国直连优先 → FRED → yFinance 三级降级

    优先级:
    1. AkShare bond_zh_us_rate  → US10Y（中国直连，最快最稳）
    2. AkShare futures_hq_spot  → DXY + VIX（新浪外盘期货实时行情）
    3. FRED DFII10                → TIPS
    4. yFinance                   → 全局兜底
    5. TIP ETF SEC yield          → TIPS 最终兜底
    """

    _DXY_SYMBOLS = ["USDX", "USDX"]
    _VIX_SYMBOLS = ["VIX", "VIX"]

    def __init__(self):
        self.session = None
        self._cache = TtlCache(default_ttl=300, maxsize=16)  # 5分钟缓存
        self._rate_limiter_group = RateLimiterGroup(max_concurrent=3, min_interval=0.3)

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            import aiohttp
            self.session = aiohttp.ClientSession()

    async def _close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    async def get_macro_indicators(self) -> Dict[str, Optional[float]]:
        """获取宏观指标 (DXY, VIX, US10Y, TIPS, Breakeven)"""
        now = time.time()
        cached = self._cache.get("macro")
        if cached:
            return cached

        macro = {}

        # Source 1: AkShare — 中国直连，最快最稳
        macro.update(await self._fetch_akshare_bond_rate())      # US10Y
        macro.update(await self._fetch_akshare_futures_spot())   # DXY + VIX

        # Source 2: FRED DFII10 (TIPS) + yFinance 补漏
        macro.update(await self._fetch_fred_yfinance(macro))

        # Source 3: TIP ETF SEC yield — TIPS 最终兜底
        if not macro.get("tips"):
            macro.update(await self._fetch_tip_etf())

        # Breakeven = US10Y - TIPS
        if macro.get("us10y") is not None and macro.get("tips") is not None:
            macro["breakeven"] = round(macro["us10y"] - macro["tips"], 4)

        self._cache.set("macro", macro)
        return macro

    async def _fetch_akshare_bond_rate(self) -> Dict:
        """Source 1a: AkShare bond_zh_us_rate — US10Y（中国直连）"""
        def _sync():
            import akshare as ak
            df = ak.bond_zh_us_rate()
            if df is not None and not df.empty:
                last = df.iloc[-1]
                for col in df.columns:
                    if '10' in str(col) and '美' in str(col):
                        try:
                            return {"us10y": round(float(last[col]), 4)}
                        except (ValueError, TypeError):
                            pass
            return {}
        try:
            return await asyncio.to_thread(_sync)
        except Exception as e:
            logger.debug(f"AkShare bond_zh_us_rate failed: {e}")
            return {}

    async def _fetch_akshare_futures_spot(self) -> Dict:
        """Source 1b: AkShare futures_hq_spot — DXY + VIX（新浪外盘期货实时行情）"""
        def _sync():
            import akshare as ak
            df = ak.futures_hq_spot()
            if df is None or df.empty:
                return {}

            result = {}
            dxy_row = df[df["symbol"].str.upper().isin(self._DXY_SYMBOLS)]
            if not dxy_row.empty:
                try:
                    result["dxy"] = round(float(dxy_row.iloc[0]["current_price"]), 2)
                except (ValueError, TypeError, KeyError):
                    pass

            vix_row = df[df["symbol"].str.upper().isin(self._VIX_SYMBOLS)]
            if not vix_row.empty:
                try:
                    result["vix"] = round(float(vix_row.iloc[0]["current_price"]), 2)
                except (ValueError, TypeError, KeyError):
                    pass
            return result
        try:
            return await asyncio.to_thread(_sync)
        except Exception as e:
            logger.debug(f"AkShare futures_hq_spot failed: {e}")
            return {}

    async def _fetch_fred_yfinance(self, existing: Dict) -> Dict:
        """Source 2: FRED DFII10 (TIPS) + yFinance 补漏 DXY/VIX/US10Y"""
        result = {}
        need_dxy = not existing.get("dxy")
        need_vix = not existing.get("vix")
        need_us10y = not existing.get("us10y")

        if need_dxy or need_vix or need_us10y:
            def _sync_yf():
                import yfinance as yf
                r = {}
                for sym, key in [("DX-Y.NYB", "dxy"), ("^VIX", "vix"), ("^TNX", "us10y")]:
                    if (key == "dxy" and need_dxy) or (key == "vix" and need_vix) or (key == "us10y" and need_us10y):
                        try:
                            t = yf.Ticker(sym)
                            h = t.history(period="1d")
                            if not h.empty:
                                r[key] = round(float(h['Close'].iloc[-1]), 4)
                        except Exception:
                            pass
                return r
            try:
                result.update(await asyncio.to_thread(_sync_yf))
            except Exception:
                pass

        # FRED DFII10 for TIPS
        if not existing.get("tips"):
            try:
                await self._ensure_session()
                url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFII10"
                async with self._rate_limiter_group.limit(url):
                    async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            lines = [l for l in text.strip().split('\n') if l and not l.startswith('date')]
                            if lines:
                                last = lines[-1].split(',')
                                if len(last) >= 2 and last[1].strip():
                                    result["tips"] = round(float(last[1].strip()), 4)
            except Exception:
                pass
        return result

    async def _fetch_tip_etf(self) -> Dict:
        """Source 3: TIP ETF SEC yield（TIPS 最终兜底，偏高~0.6%）"""
        def _sync():
            import yfinance as yf
            t = yf.Ticker("TIP")
            return t.info
        try:
            info = await asyncio.to_thread(_sync)
            sy = info.get("yield") or info.get("trailingAnnualDividendYield")
            if sy:
                return {"tips": round(float(sy) * 100, 4)}
        except Exception:
            pass
        return {}


# ==================== 统一市场数据服务 ====================

class MarketDataService:
    """
    统一市场数据服务

    整合所有 API 类，提供统一的数据访问接口
    """

    def __init__(self):
        self.stock_price_api = StockPriceAPI()
        self.fund_price_api = FundPriceAPI()
        self.fund_holdings_api = FundHoldingsAPI()
        self.index_price_api = IndexPriceAPI()
        self.global_index_api = GlobalIndexAPI()
        self.etf_price_api = ETFPriceAPI()
        self.bond_index_api = BondIndexAPI()
        self.macro_indicator_api = MacroIndicatorAPI()

    async def get_stock_price(self, code: str) -> Optional[MarketData]:
        """获取股票价格"""
        return await self.stock_price_api.get_stock_price(code)

    async def get_fund_data(self, code: str) -> Optional[FundData]:
        """获取基金完整数据"""
        return await self.fund_price_api.get_fund_data(code)

    async def get_fund_holdings(self, fund_code: str) -> List[Holding]:
        """获取基金持仓"""
        return await self.fund_holdings_api.get_fund_holdings(fund_code)

    async def get_index_price(self, code: str) -> Optional[MarketData]:
        """获取指数价格"""
        return await self.index_price_api.get_index_price(code)

    async def get_index_realtime_data(self, index_code: str) -> Optional[Dict[str, Any]]:
        """获取指数实时数据"""
        # 检查是否为债券指数代码
        if index_code.startswith("CBA") or index_code.startswith("H110") or index_code == "bond_index":
            bond_data = await self.bond_index_api.get_bond_index_data(index_code)
            if bond_data:
                return bond_data
        return await self.index_price_api.get_index_realtime_data(index_code)

    async def get_global_index_realtime_data(self, index_code: str) -> Optional[Dict[str, Any]]:
        """获取海外指数实时数据"""
        return await self.global_index_api.get_global_index_realtime_data(index_code)

    async def get_etf_realtime_data(self, code: str) -> Optional[Dict[str, Any]]:
        """获取 ETF 实时数据"""
        return await self.etf_price_api.get_etf_realtime_data(code)

    async def get_macro_indicators(self) -> Dict[str, Optional[float]]:
        """获取宏观指标 (DXY, VIX, US10Y, TIPS, Breakeven)"""
        return await self.macro_indicator_api.get_macro_indicators()

    async def close(self):
        """关闭所有会话"""
        await self.stock_price_api._close_session()
        await self.fund_price_api._close_session()
        await self.fund_holdings_api._close_session()
        await self.index_price_api._close_session()
        await self.global_index_api._close_session()
        await self.etf_price_api._close_session()
        await self.bond_index_api._close_session()
        await self.macro_indicator_api._close_session()

    def clear_cache(self):
        """清除所有 API 类的内存缓存"""
        for api in [self.stock_price_api, self.fund_price_api, self.index_price_api,
                    self.bond_index_api, self.global_index_api, self.etf_price_api,
                    self.macro_indicator_api]:
            if hasattr(api, '_cache'):
                api._cache.clear()


# ==================== 单例实例 ====================

market_data_service = MarketDataService()
