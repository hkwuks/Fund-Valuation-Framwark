from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


class AssetType(str, Enum):
    STOCK = "stock"
    FUND = "fund"
    INDEX = "index"
    BOND = "bond"


class MarketType(str, Enum):
    ON_EXCHANGE = "on_exchange"
    OFF_EXCHANGE = "off_exchange"
    UNKNOWN = "unknown"


class ValuationType(str, Enum):
    REAL_TIME_PRICE = "real_time_price"
    INDEX_BASED = "index_based"
    HOLDINGS_BASED = "holdings_based"
    HYBRID_BOND = "hybrid_bond"  # 偏债混合/二级债基
    HYBRID_QDII = "hybrid_qdii"  # 主动管理型 QDII（持仓 + 指数混合）
    BENCHMARK_ONLY = "benchmark_only"
    NOT_SUPPORTED = "not_supported"


class Holding(BaseModel):
    asset_code: str = Field(..., description="资产代码")
    asset_name: str = Field(..., description="资产名称")
    asset_type: AssetType = Field(..., description="资产类型")
    quantity: float = Field(..., description="持仓数量")
    price: Optional[float] = Field(None, description="当前价格")
    market_value: Optional[float] = Field(None, description="市值")
    weight: Optional[float] = Field(None, description="占比")


class Fund(BaseModel):
    fund_code: str = Field(..., description="基金代码")
    fund_name: str = Field(..., description="基金名称")
    fund_type: str = Field(..., description="基金类型")
    total_shares: float = Field(..., description="总份额")
    nav: Optional[float] = Field(None, description="单位净值")
    market_type: MarketType = Field(MarketType.UNKNOWN, description="市场类型(场内/场外)")
    trade_mode: Optional[str] = Field(None, description="交易时序(t0/t1/t2)")
    subscription_confirm_days: Optional[int] = Field(None, ge=0, description="申购确认所需交易日")
    redemption_confirm_days: Optional[int] = Field(None, ge=0, description="赎回确认所需交易日")
    cash_arrival_days: Optional[int] = Field(None, ge=0, description="赎回资金到账所需交易日")
    holdings: List[Holding] = Field(default_factory=list, description="持仓列表")
    estimated_nav: Optional[float] = Field(None, description="估算净值")
    last_update: Optional[datetime] = Field(None, description="最后更新时间")


class ValuationResult(BaseModel):
    fund_code: str
    fund_name: str
    valuation_type: ValuationType = Field(
        ValuationType.NOT_SUPPORTED, description="估值类型"
    )
    estimated_nav: Optional[float] = Field(None, description="估算净值")
    estimated_change_percent: Optional[float] = Field(None, description="估算涨跌幅(%)")
    previous_nav: Optional[float] = Field(None, description="昨日净值")
    latest_nav: Optional[float] = Field(None, description="最新净值")
    nav_date: Optional[str] = Field(None, description="净值日期(YYYY-MM-DD)")
    total_value: float = Field(0.0, description="总价值")
    holdings_value: Dict[str, Any] = Field(
        default_factory=dict, description="持仓贡献值"
    )
    benchmark_info: Optional[Dict[str, Any]] = Field(None, description="业绩基准信息")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="估值置信度(0-1)")
    confidence_note: Optional[str] = Field(None, description="置信度说明")
    valuation_method: Optional[str] = Field(None, description="估值方法说明")
    timestamp: datetime
    # 指数估值分位
    index_code: Optional[str] = Field(None, description="跟踪指数代码")
    index_name: Optional[str] = Field(None, description="跟踪指数名称")
    pe_value: Optional[float] = Field(None, description="指数当前市盈率")
    pe_percentile: Optional[float] = Field(None, description="指数市盈率历史分位(0-100)")
    pb_value: Optional[float] = Field(None, description="指数当前市净率")
    pb_percentile: Optional[float] = Field(None, description="指数市净率历史分位(0-100)")
    # 定投信号
    valuation_signal: Optional[str] = Field(None, description="定投信号：深度低估/低估/合理/偏高/高估")
    signal_action: Optional[str] = Field(None, description="建议操作：加倍定投/正常定投/持有/止盈/卖出")
    signal_source: Optional[str] = Field(None, description="信号数据来源：PE分位/债券收益率/价格分位/黄金价格")
    # 场内折溢价
    iopv: Optional[float] = Field(None, description="场内ETF IOPV 实时估值")
    premium_percent: Optional[float] = Field(None, description="场内ETF折溢价率(%，正=溢价)")


class MarketData(BaseModel):
    code: str
    name: str
    price: float
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[float] = None
    timestamp: datetime


class FundData(BaseModel):
    fund_code: str = Field(..., description="基金代码")
    fund_name: str = Field(..., description="基金名称")
    fund_type: str = Field(..., description="基金类型")
    nav: Optional[float] = Field(None, description="单位净值")
    nav_date: Optional[str] = Field(None, description="净值日期(YYYY-MM-DD)")
    previous_nav: Optional[float] = Field(None, description="昨日净值")
    establish_date: Optional[str] = Field(None, description="成立日期")
    market_type: MarketType = Field(
        MarketType.UNKNOWN, description="市场类型(场内/场外)"
    )
    benchmark: Optional[str] = Field(None, description="业绩比较基准")
    tracking_index: Optional[str] = Field(None, description="跟踪指数代码")
    price: Optional[float] = Field(None, description="当前价格")
    change: Optional[float] = Field(None, description="涨跌额")
    change_percent: Optional[float] = Field(None, description="涨跌幅(%)")
    volume: Optional[float] = Field(None, description="成交量")
    timestamp: Optional[datetime] = Field(None, description="数据时间戳")


class FundListResponse(BaseModel):
    funds: List[Fund]
    total: int


class ValuationRequest(BaseModel):
    fund_codes: List[str] = Field(..., description="基金代码列表")
