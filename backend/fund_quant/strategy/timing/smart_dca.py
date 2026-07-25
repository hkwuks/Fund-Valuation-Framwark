"""智能定投策略 — 估值偏差动态调仓 + 止盈检查"""

from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class SmartDcaStrategy(FundStrategyBase):
    """智能定投策略: 基础定投 + 估值偏差调仓 + 止盈"""
    strategy_name = "smart_dca"
    strategy_type = "timing"
    description = "基于估值偏差动态调整定投金额的智能定投策略"
    default_params = {
        "base_amount": 1000.0,
        "invest_freq": "weekly",
        "z_max": 3.0,
        "profit_take_threshold": 0.30,
        "profit_take_ratio": 0.5,
    }
    param_ranges = {
        "z_max": {"min": 1.0, "max": 5.0},
        "profit_take_threshold": {"min": 0.1, "max": 0.5},
        "profit_take_ratio": {"min": 0.1, "max": 1.0},
    }
    formula_description = "基于估值偏差动态调整定投金额的智能定投策略"
    applicable_fund_types = []
    min_history_days = 60

    def __init__(self, params: Optional[dict] = None):
        super().__init__(params)
        self._total_cost = 0.0
        self._total_shares = 0.0

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        """执行智能定投评估"""
        fund_code = self._state.get("fund_code", "")
        nav_values = self._state.get("nav_values", [])
        if len(nav_values) < 20:
            return []

        arr = np.array(nav_values, dtype=np.float64)
        returns = np.diff(arr) / arr[:-1]

        # 计算估值偏差z-score (复用估值偏差逻辑)
        lookback = min(60, len(returns))
        window = returns[-lookback:]
        z_score = (window[-1] - np.mean(window)) / max(np.std(window, ddof=1), 1e-8)

        # 调仓系数
        z_max = self.params["z_max"]
        base = self.params["base_amount"]

        z_score_clipped = np.clip(z_score, -z_max, z_max)
        adjustment = max(0.0, 1.0 - z_score_clipped / z_max)

        actual_amount = base * adjustment

        # 特殊区间
        if z_score < -1.5:
            actual_amount = base * 1.5  # 加倍定投
        elif z_score > 1.5:
            actual_amount = 0.0  # 暂停定投

        # 止盈检查
        latest_nav = float(arr[-1])
        current_value = self._total_shares * latest_nav
        cumulative_return = (current_value - self._total_cost) / max(self._total_cost, 1.0)

        profit_threshold = self.params["profit_take_threshold"]
        profit_ratio = self.params["profit_take_ratio"]

        signals = []

        if cumulative_return > profit_threshold and self._total_shares > 0:
            # 触发止盈
            signals.append(self.emit_signal(
                SignalType.TIMING, fund_code, Direction.REBALANCE,
                confidence=0.8,
                reason=f"累计收益 {cumulative_return:.1%} > 止盈阈值 {profit_threshold:.0%}, "
                       f"建议赎回 {profit_ratio:.0%}",
                suggested_pct=-profit_ratio,
            ))

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
