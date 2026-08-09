"""黄金均值回归择时策略 — 布林带 + RSI

借鉴 gold 系统 MeanReversionStrategy 的逻辑（布林带下轨+RSI超卖做多 / 上轨+RSI超买做空），
适配到基金净值：
  - 接口从 on_bar 事件驱动改为 on_evaluate 评估驱动（一次性给 NAV 数组）
  - 只有日频 NAV（≈close），无 high/low/volume，因此去掉 ATR 止损与 Donchian
  - 布林带周期 20 → 60：基金 T+1 交易、日频净值，20 日带在净值上噪音过大
  - 方向语义 LONG/SHORT → BUY/SELL（SELL 表示减仓，不做空）
"""
from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class GoldReversionStrategy(FundStrategyBase):
    """黄金均值回归择时: 布林带下轨+RSI超卖买入 / 上轨+RSI超买减仓"""

    strategy_name = "gold_reversion"
    strategy_type = "timing"
    description = "黄金基金均值回归: 布林带(60日)+RSI超卖/超买"
    default_params = {
        "boll_period": 60,
        "boll_dev": 2.0,
        "rsi_period": 14,
        "rsi_overbought": 70,
        "rsi_oversold": 30,
        "confidence_min": 0.5,
        "confidence_max": 0.9,
    }
    param_ranges = {
        "boll_period": {"min": 20, "max": 120},
        "boll_dev": {"min": 1.0, "max": 3.0},
        "rsi_overbought": {"min": 60, "max": 85},
        "rsi_oversold": {"min": 15, "max": 40},
    }
    formula_description = "黄金基金布林带+RSI均值回归择时策略"
    applicable_fund_types = ["commodity"]
    min_history_days = 120

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        fund_code = self._state.get("fund_code", "")
        nav_values = self._state.get("nav_values", [])
        if len(nav_values) < self.min_history_days:
            return []

        arr = np.array(nav_values, dtype=np.float64)
        if len(arr) < self.params["boll_period"]:
            return []

        bb_upper, bb_middle, bb_lower = self._calc_bollinger(arr)
        rsi = self._calc_rsi(arr)
        if bb_upper is None or rsi is None:
            return []

        price = float(arr[-1])
        band = max(bb_middle - bb_lower, 1e-9)  # 布林带宽（半宽）

        # 超卖: 价格跌破下轨 且 RSI 低于阈值 → 买入
        if price <= bb_lower and rsi < self.params["rsi_oversold"]:
            penetration = max((bb_lower - price) / band, 0.0)   # 穿透下轨深度
            rsi_depth = (self.params["rsi_oversold"] - rsi) / self.params["rsi_oversold"]
            confidence = min(self.params["confidence_max"],
                             self.params["confidence_min"] + 0.4 * max(penetration, rsi_depth))
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.BUY,
                confidence=confidence,
                reason=(f"布林下轨+RSI超卖: 价 {price:.3f} < 下轨 {bb_lower:.3f}, "
                        f"RSI={rsi:.1f} < {self.params['rsi_oversold']}, 预期反弹"),
                suggested_pct=0.1,
            )]

        # 超买: 价格突破上轨 且 RSI 高于阈值 → 减仓
        if price >= bb_upper and rsi > self.params["rsi_overbought"]:
            penetration = max((price - bb_upper) / band, 0.0)
            rsi_depth = (rsi - self.params["rsi_overbought"]) / (100 - self.params["rsi_overbought"])
            confidence = min(self.params["confidence_max"],
                             self.params["confidence_min"] + 0.4 * max(penetration, rsi_depth))
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.SELL,
                confidence=confidence,
                reason=(f"布林上轨+RSI超买: 价 {price:.3f} > 上轨 {bb_upper:.3f}, "
                        f"RSI={rsi:.1f} > {self.params['rsi_overbought']}, 预期回落"),
                suggested_pct=-0.1,
            )]

        return [self.emit_signal(
            SignalType.TIMING, fund_code, Direction.HOLD,
            confidence=0.5,
            reason=f"布林带内, RSI={rsi:.1f}, 持有",
        )]

    def _calc_bollinger(self, arr: np.ndarray):
        """布林带: 中轨=均值, 上下轨=均值±boll_dev×标准差"""
        recent = arr[-self.params["boll_period"]:]
        middle = float(np.mean(recent))
        std = float(np.std(recent, ddof=1))
        if std < 1e-10:
            return None, None, None
        dev = self.params["boll_dev"] * std
        return middle + dev, middle, middle - dev

    def _calc_rsi(self, arr: np.ndarray) -> Optional[float]:
        """Wilder 简化 RSI（按周期均值）"""
        returns = np.diff(arr) / arr[:-1]
        if len(returns) < self.params["rsi_period"] + 1:
            return None
        gains = np.where(returns > 0, returns, 0.0)
        losses = np.where(returns < 0, -returns, 0.0)
        avg_gain = float(np.mean(gains[-self.params["rsi_period"]:]))
        avg_loss = float(np.mean(losses[-self.params["rsi_period"]:]))
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)


StrategyRegistry.register(GoldReversionStrategy)
