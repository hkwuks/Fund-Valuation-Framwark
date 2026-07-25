"""债券信用利差择时策略 — 信用利差 + 收益率曲线"""
from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class CreditSpreadStrategy(FundStrategyBase):
    """信用利差择时策略: 信用利差动量 + 收益率曲线斜率

    信用利差走阔 → 信用债承压 → 卖出
    信用利差收窄 → 信用债回暖 → 买入
    收益率曲线陡峭化 → 减久期；平坦化 → 加久期
    """
    strategy_name = "credit_spread"
    strategy_type = "timing"
    description = "基于信用利差和收益率曲线的债券择时策略"
    default_params = {
        "spread_lookback": 20,
        "spread_buy_threshold": -0.02,   # 利差收窄超过 2% 触发买入
        "spread_sell_threshold": 0.02,   # 利差走阔超过 2% 触发卖出
        "curve_lookback": 20,
        "curve_buy_threshold": -0.05,    # 曲线平坦化超过 5% → 加久期
        "curve_sell_threshold": 0.05,    # 曲线陡峭化超过 5% → 减久期
    }
    param_ranges = {
        "spread_lookback": {"min": 10, "max": 60},
        "spread_buy_threshold": {"min": -0.1, "max": 0},
        "spread_sell_threshold": {"min": 0, "max": 0.1},
        "curve_lookback": {"min": 10, "max": 60},
    }
    formula_description = "基于信用利差和收益率曲线的债券择时策略"
    applicable_fund_types = ["bond"]
    min_history_days = 20

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        fund_code = self._state.get("fund_code", "")
        spread_data = self._state.get("credit_spread_history", None)
        curve_data = self._state.get("yield_curve_history", None)

        if not spread_data and not curve_data:
            # ponytail: 无利差数据时 return HOLD，等待数据源接入
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.HOLD,
                confidence=0.5, reason="信用利差数据未就绪",
            )]

        signal = 0.0
        reasons = []

        # 1. 信用利差信号
        if spread_data and len(spread_data) >= self.params["spread_lookback"]:
            spread_arr = np.array(spread_data[-self.params["spread_lookback"]:], dtype=np.float64)
            spread_change = (spread_arr[-1] - spread_arr[0]) / max(abs(spread_arr[0]), 1e-6)
            if spread_change < self.params["spread_buy_threshold"]:
                signal += 1.0  # 利差收窄 → 买入
                reasons.append(f"利差收窄 {spread_change:.2%}")
            elif spread_change > self.params["spread_sell_threshold"]:
                signal -= 1.0  # 利差走阔 → 卖出
                reasons.append(f"利差走阔 {spread_change:.2%}")
            else:
                reasons.append(f"利差中性 {spread_change:.2%}")

        # 2. 收益率曲线信号
        if curve_data and len(curve_data) >= self.params["curve_lookback"]:
            curve_arr = np.array(curve_data[-self.params["curve_lookback"]:], dtype=np.float64)
            curve_change = (curve_arr[-1] - curve_arr[0]) / max(abs(curve_arr[0]), 1e-6)
            if curve_change < self.params["curve_buy_threshold"]:
                signal += 0.8  # 曲线平坦化 → 加久期 → 买入
                reasons.append(f"曲线平坦化 {curve_change:.2%}")
            elif curve_change > self.params["curve_sell_threshold"]:
                signal -= 0.8  # 曲线陡峭化 → 减久期 → 卖出
                reasons.append(f"曲线陡峭化 {curve_change:.2%}")
            else:
                reasons.append(f"曲线中性 {curve_change:.2%}")

        confidence = min(abs(signal) / 2.0, 1.0)

        if signal > 0.5:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.BUY,
                confidence=confidence,
                reason="; ".join(reasons),
            )]
        elif signal < -0.5:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.SELL,
                confidence=confidence,
                reason="; ".join(reasons),
            )]
        return [self.emit_signal(
            SignalType.TIMING, fund_code, Direction.HOLD,
            confidence=confidence,
            reason="; ".join(reasons),
        )]


StrategyRegistry.register(CreditSpreadStrategy)
