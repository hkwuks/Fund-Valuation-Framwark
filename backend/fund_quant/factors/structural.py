"""基金结构因子（规模、费率）"""
from datetime import date
from typing import Any
import numpy as np

from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class FundScaleFactor(Factor):
    """基金规模因子"""
    meta = FactorMeta(
        name="fund_scale", display_name="基金规模",
        category="structural", domain="fund",
        description="对数规模（小规模基金有流动性溢价）",
        direction=1, params={"lookback": 1},
        formula="log10(scale / 1e7) / 3",
        fund_types=["equity", "index", "balanced", "bond", "qdii", "fof", "commodity"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                meta = data.get_factor_input([s], as_of, 1)
                if isinstance(meta, dict) and "scale" in meta:
                    scale = meta["scale"]
                    result[s] = float(np.log10(max(scale, 1e7)) - 7) / 3
            except Exception:
                continue
        return result


class FeeRateFactor(Factor):
    """综合费率因子"""
    meta = FactorMeta(
        name="fee_rate", display_name="综合费率",
        category="structural", domain="fund",
        description="管理费+托管费（越低越好）",
        direction=-1, params={"lookback": 1},
        formula="management_fee + custody_fee",
        fund_types=["equity", "index", "balanced", "bond", "qdii", "fof"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                meta = data.get_factor_input([s], as_of, 1)
                if isinstance(meta, dict):
                    mgmt = meta.get("management_fee", 0.015)
                    cust = meta.get("custody_fee", 0.002)
                    result[s] = mgmt + cust
            except Exception:
                continue
        return result
