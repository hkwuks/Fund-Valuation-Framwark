"""行为因子（日历效应）"""
from datetime import date
from typing import Any
import numpy as np

from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class CalendarReturnFactor(Factor):
    """日历效应因子——过去同月平均收益"""
    meta = FactorMeta(
        name="calendar_return", display_name="日历效应",
        category="behavioral", domain="fund",
        description="过去同月的平均收益",
        direction=1, params={"lookback": 365 * 3},
        formula="mean(returns[month==current_month])",
        fund_types=["equity", "qdii"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        target_month = as_of.month
        for s in symbols:
            try:
                navs = data.get_factor_input([s], as_of, lookback)
                if not navs or not isinstance(navs, (list, tuple)):
                    continue
                arr = np.array([n for n in navs if isinstance(n, (int, float)) and n > 0])
                if len(arr) < 60:
                    continue
                returns = np.diff(arr) / arr[:-1]
                chunk = len(returns) // 12
                if chunk < 1:
                    continue
                month_ret = np.mean(returns[(target_month - 1) * chunk:
                                            target_month * chunk])
                result[s] = float(month_ret)
            except Exception:
                continue
        return result
