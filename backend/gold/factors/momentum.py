"""黄金动量因子"""
from datetime import date
from typing import Any
import numpy as np

from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class MomentumMultiFactor(Factor):
    """多周期动量因子——均线排列强度"""
    meta = FactorMeta(
        name="momentum_multi", display_name="多周期动量",
        category="momentum", domain="gold",
        description="sign(MA5-MA20) + sign(MA20-MA60)",
        direction=1, params={"lookback": 60},
        formula="sign(MA5-MA20) + sign(MA20-MA60)",
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
                ma5 = np.mean(closes[-5:])
                ma20 = np.mean(closes[-20:])
                ma60 = np.mean(closes[-60:])
                result[s] = float(np.sign(ma5 - ma20) + np.sign(ma20 - ma60))
            except Exception:
                continue
        return result
