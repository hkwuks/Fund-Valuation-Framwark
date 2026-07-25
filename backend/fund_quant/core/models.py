"""FundQuant 数据模型"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List, Dict, Union, Tuple
from pydantic import BaseModel, Field
from .enums import SignalType, Direction, FundType, StrategyType

# 新旧 FundType 兼容映射：前端/K线/DB 数据可能传旧值
TYPE_COMPAT = {
    "stock": "equity",
    "hybrid": "equity",
    "etf": "index",
    "etf_link": "index",
}


# ═══════════════════════════════════════════
# 策略上下文
# ═══════════════════════════════════════════

class StrategyContext(BaseModel):
    """策略上下文"""
    strategy_name: str
    strategy_type: StrategyType
    params: dict = Field(default_factory=dict)
    fund_codes: List[str] = Field(default_factory=list)
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class FundDataPoint(BaseModel):
    """单日基金数据点"""
    date: date
    nav: Optional[float] = None
    adjusted_nav: Optional[float] = None
    estimated_nav: Optional[float] = None
    valuation_deviation: Optional[float] = None


class InformationSet(BaseModel):
    """可用信息集（前视偏差防护）"""
    nav_available_up_to: date
    qdii_nav_available_up_to: Optional[date] = None  # None = 同 nav_available_up_to
    intraday_quotes_available: date
    holdings_disclosed_up_to: date
    holdings_effective_date: date


class Portfolio(BaseModel):
    """组合快照"""
    total_value: float = 0.0
    cash: float = 0.0
    positions: Dict[str, float] = Field(default_factory=dict)  # fund_code -> weight
    nav_values: Dict[str, float] = Field(default_factory=dict)  # fund_code -> latest nav
    created_at: datetime = Field(default_factory=datetime.now)


# ═══════════════════════════════════════════
# 信号模型
# ═══════════════════════════════════════════

class FundSignal(BaseModel):
    """基金交易信号"""
    signal_id: str
    fund_code: str
    fund_name: str = ""
    fund_type: str = ""
    signal_type: SignalType
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    suggested_amount: Optional[float] = None
    suggested_pct: Optional[float] = None
    valuation_deviation: Optional[float] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    strategy_name: str = ""
    risk_check_passed: bool = True
    risk_warnings: List[str] = Field(default_factory=list)
    urgency: str = "medium"  # low / medium / high
    valid_until: Optional[date] = None
    historical_performance: Optional[Dict] = None


class FundQuantResult(BaseModel):
    """量化评估结果"""
    fund_code: str
    fund_name: str = ""
    fund_type: str = ""
    timing_score: Optional[float] = None  # -1 ~ 1
    selection_score: Optional[float] = None  # 0 ~ 100
    allocation_weight: Optional[float] = None  # 0 ~ 1
    signals: List[FundSignal] = Field(default_factory=list)
    risk_metrics: Optional["RiskMetrics"] = None
    timestamp: datetime = Field(default_factory=datetime.now)


# ═══════════════════════════════════════════
# 风险模型
# ═══════════════════════════════════════════

class RiskMetrics(BaseModel):
    """风险指标"""
    var_95: float = 0.0
    cvar_95: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    sharpe_ratio: Optional[float] = None
    tracking_error: Optional[float] = None
    sortino_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None


class RiskCheckResult(BaseModel):
    """风控检查结果"""
    passed: bool = True
    check_name: str = ""
    reason: str = ""
    details: dict = Field(default_factory=dict)


# ═══════════════════════════════════════════
# 回测模型
# ═══════════════════════════════════════════

class CostModelConfig(BaseModel):
    """费率模型配置"""
    fund_type: str = "equity"
    subscription_fee_tiers: Union[Dict[str, float], Dict[str, List[Tuple[str, float]]]] = Field(
        default_factory=lambda: {
            "stock": 0.015,
            "hybrid": 0.015,
            "bond": 0.008,
            "index": 0.010,
            "qdii": 0.015,
            "money": 0.0,
            "fof": 0.012,
        }
    )
    holding_period_discount: Dict[int, float] = Field(
        default_factory=lambda: {
            7: 1.5,
            30: 0.75,
            365: 0.5,
            730: 0.25,
            9999: 0.0,
        }
    )
    management_fee_rate: Union[Dict[str, float], Dict[str, List[Tuple[str, float]]]] = Field(
        default_factory=lambda: {
            "stock": 0.015,
            "hybrid": 0.012,
            "bond": 0.006,
            "index": 0.005,
            "qdii": 0.018,
            "money": 0.003,
            "fof": 0.010,
        }
    )
    # ── Phase B 新增字段 ──
    c_class_service_fee: float = 0.004  # C类销售服务费(年化)
    c_class_redemption_fee: float = 0.005  # C类赎回费率
    ac_class_threshold_years: float = 1.5  # A/C份额选择阈值
    dividend_tax_holding_under_1y: float = 0.10  # <1年分红税
    dividend_tax_holding_over_1y: float = 0.0   # ≥1年分红税
    max_subscription_amount: Optional[float] = None  # 大额申购限制
    max_redemption_amount: Optional[float] = None   # 大额赎回限制
    custody_fee_rate: Union[Dict[str, float], Dict[str, List[Tuple[str, float]]]] = Field(
        default_factory=lambda: {
            "stock": 0.0025,
            "hybrid": 0.0020,
            "bond": 0.0015,
            "index": 0.0010,
            "qdii": 0.0030,
            "money": 0.0008,
            "fof": 0.0020,
        }
    )


class BacktestConfig(BaseModel):
    """回测配置"""
    strategy_name: str
    fund_codes: List[str]
    start_date: str  # YYYY-MM-DD
    end_date: str
    initial_capital: float = 100000.0
    rebalance_freq: str = "monthly"
    cost_model: CostModelConfig = Field(default_factory=CostModelConfig)
    subscription_discount: float = Field(default=0.10, ge=0.0, le=1.0)
    dividend_policy: str = "reinvest"  # "reinvest" or "cash"
    dividend_calendar: Dict[str, Dict[str, float]] = Field(default_factory=dict)  # {date_str: {fund_code: div_per_share}}
    nav_gap_policy: str = "forward_fill"  # forward_fill / skip / interpolate / raise
    min_nav_records_pct: float = 0.8  # 低于此比例时标记低数据质量
    qdii_fund_codes: List[str] = Field(default_factory=list)
    params: dict = Field(default_factory=dict)


class BacktestResult(BaseModel):
    """回测结果"""
    backtest_id: str
    config: BacktestConfig
    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    sortino_ratio: float = 0.0
    sharpe_ratio: float = 0.0
    calmar_ratio: float = 0.0
    information_ratio: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    total_trades: int = 0
    turnover_rate: float = 0.0
    fee_leakage: float = 0.0
    max_consecutive_loss_days: int = 0
    equity_curve: List[Dict] = Field(default_factory=list)
    trade_log: List[Dict] = Field(default_factory=list)
    period_returns: Dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
    status: str = "pending"  # pending / running / completed / failed


# ═══════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════

class NavPoint(BaseModel):
    """净值数据点"""
    fund_code: str
    date: date
    nav: float
    adjusted_nav: Optional[float] = None
    source: str = "eastmoney"


class HoldingItem(BaseModel):
    """持仓项"""
    asset_code: str
    asset_name: str = ""
    weight: float = 0.0
    market_value: Optional[float] = None


class FundHolding(BaseModel):
    """基金持仓"""
    fund_code: str
    report_period: date
    publish_date: date
    holdings: List[HoldingItem] = Field(default_factory=list)


class FundMeta(BaseModel):
    """基金元数据"""
    fund_code: str
    fund_name: str = ""
    fund_type: str = ""
    management_fee: Optional[float] = None
    custody_fee: Optional[float] = None
    subscription_fee_tiers: Optional[str] = None
    scale: Optional[float] = None  # 基金规模(元)
    rating: Optional[int] = None  # 晨星评级 1-5
    tracking_index: Optional[str] = None
    established_date: Optional[date] = None
    is_listed: bool = False


class FusionSignal(BaseModel):
    """融合后信号"""
    fund_code: str
    fund_name: str = ""
    direction: Direction
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    contributing_strategies: List[Dict] = Field(default_factory=list)
    conflict: bool = False
    override_reason: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    risk_check_passed: bool = True
    risk_warnings: List[str] = Field(default_factory=list)
