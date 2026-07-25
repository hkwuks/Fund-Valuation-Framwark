"""基金风险因子"""
from datetime import date
from typing import Any
import numpy as np

from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class MaxDrawdownFactor(Factor):
    """最大回撤因子"""
    meta = FactorMeta(
        name="max_drawdown", display_name="最大回撤",
        category="risk", domain="fund",
        description="历史最大回撤幅度（越小越好，direction=-1）",
        direction=-1, params={"lookback": 252},
        formula="min((cumprod(1+returns) - peak) / peak)",
        fund_types=["equity", "qdii"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                navs = data.get_factor_input([s], as_of, lookback)
                if not navs or len(navs) < 60:
                    continue
                arr = np.array([n for n in navs if isinstance(n, (int, float)) and n > 0])
                returns = np.diff(arr) / arr[:-1]
                cum = np.cumprod(1 + returns)
                peak = np.maximum.accumulate(cum)
                dd = (cum - peak) / peak
                result[s] = float(abs(np.min(dd)))
            except Exception:
                continue
        return result
