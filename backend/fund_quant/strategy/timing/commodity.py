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
    description = "黄金基金择时: 中长期动量 + 宏观覆层 + 超卖/超买修正 + 波动率过滤"
    default_params = {
        "momentum_periods": [60, 120, 250],
        "weights": [0.4, 0.35, 0.25],
        "skip_days": 15,
        "buy_threshold": 0.03,
        "sell_threshold": -0.03,
        "vol_regime_lookback": 252,
        "vol_high_percentile": 0.8,
        "mean_reversion_lookback": 120,
        "deviation_threshold": 0.08,
        "mr_weight": 0.3,
        "macro_lookback_days": 20,
        "macro_weight": 0.3,
    }
    param_ranges = {
        "buy_threshold": {"min": 0.01, "max": 0.1},
        "sell_threshold": {"min": -0.1, "max": -0.01},
        "skip_days": {"min": 0, "max": 30},
        "vol_high_percentile": {"min": 0.5, "max": 0.95},
        "deviation_threshold": {"min": 0.03, "max": 0.2},
        "mr_weight": {"min": 0, "max": 0.5},
        "macro_lookback_days": {"min": 5, "max": 60},
        "macro_weight": {"min": 0, "max": 0.5},
    }
    formula_description = "黄金基金中长期动量(60/120/250日) + 宏观覆层(DXY/US10Y/VIX) + 均值回归修正择时策略"
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

        # 2. 均值回归修正：偏离 120 日均线超阈值时反向（超卖看多 / 超买看空）
        # 用百分比偏离而非 z-score：黄金 z-score 分布右偏（长期上涨），
        # 超卖方向需要极大偏离才到 -2σ，导致超卖修正永不触发
        mr_score = 0.0
        dev = 0.0
        mr_lookback = self.params["mean_reversion_lookback"]
        if len(arr) >= mr_lookback:
            mu = float(np.mean(arr[-mr_lookback:]))
            if mu > 0:
                dev = arr[-1] / mu - 1.0
                th = self.params["deviation_threshold"]
                mr_w = self.params["mr_weight"]
                if dev < -th:
                    # 超卖（低于均线 8%+）→ 看多修正（最高 +mr_w）
                    mr_score = min((-dev - th) / th, 1.0) * mr_w
                elif dev > th:
                    # 超买（高于均线 8%+）→ 看空修正（最高 -mr_w）
                    mr_score = -min((dev - th) / th, 1.0) * mr_w

        # 3. 宏观覆层：复用 gold 系统的 DXY/US10Y/VIX 数据
        #    DXY 上升 → 美元走强 → 黄金承压（逆相关）
        #    US10Y 上升 → 利率上行 → 黄金承压（逆相关）
        #    VIX 上升 → 避险需求 → 黄金利好（正相关）
        macro_score, dxy_chg, yld_chg, vix_lvl = self._macro_score()

        # 4. 波动率状态过滤 — 高波动时降低置信度
        vol_regime = 0.0
        if len(returns) >= self.params["vol_regime_lookback"]:
            recent_vol = float(np.std(returns[-20:]))
            hist_vols = [float(np.std(returns[max(0, i - 20):i]))
                         for i in range(20, len(returns))]
            if hist_vols:
                pct = sum(1 for v in hist_vols if v < recent_vol) / len(hist_vols)
                vol_regime = pct  # 0=低波动, 1=高波动

        # 5. 合成信号：动量为主(1.0)，宏观覆层与极端偏离做修正
        buy_th = self.params["buy_threshold"]
        sell_th = self.params["sell_threshold"]

        # 高波动率时置信度打折
        vol_multiplier = 1.0
        if vol_regime > self.params["vol_high_percentile"]:
            vol_multiplier = 0.5

        effective_score = momentum_score + mr_score + macro_score

        mr_reason = ""
        if mr_score > 0:
            mr_reason = f", 超卖修正(偏离均线{dev:+.1%})"
        elif mr_score < 0:
            mr_reason = f", 超买修正(偏离均线{dev:+.1%})"
        macro_reason = f", 宏观DXY{dxy_chg:+.1%}/US10Y{yld_chg:+.1%}/VIX{vix_lvl:.0f}" if macro_score != 0 else ""

        if effective_score > buy_th:
            confidence = min(abs(effective_score) / (buy_th * 2), 1.0) * vol_multiplier
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.BUY,
                confidence=confidence,
                reason=f"黄金动量 {momentum_score:.4f} 超买阈值，建议加仓{mr_reason}{macro_reason}",
            )]
        elif effective_score < sell_th:
            confidence = min(abs(effective_score) / (abs(sell_th) * 2), 1.0) * vol_multiplier
            return [self.emit_signal(
                SignalType.TIMING, fund_code, Direction.SELL,
                confidence=confidence,
                reason=f"黄金动量 {momentum_score:.4f} 跌破卖出阈值，建议减仓{mr_reason}{macro_reason}",
            )]
        return [self.emit_signal(
            SignalType.TIMING, fund_code, Direction.HOLD,
            confidence=0.5, reason=f"黄金动量 {momentum_score:.4f} 中性{mr_reason}{macro_reason}",
        )]

    def _macro_score(self) -> tuple:
        """宏观覆层信号：DXY(逆)/US10Y(逆)/VIX(正)，对齐到最新净值日

        Returns:
            (macro_score, dxy_chg, yld_chg, vix_level)
            macro_score ∈ [-macro_weight, +macro_weight]
        """
        md = self._state.get("macro_data")
        if not md:
            return 0.0, 0.0, 0.0, 0.0
        nav_dates = self._state.get("nav_dates", [])
        as_of = str(nav_dates[-1])[:10] if nav_dates else None

        lookback = self.params["macro_lookback_days"]
        macro_w = self.params["macro_weight"]

        def _chg(series: dict, n: int, as_of):
            """取截至 as_of 的最近 n 个值，算相对变化"""
            dates = sorted(d for d in series if d <= (as_of or "9999-12-31"))
            if len(dates) < 2:
                return 0.0
            vals = [series[d] for d in dates[-n:]]
            base = vals[0]
            if abs(base) < 1e-9:
                return 0.0
            return (vals[-1] - base) / base

        # 计算各指标变化（若数据存在）
        dxy_chg = _chg(md.get("DXY", {}), lookback, as_of)
        yld_chg = _chg(md.get("US10Y", {}), lookback, as_of)
        vix_lvl = 0.0
        vix_series = md.get("VIX", {})
        vix_dates = sorted(d for d in vix_series if d <= (as_of or "9999-12-31"))
        if vix_dates:
            vix_lvl = vix_series[vix_dates[-1]]

        score = 0.0
        # DXY 走强(>0) → 看空黄金 → 负贡献
        score -= np.clip(dxy_chg / 0.03, -1.0, 1.0) * macro_w * 0.4
        # US10Y 上行(>0) → 看空黄金 → 负贡献
        score -= np.clip(yld_chg / 0.05, -1.0, 1.0) * macro_w * 0.4
        # VIX 高 → 避险买黄金 → 正贡献（VIX>20 开始增强）
        if vix_lvl > 0:
            score += np.clip((vix_lvl - 20.0) / 20.0, 0.0, 1.0) * macro_w * 0.2

        score = float(np.clip(score, -macro_w, macro_w))
        return score, dxy_chg, yld_chg, vix_lvl


StrategyRegistry.register(GoldMomentumStrategy)
