"""黄金基本面因子（库存变化）"""
from datetime import date
from typing import Any

from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class InventoryChangeFactor(Factor):
    """库存变化因子"""
    meta = FactorMeta(
        name="inventory_change", display_name="库存变化",
        category="fundamental", domain="gold",
        description="SHFE 金交所库存增减",
        direction=-1, params={"lookback": 60},
        formula="(current_inventory - avg_60d) / avg_60d",
        fund_types=["commodity"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                inv = data.get_factor_input([s], as_of, lookback)
                if isinstance(inv, dict):
                    current = inv.get("current_inventory", 0)
                    avg = inv.get("avg_inventory_60d")
                    if avg and avg > 0:
                        result[s] = (current - avg) / avg
            except Exception:
                continue
        return result
