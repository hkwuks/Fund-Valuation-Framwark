"""动量择时策略 — TSMOM多周期融合 + 反转修正"""

from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class MomentumStrategy(FundStrategyBase):
    """动量择时策略: TSMOM多周期融合 + 短期反转修正"""
    strategy_name = "momentum"
    strategy_type = "timing"
    description = "基于时间序列动量(TSMOM)的择时策略, 支持多周期加权融合"
    default_params = {
        "momentum_periods": [20, 60, 120],
        "weights": [0.5, 0.3, 0.2],
        "skip_days": 5,
        "buy_threshold": 0.02,
        "sell_threshold": -0.02,
        "reversal_threshold": 0.03,
    }
    param_ranges = {
        "buy_threshold": {"min": 0.01, "max": 0.1},
        "skip_days": {"min": 0, "max": 10},
        "sell_threshold": {"min": -0.1, "max": -0.005},
        "reversal_threshold": {"min": 0.01, "max": 0.1},
    }
    formula_description = "基于时间序列动量(TSMOM)多周期加权+反转修正的择时策略"
    applicable_fund_types = ["stock", "hybrid", "index"]
    min_history_days = 120

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        """执行动量择时评估"""
        fund_code = self._state.get("fund_code", "")
        nav_values = self._state.get("nav_values", [])
        if len(nav_values) < max(self.params["momentum_periods"]) + self.params["skip_days"]:
            return []

        arr = np.array(nav_values, dtype=np.float64)
        returns = np.diff(arr) / arr[:-1]

        if len(returns) < max(self.params["momentum_periods"]):
            return []

        # 1. 计算各周期TSMOM (含skip period)
        skip = self.params["skip_days"]
        periods = self.params["momentum_periods"]
        weights = self.params["weights"]

        momentum_scores = []
        for i, n in enumerate(periods):
            if len(returns) < n + skip:
                momentum_scores.append(0.0)
                continue
            # TSMOM(n) = 累计收益 (跳过最近skip_days天)
            period_returns = returns[-(n + skip):-skip] if skip > 0 else returns[-n:]
            cumulative_return = float(np.sum(period_returns))
            # Winsorize极端值 (滚动MAD截断)
            momentum_scores.append(cumulative_return)

        # 2. 多周期加权融合
        total_weight = sum(weights[:len(momentum_scores)])
        if total_weight <= 0:
            return []

        weighted_score = sum(
            w * s for w, s in zip(weights, momentum_scores)
        ) / total_weight

        # 3. 短期反转修正
        if skip > 0 and len(returns) > 20:
            short_term = float(np.sum(returns[-20:]))
            long_term = sum(momentum_scores) / max(len(momentum_scores), 1)
            reversal = short_term - long_term
            reversal_threshold = self.params["reversal_threshold"]

            if weighted_score > 0 and reversal < -reversal_threshold:
                # 长期向上但短期已反转: 动量衰减
                weighted_score *= 0.5  # 减半置信度
                reason_suffix = f" (短期反转信号: {reversal:.4f})"
            else:
                reason_suffix = ""

        # 4. 信号判定
        buy_threshold = self.params["buy_threshold"]
        sell_threshold = self.params["sell_threshold"]

        confidence = min(abs(weighted_score) / (buy_threshold * 2), 1.0)

        if weighted_score > buy_threshold:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.BUY,
                confidence=confidence,
                reason=f"动量得分 {weighted_score:.4f} > 买入阈值 {buy_threshold}, 建议加仓{reason_suffix}",
                suggested_pct=min(weighted_score / buy_threshold * 0.15, 0.3),
            )]
        elif weighted_score < sell_threshold:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.SELL,
                confidence=confidence,
                reason=f"动量得分 {weighted_score:.4f} < 卖出阈值 {sell_threshold}, 建议减仓{reason_suffix}",
                suggested_pct=max(weighted_score / sell_threshold * -0.15, -0.3),
            )]
        else:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.HOLD,
                confidence=confidence,
                reason=f"动量得分 {weighted_score:.4f} 在阈值区间内, 持有{reason_suffix}",
            )]


StrategyRegistry.register(MomentumStrategy)
