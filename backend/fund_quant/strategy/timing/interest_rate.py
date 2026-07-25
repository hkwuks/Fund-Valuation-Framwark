"""利率敏感度择时策略 — 债券型专属, 久期识别 + 动态阈值"""

from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class InterestRateStrategy(FundStrategyBase):
    """利率敏感度择时策略: 基于国债收益率变化调整久期"""
    strategy_name = "interest_rate"
    strategy_type = "timing"
    description = "基于国债收益率变化的债券型基金择时策略, 含久期识别"
    default_params = {
        "lookback_days": 20,
        "momentum_threshold": 0.05,
        "yield_source": "10y_cgb",
    }
    param_ranges = {
        "lookback_days": {"min": 5, "max": 60},
        "momentum_threshold": {"min": 0.01, "max": 0.2},
    }
    formula_description = "基于国债收益率变化的债券型基金久期调整策略"
    applicable_fund_types = ["bond"]
    min_history_days = 60

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        """执行利率敏感度择时评估"""
        fund_code = self._state.get("fund_code", "")
        nav_values = self._state.get("nav_values", [])

        if len(nav_values) < self.params["lookback_days"] + 10:
            return []

        arr = np.array(nav_values, dtype=np.float64)
        returns = np.diff(arr) / arr[:-1]

        if len(returns) < self.params["lookback_days"]:
            return []

        # 尝试从state获取国债收益率数据
        yield_data = self._state.get("yield_10y_history", None)
        lookback = self.params["lookback_days"]

        if yield_data and len(yield_data) >= lookback:
            yields = np.array(yield_data[-lookback:], dtype=np.float64)
            # 利率变化动量
            rate_momentum = (yields[-1] - yields[0]) / max(yields[0], 1e-6)
        else:
            # 无收益率数据时用净值反向推断 (简化)
            bond_returns = returns[-lookback:]
            rate_momentum = -float(np.mean(bond_returns)) * lookback  # 利率↑净值↓

        threshold = self.params["momentum_threshold"]

        # 置信度
        confidence = min(abs(rate_momentum) / (threshold * 2), 1.0)

        if rate_momentum > threshold:
            # 利率上行 → 减仓长久期
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.SELL,
                confidence=confidence,
                reason=f"利率上行 (动量={rate_momentum:.2%}), 建议缩短久期",
                suggested_pct=-0.1,
            )]
        elif rate_momentum < -threshold:
            # 利率下行 → 加仓长久期
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.BUY,
                confidence=confidence,
                reason=f"利率下行 (动量={rate_momentum:.2%}), 建议延长久期",
                suggested_pct=0.1,
            )]
        else:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.HOLD,
                confidence=confidence,
                reason=f"利率变化动量 {rate_momentum:.2%} 在阈值内, 持有",
            )]


StrategyRegistry.register(InterestRateStrategy)
