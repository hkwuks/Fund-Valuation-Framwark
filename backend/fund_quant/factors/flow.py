"""资金流因子（基金特有）"""
from datetime import date
from typing import Any

from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class FundFlowFactor(Factor):
    """资金净流因子——中国基金最有效的单一因子"""
    meta = FactorMeta(
        name="fund_flow", display_name="资金净流",
        category="flow", domain="fund",
        description="(规模变化 - NAV增长) / 上期规模",
        direction=1, params={"lookback": 90},
        formula="(scale_t - scale_{t-1}) / scale_{t-1} - NAV_growth",
        fund_types=["equity", "index", "balanced", "bond", "qdii", "fof"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                flow_data = data.get_factor_input([s], as_of, lookback)
                if not isinstance(flow_data, dict):
                    continue
                s_prev = flow_data.get("scale_prev")
                s_curr = flow_data.get("scale_curr")
                nav_ret = flow_data.get("nav_return", 0)
                if s_prev and s_curr and s_prev > 0:
                    flow = (s_curr - s_prev) / s_prev - nav_ret
                    result[s] = float(flow)
            except Exception:
                continue
        return result
