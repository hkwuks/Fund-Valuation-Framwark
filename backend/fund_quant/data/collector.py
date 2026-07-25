"""FundQuant 数据采集器 - AkShare/东方财富接口封装（含重试机制）"""

import asyncio
import time
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Callable, Any
from loguru import logger

from ..core.errors import DataCollectionError
from ..core.models import NavPoint, FundHolding, HoldingItem, FundMeta
from ..core.config import fund_quant_settings
from ..data.classifier import classify_fund_for_quant


class FundDataCollector:
    """基金数据采集器"""

    MAX_RETRIES = 3
    RETRY_DELAYS = [5, 15, 30]  # seconds

    def __init__(self):
        self._session = None
        self._rate_limit = fund_quant_settings.COLLECTION_RATE_LIMIT
        self._last_request_time = 0.0

    async def _rate_limit_wait(self):
        """速率限制"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit - elapsed)
        self._last_request_time = time.time()

    async def _with_retry(self, fn: Callable, *args, **kwargs) -> Any:
        """带重试机制的采集调用（在线程池运行同步采集函数）"""
        await self._rate_limit_wait()
        last_exc = None
        for attempt in range(self.MAX_RETRIES):
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            except Exception as e:
                last_exc = e
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_DELAYS[min(attempt, len(self.RETRY_DELAYS) - 1)]
                    logger.warning(f"采集重试 {attempt + 1}/{self.MAX_RETRIES}: {fn.__name__} 失败 ({e}), {delay}s后重试")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"采集失败 (已重试{self.MAX_RETRIES}次): {fn.__name__}: {e}")
                    raise DataCollectionError(str(last_exc), str(args[0] if args else ""))

    # ── 历史净值采集 ──

    async def fetch_nav_history(self, fund_code: str,
                                 start_date: Optional[str] = None,
                                 end_date: Optional[str] = None) -> List[NavPoint]:
        """采集基金历史净值"""
        return await self._with_retry(self._fetch_nav_history_impl, fund_code, start_date, end_date)

    def _fetch_nav_history_impl(self, fund_code: str,
                                       start_date: Optional[str] = None,
                                       end_date: Optional[str] = None) -> List[NavPoint]:
        import akshare as ak
        import os as _os
        # 绕过系统代理 — eastmoney API 需要直连
        saved = {k: _os.environ.pop(k, None) for k in ['HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy']}
        try:
            from requests import Session as _Session, adapters as _adapters
            fund_nav = ak.fund_open_fund_info_em(
                symbol=fund_code,
                indicator="单位净值走势",
            )
        except Exception:
            fund_nav = ak.fund_etf_hist_em(
                symbol=fund_code,
                start_date=start_date or "20000101",
                end_date=end_date or date.today().strftime("%Y%m%d"),
                adjust="",
            )
        finally:
            for k, v in saved.items():
                if v is not None: _os.environ[k] = v

        points = []
        # fund_open_fund_info_em → ['净值日期','单位净值','日增长率']
        # fund_etf_hist_em → ['日期','单位净值','累计净值',...]
        date_col = "净值日期" if "净值日期" in fund_nav.columns else "日期"
        for _, row in fund_nav.iterrows():
            try:
                d = row[date_col]
                if isinstance(d, str):
                    d = datetime.strptime(d, "%Y-%m-%d").date()
            except (ValueError, KeyError):
                continue
            points.append(NavPoint(
                fund_code=fund_code,
                date=d,
                nav=float(row.get("单位净值", 0)),
                adjusted_nav=float(row.get("累计净值", 0)) if "累计净值" in row else None,
                source="eastmoney",
            ))
        return points

    async def fetch_valuation_history(self, fund_code: str,
                                       start_date: Optional[str] = None,
                                       end_date: Optional[str] = None) -> List[dict]:
        """采集估值历史数据"""
        await self._rate_limit_wait()
        try:
            import akshare as ak
            df = await asyncio.to_thread(
                ak.fund_etf_hist_em,
                symbol=fund_code,
                start_date=start_date or "20000101",
                end_date=end_date or date.today().strftime("%Y%m%d"),
                adjust="qfq",
            )
            results = []
            for _, row in df.iterrows():
                try:
                    d = row.get("净值日期") or row.get("日期", "")
                    if isinstance(d, str):
                        d = datetime.strptime(d, "%Y-%m-%d").date()
                except (ValueError, KeyError):
                    continue
                results.append({
                    "fund_code": fund_code,
                    "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                    "estimated_nav": float(row.get("估算值", 0)) if "估算值" in row else None,
                    "actual_nav": float(row.get("单位净值", 0)),
                    "deviation_pct": float(row.get("偏差", 0)) if "偏差" in row else None,
                })
            return results
        except ImportError:
            raise DataCollectionError("akshare not installed", fund_code)
        except Exception as e:
            raise DataCollectionError(str(e), fund_code)

    # ── 持仓数据采集 ──

    async def fetch_holdings(self, fund_code: str) -> List[FundHolding]:
        """采集基金持仓数据"""
        await self._rate_limit_wait()
        try:
            import akshare as ak
            df = await asyncio.to_thread(ak.fund_portfolio_hold_em, symbol=fund_code, date="")
            if df is None or df.empty:
                return []

            results = []
            for period_str in df["报告期"].unique() if "报告期" in df.columns else []:
                period_df = df[df["报告期"] == period_str]
                try:
                    report_period = datetime.strptime(period_str, "%Y-%m-%d").date()
                except ValueError:
                    continue

                items = []
                for _, row in period_df.iterrows():
                    items.append(HoldingItem(
                        asset_code=str(row.get("股票代码", "")),
                        asset_name=str(row.get("股票名称", "")),
                        weight=float(row.get("占净值比例", 0)) / 100.0,
                        market_value=float(row.get("持仓市值", 0)) if "持仓市值" in row else None,
                    ))

                publish_date = report_period + timedelta(days=20)
                results.append(FundHolding(
                    fund_code=fund_code,
                    report_period=report_period,
                    publish_date=publish_date,
                    holdings=items,
                ))
            return results
        except ImportError:
            raise DataCollectionError("akshare not installed", fund_code)
        except Exception as e:
            raise DataCollectionError(str(e), fund_code)

    # ── 基金评级采集 ──

    async def fetch_rating(self, fund_code: str) -> Optional[int]:
        """采集基金晨星评级 (1-5)

        从东方财富获取基金评级数据
        Returns:
            int 1-5, 或 None 表示无数据
        """
        return await self._with_retry(self._fetch_rating_impl, fund_code)

    def _fetch_rating_impl(self, fund_code: str) -> Optional[int]:
        try:
            import akshare as ak
            # 使用 ak.fund_info_em 获取包含评级的元数据
            info = ak.fund_info_em(symbol=fund_code)
            if info is None or info.empty:
                return None
            info_dict = info.set_index("item")["value"].to_dict() if "item" in info.columns else {}
            # 尝试多个可能的评级字段
            for key in ["晨星评级", "评级", "最新评级"]:
                rating_str = info_dict.get(key)
                if rating_str:
                    try:
                        rating = int(float(rating_str))
                        if 1 <= rating <= 5:
                            return rating
                    except (ValueError, TypeError):
                        continue
            return None
        except Exception:
            logger.debug(f"评级采集失败: {fund_code}")
            return None

    # ── 分红数据采集 ──

    async def fetch_dividend_history(self, fund_code: str) -> List[dict]:
        """采集基金分红历史

        Returns:
            [{date, dividend_per_share, ...}]
        """
        return await self._with_retry(self._fetch_dividend_impl, fund_code)

    def _fetch_dividend_impl(self, fund_code: str) -> List[dict]:
        try:
            import akshare as ak
            # 基金分红数据
            df = ak.fund_dividend_em(symbol=fund_code)
            if df is None or df.empty:
                return []

            results = []
            date_col = None
            for col in ["除权日", "红利发放日", "公告日", "登记日"]:
                if col in df.columns:
                    date_col = col
                    break

            if not date_col:
                return []

            for _, row in df.iterrows():
                d_str = str(row.get(date_col, ""))
                if not d_str:
                    continue
                try:
                    div_date = datetime.strptime(d_str[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue

                dividend_per_share = 0.0
                for col in ["每份分红", "分红金额", "派息"]:
                    val = row.get(col)
                    if val:
                        try:
                            dividend_per_share = float(val)
                            break
                        except (ValueError, TypeError):
                            continue

                results.append({
                    "fund_code": fund_code,
                    "date": div_date.isoformat(),
                    "dividend_per_share": dividend_per_share,
                    "announce_date": str(row.get("公告日", d_str))[:10] if "公告日" in row else d_str[:10],
                })
            return results
        except Exception as e:
            logger.debug(f"分红采集失败: {fund_code}: {e}")
            return []

    # ── 基金元数据采集 ──

    async def fetch_fund_meta(self, fund_code: str) -> Optional[FundMeta]:
        """采集基金元数据"""
        await self._rate_limit_wait()
        try:
            import akshare as ak
            info = await asyncio.to_thread(ak.fund_info_em, symbol=fund_code)
            if info is None or info.empty:
                return None

            meta = FundMeta(fund_code=fund_code)
            info_dict = info.set_index("item")["value"].to_dict() if "item" in info.columns else {}

            meta.fund_name = info_dict.get("基金简称", "")
            meta.fund_type = classify_fund_for_quant(info_dict.get("基金类型", ""), info_dict.get("基金简称", "")).value

            fee_str = info_dict.get("管理费率", "0")
            try:
                meta.management_fee = float(fee_str.replace("%", "")) / 100.0 if "%" in fee_str else float(fee_str)
            except ValueError:
                pass

            fee_str = info_dict.get("托管费率", "0")
            try:
                meta.custody_fee = float(fee_str.replace("%", "")) / 100.0 if "%" in fee_str else float(fee_str)
            except ValueError:
                pass

            meta.established_date = None
            estab_str = info_dict.get("成立日期", "")
            if estab_str:
                try:
                    meta.established_date = datetime.strptime(estab_str, "%Y-%m-%d").date()
                except ValueError:
                    pass

            # 采集评级
            meta.rating = await self.fetch_rating(fund_code)

            return meta
        except ImportError:
            raise DataCollectionError("akshare not installed", fund_code)
        except Exception as e:
            raise DataCollectionError(str(e), fund_code)

    # ── 汇率数据采集 ──

    async def fetch_fx_rates(self) -> Dict[str, float]:
        await self._rate_limit_wait()
        try:
            import akshare as ak
            df = await asyncio.to_thread(ak.currency_boc_sina, symbol="美元人民币")
            rates = {"USD/CNY": float(df["买入价"].iloc[0]) if not df.empty else 7.0}
            return rates
        except Exception:
            logger.warning("汇率数据获取失败，返回默认值")
            return {"USD/CNY": 7.0}

    # ── 国债收益率采集 ──

    async def fetch_yield_10y(self) -> Optional[float]:
        await self._rate_limit_wait()
        try:
            import akshare as ak
            df = await asyncio.to_thread(ak.bond_china_yield, start_date="20200101")
            if df is not None and not df.empty and "10年" in df.columns:
                return float(df["10年"].iloc[-1]) / 100.0
            return None
        except Exception:
            logger.warning("国债收益率获取失败")
            return None


fund_data_collector = FundDataCollector()
