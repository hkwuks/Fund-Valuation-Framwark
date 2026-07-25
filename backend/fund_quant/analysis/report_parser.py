"""FOF 定期报告解析器 — Level 5 穿透分析

从基金半年报/年报中提取底层基金持仓明细。

数据源:
  - 公告列表: ak.fund_announcement_report_em(fund_code)
  - 报告内容: fundf10.eastmoney.com 公告详情页

流程:
  1. 获取 FOF 基金的定期报告列表
  2. 找到最近的半年报或年报
  3. 解析报告 HTML 提取「基金投资明细」表格
  4. 返回 [{fund_code, fund_name, ratio, market_value}]
"""
from typing import Optional, list
from dataclasses import dataclass
import re
from loguru import logger


@dataclass
class FoFHoldingItem:
    """FOF 底层基金持仓明细"""
    fund_code: str
    fund_name: str
    ratio: float         # 占净值比例 (%)
    market_value: float  # 持仓市值 (万元)


def _find_latest_report(reports_df, prefer_mid_year: bool = True) -> Optional[str]:
    """从公告列表中找到最近可用的中报/年报报告ID

    Args:
        reports_df: fund_announcement_report_em() 返回的 DataFrame
        prefer_mid_year: 优先找中期报告（含完整的基金投资明细）

    Returns:
        报告ID (如 "AN202408301639593590") 或 None
    """
    import pandas as pd
    if reports_df.empty:
        return None

    # 先找中期报告（含完整基金持仓）
    if prefer_mid_year:
        mid = reports_df[
            reports_df['公告标题'].str.contains('中期报告', na=False) &
            ~reports_df['公告标题'].str.contains('摘要', na=False)
        ]
        if not mid.empty:
            mid = mid.sort_values('公告日期', ascending=False)
            return mid.iloc[0]['报告ID']

    # 再找年度报告
    annual = reports_df[
        reports_df['公告标题'].str.contains('年度报告', na=False) &
        ~reports_df['公告标题'].str.contains('摘要', na=False)
    ]
    if not annual.empty:
        annual = annual.sort_values('公告日期', ascending=False)
        return annual.iloc[0]['报告ID']

    return None


def _fetch_report_detail(report_id: str) -> str:
    """从东方财富获取公告详情HTML

    Args:
        report_id: 报告ID

    Returns:
        HTML 文本，或空字符串
    """
    import requests

    # 东方财富公告详情页
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://fundf10.eastmoney.com/",
    }

    # 尝试多个 URL 模式
    urls = [
        f"https://np-anotice-stock.eastmoney.com/api/security/ann/detail?announce_id={report_id}",
    ]

    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200 and len(r.text) > 500:
                return r.text
        except Exception:
            continue

    return ""


def _parse_fund_holdings_from_html(html: str) -> list[FoFHoldingItem]:
    """从报告 HTML 中解析基金投资明细

    搜索「基金投资明细」或「基金投资」表头下的表格数据

    Args:
        html: 报告 HTML 文本

    Returns:
        FoFHoldingItem 列表
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, 'html.parser')

    # 尝试定位「基金投资明细」表格
    # 特征：表头含"基金代码"、"基金名称"、"占净值比例"等列
    results = []

    # 方法1：搜索所有表格，找含基金代码列的
    for table in soup.find_all('table'):
        headers = [th.get_text(strip=True) for th in table.find_all('th')]
        header_text = ' '.join(headers)

        if '基金代码' in header_text or '基金名称' in header_text:
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) < 3:
                    continue
                texts = [c.get_text(strip=True) for c in cells]

                fund_code = texts[0] if len(texts) > 0 else ""
                fund_name = texts[1] if len(texts) > 1 else ""

                # 尝试提取比例 — 可能在第 2 或第 3 列
                ratio_str = ""
                for t in texts[2:]:
                    if '%' in t:
                        ratio_str = t
                        break

                if not fund_code or not fund_name:
                    continue

                ratio = 0.0
                if ratio_str:
                    try:
                        ratio = float(ratio_str.replace('%', '').strip())
                    except ValueError:
                        continue

                if ratio <= 0:
                    continue

                results.append(FoFHoldingItem(
                    fund_code=fund_code,
                    fund_name=fund_name,
                    ratio=ratio,
                    market_value=0.0,  # 市值解析较复杂，暂不提取
                ))

    return results


def parse_fof_holdings(fund_code: str) -> Optional[list[FoFHoldingItem]]:
    """解析 FOF 基金最新半年报/年报中的底层基金持仓

    Args:
        fund_code: FOF 基金代码

    Returns:
        FoFHoldingItem 列表，或 None（解析失败）
    """
    try:
        import akshare as ak
        import pandas as pd

        # 1. 获取报告列表
        df = ak.fund_announcement_report_em(fund_code)
        if df.empty:
            logger.warning(f"FOF {fund_code}: 无公告记录")
            return None

        # 2. 找最近的中报
        report_id = _find_latest_report(df, prefer_mid_year=True)
        if not report_id:
            logger.warning(f"FOF {fund_code}: 未找到适用报告")
            return None

        # 3. 获取报告内容
        html = _fetch_report_detail(report_id)
        if not html:
            logger.warning(f"FOF {fund_code}: 报告 {report_id} 内容获取失败")
            return None

        # 4. 解析持仓
        holdings = _parse_fund_holdings_from_html(html)
        if not holdings:
            logger.warning(f"FOF {fund_code}: 未在报告中解析到基金持仓")
            return None

        logger.info(f"FOF {fund_code}: 成功解析 {len(holdings)} 条底层基金持仓 (报告ID={report_id})")
        return holdings

    except Exception as e:
        logger.warning(f"FOF {fund_code} 报告解析异常: {e}")
        return None


def enrich_fof_penetration_with_report(
    fund_code: str,
    current_result: "PenetrationResult",
) -> "PenetrationResult":
    """用定期报告数据增强 FOF 穿透分析结果

    如果成功解析报告，直接用报告中的权益/固收比例覆盖当前结果。

    Args:
        fund_code: FOF 基金代码
        current_result: 当前穿透分析结果

    Returns:
        更新后的 PenetrationResult
    """
    holdings = parse_fof_holdings(fund_code)
    if not holdings:
        return current_result

    # 统计底层基金类型分布
    from backend.fund_quant.data.storage import get_fund_meta
    from backend.fund_quant.data.classifier import classify_fund_for_quant

    total_ratio = sum(h.ratio for h in holdings)
    equity_ratio = 0.0
    bond_ratio = 0.0

    for h in holdings:
        meta = get_fund_meta(h.fund_code)
        fund_type_raw = meta.get("基金类型", "") if meta else ""
        fund_name = h.fund_name
        ft = classify_fund_for_quant(fund_type_raw, fund_name)

        weight = h.ratio / total_ratio if total_ratio > 0 else 0
        if ft.value in ("equity", "index", "balanced"):
            # balanced 简化处理：按 0.5 权益折算
            eq_contrib = weight * (0.5 if ft.value == "balanced" else 1.0)
            equity_ratio += eq_contrib
            bond_ratio += weight - eq_contrib
        elif ft.value == "bond":
            bond_ratio += weight
        elif ft.value == "money":
            pass  # 货币基金不算权益也不算固收
        elif ft.value == "commodity":
            equity_ratio += weight * 0.5  # 商品作为另类资产，半折算
            bond_ratio += weight * 0.5
        else:
            bond_ratio += weight  # 未知类型归固收

    total = equity_ratio + bond_ratio
    if total > 0:
        equity_ratio /= total
        bond_ratio /= total

    # 更新结果（保留原有信息）
    current_result.equity_ratio = round(equity_ratio, 4)
    current_result.bond_ratio = round(bond_ratio, 4)
    current_result.method = "report"
    current_result.confidence = 0.95
    if current_result.details is None:
        current_result.details = {}
    current_result.details["holdings_count"] = len(holdings)
    current_result.details["report_source"] = "S5_report"

    return current_result
