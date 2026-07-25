"""商品型（黄金）择时策略 — 动量 + COT 信号"""
from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class GoldMomentumStrategy(FundStrategyBase):
    """黄金动量择时策略: 多周期动量 + 波动率状态过滤

    商品型基金（黄金ETF/联接）的净值跟踪金价，适合动量策略。
    含 COT 信号增强（若 gold 数据可用）。
    """
    strategy_name = "gold_momentum"
    strategy_type = "timing"
    description = "黄金基金择时: 多周期动量 + COT 信号增强"
    default_params = {
        "momentum_periods": [20, 40, 60],
        "weights": [0.5, 0.3, 0.2],
        "skip_days": 3,
        "buy_threshold": 0.015,
        "sell_threshold": -0.015,
        "vol_regime_lookback": 252,
        "vol_high_percentile": 0.8,
    }
    param_ranges = {
        "buy_threshold": {"min": 0.005, "max": 0.05},
        "sell_threshold": {"min": -0.05, "max": -0.005},
        "skip_days": {"min": 0, "max": 10},
        "vol_high_percentile": {"min": 0.5, "max": 0.95},
    }
    formula_description = "黄金基金多周期动量+COT信号择时策略"
    applicable_fund_types = ["commodity"]
    min_history_days = 60

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        fund_code = self._state.get("fund_code", "")
        nav_values = self._state.get("nav_values", [])
        max_period = max(self.params["momentum_periods"]) + self.params["skip_days"]
        if len(nav_values) < max(60, max_period):
            return []

        arr = np.array(nav_values, dtype=np.float64)
        returns = np.diff(arr) / arr[:-1]
        if len(returns) < max_period:
            return []

        # 1. 多周期动量
        skip = self.params["skip_days"]
        score = 0.0
        total_w = 0.0
        for n, w in zip(self.params["momentum_periods"], self.params["weights"]):
            if len(returns) < n + skip:
                continue
            period_rets = returns[-(n + skip):-skip] if skip > 0 else returns[-n:]
            score += w * sum(period_rets)
            total_w += w

        if total_w <= 0:
            return []
        momentum_score = score / total_w

        # 2. 波动率状态过滤 — 高波动时降低仓位
        vol_regime = 0.0
        if len(returns) >= self.params["vol_regime_lookback"]:
            recent_vol = float(np.std(returns[-20:]))
            hist_vols = [float(np.std(returns[max(0, i - 20):i]))
                         for i in range(20, len(returns))]
            if hist_vols:
                pct = sum(1 for v in hist_vols if v < recent_vol) / len(hist_vols)
                vol_regime = pct  # 0=低波动, 1=高波动

        # 3. COT 信号增强（可选，若 gold 数据可用）
        cot_signal = self._try_cot_signal(fund_code)

        # 4. 合成信号
        buy_th = self.params["buy_threshold"]
        sell_th = self.params["sell_threshold"]

        # 高波动率时置信度打折
        vol_multiplier = 1.0
        if vol_regime > self.params["vol_high_percentile"]:
            vol_multiplier = 0.5

        effective_score = momentum_score
        if cot_signal is not None:
            effective_score = 0.7 * momentum_score + 0.3 * cot_signal

        if effective_score > buy_th:
            confidence = min(abs(effective_score) / (buy_th * 2), 1.0) * vol_multiplier
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.BUY,
                confidence=confidence,
                reason=(f"黄金动量 {momentum_score:.4f} "
                        f"(COT {cot_signal:.2f}" if cot_signal is not None else
                        f"黄金动量 {momentum_score:.4f}"),
            )]
        elif effective_score < sell_th:
            confidence = min(abs(effective_score) / (abs(sell_th) * 2), 1.0) * vol_multiplier
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.SELL,
                confidence=confidence,
                reason=f"黄金动量 {momentum_score:.4f} (空头)",
            )]
        return [self.emit_signal(
            SignalType.TIMING, fund_code, Direction.HOLD,
            confidence=0.5, reason=f"黄金动量 {momentum_score:.4f} 中性",
        )]

    def _try_cot_signal(self, fund_code: str) -> Optional[float]:
        """尝试从 gold 数据存储获取 COT 信号

        Returns:
            float: [-1, 1] 范围的 COT 信号，正=看多，负=看空
            None: COT 数据不可用
        """
        try:
            from backend.fund_quant.data.storage import get_gold_cot_signal
            cot = get_gold_cot_signal()
            if cot:
                return cot["signal"]
            return None
        except Exception:
            return None


StrategyRegistry.register(GoldMomentumStrategy)
