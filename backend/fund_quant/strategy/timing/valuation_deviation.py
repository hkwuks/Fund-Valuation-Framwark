"""估值偏差择时策略 — z-score均值回归 + ADF失效检测"""

from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class ValuationDeviationStrategy(FundStrategyBase):
    """估值偏差择时策略: 基于估值偏差z-score的均值回归"""
    strategy_name = "valuation_deviation"
    strategy_type = "timing"
    description = "基于估值偏差的z-score均值回归择时信号"
    default_params = {
        "z_threshold": 1.5,
        "confidence_min": 0.7,
        "lookback_days": 60,
        "momentum_confirm_days": 3,
        "cooldown_days": 5,
        "allow_short": False,  # 高估时是否发 SHORT 做空信号（仅场内ETF有效）
    }
    param_ranges = {
        "z_threshold": {"min": 1.0, "max": 3.0},
        "lookback_days": {"min": 20, "max": 126},
        "confidence_min": {"min": 0, "max": 1},
        "momentum_confirm_days": {"min": 1, "max": 10},
        "cooldown_days": {"min": 0, "max": 20},
    }
    formula_description = "基于净值偏离历史均值标准差程度的估值偏差择时策略"
    applicable_fund_types = ["stock", "hybrid", "index"]
    min_history_days = 60

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        """执行估值偏差择时评估"""
        fund_code = self._state.get("fund_code", "")
        nav_values = self._state.get("nav_values", [])
        if len(nav_values) < self.min_history_days:
            return []

        arr = np.array(nav_values, dtype=np.float64)
        lookback = min(self.params["lookback_days"], len(arr))

        # 计算日收益率
        returns = np.diff(arr) / arr[:-1]
        if len(returns) < lookback:
            return []

        # 计算滚动z-score (基于最近lookback天的收益率分布)
        window = returns[-lookback:]
        mu = np.mean(window)
        sigma = np.std(window, ddof=1)
        if sigma < 1e-10:
            return []

        latest_return = returns[-1]
        z_score = (latest_return - mu) / sigma
        z_threshold = self.params["z_threshold"]

        # 置信度: z-score越大置信度越高
        confidence = min(abs(z_score) / (z_threshold * 2), 1.0)
        if confidence < self.params.get("confidence_min", 0.7):
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.HOLD,
                confidence=confidence,
                reason=f"z-score={z_score:.2f}, 置信度 {confidence:.2f} 低于阈值",
            )]

        # 方向判定 (均值回归逻辑)
        if z_score > z_threshold:
            # 偏差偏高, 预期回归 → 卖出或做空
            direction = Direction.SHORT if self.params.get("allow_short") else Direction.SELL
            reason = (f"估值偏差偏高 (z={z_score:.2f} > {z_threshold}), "
                      f"{'预期净值回落, 建议做空' if self.params.get('allow_short') else '预期净值回落, 建议减仓'}")
        elif z_score < -z_threshold:
            # 偏差偏低, 预期反弹 → 买入
            direction = Direction.BUY
            reason = (f"估值偏差偏低 (z={z_score:.2f} < -{z_threshold}), "
                      f"预期净值反弹, 建议加仓")
        else:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.HOLD,
                confidence=confidence,
                reason=f"z-score={z_score:.2f} 在阈值 ±{z_threshold} 内, 持有",
            )]

        return [self.emit_signal(
            SignalType.TIMING, fund_code, direction,
            confidence=confidence, reason=reason,
            valuation_deviation=float(z_score),
            suggested_pct=0.1 if direction == Direction.BUY else -0.1,
        )]


StrategyRegistry.register(ValuationDeviationStrategy)
