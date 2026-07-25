"""黄金风险因子（波动率状态）"""
from datetime import date
from typing import Any
import numpy as np

from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class VolatilityRegimeFactor(Factor):
    """波动率状态因子——当前波动率在历史分布中的百分位"""
    meta = FactorMeta(
        name="volatility_regime", display_name="波动率状态",
        category="risk", domain="gold",
        description="当前波动率百分位",
        direction=1, params={"lookback": 252},
        formula="percentile(current_vol, hist_vol_252d)",
        fund_types=["commodity"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                bars = data.get_factor_input([s], as_of, lookback)
                if not isinstance(bars, (list, tuple)) or len(bars) < 60:
                    continue
                closes = np.array([b for b in bars if isinstance(b, (int, float)) and b > 0])
                if len(closes) < 60:
                    continue
                returns = np.diff(closes) / closes[:-1]
                if len(returns) < 60:
                    continue
                current_vol = float(np.std(returns[-20:]))
                hist_vols = [float(np.std(returns[max(0, i-20):i]))
                             for i in range(20, len(returns))]
                if not hist_vols:
                    continue
                percentile = sum(1 for v in hist_vols if v < current_vol) / len(hist_vols)
                result[s] = percentile
            except Exception:
                continue
        return result
