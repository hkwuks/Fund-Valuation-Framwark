"""基金经理因子"""
from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class ManagerTenureFactor(Factor):
    """基金经理年限因子"""
    meta = FactorMeta(
        name="manager_tenure", display_name="基金经理年限",
        category="manager", domain="fund",
        description="当前基金经理任职年数",
        direction=1, params={"lookback": 1},
        formula="years_since_appointment",
        fund_types=["equity", "balanced", "qdii"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                m = data.get_factor_input([s], as_of, 1)
                if isinstance(m, dict):
                    tenure = m.get("manager_tenure", 3)
                    result[s] = float(tenure)
            except Exception:
                continue
        return result
