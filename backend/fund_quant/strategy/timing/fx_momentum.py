"""汇率动量择时策略 — QDII专属, 多币种加权"""

from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet

# 标准汇率权重配置 (可按基金投资地域调整)
CURRENCY_WEIGHTS = {
    "USDCNY": {"name": "美元/人民币", "default_weight": 0.5},
    "EURCNY": {"name": "欧元/人民币", "default_weight": 0.2},
    "JPYCNY": {"name": "日元/人民币", "default_weight": 0.15},
    "HKDDCNY": {"name": "港币/人民币", "default_weight": 0.15},
}


class FxMomentumStrategy(FundStrategyBase):
    """汇率动量择时策略: 多币种加权动量"""
    strategy_name = "fx_momentum"
    strategy_type = "timing"
    description = "基于多币种汇率动量的QDII仓位调整策略"
    default_params = {
        "lookback_days": 20,
        "momentum_threshold": 0.02,
        "fx_vol_alert": 0.05,
    }
    param_ranges = {
        "lookback_days": {"min": 10, "max": 60},
        "momentum_threshold": {"min": 0.005, "max": 0.1},
        "fx_vol_alert": {"min": 0.02, "max": 0.15},
    }
    formula_description = "基于多币种汇率动量的QDII仓位调整策略"
    applicable_fund_types = ["qdii"]
    min_history_days = 60

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        """执行汇率动量择时评估"""
        fund_code = self._state.get("fund_code", "")

        # 从state获取汇率数据 (由外部注入)
        fx_rates = self._state.get("fx_rates_history", {})
        if not fx_rates:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.HOLD,
                confidence=0.0,
                reason="汇率数据不可用, 持有",
            )]

        lookback = self.params["lookback_days"]
        threshold = self.params["momentum_threshold"]

        # 计算多币种加权动量
        fx_momentum_total = 0.0
        total_weight = 0.0

        for currency, config in CURRENCY_WEIGHTS.items():
            history = fx_rates.get(currency, [])
            if len(history) < lookback:
                continue

            rates = np.array(history[-lookback:], dtype=np.float64)
            momentum = (rates[-1] - rates[0]) / max(rates[0], 1e-6)
            weight = config["default_weight"]
            fx_momentum_total += weight * momentum
            total_weight += weight

        if total_weight <= 0:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.HOLD,
                confidence=0.0, reason="无有效汇率数据",
            )]

        fx_momentum = fx_momentum_total / total_weight

        # 汇率波动率风控
        fx_vol = 0.0
        vol_count = 0
        for currency, config in CURRENCY_WEIGHTS.items():
            history = fx_rates.get(currency, [])
            if len(history) >= 30:
                rates = np.array(history[-30:], dtype=np.float64)
                changes = np.diff(rates) / rates[:-1]
                fx_vol += np.std(changes) * config["default_weight"]
                vol_count += 1

        avg_vol = fx_vol / vol_count if vol_count > 0 else 0.0
        vol_alert = avg_vol > self.params["fx_vol_alert"]

        # 置信度
        confidence = min(abs(fx_momentum) / (threshold * 2), 1.0)
        if vol_alert:
            confidence *= 0.5  # 高波动降置信度

        reason_base = f"汇率动量 {fx_momentum:.2%}"
        if vol_alert:
            reason_base += f" (波动率预警 {avg_vol:.2%} > {self.params['fx_vol_alert']:.0%})"

        if fx_momentum > threshold and not vol_alert:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.BUY,
                confidence=confidence,
                reason=f"{reason_base}, 人民币贬值趋势, QDII受益, 建议加仓",
                suggested_pct=0.15,
            )]
        elif fx_momentum < -threshold and not vol_alert:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.SELL,
                confidence=confidence,
                reason=f"{reason_base}, 人民币升值趋势, QDII受损, 建议减仓",
                suggested_pct=-0.15,
            )]
        else:
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.HOLD,
                confidence=confidence,
                reason=f"{reason_base}, 在阈值内, 持有",
            )]


StrategyRegistry.register(FxMomentumStrategy)
