"""商品型（黄金）择时策略 — 中长期动量 + 均值回归修正 + 波动率状态过滤"""
from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class GoldMomentumStrategy(FundStrategyBase):
    """黄金动量择时策略: 中长期动量 + 极端偏离修正 + 波动率状态过滤

    商品型基金（黄金ETF/联接）的净值跟踪金价。
    - 动量用 60/120/250 日（3/6/12月），匹配黄金主趋势周期
    - skip_days=15 过滤短期反转噪音
    - 偏离 120 日均线超 2σ 时给均值回归修正（超卖看多 / 超买看空）
    - 高波动率状态降置信度
    """
    strategy_name = "gold_momentum"
    strategy_type = "timing"
    description = "黄金基金择时: 中长期动量 + 超卖/超买修正 + 波动率过滤"
    default_params = {
        "momentum_periods": [60, 120, 250],
        "weights": [0.4, 0.35, 0.25],
        "skip_days": 15,
        "buy_threshold": 0.03,
        "sell_threshold": -0.03,
        "vol_regime_lookback": 252,
        "vol_high_percentile": 0.8,
        "mean_reversion_lookback": 120,
        "z_threshold": 2.0,
        "mr_weight": 0.3,
    }
    param_ranges = {
        "buy_threshold": {"min": 0.01, "max": 0.1},
        "sell_threshold": {"min": -0.1, "max": -0.01},
        "skip_days": {"min": 0, "max": 30},
        "vol_high_percentile": {"min": 0.5, "max": 0.95},
        "z_threshold": {"min": 1.0, "max": 3.0},
        "mr_weight": {"min": 0, "max": 0.5},
    }
    formula_description = "黄金基金中长期动量(60/120/250日) + 均值回归修正择时策略"
    applicable_fund_types = ["commodity"]
    min_history_days = 270

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        fund_code = self._state.get("fund_code", "")
        nav_values = self._state.get("nav_values", [])
        max_period = max(self.params["momentum_periods"]) + self.params["skip_days"]
        if len(nav_values) < max(self.min_history_days, max_period):
            return []

        arr = np.array(nav_values, dtype=np.float64)
        returns = np.diff(arr) / arr[:-1]
        if len(returns) < max_period:
            return []

        # 1. 多周期动量（跳过最近 skip 天，过滤短期噪音）
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

        # 2. 均值回归修正：偏离均线超 2σ 时反向（动量跌过头/涨过头）
        mr_score = 0.0
        z_dev = 0.0
        mr_lookback = self.params["mean_reversion_lookback"]
        if len(arr) >= mr_lookback:
            window = arr[-mr_lookback:]
            mu = float(np.mean(window))
            sigma = float(np.std(window, ddof=1))
            if sigma > 1e-10:
                z_dev = (arr[-1] - mu) / sigma
                z_th = self.params["z_threshold"]
                mr_w = self.params["mr_weight"]
                if z_dev < -z_th:
                    # 超卖 → 看多修正（最高 +mr_w）
                    mr_score = min((-z_dev - z_th) / z_th, 1.0) * mr_w
                elif z_dev > z_th:
                    # 超买 → 看空修正（最高 -mr_w）
                    mr_score = -min((z_dev - z_th) / z_th, 1.0) * mr_w

        # 3. 波动率状态过滤 — 高波动时降低置信度
        vol_regime = 0.0
        if len(returns) >= self.params["vol_regime_lookback"]:
            recent_vol = float(np.std(returns[-20:]))
            hist_vols = [float(np.std(returns[max(0, i - 20):i]))
                         for i in range(20, len(returns))]
            if hist_vols:
                pct = sum(1 for v in hist_vols if v < recent_vol) / len(hist_vols)
                vol_regime = pct  # 0=低波动, 1=高波动

        # 4. 合成信号：动量为主(1.0)，极端偏离只做修正
        buy_th = self.params["buy_threshold"]
        sell_th = self.params["sell_threshold"]

        # 高波动率时置信度打折
        vol_multiplier = 1.0
        if vol_regime > self.params["vol_high_percentile"]:
            vol_multiplier = 0.5

        effective_score = momentum_score + mr_score

        mr_reason = ""
        if mr_score > 0:
            mr_reason = f", 超卖修正 z={z_dev:.2f}"
        elif mr_score < 0:
            mr_reason = f", 超买修正 z={z_dev:.2f}"

        if effective_score > buy_th:
            confidence = min(abs(effective_score) / (buy_th * 2), 1.0) * vol_multiplier
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.BUY,
                confidence=confidence,
                reason=f"黄金动量 {momentum_score:.4f} 超买阈值，建议加仓{mr_reason}",
            )]
        elif effective_score < sell_th:
            confidence = min(abs(effective_score) / (abs(sell_th) * 2), 1.0) * vol_multiplier
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.SELL,
                confidence=confidence,
                reason=f"黄金动量 {momentum_score:.4f} 跌破卖出阈值，建议减仓{mr_reason}",
            )]
        return [self.emit_signal(
            SignalType.TIMING, fund_code, Direction.HOLD,
            confidence=0.5, reason=f"黄金动量 {momentum_score:.4f} 中性{mr_reason}",
        )]


StrategyRegistry.register(GoldMomentumStrategy)
