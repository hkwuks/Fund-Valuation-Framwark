from typing import Dict, List, Optional, Tuple
from pathlib import Path
from datetime import datetime
import re
import numpy as np
import pandas as pd
import yfinance as yf
from backend.models import (
    Holding,
    ValuationResult,
    ValuationType,
    MarketType,
)
from backend.market_data import (
    market_data_service,
    determine_market_type,
    GLOBAL_INDEX_MAPPING,
    INDEX_MAPPING,
)
from backend.ttl_cache import TtlCache
from backend.valuation_sources import (
    valuation_signal_service,
    BOND_INDEX_CODES,
    OVERSEAS_ETF_MAP,
)
from loguru import logger

logger.add(str(Path(__file__).parent.parent / "logs" / "fund_valuation.log"), encoding="utf-8")





def get_valuation_method_name(valuation_type: ValuationType) -> str:
    """根据估值类型返回估值方法名称"""
    mapping = {
        ValuationType.REAL_TIME_PRICE: "实时价格估值",
        ValuationType.INDEX_BASED: "指数估值",
        ValuationType.HOLDINGS_BASED: "持仓估值",
        ValuationType.HYBRID_BOND: "混合估值（债券 + 股票）",
        ValuationType.HYBRID_QDII: "混合估值（持仓 + 指数）",
        ValuationType.BENCHMARK_ONLY: "业绩基准参考",
        ValuationType.NOT_SUPPORTED: "暂不支持",
    }
    return mapping.get(valuation_type, "未知")

# ETF 代码到跟踪指数的映射
ETF_INDEX_MAPPING = {
    # 沪深 300 相关
    "510300": "000300",  # 华泰柏瑞沪深 300ETF
    "159919": "000300",  # 嘉实沪深 300ETF
    "510330": "000300",  # 华夏沪深 300ETF
    # 中证 500 相关
    "510500": "000905",  # 南方中证 500ETF
    "510510": "000905",  # 广发中证 500ETF
    # 创业板相关
    "159915": "399006",  # 易方达创业板 ETF
    "159948": "399006",  # 南方创业板 ETF
    # 上证 50 相关
    "510050": "000016",  # 华夏上证 50ETF
    "510680": "000016",  # 万家上证 50ETF
    # 科创 50 相关
    "588000": "000688",  # 华夏科创 50ETF
    "588080": "000688",  # 易方达科创 50ETF
    # 中证 1000 相关
    "512100": "000852",  # 南方中证 1000ETF
    "159845": "000852",  # 华夏中证 1000ETF
    # 创业板 50
    "159949": "399673",  # 华安创业板 50ETF
    # 证券/金融
    "512880": "399975",  # 国泰中证全指证券公司 ETF
    "510880": "000922",  # 华泰柏瑞红利 ETF
    # 黄金
    "518880": "au",  # 华安黄金 ETF
    "159934": "au",  # 易方达黄金 ETF
    # 港股相关
    "513330": "hsi",  # 华夏恒生互联网 ETF
    "513180": "hsi",  # 华夏恒生 ETF
    "513370": "sp_hkconnect",  # 标普港股通低波红利 ETF
    "513020": "hk_tech",  # 国泰中证港股通科技 ETF
    # 美股相关
    "513100": "nasdaq100",  # 国泰纳斯达克 100ETF
    "513500": "sp500",  # 博时标普 500ETF
    # 德国 DAX
    "513030": "dax",  # 华安德国 DAX ETF
    # 富时现金流
    "159399": "ftse_cashflow",  # 国泰富时现金流 ETF（正确代码）
    # "159513": "ftse_cashflow",  # 注释掉错误的代码
    # 中证白酒
    "519908": "399997",  # 华夏消费 ETF（参考白酒指数）
    # 医疗 ETF
    "159992": "399989",  # 银华中证医疗 ETF
    "512170": "399989",  # 华宝中证医疗 ETF
    # 黄金股
    "159562": "931632",  # 国泰黄金股 ETF
    # 恒生红利
    "513130": "hshk_dividend",  # 恒生港股通高股息 ETF
    # 恒生红利低波
    "513210": "hshk_dividend",  # 易方达恒生红利低波 ETF
    # 标普 A 股大盘红利低波
    "515450": "sp_china_a_dividend",  # 南方标普中国 A 股大盘红利低波 50ETF
}

# QDII ETF 列表（跨境 ETF，容易出现溢价/折价，估值时应优先使用指数数据而非 ETF 交易价格）
QDII_ETF_LIST = {
    "513030",  # 德国 DAX
    "513100",  # 纳斯达克 100
    "513500",  # 标普 500
    "513180",  # 恒生 ETF
    "513210",  # 恒生红利低波
    "513370",  # 标普港股通低波红利
    "513330",  # 恒生互联网
    "513010",  # 恒生科技
    "513050",  # 中概互联
    "513300",  # 纳斯达克科技
}

# ETF 联接基金到目标 ETF 的映射
# 注意：ETF 联接基金通常投资于同一基金管理公司的场内 ETF
ETF_LINKING_MAPPING = {
    # 沪深 300 联接
    "000311": "510300",  # 华夏沪深 300ETF 联接 A
    "000312": "510300",  # 华夏沪深 300ETF 联接 C
    "005611": "510300",  # 易方达沪深 300ETF 联接 A
    "005612": "510300",  # 易方达沪深 300ETF 联接 C
    # 中证 500 联接
    "001415": "510500",  # 南方中证 500ETF 联接 A
    "004347": "510500",  # 南方中证 500ETF 联接 C
    # 创业板 ETF 联接
    "000656": "159915",  # 易方达创业板 ETF 联接 A
    "004742": "159915",  # 易方达创业板 ETF 联接 C
    # 科创 50 联接
    "011612": "588000",  # 华夏科创 50ETF 联接 A
    "011613": "588000",  # 华夏科创 50ETF 联接 C
    # 上证 50 联接
    "001051": "510050",  # 华夏上证 50ETF 联接 A
    "005739": "510050",  # 华夏上证 50ETF 联接 C
    # 恒生 ETF 联接
    "000071": "513180",  # 华夏恒生 ETF 联接 A
    "000072": "513180",  # 华夏恒生 ETF 联接 C
    # 注意：019260/019261 富国恒生红利 ETF 联接实际跟踪"恒生港股通高股息低波动指数"
    # 市场上没有富国恒生红利 ETF，该基金是普通指数基金，不是 ETF 联接基金
    # 已移除映射，让其作为普通指数基金使用跟踪指数进行估值
    # "019260": "513180",  # 移除：富国恒生红利 ETF 联接 A（映射错误，513180 跟踪恒生指数而非恒生红利指数）
    # "019261": "513180",  # 移除：富国恒生红利 ETF 联接 C
    # 纳指 100 联接
    "000834": "513100",  # 广发纳斯达克 100ETF 联接 A
    "006479": "513100",  # 广发纳斯达克 100ETF 联接 C
    # 标普 500 联接
    "003720": "513500",  # 博时标普 500ETF 联接 A
    "003721": "513500",  # 博时标普 500ETF 联接 C
    "019305": "513500",  # 摩根标普 500 指数 (QDII) 人民币 C
    # 注意：005051/005052 是 LOF 基金，不是 ETF 联接基金，直接跟踪指数
    # "005051": "513370",  # 注释掉：摩根标普港股通低波红利指数 A（LOF）
    # "005052": "513370",  # 注释掉：摩根标普港股通低波红利指数 C（LOF）
    # 富时现金流 ETF 联接
    "023920": "159399",  # 国泰富时现金流 ETF 联接 C
    "023919": "159399",  # 国泰富时现金流 ETF 联接 A
    # 德国 DAX 联接
    "000614": "513030",  # 华安德国 (DAX) 联接 A
    # 黄金 ETF 联接
    "000218": "518880",  # 国泰黄金 ETF 联接 A
    "000217": "518880",  # 国泰黄金 ETF 联接 C
    "021674": "159562",  # 国泰黄金股 ETF 联接 C
    # 中证白酒/消费
    "001632": "519908",  # 华夏消费升级混合 A (参考消费指数)
    # 医疗 ETF 联接 - 华宝医疗 ETF 联接对应华宝医疗 ETF(512170)
    "012323": "512170",  # 华宝中证医疗 ETF 联接 C
    # 注意：012322 实际是东财云计算增强 C，不是华宝医疗 ETF 联接
    # "012322": "512170",  # 注释掉错误的映射
    # 159992 是银华中证创新药产业 ETF，不是汇添富中证医疗 ETF
    # 汇添富中证医疗 ETF 联接的正确代码需要查证
    # 中证红利 ETF 联接
    "007751": "510880",  # 华泰柏瑞中证红利 ETF 联接 A
    "007752": "510880",  # 华泰柏瑞中证红利 ETF 联接 C
    # 港股通科技 ETF 联接
    "015740": "513020",  # 国泰中证港股通科技 ETF 联接 C
    "015739": "513020",  # 国泰中证港股通科技 ETF 联接 A
    # 标普 A 股大盘红利低波 ETF 联接
    "008164": "515450",  # 南方标普中国 A 股大盘红利低波 50ETF 联接 C
    "008163": "515450",  # 南方标普中国 A 股大盘红利低波 50ETF 联接 A
    # 恒生红利低波 ETF 联接
    "021457": "513210",  # 易方达恒生红利低波 ETF 联接 A
    "021458": "513210",  # 易方达恒生红利低波 ETF 联接 C
    # 港股通 ETF 联接
    "021031": "159369",  # 汇添富国证港股通创新药 ETF 发起式联接 C
    "021030": "159369",  # 汇添富国证港股通创新药 ETF 发起式联接 A
    "018721": "513860",  # 华夏中证港股通 50ETF 发起式联接 A
    "018722": "513860",  # 华夏中证港股通 50ETF 发起式联接 C
}


class FundValuationService:
    """基金估值服务"""

    def _is_etf_linking(self, fund_code: str, fund_name: str = "") -> bool:
        """判断是否为 ETF 联接基金"""
        if fund_code in ETF_LINKING_MAPPING:
            return True
        if "联接" in (fund_name or ""):
            return True
        return False

    def _get_target_etf(self, fund_code: str, fund_name: str = "") -> Optional[str]:
        """
        获取 ETF 联接基金对应的目标 ETF 代码

        核心原则：ETF 联接基金通常投资于同一基金管理公司的场内 ETF
        例如：
        - 华宝医疗 ETF 联接 (012323) -> 华宝医疗 ETF(512170)，不是 159992
        - 汇添富中证医疗 ETF 联接 (006600) -> 汇添富中证医疗 ETF(159992)

        重要：只有名称中包含"联接"的基金才会进行目标 ETF 匹配，
        避免将 LOF 基金或其他指数基金误判为 ETF 联接基金。
        """
        # 0. 检查基金名称是否包含"联接"，只有 ETF 联接基金才需要此方法
        # 这避免了 LOF 基金或其他指数基金被误判
        if "联接" not in (fund_name or ""):
            return None

        # 1. 首先尝试从映射表中查找（最准确）
        if fund_code in ETF_LINKING_MAPPING:
            return ETF_LINKING_MAPPING[fund_code]

        # 2. 根据基金名称关键字推断目标 ETF（考虑基金公司前缀）
        fund_name_lower = (fund_name or "").lower()
        fund_name_upper = (fund_name or "").upper()

        # 提取基金公司前缀（基金名称的前 2-3 个字）
        # 常见基金公司：华夏、易方达、南方、国泰、华宝、汇添富、博时、广发、富国、华安等
        fund_company_prefixes = [
            "华夏", "易方达", "南方", "国泰", "华宝", "汇添富", "博时",
            "广发", "富国", "华安", "嘉实", "银华", "招商", "鹏华",
            "工银", "建行", "交银", "中欧", "信达", "东方", "兴业"
        ]

        fund_company = None
        for prefix in fund_company_prefixes:
            if fund_name.startswith(prefix):
                fund_company = prefix
                break

        # 沪深 300 联接
        if "沪深 300" in fund_name_lower:
            if fund_company == "华夏":
                return "510300"  # 华夏沪深 300ETF
            if fund_company == "易方达":
                return "510300"  # 易方达沪深 300ETF
            if fund_company == "南方":
                return "510300"  # 南方沪深 300ETF
            return "510300"  # 默认

        # 中证 500 联接
        if "中证 500" in fund_name_lower:
            if fund_company == "南方":
                return "510500"  # 南方中证 500ETF
            return "510500"

        # 创业板 ETF 联接
        if "创业板" in fund_name_lower and "ETF" in fund_name_upper:
            if fund_company == "易方达":
                return "159915"  # 易方达创业板 ETF
            return "159915"

        # 科创 50 联接
        if "科创 50" in fund_name_lower:
            if fund_company == "华夏":
                return "588000"  # 华夏科创 50ETF
            if fund_company == "易方达":
                return "588080"  # 易方达科创 50ETF
            return "588000"

        # 上证 50 联接
        if "上证 50" in fund_name_lower:
            if fund_company == "华夏":
                return "510050"  # 华夏上证 50ETF
            return "510050"

        # 恒生 ETF 联接
        if "恒生" in fund_name_lower:
            # 注意：恒生红利/恒生红利低波类的 ETF 联接基金，跟踪的是"恒生港股通高股息低波动指数"
            # 富国恒生红利 ETF 联接 (019260/019261) 虽然名称有"联接"，但富国没有对应的恒生红利 ETF
            # 让它作为普通指数基金处理，使用跟踪指数 hshk_dividend 进行估值
            if "红利" in fund_name_lower:
                return None  # 不作为 ETF 联接基金，作为普通指数基金处理
            if fund_company == "华夏":
                return "513180"  # 华夏恒生 ETF
            if fund_company == "易方达":
                return "513210"  # 易方达恒生红利低波 ETF
            return "513180"  # 默认恒生 ETF

        # 标普中国 A 股大盘红利低波联接
        if "标普" in fund_name_lower and "红利低波" in fund_name_lower:
            if fund_company == "南方":
                return "515450"  # 南方标普中国 A 股大盘红利低波 50ETF
            return "515450"

        # 纳指 100 联接
        if "纳斯达克" in fund_name_lower or "纳指" in fund_name_lower:
            if fund_company == "广发":
                return "513100"  # 广发纳斯达克 100ETF
            return "513100"

        # 标普 500 联接
        if "标普" in fund_name_lower:
            if fund_company == "博时":
                return "513500"  # 博时标普 500ETF
            return "513500"

        # 黄金 ETF 联接
        if "黄金" in fund_name_lower and "ETF" in fund_name_upper:
            if fund_company == "国泰":
                return "518880"  # 国泰黄金 ETF
            if fund_company == "华安":
                return "518880"  # 华安黄金 ETF
            if fund_company == "易方达":
                return "159934"  # 易方达黄金 ETF
            return "518880"

        # 医疗 ETF 联接 - 根据基金公司判断
        if "医疗" in fund_name_lower:
            if fund_company == "华宝":
                return "512170"  # 华宝中证医疗 ETF
            if fund_company == "汇添富" or "汇添" in fund_name_lower:
                return "159992"  # 汇添富中证医疗 ETF
            if fund_company == "国泰":
                return "512170"  # 国泰中证医疗 ETF
            if fund_company == "鹏华":
                return "512170"  # 鹏华中证医疗 ETF
            return "512170"  # 默认 512170 更常见

        # 医药 ETF 联接
        if "医药" in fund_name_lower:
            if fund_company == "广发":
                return "512010"  # 广发医药卫生 ETF
            return "512010"

        # 红利 ETF 联接
        if "红利" in fund_name_lower:
            if fund_company == "华泰柏瑞":
                return "510880"  # 华泰柏瑞红利 ETF
            return "510880"

        # 中证白酒/消费
        if "白酒" in fund_name_lower or "消费" in fund_name_lower:
            if fund_company == "华夏":
                return "519908"  # 华夏消费升级
            return "519908"

        return None

    def _classify_fund(self, fund_info, fund_code: str) -> Tuple[ValuationType, float, str]:
        """
        根据基金信息判断估值类型和置信度

        分类优先级：
        1. 场内基金（ETF/LOF）→ 实时价格（100% 准确）
        2. ETF 联接基金 → 基于目标 ETF 涨跌幅（90-95% 准确）
        3. 指数基金 → 基于跟踪指数（80-90% 准确）
        4. 主动型股票/混合 → 基于持仓股票（50-70% 准确）
        5. QDII 基金 → 基于对应市场指数（70-80% 准确）
        6. 债券/货币基金 → 不支持估值（波动小，无估值意义）

        Returns:
            Tuple[ValuationType, float, str]: (估值类型，置信度，说明)
        """
        fund_type_lower = fund_info.fund_type.lower() if fund_info.fund_type else ""
        fund_name = fund_info.fund_name or ""

        # 1. 场内基金：ETF/LOF 使用实时价格
        # 但 QDII 基金除外（QDII-LOF 可能有较大溢价/折价，实时价格不准确）
        is_qdii = "qdii" in fund_type_lower or any(kw in fund_name for kw in ["纳斯达克", "标普", "恒生", "港股", "美股", "香港"])

        if fund_info.market_type == MarketType.ON_EXCHANGE and not is_qdii:
            if "ETF" in fund_name.upper() or "ETF" in fund_type_lower:
                return ValuationType.REAL_TIME_PRICE, 1.0, "场内 ETF 实时交易价格"
            if "LOF" in fund_name.upper() or "LOF" in fund_type_lower:
                return ValuationType.REAL_TIME_PRICE, 0.98, "场内 LOF 实时交易价格"
            return ValuationType.REAL_TIME_PRICE, 0.95, "场内基金实时价格"

        # 2. ETF 联接基金（优先级高于普通指数基金）
        if "联接" in fund_name or self._is_etf_linking(fund_code, fund_name):
            return ValuationType.INDEX_BASED, 0.90, "ETF 联接基金，基于目标 ETF 估算"

        # 3. ETF 基金
        if "etf" in fund_name.lower() or "etf" in fund_type_lower:
            return ValuationType.INDEX_BASED, 0.90, "ETF 基金，基于跟踪指数估算"

        # 4. 债券/货币基金 - 需要区分债券指数基金和纯债券基金
        # "固收" = 固定收益类，通常指债券型基金
        # 债券指数基金（如"指数型 - 固收"）应该使用指数法估值
        if any(kw in fund_type_lower for kw in ["债券", "货币", "固收"]):
            # 如果是债券指数基金，使用指数法估值
            if any(kw in fund_type_lower for kw in ["指数"]) or "指数" in fund_name:
                return ValuationType.INDEX_BASED, 0.70, "债券指数基金，基于跟踪指数估算（债券波动较小）"
            # 二级债基（债券型 - 混合二级）可以使用混合估值：债券指数 + 股票持仓
            if "混合二级" in fund_type_lower or "二级债基" in fund_name.lower():
                return ValuationType.HYBRID_BOND, 0.55, "二级债基，基于债券指数×80% + 股票持仓×20% 估算"
            # 一级债基（债券型 - 混合一级）可以使用债券指数 + 可转债混合估值
            # 一级债基不可投资股票，但可投资可转债（通常占比 10-20%）
            if "混合一级" in fund_type_lower or "一级债基" in fund_name.lower():
                return ValuationType.HYBRID_BOND, 0.50, "一级债基，基于债券指数×85% + 可转债指数×15% 估算"
            # 偏债混合基金可以使用混合估值：债券指数 + 股票持仓
            if "偏债" in fund_type_lower:
                return ValuationType.HYBRID_BOND, 0.50, "偏债混合，基于债券指数 + 股票持仓估算"
            # 长债基金可以使用债券指数估值（长债久期长，对利率敏感，有一定波动）
            if "长债" in fund_type_lower:
                return ValuationType.INDEX_BASED, 0.65, "长债基金，基于中债长债指数估算（久期>3 年，对利率敏感）"
            # 纯债券基金/货币基金，波动小，不支持日内估值
            return ValuationType.NOT_SUPPORTED, 0.0, "债券/货币基金波动小，无需日内估值"

        # 5. 指数基金（包括股票型指数、QDII 指数等）
        if any(kw in fund_type_lower for kw in ["指数", "被动"]):
            return ValuationType.INDEX_BASED, 0.85, "指数基金，基于跟踪指数估算"

        # 6. 主动型股票基金 → 使用持仓估值
        if any(kw in fund_type_lower for kw in ["股票型", "偏股"]):
            return ValuationType.HOLDINGS_BASED, 0.60, "主动型股票基金，基于前 10 大持仓加权估算"

        # 6. 主动型混合基金 → 根据偏股/偏债/平衡分类处理
        if any(kw in fund_type_lower for kw in ["混合"]):
            if "偏股" in fund_type_lower:
                return ValuationType.HOLDINGS_BASED, 0.65, "偏股混合基金，基于股票持仓加权估算（股票仓位>60%）"
            if "偏债" in fund_type_lower:
                return ValuationType.HYBRID_BOND, 0.50, "偏债混合基金，基于债券指数 + 股票持仓估算（债券仓位>60%）"
            if "灵活" in fund_type_lower:
                # 灵活配置型基金，仓位变化大，优先使用持仓估值
                return ValuationType.HOLDINGS_BASED, 0.45, "灵活配置混合基金，基于持仓股票估算（仓位可能变化大）"
            # 普通混合基金，默认使用持仓估值
            return ValuationType.HOLDINGS_BASED, 0.50, "混合基金，基于前 10 大持仓加权估算"

        # 7. QDII 基金
        # 需要区分指数型 QDII 和主动管理型 QDII
        is_qdii = "qdii" in fund_type_lower
        # 指数型 QDII 关键字（这些基金跟踪特定指数）
        index_keywords = ["纳斯达克", "标普", "恒生", "港股通", "美股指数", "日经", "富时", "德国 DAX"]
        # 主动管理型 QDII 关键字（这些基金不跟踪指数，主动选股）
        active_keywords = ["优选", "精选", "配置", "成长", "价值", "中小盘", "大盘", "混合"]

        if is_qdii:
            # 检查是否包含指数关键字
            has_index_keyword = any(kw in fund_name for kw in index_keywords)
            # 检查是否包含主动管理关键字
            has_active_keyword = any(kw in fund_name for kw in active_keywords)

            if has_index_keyword and not has_active_keyword:
                # 指数型 QDII，使用指数法估值
                return ValuationType.INDEX_BASED, 0.70, "QDII 指数基金，基于对应市场指数估算（注意时差）"
            else:
                # 主动管理型 QDII，使用混合估值方法：
                # 1. 优先使用持仓估值（如果数据可用）
                # 2. 如果持仓数据不足，使用市场指数作为参考（因为主动管理型基金仍然受市场整体影响）
                return ValuationType.HYBRID_QDII, 0.55, "主动管理型 QDII 基金，基于持仓×市场指数混合估算"

        # 8. 默认：无法明确分类的基金，尝试使用持仓估值
        return ValuationType.HOLDINGS_BASED, 0.40, "主动型基金，基于持仓估算（置信度较低）"


    async def get_etf_realtime_price(self, fund_code: str) -> Optional[Dict]:
        """
        获取场内ETF实时价格（复用 market_data_service）
        """
        data = await market_data_service.get_etf_realtime_data(fund_code)
        if data:
            data["timestamp"] = datetime.now().isoformat()
        return data

    async def get_index_realtime_data(self, index_code: str) -> Optional[Dict]:
        """
        获取指数实时数据（复用 market_data_service）
        """
        data = await market_data_service.get_index_realtime_data(index_code)
        if data:
            data["timestamp"] = datetime.now().isoformat()
        return data

    async def get_global_index_realtime_data(self, index_code: str) -> Optional[Dict]:
        """
        获取海外指数实时数据（复用 market_data_service）
        """
        data = await market_data_service.get_global_index_realtime_data(index_code)
        if data:
            data["timestamp"] = datetime.now().isoformat()
        return data

    async def get_index_data(self, index_code: str) -> Optional[Dict]:
        """
        获取指数数据（自动判断国内/海外指数）

        Args:
            index_code: 指数代码

        Returns:
            Optional[Dict]: 指数数据
        """
        try:
            # 债券指数不支持实时数据
            if (
                index_code.lower() == "bond_index"
                or index_code == "bond_index_not_supported"
            ):
                # 债券指数通常没有实时涨跌幅数据，返回一个特殊标记
                # 让调用方知道这是"数据不支持"而不是"错误"
                logger.info(f"债券指数 {index_code} 不支持实时涨跌幅数据，返回昨日净值参考")
                return {"code": index_code, "name": "中债指数", "change_percent": 0, "note": "债券指数波动小，无实时数据"}

            # 优先使用 INDEX_MAPPING（包含 ETF 替代逻辑）
            # 对于同时在 INDEX_MAPPING 和 GLOBAL_INDEX_MAPPING 中的指数（如 hshk_dividend）
            # 先尝试使用 IndexPriceAPI 获取数据
            if index_code.lower() in INDEX_MAPPING:
                logger.debug(f"尝试使用国内指数 API 获取：{index_code}")
                data = await self.get_index_realtime_data(index_code)
                if data:
                    if "timestamp" not in data:
                        data["timestamp"] = datetime.now().isoformat()
                    logger.debug(f"指数 {index_code} 数据获取成功：涨跌幅={data.get('change_percent')}%")
                    return data

            # INDEX_MAPPING 获取失败，尝试使用 GLOBAL_INDEX_MAPPING
            if index_code.lower() in GLOBAL_INDEX_MAPPING:
                logger.debug(f"获取海外指数数据：{index_code}")
                data = await self.get_global_index_realtime_data(index_code)
            else:
                logger.warning(f"指数 {index_code} 不在映射表中")
                return None

            if data:
                if "timestamp" not in data:
                    data["timestamp"] = datetime.now().isoformat()
                logger.debug(f"指数 {index_code} 数据获取成功：涨跌幅={data.get('change_percent')}%")
            else:
                logger.warning(f"指数 {index_code} 数据获取失败")

            return data
        except Exception as e:
            logger.error(f"获取指数 {index_code} 数据异常：{e}")
            return None

    async def get_tracking_index(
        self,
        fund_code: str,
        fund_name: str = "",
        tracking_index_name: Optional[str] = "",
    ) -> Optional[str]:
        """
        获取基金的跟踪指数代码

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            tracking_index_name: 跟踪指数名称（从基金详情获取）

        Returns:
            Optional[str]: 跟踪指数代码
        """
        if fund_code in ETF_INDEX_MAPPING:
            return ETF_INDEX_MAPPING[fund_code]

        index_keywords = [
            # 港股通红利相关（优先级最高，最具体）
            ("标普港股通低波", "sp_hkconnect"),
            ("标普港股通", "sp_hkconnect"),
            ("港股通红利", "hshk_dividend"),
            ("恒生港股通高股息", "hshk_dividend"),
            ("恒生红利低波", "hshk_dividend"),
            ("恒生红利", "hshk_dividend"),
            # 富时指数
            ("富时现金流", "ftse_cashflow"),
            ("现金流 ETF", "ftse_cashflow"),
            ("富时 A50", "a50"),
            ("富时中国", "a50"),
            ("A50", "a50"),
            # 标普A股红利低波（必须放在"标普"之前，否则会被误匹配为标普500）
            ("标普红利低波", "sp_china_a_dividend"),
            ("标普A股", "sp_china_a_dividend"),
            # 海外指数
            ("纳斯达克 100", "nasdaq100"),
            ("纳斯达克精选", "nasdaq"),
            ("纳斯达克", "nasdaq"),
            ("纳指 100", "nasdaq100"),
            ("纳指", "nasdaq"),
            ("标普 500", "sp500"),
            ("标普", "sp500"),
            ("道琼斯", "dji"),
            ("德国 DAX", "dax"),
            ("德国 30", "dax"),
            ("德国", "dax"),
            ("恒生指数", "hsi"),
            ("恒生", "hsi"),
            ("香港", "hsi"),  # 香港优选、香港精选等基金，默认使用恒生指数
            # 香港互认基金（摩根/惠理系列），近似用恒生指数
            ("亚洲股息", "hsi"),
            ("亚洲", "hsi"),
            ("高息股票", "hsi"),
            ("高息股", "hsi"),
            ("价值基金", "hsi"),
            ("日经 225", "nikkei"),
            ("日经", "nikkei"),
            # 黄金相关
            ("黄金股 ETF", "931632"),
            ("黄金股", "931632"),
            ("黄金 ETF", "au"),
            ("黄金", "au"),
            # 国内宽基指数
            ("沪深 300", "000300"),
            ("中证 500", "000905"),
            ("中证 1000", "000852"),
            ("中证 800", "000906"),
            ("上证 50", "000016"),
            ("创业板 50", "399673"),
            ("创业板指", "399006"),
            ("创业板", "399006"),
            ("科创 50", "000688"),
            ("科创板", "000688"),
            ("国证 2000", "399303"),
            ("中小板指", "399005"),
            ("上证指数", "000001"),
            ("上证", "000001"),
            ("深证成指", "399001"),
            ("深证", "399001"),
            # 行业/主题指数
            ("中证白酒", "399997"),
            ("白酒", "399997"),
            ("食品饮料", "399997"),
            ("中证食品", "399997"),
            ("信息产业", "000934"),
            ("中证信息", "000934"),
            ("信息技术", "000934"),
            ("中证新能源", "399808"),
            ("新能源", "399808"),
            ("中证军工", "399967"),
            ("军工", "399967"),
            ("中证银行", "399986"),
            ("银行", "399986"),
            ("中证证券", "399975"),
            ("证券", "399975"),
            ("券商", "399975"),
            ("中证消费", "000932"),
            ("消费", "000932"),
            ("中证医药", "000933"),
            ("中证医药卫生", "000933"),
            ("全指医药", "000991"),
            ("医疗", "399989"),
            ("中证医疗", "399989"),
            ("创新药", "931152"),
            ("中证红利", "000922"),
            ("红利低波", "csi_dividend"),
            ("红利", "000922"),
            # 债券 - 中债系列（优先级高，具体匹配）
            ("中债综合", "CBA00101"),
            ("中债总指数", "CBA00201"),
            ("中债国债", "CBA00401"),
            ("中债金融债", "CBA00501"),
            ("中债企业债", "CBA00601"),
            ("中债政策性金融债", "CBA00501"),
            ("国开行", "CBA00501"),
            # 债券 - 中证系列
            ("中证国债", "H11070"),
            ("中证金融债", "H11071"),
            ("中证企业债", "H11072"),
            # 债券 - 通用匹配（优先级最低）
            ("债券指数", "bond_index"),
            ("债券", "bond_index"),
            ("中债", "CBA00101"),  # 默认使用中债综合指数
        ]

        safe_tracking_index = tracking_index_name or ""
        # 移除空格以增强匹配鲁棒性（基金名称中可能包含或不包含空格）
        search_text = f"{safe_tracking_index} {fund_name}".replace(" ", "").replace("\u3000", "")
        logger.debug(
            f"Searching tracking index for {fund_code}: tracking_index_name={tracking_index_name}, fund_name={fund_name}, search_text={search_text}"
        )
        for keyword, index_code in index_keywords:
            # 也对关键字移除空格，确保匹配成功
            if keyword.replace(" ", "").replace("\u3000", "") in search_text:
                logger.debug(f"匹配到关键字 '{keyword}', 返回指数代码：{index_code}")
                return index_code

        return None

    async def calculate_etf_valuation(
        self, fund_code: str, fund_name: str
    ) -> Optional[ValuationResult]:
        """
        计算场内ETF估值（直接获取实时价格）

        Args:
            fund_code: ETF代码
            fund_name: ETF名称

        Returns:
            Optional[ValuationResult]: 估值结果
        """
        try:
            realtime_data = await self.get_etf_realtime_price(fund_code)

            if realtime_data:
                logger.debug(f"ETF {fund_code} 使用实时价格：{realtime_data['price']}, 涨跌幅={realtime_data['change_percent']}%")
            else:
                # ETF 实时数据获取失败，fallback 到跟踪指数
                logger.warning(f"ETF {fund_code} 实时数据获取失败，尝试使用跟踪指数 fallback")
                tracking_index = await self.get_tracking_index(fund_code, fund_name)
                if tracking_index:
                    index_data = await self.get_index_data(tracking_index)
                    if index_data:
                        index_change = index_data.get("change_percent", 0)
                        logger.success(f"ETF {fund_code} fallback 成功：使用指数 {tracking_index} 估算，涨跌幅={index_change}%")
                        return ValuationResult(
                            fund_code=fund_code,
                            fund_name=fund_name,
                            valuation_type=ValuationType.INDEX_BASED,
                            estimated_nav=None,
                            estimated_change_percent=index_change,
                            previous_nav=None,
                            latest_nav=None,
                            nav_date=None,
                            total_value=None,
                            holdings_value={},
                            benchmark_info={
                                "index_code": tracking_index,
                                "index_name": index_data.get("name", ""),
                                "index_change_percent": index_change,
                            },
                            confidence=0.7,
                            confidence_note=f"ETF 实时数据暂不可用，使用跟踪指数 ({tracking_index}) 涨跌幅参考",
                            timestamp=datetime.now(),
                        )
                logger.warning(f"ETF {fund_code}: 所有数据源均失败，无法获取估值数据")
                return None

            return ValuationResult(
                fund_code=fund_code,
                fund_name=fund_name or realtime_data.get("name", ""),
                valuation_type=ValuationType.REAL_TIME_PRICE,
                estimated_nav=realtime_data["price"],
                estimated_change_percent=realtime_data["change_percent"],
                previous_nav=realtime_data.get("previous_close"),
                latest_nav=realtime_data["price"],
                nav_date=None,  # ETF实时价格没有净值日期
                total_value=realtime_data["price"],
                holdings_value={},
                benchmark_info=None,
                confidence=1.0,
                confidence_note="场内ETF实时价格，100%准确",
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.exception(f"ETF 估值计算异常 {fund_code}: {e}")
            return None

    async def calculate_index_fund_valuation(
        self,
        fund_code: str,
        fund_name: str,
        previous_nav: float,
        actual_nav: Optional[float] = None,
        tracking_index: Optional[str] = None,
        nav_date: Optional[str] = None,
    ) -> Optional[ValuationResult]:
        """
        计算指数基金估值（基于跟踪指数）

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            previous_nav: 昨日净值
            actual_nav: 今日净值（可选）
            tracking_index: 跟踪指数代码
            nav_date: 净值日期（可选）

        Returns:
            Optional[ValuationResult]: 估值结果
        """
        try:
            # 1. 首先检查是否为 ETF 联接基金
            target_etf = self._get_target_etf(fund_code, fund_name)

            if target_etf:
                # ETF 联接基金，使用目标 ETF 的涨跌幅
                logger.info(f"基金 {fund_code} 是 ETF 联接基金，目标 ETF: {target_etf}")
                return await self._calculate_etf_linking_valuation(
                    fund_code, fund_name, previous_nav, target_etf, actual_nav, nav_date
                )

            # 2. 普通指数基金，使用跟踪指数
            if not tracking_index:
                tracking_index = await self.get_tracking_index(fund_code, fund_name)

            if not tracking_index:
                logger.warning(f"Cannot find tracking index for fund {fund_code}")
                return ValuationResult(
                    fund_code=fund_code,
                    fund_name=fund_name,
                    valuation_type=ValuationType.BENCHMARK_ONLY,
                    estimated_nav=None,
                    estimated_change_percent=None,  # 改为 None，表示无法估算
                    previous_nav=previous_nav,
                    latest_nav=actual_nav,
                    nav_date=nav_date,
                    total_value=previous_nav,
                    holdings_value={},
                    benchmark_info=None,
                    confidence=0.3,
                    confidence_note="无法找到跟踪指数，无法估算涨跌幅",
                    timestamp=datetime.now(),
                )

            index_data = await self.get_index_data(tracking_index)

            if not index_data:
                logger.warning(f"指数基金 {fund_code}: 跟踪指数 {tracking_index} 数据获取失败，尝试使用持仓估值 fallback")
                # 指数数据获取失败，fallback 到持仓估值
                holdings_result = await self.calculate_holdings_based_valuation(
                    fund_code, fund_name, previous_nav, nav_date
                )
                if holdings_result and holdings_result.confidence >= 0.25:
                    logger.success(f"指数基金 {fund_code} fallback 成功：使用持仓估值，置信度={holdings_result.confidence}")
                    return holdings_result
                logger.warning(f"指数基金 {fund_code}: 持仓估值 fallback 也失败")

                # 对于 QDII/海外指数基金，如果所有方法都失败，尝试使用替代指数
                is_qdii = any(kw in fund_name for kw in ["纳斯达克", "标普", "恒生", "港股", "美股", "QDII", "香港"])

                # 尝试使用替代指数（对于某些冷门港股指数，使用恒生指数作为参考）
                proxy_index = None
                if tracking_index == "sp_hkconnect":  # 标普港股通低波红利 -> 恒生港股通高股息 -> 恒生指数
                    # 先尝试恒生港股通高股息，如果失败再尝试恒生指数
                    proxy_index = "hshk_dividend"
                    logger.info(f"QDII 基金 {fund_code} 首选替代指数 {proxy_index} 作为参考")
                elif tracking_index == "hshk_dividend":  # 恒生港股通高股息 -> 恒生指数
                    proxy_index = "hsi"
                    logger.info(f"QDII 基金 {fund_code} 使用替代指数 {proxy_index} 作为参考")

                if proxy_index:
                    proxy_index_data = await self.get_index_data(proxy_index)
                    if not proxy_index_data or proxy_index_data.get("change_percent") is None:
                        # 第一级替代指数也失败，尝试第二级替代（恒生指数）
                        if proxy_index == "hshk_dividend":
                            proxy_index = "hsi"
                            logger.info(f"QDII 基金 {fund_code} 第二替代指数 {proxy_index} 作为参考")
                            proxy_index_data = await self.get_index_data(proxy_index)

                    if proxy_index_data and proxy_index_data.get("change_percent") is not None:
                        # 使用替代指数计算涨跌幅
                        proxy_change_percent = proxy_index_data["change_percent"]
                        # 应用一个保守的折扣系数（因为替代指数不一定完全相关）
                        estimated_change_percent = proxy_change_percent * 0.7  # 70% 相关性
                        estimated_nav = previous_nav * (1 + estimated_change_percent / 100)

                        logger.success(f"QDII 基金 {fund_code} 使用替代指数 {proxy_index} 估算成功")
                        return ValuationResult(
                            fund_code=fund_code,
                            fund_name=fund_name,
                            valuation_type=ValuationType.BENCHMARK_ONLY,
                            estimated_nav=round(estimated_nav, 4),
                            estimated_change_percent=round(estimated_change_percent, 2),
                            previous_nav=previous_nav,
                            latest_nav=actual_nav,
                            nav_date=nav_date,
                            total_value=estimated_nav,
                            holdings_value={},
                            benchmark_info={
                                "tracking_index": tracking_index,
                                "proxy_index": proxy_index,
                                "proxy_change_percent": proxy_change_percent,
                                "note": "使用替代指数估算，相关性约 70%",
                            },
                            confidence=0.4,  # 使用替代指数，置信度较低
                            confidence_note=f"原跟踪指数数据不可用，使用{proxy_index_data.get('name', proxy_index)} 作为参考（乘以 70% 相关性系数）。QDII 基金估值仅供参考。",
                            timestamp=datetime.now(),
                        )

                # 对于 QDII/海外指数基金，如果所有方法都失败，返回一个更有信息量的结果
                # 用户至少可以看到昨日净值和跟踪指数信息，即使无法计算实时涨跌幅

                return ValuationResult(
                    fund_code=fund_code,
                    fund_name=fund_name,
                    valuation_type=ValuationType.BENCHMARK_ONLY,
                    estimated_nav=previous_nav,
                    estimated_change_percent=None,
                    previous_nav=previous_nav,
                    latest_nav=actual_nav,
                    nav_date=nav_date,
                    total_value=previous_nav,
                    holdings_value={},
                    benchmark_info={"tracking_index": tracking_index, "note": "指数数据暂不可用"},
                    confidence=0.3 if is_qdii else 0.5,
                    confidence_note=f"跟踪指数 ({tracking_index}) 数据暂不可用，显示昨日净值。{('QDII 基金受时差和数据源限制，估值可能延迟。' if is_qdii else '')}",
                    timestamp=datetime.now(),
                )

            # 检查是否为债券指数（无实时数据）
            if index_data.get("note") == "债券指数波动小，无实时数据":
                logger.info(f"债券指数基金 {fund_code}: 使用昨日净值作为估值参考（债券波动小）")
                return ValuationResult(
                    fund_code=fund_code,
                    fund_name=fund_name,
                    valuation_type=ValuationType.INDEX_BASED,
                    estimated_nav=previous_nav,
                    estimated_change_percent=0,
                    previous_nav=previous_nav,
                    latest_nav=actual_nav,
                    nav_date=nav_date,
                    total_value=previous_nav,
                    holdings_value={},
                    benchmark_info={
                        "index_code": tracking_index,
                        "index_name": index_data.get("name", ""),
                        "note": "债券指数波动小，日内估值参考意义不大",
                    },
                    confidence=0.7,
                    confidence_note="债券指数基金波动较小，以昨日净值作为参考",
                    timestamp=datetime.now(),
                )

            index_change_percent = index_data["change_percent"]
            tracking_error = 0.002
            # 应用跟踪误差修正（仅小幅调整，不显著影响估值）
            # 注意：跟踪误差是长期统计值，这里仅做轻微调整
            estimated_change_percent = index_change_percent * (1 - tracking_error)

            # 基于昨日净值和指数涨跌幅计算估算净值
            estimated_nav = previous_nav * (1 + estimated_change_percent / 100)

            return ValuationResult(
                fund_code=fund_code,
                fund_name=fund_name,
                valuation_type=ValuationType.INDEX_BASED,
                estimated_nav=round(estimated_nav, 4),
                estimated_change_percent=round(estimated_change_percent, 2),
                previous_nav=previous_nav,
                latest_nav=actual_nav,
                nav_date=nav_date,
                total_value=estimated_nav,
                holdings_value={},
                benchmark_info={
                    "index_code": tracking_index,
                    "index_name": index_data.get("name", ""),
                    "index_change_percent": index_change_percent,
                },
                confidence=1.0 if actual_nav is not None else 0.85,
                confidence_note="使用实际净值"
                if actual_nav is not None
                else "基于跟踪指数估值，误差约0.2%（跟踪误差）",
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Error calculating index fund valuation for {fund_code}: {e}")
            return None

    async def _calculate_etf_linking_valuation(
        self,
        fund_code: str,
        fund_name: str,
        previous_nav: float,
        target_etf: str,
        actual_nav: Optional[float] = None,
        nav_date: Optional[str] = None,
    ) -> Optional[ValuationResult]:
        """
        计算 ETF 联接基金的估值

        ETF 联接基金投资于目标 ETF，其涨跌幅应该与目标 ETF 高度相关
        考虑因素：
        1. 目标 ETF 的涨跌幅
        2. 联接基金的管理费、托管费（年化约 0.5-1%）
        3. 现金仓位（通常保留 5% 现金应对赎回）

        重要：对于 QDII ETF（跨境 ETF），由于容易出现溢价/折价，
        优先使用跟踪指数数据而非 ETF 交易价格进行估值。

        Fallback 策略：
        1. 首选：跟踪指数实时数据（特别是 QDII ETF）
        2. 次选：目标 ETF 实时价格（仅适用于非 QDII ETF）
        3. 再次：基于持仓股票估值
        4. 最后：返回基准净值（昨日净值）

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            previous_nav: 昨日净值
            target_etf: 目标 ETF 代码
            actual_nav: 今日净值（可选）
            nav_date: 净值日期（可选）

        Returns:
            Optional[ValuationResult]: 估值结果
        """
        try:
            # 获取跟踪指数
            tracking_index = ETF_INDEX_MAPPING.get(target_etf)

            # 判断是否为 QDII ETF（跨境 ETF）
            is_qdii = target_etf in QDII_ETF_LIST

            if is_qdii:
                # QDII ETF：优先使用指数数据，因为 ETF 交易价格可能有溢价/折价
                logger.info(f"QDII ETF 联接基金 {fund_code}，目标 ETF {target_etf} 为跨境 ETF，优先使用指数数据")
                if tracking_index:
                    index_data = await self.get_index_data(tracking_index)
                    if index_data:
                        index_change = index_data.get("change_percent", 0)
                        # ETF 联接基金通常有 90-95% 仓位投资于 ETF
                        estimated_change = index_change * 0.92  # 考虑仓位和费用影响
                        estimated_nav = previous_nav * (1 + estimated_change / 100)

                        logger.success(f"QDII ETF 联接基金 {fund_code} 使用指数 {tracking_index} 估算，涨跌幅={estimated_change}%")
                        return ValuationResult(
                            fund_code=fund_code,
                            fund_name=fund_name,
                            valuation_type=ValuationType.INDEX_BASED,
                            estimated_nav=round(estimated_nav, 4),
                            estimated_change_percent=round(estimated_change, 2),
                            previous_nav=previous_nav,
                            latest_nav=actual_nav,
                            nav_date=nav_date,
                            total_value=estimated_nav,
                            holdings_value={"target_etf": target_etf, "use_index_for_qdii": True},
                            benchmark_info={
                                "index_code": tracking_index,
                                "index_change": index_change,
                                "note": "QDII ETF 使用指数数据避免溢价/折价影响",
                            },
                            confidence=0.85,
                            confidence_note=f"QDII ETF 联接基金，基于跟踪指数 ({tracking_index}) 估算（避免 ETF 溢价/折价影响）",
                            timestamp=datetime.now(),
                        )
                    else:
                        logger.warning(f"QDII ETF 联接基金 {fund_code}: 跟踪指数 {tracking_index} 数据获取失败，尝试 fallback 到 ETF 价格")

            # 非 QDII ETF 或 QDII 指数数据获取失败：获取目标 ETF 的实时数据
            etf_data = await market_data_service.get_etf_realtime_data(target_etf)

            if not etf_data:
                # 目标 ETF 数据获取失败，fallback 到指数
                # 这是关键修复：ETF 联接基金不应该因为 ETF 数据获取失败而返回 None
                logger.warning(f"目标 ETF {target_etf} 数据获取失败，尝试使用指数 fallback")
                if tracking_index:
                    index_data = await self.get_index_data(tracking_index)
                    if index_data:
                        index_change = index_data.get("change_percent", 0)
                        # ETF 联接基金通常有 90-95% 仓位投资于 ETF
                        estimated_change = index_change * 0.92  # 考虑仓位和费用影响
                        estimated_nav = previous_nav * (1 + estimated_change / 100)

                        logger.success(f"ETF 联接基金 {fund_code} fallback 成功：使用指数 {tracking_index} 估算，涨跌幅={estimated_change}%")
                        return ValuationResult(
                            fund_code=fund_code,
                            fund_name=fund_name,
                            valuation_type=ValuationType.INDEX_BASED,
                            estimated_nav=round(estimated_nav, 4),
                            estimated_change_percent=round(estimated_change, 2),
                            previous_nav=previous_nav,
                            latest_nav=actual_nav,
                            nav_date=nav_date,
                            total_value=estimated_nav,
                            holdings_value={"target_etf": target_etf, "fallback_to_index": True},
                            benchmark_info={"index_code": tracking_index, "index_change": index_change},
                            confidence=0.75,
                            confidence_note=f"目标 ETF({target_etf}) 数据暂不可用，使用跟踪指数 ({tracking_index}) 估算",
                            timestamp=datetime.now(),
                        )
                    else:
                        logger.warning(f"ETF 联接基金 {fund_code}: 跟踪指数 {tracking_index} 数据也获取失败")
                else:
                    logger.warning(f"ETF 联接基金 {fund_code}: 无法找到目标 ETF {target_etf} 对应的跟踪指数")

                # 如果指数数据也获取失败，尝试使用持仓估值作为最后的 fallback
                # 这对于指数型基金可能不太适用，但总比没有估值好
                logger.info(f"ETF 联接基金 {fund_code} 尝试使用持仓估值作为 fallback")
                holdings_result = await self.calculate_holdings_based_valuation(
                    fund_code, fund_name, previous_nav, nav_date
                )
                if holdings_result and holdings_result.confidence >= 0.25:
                    logger.success(f"ETF 联接基金 {fund_code} fallback 成功：使用持仓估值，置信度={holdings_result.confidence}")
                    return holdings_result

                # 所有方法都失败，返回一个基准结果
                logger.warning(f"ETF 联接基金 {fund_code} 所有 fallback 方法均失败，返回基准净值")
                return ValuationResult(
                    fund_code=fund_code,
                    fund_name=fund_name,
                    valuation_type=ValuationType.BENCHMARK_ONLY,
                    estimated_nav=previous_nav,
                    estimated_change_percent=None,
                    previous_nav=previous_nav,
                    latest_nav=actual_nav,
                    nav_date=nav_date,
                    total_value=previous_nav,
                    holdings_value={},
                    benchmark_info={"target_etf": target_etf, "note": "数据暂不可用"},
                    confidence=0.5,
                    confidence_note=f"目标 ETF({target_etf}) 和指数数据暂不可用，显示昨日净值",
                    timestamp=datetime.now(),
                )

            # 对于非 QDII ETF，使用 ETF 交易价格计算
            etf_change_percent = etf_data.get("change_percent", 0)

            # ETF 联接基金通常有 90-95% 仓位投资于 ETF，保留 5% 现金
            etf_position_ratio = 0.95  # 95% 仓位
            estimated_change = etf_change_percent * etf_position_ratio
            estimated_nav = previous_nav * (1 + estimated_change / 100)

            return ValuationResult(
                fund_code=fund_code,
                fund_name=fund_name,
                valuation_type=ValuationType.INDEX_BASED,
                estimated_nav=round(estimated_nav, 4),
                estimated_change_percent=round(estimated_change, 2),
                previous_nav=previous_nav,
                latest_nav=actual_nav,
                nav_date=nav_date,
                total_value=estimated_nav,
                holdings_value={
                    "target_etf": target_etf,
                    "etf_change_percent": etf_change_percent,
                    "position_ratio": etf_position_ratio,
                },
                benchmark_info={
                    "target_etf": target_etf,
                    "etf_name": etf_data.get("name", ""),
                    "etf_change": etf_change_percent,
                },
                confidence=0.90 if actual_nav is None else 0.95,
                confidence_note=f"ETF 联接基金，基于目标 ETF({target_etf}) 估算，考虑 95% 仓位",
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.exception(f"计算 ETF 联接基金估值失败 {fund_code}: {e}")
            return None

    async def calculate_holdings_based_valuation(
        self,
        fund_code: str,
        fund_name: str,
        previous_nav: float,
        nav_date: Optional[str] = None,
    ) -> Optional[ValuationResult]:
        """
        基于持仓股票比例计算基金估值

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            previous_nav: 昨日净值

        Returns:
            Optional[ValuationResult]: 估值结果
        """
        try:
            holdings = await market_data_service.get_fund_holdings(fund_code)

            if not holdings:
                logger.warning(f"No holdings data for fund {fund_code}")
                return None

            top_holdings = holdings[:10]

            stock_codes = [h.asset_code for h in top_holdings if h.asset_code]
            stock_prices = {}

            for stock_code in stock_codes:
                try:
                    data = await market_data_service.get_stock_price(stock_code)
                    if data:
                        stock_prices[stock_code] = {
                            "name": data.name,
                            "price": data.price,
                            "change_percent": data.change_percent or 0,
                        }
                except Exception as e:
                    logger.warning(f"Error getting price for stock {stock_code}: {e}")
                    continue

            if not stock_prices:
                logger.warning(f"No stock prices available for fund {fund_code}")
                return None

            total_weight = 0.0
            weighted_change = 0.0
            holdings_value = {}

            for holding in top_holdings:
                if holding.asset_code in stock_prices:
                    stock_data = stock_prices[holding.asset_code]
                    weight = holding.weight if holding.weight else 0
                    change_percent = stock_data["change_percent"]

                    contribution = weight * change_percent / 100
                    weighted_change += contribution
                    total_weight += weight

                    holdings_value[holding.asset_name] = {
                        "weight": weight,
                        "change_percent": change_percent,
                        "contribution": round(contribution, 4),
                    }

            if total_weight > 0:
                coverage_ratio = min(total_weight / 100, 1.0)
            else:
                coverage_ratio = 0

            estimated_change_percent = weighted_change
            estimated_nav = previous_nav * (1 + estimated_change_percent / 100)

            confidence = min(0.9, 0.5 + coverage_ratio * 0.4)

            if coverage_ratio >= 0.8:
                confidence_note = (
                    f"基于持仓估值，覆盖率{coverage_ratio * 100:.0f}%，参考价值较高"
                )
            elif coverage_ratio >= 0.5:
                confidence_note = (
                    f"基于持仓估值，覆盖率{coverage_ratio * 100:.0f}%，存在一定偏差"
                )
            else:
                confidence_note = (
                    f"基于持仓估值，覆盖率仅{coverage_ratio * 100:.0f}%，偏差可能较大"
                )

            return ValuationResult(
                fund_code=fund_code,
                fund_name=fund_name,
                valuation_type=ValuationType.HOLDINGS_BASED,
                estimated_nav=round(estimated_nav, 4),
                estimated_change_percent=round(estimated_change_percent, 2),
                previous_nav=previous_nav,
                latest_nav=None, # TODO: 实时净值没有
                nav_date=nav_date,
                total_value=estimated_nav,
                holdings_value=holdings_value,
                benchmark_info={
                    "total_weight": round(total_weight, 2),
                    "coverage_ratio": round(coverage_ratio, 2),
                    "holdings_count": len(holdings_value),
                },
                confidence=round(confidence, 2),
                confidence_note=confidence_note,
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(
                f"Error calculating holdings-based valuation for {fund_code}: {e}"
            )
            return None

    async def calculate_active_fund_valuation(
        self,
        fund_code: str,
        fund_name: str,
        previous_nav: float,
        benchmark: Optional[str] = None,
        nav_date: Optional[str] = None,
    ) -> Optional[ValuationResult]:
        """
        计算主动型基金估值

        优先级：
        1. 首先尝试使用持仓股票估值（前 10 大持仓加权）
        2. 如果持仓数据不可用，使用业绩基准作为参考

        注意：即使持仓覆盖率较低，持仓估值仍然比纯基准参考更有价值
        置信度阈值从 0.4 降低到 0.25，让更多持仓估值结果能够显示

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            previous_nav: 昨日净值
            benchmark: 业绩比较基准
            nav_date: 净值日期

        Returns:
            Optional[ValuationResult]: 估值结果
        """
        try:
            # 1. 首先尝试持仓估值
            holdings_result = await self.calculate_holdings_based_valuation(
                fund_code, fund_name, previous_nav, nav_date
            )

            # 如果持仓估值置信度>=0.25，使用持仓估值（阈值从 0.4 降低到 0.25）
            # 原因：即使持仓覆盖率只有 25%，基于实际持仓的估算仍然比纯基准参考更有价值
            # 置信度会在结果中明确显示，用户可以自行判断
            if holdings_result and holdings_result.confidence >= 0.25:
                logger.info(f"主动型基金 {fund_code} 使用持仓估值，置信度={holdings_result.confidence}")
                return holdings_result

            # 2. 持仓估值置信度太低或失败，fallback 到业绩基准
            logger.info(f"主动型基金 {fund_code} 持仓估值置信度太低 ({holdings_result.confidence if holdings_result else 'None'})，使用业绩基准参考")
            benchmark_info = None

            if benchmark:
                benchmark_index = self._extract_benchmark_index(benchmark)
                if benchmark_index:
                    index_data = await self.get_index_realtime_data(benchmark_index)
                    if index_data:
                        benchmark_info = {
                            "benchmark_name": benchmark,
                            "index_code": benchmark_index,
                            "index_name": index_data.get("name", ""),
                            "index_change_percent": index_data["change_percent"],
                        }

            return ValuationResult(
                fund_code=fund_code,
                fund_name=fund_name,
                valuation_type=ValuationType.BENCHMARK_ONLY,
                estimated_nav=None,
                estimated_change_percent=None,
                previous_nav=previous_nav,
                latest_nav=None,
                nav_date=nav_date,
                total_value=previous_nav,
                holdings_value={},
                benchmark_info=benchmark_info,
                confidence=0.3 if benchmark_info else 0.2,
                confidence_note="持仓数据不足，仅供参考业绩基准" if benchmark_info else "无持仓数据和业绩基准，无法估值",
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.exception(
                f"Error calculating active fund valuation for {fund_code}: {e}"
            )
            return None

    def _extract_benchmark_index(self, benchmark: str) -> Optional[str]:
        """从业绩比较基准中提取指数代码"""
        index_keywords = {
            "沪深300": "000300",
            "中证500": "000905",
            "中证800": "000906",
            "上证50": "000016",
            "创业板指": "399006",
            "科创50": "000688",
            "中证1000": "000852",
        }

        for keyword, code in index_keywords.items():
            if keyword in benchmark:
                return code

        return None

    async def calculate_hybrid_bond_valuation(
        self,
        fund_code: str,
        fund_name: str,
        previous_nav: float,
        nav_date: Optional[str] = None,
        fund_data=None,
    ) -> Optional[ValuationResult]:
        """
        计算偏债混合基金/二级债基的混合估值

        估值方法：
        - 债券部分（约 70-90%）：使用债券指数涨跌幅
        - 股票部分（约 10-30%）：使用持仓股票加权涨跌幅

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            previous_nav: 昨日净值
            nav_date: 净值日期
            fund_data: 基金数据对象

        Returns:
            Optional[ValuationResult]: 估值结果
        """
        try:
            # 1. 获取基金持仓（股票部分）
            holdings_result = await self.calculate_holdings_based_valuation(
                fund_code, fund_name, previous_nav, nav_date
            )

            # 2. 获取债券指数数据
            bond_index = "CBA00101"  # 默认使用中债综合指数
            bond_data = await self.get_index_data(bond_index)
            bond_change = bond_data.get("change_percent", 0.0) if bond_data else 0.0

            # 3. 根据基金类型确定股债仓位比例
            fund_type_lower = (fund_data.fund_type or "").lower() if fund_data else ""

            if "混合二级" in fund_type_lower or "二级债基" in fund_name.lower():
                # 二级债基：债券 80% + 股票 20%
                bond_ratio = 0.80
                stock_ratio = 0.20
                ratio_note = "二级债基（债券 80% + 股票 20%）"
            elif "偏债" in fund_type_lower:
                # 偏债混合：债券 70% + 股票 30%
                bond_ratio = 0.70
                stock_ratio = 0.30
                ratio_note = "偏债混合（债券 70% + 股票 30%）"
            elif "混合一级" in fund_type_lower or "一级债基" in fund_name.lower():
                # 一级债基：债券 85% + 可转债 15%
                # 一级债基不可投资股票，但可投资可转债
                bond_ratio = 0.85
                stock_ratio = 0.15  # 可转债部分
                ratio_note = "一级债基（债券 85% + 可转债 15%）"
            else:
                # 默认：债券 85% + 股票 15%
                bond_ratio = 0.85
                stock_ratio = 0.15
                ratio_note = "债基（债券 85% + 股票 15%）"

            # 4. 计算混合涨跌幅
            # 对于一级债基，"股票部分" 实际上是可转债部分，使用可转债指数估算
            stock_change = holdings_result.estimated_change_percent if holdings_result else 0.0

            # 如果是一级债基且没有可转债数据，使用可转债指数近似（中证转债指数）
            if ("混合一级" in fund_type_lower or "一级债基" in fund_name.lower()) and not stock_change:
                # 可转债指数波动较小，通常约为主板的 30-50%
                stock_change = 0.0  # 可转债日内波动小，暂不估算
                ratio_note += "（可转债数据暂不可用）"

            hybrid_change = bond_change * bond_ratio + stock_change * stock_ratio

            # 5. 计算估算净值
            estimated_nav = previous_nav * (1 + hybrid_change / 100)

            # 6. 确定置信度
            if holdings_result and holdings_result.confidence:
                # 置信度 = 股票部分置信度 × 股票仓位占比 + 0.8 × 债券仓位占比
                confidence = holdings_result.confidence * stock_ratio + 0.8 * bond_ratio
            else:
                # 如果没有股票持仓数据，仅使用债券部分
                confidence = 0.6 * bond_ratio
                hybrid_change = bond_change * bond_ratio
                estimated_nav = previous_nav * (1 + hybrid_change / 100)
                ratio_note += "（仅有债券部分数据）"

            confidence_note = f"混合估值：{ratio_note}"

            return ValuationResult(
                fund_code=fund_code,
                fund_name=fund_name,
                valuation_type=ValuationType.HYBRID_BOND,
                estimated_nav=round(estimated_nav, 4),
                estimated_change_percent=round(hybrid_change, 2),
                previous_nav=previous_nav,
                latest_nav=None,
                nav_date=nav_date,
                total_value=estimated_nav,
                holdings_value={
                    "bond_index": bond_index,
                    "bond_change": bond_change,
                    "bond_ratio": bond_ratio,
                    "stock_change": stock_change,
                    "stock_ratio": stock_ratio,
                } if holdings_result else {"bond_index": bond_index, "bond_change": bond_change},
                benchmark_info={
                    "bond_index_code": bond_index,
                    "bond_index_change": bond_change,
                    "stock_ratio": stock_ratio,
                    "bond_ratio": bond_ratio,
                },
                confidence=round(confidence, 2),
                confidence_note=confidence_note,
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Error calculating hybrid bond valuation for {fund_code}: {e}")
            return None

    async def calculate_hybrid_qdii_valuation(
        self,
        fund_code: str,
        fund_name: str,
        previous_nav: float,
        nav_date: Optional[str] = None,
    ) -> Optional[ValuationResult]:
        """
        计算主动管理型 QDII 基金的混合估值

        估值方法：
        - 持仓部分（已知的前 10 大持仓）：使用实际持仓股票涨跌幅
        - 剩余部分（未知持仓）：使用市场指数涨跌幅作为参考
        - 最终估值 = 持仓部分权重 × 持仓涨跌幅 + 剩余部分权重 × 指数涨跌幅

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            previous_nav: 昨日净值
            nav_date: 净值日期

        Returns:
            Optional[ValuationResult]: 估值结果
        """
        try:
            # 1. 获取基金持仓
            holdings = await market_data_service.get_fund_holdings(fund_code)

            # 2. 确定市场指数（根据基金名称判断）
            index_code = self._get_qdii_market_index(fund_name)

            # 3. 获取指数数据
            index_data = await self.get_index_data(index_code) if index_code else None
            index_change = index_data.get("change_percent", 0) if index_data else 0

            # 4. 计算持仓部分涨跌幅
            holdings_change = 0
            holdings_weight = 0
            holdings_value = {}

            if holdings:
                for holding in holdings[:10]:
                    try:
                        stock_data = await market_data_service.get_stock_price(holding.asset_code)
                        if stock_data:
                            weight = holding.weight if holding.weight else 0
                            change_percent = stock_data.change_percent or 0
                            contribution = weight * change_percent / 100
                            holdings_change += contribution
                            holdings_weight += weight
                            holdings_value[holding.asset_name] = {
                                "weight": weight,
                                "change_percent": change_percent,
                                "contribution": round(contribution, 4),
                            }
                    except Exception as e:
                        logger.debug(f"获取持仓股票价格失败 {holding.asset_code}: {e}")

            # 5. 混合估值
            # 假设：持仓部分权重 = holdings_weight，剩余部分权重 = 100 - holdings_weight
            remaining_weight = 100 - holdings_weight
            # 剩余部分使用指数涨跌幅
            remaining_contribution = remaining_weight * index_change / 100

            # 总涨跌幅 = 持仓部分 + 剩余部分
            estimated_change_percent = holdings_change + remaining_contribution
            estimated_nav = previous_nav * (1 + estimated_change_percent / 100)

            # 6. 计算置信度
            # 持仓覆盖率越高，置信度越高
            coverage_ratio = holdings_weight / 100
            confidence = 0.4 + coverage_ratio * 0.4  # 40% 基础 + 最高 40% 覆盖率加成

            confidence_note = (
                f"混合估值：持仓{holdings_weight:.1f}% × 实际涨跌幅 + 剩余{remaining_weight:.1f}% × 指数涨跌幅 ({index_change:.2f}%)"
            )

            return ValuationResult(
                fund_code=fund_code,
                fund_name=fund_name,
                valuation_type=ValuationType.HYBRID_QDII,
                estimated_nav=round(estimated_nav, 4),
                estimated_change_percent=round(estimated_change_percent, 2),
                previous_nav=previous_nav,
                latest_nav=None,
                nav_date=nav_date,
                total_value=estimated_nav,
                holdings_value=holdings_value,
                benchmark_info={
                    "holdings_weight": round(holdings_weight, 2),
                    "remaining_weight": round(remaining_weight, 2),
                    "index_code": index_code,
                    "index_change": index_change,
                },
                confidence=round(confidence, 2),
                confidence_note=confidence_note,
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Error calculating hybrid QDII valuation for {fund_code}: {e}")
            return None

    def _get_qdii_market_index(self, fund_name: str) -> Optional[str]:
        """
        根据 QDII 基金名称判断应该使用哪个市场指数

        Returns:
            Optional[str]: 指数代码
        """
        fund_name_lower = (fund_name or "").lower()

        # 港股市场
        if any(kw in fund_name_lower for kw in ["香港", "港股", "恒生", "h 股"]):
            return "hsi"  # 恒生指数

        # 美股市场
        if any(kw in fund_name_lower for kw in ["美国", "美股", "纳斯达克", "标普", "道琼斯"]):
            if "纳斯达克" in fund_name_lower:
                return "nasdaq100"
            if "标普" in fund_name_lower:
                return "sp500"
            return "sp500"  # 默认使用标普 500

        # 日本市场
        if any(kw in fund_name_lower for kw in ["日本", "日经"]):
            return "nikkei"

        # 德国市场
        if any(kw in fund_name_lower for kw in ["德国", "dax"]):
            return "dax"

        # 香港市场作为默认（因为大多数 QDII 投资港股）
        return "hsi"

    def _get_previous_nav(
        self,
        fund_code: str,
        fund_data,
        provided_previous_nav: Optional[float] = None,
    ) -> Tuple[Optional[float], Optional[str], str]:
        """
        获取用于估值计算的昨日净值

        优先级策略：
        1. 优先使用 fund_data.previous_nav（如果存在且有效）
        2. 使用 provided_previous_nav（如果提供且有效）
        3. 从 nav_date 判断：如果净值日期是今天，说明公布的是昨日净值，可直接使用
        4. 如果净值日期是昨天或更早，使用该净值作为基准（但需要说明估值可能不准确）
        5. 使用 fund_data.nav 作为最后备选（带警告）

        Returns:
            Tuple[Optional[float], Optional[str], str]: (昨日净值，净值日期，日志信息)
        """
        log_message = ""

        # 优先级 1: 使用 fund_data.previous_nav（最直接的数据）
        if fund_data.previous_nav is not None and fund_data.previous_nav > 0:
            log_message = (
                f"基金 {fund_code} 使用系统提供的昨日净值：{fund_data.previous_nav}"
            )
            return fund_data.previous_nav, fund_data.nav_date, log_message

        # 优先级 2: 使用提供的 previous_nav
        if provided_previous_nav is not None and provided_previous_nav > 0:
            log_message = (
                f"基金 {fund_code} 使用外部提供的昨日净值：{provided_previous_nav}"
            )
            return provided_previous_nav, None, log_message

        # 优先级 3: 从 nav 和 nav_date 判断
        if fund_data.nav is not None and fund_data.nav > 0 and fund_data.nav_date:
            try:
                nav_date_obj = datetime.strptime(fund_data.nav_date, "%Y-%m-%d").date()
                today_obj = datetime.now().date()
                days_diff = (today_obj - nav_date_obj).days

                # 基金净值公布规则：
                # - 工作日晚上公布当天净值（T 日净值在 T 日晚上公布）
                # - 所以如果净值日期是今天或昨天，都可以作为"昨日净值"使用

                if days_diff == 0:
                    # 净值日期是今天，说明这是今天公布的净值（实际上是昨天的净值）
                    # 这是最理想的情况，可以直接使用
                    log_message = (
                        f"基金 {fund_code} 净值日期为今天 ({fund_data.nav_date})，"
                        f"使用公布的净值作为昨日净值：{fund_data.nav}"
                    )
                    return fund_data.nav, fund_data.nav_date, log_message
                elif days_diff == 1:
                    # 净值日期是昨天，说明这是昨天公布的净值（实际上是前天的净值）
                    # 也可以接受，但需要说明估值可能不准确
                    log_message = (
                        f"基金 {fund_code} 净值日期为昨天 ({fund_data.nav_date})，"
                        f"使用公布的净值作为昨日净值参考：{fund_data.nav}"
                    )
                    return fund_data.nav, fund_data.nav_date, log_message
                elif days_diff > 1:
                    # 净值日期较早，数据可能过时
                    log_message = (
                        f"基金 {fund_code} 净值日期为 {fund_data.nav_date} ({days_diff}天前)，"
                        f"数据已过时，使用最新净值作为参考：{fund_data.nav} (估值可能不准确)"
                    )
                    return fund_data.nav, fund_data.nav_date, log_message
                else:
                    # days_diff < 0，净值日期是未来（可能是数据错误或时区问题）
                    log_message = f"基金 {fund_code} 净值日期 {fund_data.nav_date} 是未来日期，使用当前净值：{fund_data.nav}"
                    return fund_data.nav, fund_data.nav_date, log_message
            except ValueError as e:
                log_message = f"基金 {fund_code} 净值日期格式无法解析：{fund_data.nav_date}, 错误：{e}"
                logger.warning(log_message)
                # 格式错误时，如果 nav 有效，仍然可以使用
                if fund_data.nav is not None and fund_data.nav > 0:
                    return fund_data.nav, None, log_message
                return None, None, log_message

        # 优先级 4: 仅使用 nav 作为最后备选（无日期信息）
        if fund_data.nav is not None and fund_data.nav > 0:
            log_message = (
                f"基金 {fund_code} 无法获取净值日期，使用当前净值作为参考：{fund_data.nav} "
                f"(注意：此情况下估值可能不准确)"
            )
            return fund_data.nav, None, log_message

        # 所有方法都失败了
        log_message = f"基金 {fund_code} 无法获取任何净值数据"
        return None, None, log_message

    async def calculate_fund_valuation(
        self,
        fund_code: str,
        prefer_holdings: bool = True,
    ) -> Optional[ValuationResult]:
        """
        计算基金估值（自动判断基金类型并选择合适的估值方法）

        Args:
            fund_code: 基金代码
            prefer_holdings: 是否优先使用持仓估值（默认True）

        Returns:
            Optional[ValuationResult]: 估值结果
        """
        try:
            fund_data = await market_data_service.get_fund_data(fund_code)
            if not fund_data:
                logger.error(f"Failed to get fund info for {fund_code}")
                return None

            fund_name = fund_data.fund_name or ""

            if fund_data.market_type == MarketType.UNKNOWN:
                fund_data.market_type = determine_market_type(
                    fund_code, fund_name, fund_data.fund_type
                )

            valuation_type, confidence, confidence_note = self._classify_fund(fund_data, fund_code)

            if valuation_type == ValuationType.REAL_TIME_PRICE:
                return await self.calculate_etf_valuation(fund_code, fund_name)

            # 使用新方法获取昨日净值
            previous_nav, nav_date, nav_log = self._get_previous_nav(fund_code, fund_data)
            logger.info(nav_log)

            if previous_nav is None:
                logger.error(f"No previous NAV available for {fund_code}")
                return None

            # 验证净值日期格式
            if nav_date:
                try:
                    datetime.strptime(nav_date, "%Y-%m-%d")
                except ValueError:
                    logger.warning(f"基金 {fund_code} 净值日期格式不正确：{nav_date}")
                    nav_date = None

            if valuation_type == ValuationType.INDEX_BASED:
                tracking_index = await self.get_tracking_index(
                    fund_code, fund_name, fund_data.tracking_index
                )
                # 使用实际净值（如果可用）
                actual_nav = fund_data.nav if fund_data.nav else None

                result = await self.calculate_index_fund_valuation(
                    fund_code,
                    fund_name,
                    previous_nav,
                    actual_nav,
                    tracking_index,
                    nav_date
                )

                # 如果指数估值失败，尝试 fallback 到持仓估值
                if result is None and prefer_holdings:
                    logger.info(f"指数估值失败，尝试使用持仓估值：{fund_code}")
                    holdings_result = await self.calculate_holdings_based_valuation(
                        fund_code, fund_name, previous_nav, nav_date
                    )
                    if holdings_result:
                        return holdings_result

                return result

            if valuation_type == ValuationType.HOLDINGS_BASED:
                # 主动型股票/混合基金，使用持仓估值
                holdings_result = await self.calculate_holdings_based_valuation(
                    fund_code, fund_name, previous_nav, nav_date
                )
                # 置信度阈值从 0.4 降低到 0.25，让即使覆盖率较低的持仓估值也能显示
                # 原因：有估值总比没有好，置信度会在结果中明确告知用户
                if holdings_result and holdings_result.confidence >= 0.25:
                    logger.info(f"主动型基金 {fund_code} 使用持仓估值，置信度={holdings_result.confidence}")
                    return holdings_result
                # 持仓估值置信度太低或失败，fallback 到业绩基准
                logger.info(f"主动型基金 {fund_code} 持仓估值置信度太低 ({holdings_result.confidence if holdings_result else 'None'})，使用业绩基准参考")
                return await self.calculate_active_fund_valuation(
                    fund_code, fund_name, previous_nav, fund_data.benchmark, nav_date
                )

            if valuation_type == ValuationType.HYBRID_BOND:
                # 偏债混合/二级债基，使用混合估值：债券指数 + 股票持仓
                logger.info(f"偏债混合/二级债基 {fund_code}，使用混合估值方法")
                return await self.calculate_hybrid_bond_valuation(
                    fund_code, fund_name, previous_nav, nav_date, fund_data
                )

            if valuation_type == ValuationType.HYBRID_QDII:
                # 主动管理型 QDII，使用混合估值：持仓估值 + 市场指数
                logger.info(f"主动管理型 QDII {fund_code}，使用持仓 + 指数混合估值")
                return await self.calculate_hybrid_qdii_valuation(
                    fund_code, fund_name, previous_nav, nav_date
                )

            if valuation_type == ValuationType.BENCHMARK_ONLY:
                # QDII 或其他无法使用持仓估值的基金，使用业绩基准参考
                return await self.calculate_active_fund_valuation(
                    fund_code, fund_name, previous_nav, fund_data.benchmark, nav_date
                )

            result = ValuationResult(
                fund_code=fund_code,
                fund_name=fund_name,
                valuation_type=ValuationType.NOT_SUPPORTED,
                estimated_nav=previous_nav,
                estimated_change_percent=None,
                previous_nav=previous_nav,
                latest_nav=previous_nav,
                nav_date=nav_date,
                total_value=previous_nav,
                holdings_value={},
                benchmark_info=None,
                confidence=0.0,
                confidence_note="该基金类型暂不支持估值",
                timestamp=datetime.now(),
            )
        except Exception as e:
            logger.error(f"Error calculating fund valuation for {fund_code}: {e}")
            return None

    async def get_valuation_with_method(self, fund_code: str, prefer_holdings: bool = True) -> Optional[ValuationResult]:
        """
        获取基金估值，并添加估值方法说明
        """
        result = await self.calculate_fund_valuation(fund_code, prefer_holdings)
        if result and not result.valuation_method:
            result.valuation_method = get_valuation_method_name(result.valuation_type)
        # 附加跟踪指数的 PE/PB 估值分位 + 定投信号
        if result and result.pe_percentile is None:
            fund_data = await market_data_service.get_fund_data(fund_code)
            fund_name = fund_data.fund_name if fund_data else ""
            fund_type = fund_data.fund_type if fund_data else ""
            tracking_index = await self.get_tracking_index(fund_code, fund_name)

            signal_data = valuation_signal_service.get_signal(tracking_index, fund_type)
            if signal_data:
                result.index_code = signal_data.get("index_code")
                result.index_name = signal_data.get("index_name")
                result.pe_value = signal_data.get("pe_value")
                result.pe_percentile = signal_data.get("pe_percentile")
                result.pb_value = signal_data.get("pb_value")
                result.pb_percentile = signal_data.get("pb_percentile")
                result.valuation_signal = signal_data.get("signal")
                result.signal_action = signal_data.get("signal_action")
                result.signal_source = signal_data.get("source", "")
        return result


fund_valuation_service = FundValuationService()
