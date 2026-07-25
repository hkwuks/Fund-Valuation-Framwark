"""基金持仓集中度因子"""
from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class HoldingConcentrationFactor(Factor):
    """持仓集中度因子——前10重仓股占比"""
    meta = FactorMeta(
        name="holding_concentration", display_name="持仓集中度",
        category="concentration", domain="fund",
        description="前10大重仓股占净值比，集中度高→波动大",
        direction=1, params={"lookback": 1},
        formula="top10_holdings_pct",
        fund_types=["equity", "balanced", "qdii"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                h = data.get_factor_input([s], as_of, 1)
                if isinstance(h, dict):
                    pct = h.get("top10_pct", 50)
                    result[s] = float(pct) / 100.0
            except Exception:
                continue
        return result
