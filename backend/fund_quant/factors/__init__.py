"""Fund 域因子实现与注册。"""
from backend.core.factor.registry import FactorRegistry

from .behavioral import CalendarReturnFactor
from .concentration import HoldingConcentrationFactor
from .flow import FundFlowFactor
from .manager import ManagerTenureFactor
from .risk import MaxDrawdownFactor
from .risk_adjusted import CaptureRatioFactor, InfoRatioFactor, SharpeRatioFactor
from .structural import FeeRateFactor, FundScaleFactor


FUND_FACTORS = (
    CalendarReturnFactor,
    HoldingConcentrationFactor,
    FundFlowFactor,
    ManagerTenureFactor,
    MaxDrawdownFactor,
    CaptureRatioFactor,
    InfoRatioFactor,
    SharpeRatioFactor,
    FeeRateFactor,
    FundScaleFactor,
)


def register_fund_factors() -> None:
    """注册基金因子；重复调用安全，便于 API 和测试显式初始化。"""
    registered = {meta.name for meta in FactorRegistry.list(domain="fund")}
    for factor_cls in FUND_FACTORS:
        if factor_cls.meta.name not in registered:
            FactorRegistry.register(factor_cls, factor_cls.meta)


register_fund_factors()
