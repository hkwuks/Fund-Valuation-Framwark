"""智能定投策略 — 估值偏差动态调仓 + 止盈检查"""

from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class SmartDcaStrategy(FundStrategyBase):
    """智能定投策略: 基础定投 + 估值偏差调仓"""
    strategy_name = "smart_dca"
    strategy_type = "timing"
    description = "基于估值偏差动态调整定投金额的智能定投策略"
    default_params = {
        "base_amount": 1000.0,
        "invest_freq": "weekly",
        "z_max": 3.0,
    }
    param_ranges = {
        "z_max": {"min": 1.0, "max": 5.0},
    }
    formula_description = "基于估值偏差动态调整定投金额的智能定投策略"
    applicable_fund_types = []
    min_history_days = 60

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        """执行智能定投评估"""
        fund_code = self._state.get("fund_code", "")
        nav_values = self._state.get("nav_values", [])
        if len(nav_values) < 60:
            return []

        arr = np.array(nav_values, dtype=np.float64)

        # 估值偏差 z-score：净值偏离历史均值（同 valuation_deviation 语义）
        lookback = min(120, len(arr))
        window = arr[-lookback:]
        mu = np.mean(window)
        sigma = np.std(window, ddof=1)
        if sigma < 1e-10:
            return []
        z_score = (arr[-1] - mu) / sigma

        # 调仓系数
        z_max = self.params["z_max"]
        base = self.params["base_amount"]

        z_score_clipped = np.clip(z_score, -z_max, z_max)
        adjustment = max(0.0, 1.0 - z_score_clipped / z_max)

        actual_amount = base * adjustment

        # 特殊区间
        if z_score < -1.5:
            actual_amount = base * 1.5  # 低估加倍定投
        elif z_score > 1.5:
            actual_amount = 0.0  # 高估暂停定投

        signals = []

        if actual_amount > 0:
            signals.append(self.emit_signal(
                SignalType.TIMING, fund_code, Direction.BUY,
                confidence=0.7,
                reason=f"定投金额 ¥{actual_amount:.0f} (z-score={z_score:.2f})",
                suggested_amount=actual_amount,
            ))
        elif z_score <= 1.5:
            signals.append(self.emit_signal(
                SignalType.TIMING, fund_code, Direction.HOLD,
                confidence=0.6,
                reason=f"估值偏高 (z={z_score:.2f}), 暂停定投",
            ))

        return signals


StrategyRegistry.register(SmartDcaStrategy)
