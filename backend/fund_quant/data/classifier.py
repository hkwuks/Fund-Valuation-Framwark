"""基金量化分类判定函数"""

from ..core.enums import FundType


def classify_fund_for_quant(fund_type_raw: str, fund_name: str = "") -> FundType:
    """根据 akshare 基金类型字符串和基金名称判定量化分类

    Args:
        fund_type_raw: akshare fund_info_em 返回的"基金类型"字段
        fund_name: 基金简称（用于二次确认，尤其是 QDII 子类）

    Returns:
        FundType 枚举值

    优先级规则:
        1. QDII（顶级标签）
        2. FOF
        3. 商品/黄金
        4. keyword 匹配（偏股→equity, 指数→index, 混合→balanced, 债→bond, 货币→money）
        5. 默认 equity
    """
    if not fund_type_raw:
        return FundType.EQUITY

    # 1. QDII 优先
    if "QDII" in fund_type_raw or "qdii" in fund_name.lower():
        return FundType.QDII

    # 2. FOF
    if "FOF" in fund_type_raw or "基金中基金" in fund_type_raw:
        return FundType.FOF

    # 3. 商品/黄金
    if any(kw in fund_type_raw for kw in ["商品", "黄金"]):
        return FundType.COMMODITY

    # 4. keyword 映射
    return _classify_by_keywords(fund_type_raw)


def classify_qdii_subtype(fund_name: str) -> str:
    """QDII 基金底层资产类型判定

    Returns:
        "index" — QDII 指数基金（跟踪特定海外指数）
        "bond"  — QDII 债券基金
        "equity" — QDII 主动管理基金（默认）
    """
    name = fund_name or ""
    if "债券" in name:
        return "bond"
    index_kw = ["指数", "ETF", "纳斯达克", "标普", "恒生", "港股通",
                "美股指数", "日经", "富时", "德国DAX"]
    for kw in index_kw:
        if kw in name:
            return "index"
    return "equity"


def _classify_by_keywords(raw: str) -> FundType:
    """AKSHARE 类型原始字符串 → FundType 映射"""
    mapping = [
        # equity
        ("股票", FundType.EQUITY),
        ("偏股混合", FundType.EQUITY),
        ("普通股票型", FundType.EQUITY),
        # index
        ("被动指数", FundType.INDEX),
        ("增强指数", FundType.INDEX),
        ("ETF联接", FundType.INDEX),
        # balanced — 放在 bond 之前，因为"债券型-混合二级"含"债券"也含"二级"
        ("混合二级", FundType.BALANCED),
        ("平衡混合", FundType.BALANCED),
        ("灵活配置", FundType.BALANCED),
        # bond
        ("纯债", FundType.BOND),
        ("长债", FundType.BOND),
        ("中短债", FundType.BOND),
        ("短期纯债", FundType.BOND),
        ("混合一级", FundType.BOND),
        # money
        ("货币", FundType.MONEY),
    ]
    for keyword, ftype in mapping:
        if keyword in raw:
            return ftype

    # ETF 和联接放在 index 段，因为它们出现在原始类型中
    if "ETF" in raw:
        return FundType.INDEX
    if "联接" in raw:
        return FundType.INDEX

    return FundType.EQUITY  # 默认
