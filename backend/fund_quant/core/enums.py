"""FundQuant 枚举定义"""

from enum import Enum


class SignalType(str, Enum):
    """信号类型"""
    TIMING = "timing"
    SELECTION = "selection"
    ALLOCATION = "allocation"
    RISK = "risk"


class Direction(str, Enum):
    """信号方向"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    REBALANCE = "rebalance"


class FundType(str, Enum):
    """基金量化分类"""
    EQUITY = "equity"           # 主动股票型（含偏股混合，股≥60%）
    INDEX = "index"             # 指数型（ETF/联接/被动/增强）
    BALANCED = "balanced"       # 平衡混合型（含二级债基/灵活配置）
    BOND = "bond"               # 债券型（纯债/长债/短债/一级债基）
    MONEY = "money"             # 货币型
    QDII = "qdii"               # 海外型
    COMMODITY = "commodity"     # 商品型（黄金等）
    FOF = "fof"                 # FOF型


class StrategyType(str, Enum):
    """策略类型"""
    TIMING = "timing"
    SELECTION = "selection"
    ALLOCATION = "allocation"


class RiskLevel(str, Enum):
    """风险等级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConfidenceLevel(str, Enum):
    """置信度等级"""
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


class DataQuality(str, Enum):
    """数据质量标记"""
    GOOD = "good"
    STALE = "stale"
    SUSPICIOUS = "suspicious"
    MISSING = "missing"
    ERROR = "error"
