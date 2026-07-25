"""黄金情绪因子（持仓量、COT）"""
from datetime import date
from typing import Any
import numpy as np

from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class OpenInterestChangeFactor(Factor):
    """持仓量变化因子"""
    meta = FactorMeta(
        name="open_interest_change", display_name="持仓量变化",
        category="sentiment", domain="gold",
        description="持仓量增减反映资金参与度",
        direction=1, params={"lookback": 60},
        formula="(current_oi - avg_oi) / avg_oi",
        fund_types=["commodity"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                oi_data = data.get_factor_input([s], as_of, lookback)
                if isinstance(oi_data, dict):
                    current = oi_data.get("current_oi", 0)
                    avg = oi_data.get("avg_oi_60d")
                    if avg and avg > 0:
                        result[s] = (current - avg) / avg
            except Exception:
                continue
        return result


class COTSignalFactor(Factor):
    """COT 持仓比因子"""
    meta = FactorMeta(
        name="coT_signal", display_name="COT持仓比",
        category="sentiment", domain="gold",
        description="商业(套保) vs 投机(基金)持仓比变化",
        direction=1, params={"lookback": 1},
        formula="(commercial_long - commercial_short) / total",
        fund_types=["commodity"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                cot = data.get_factor_input([s], as_of, 1)
                if isinstance(cot, dict):
                    cl = cot.get("commercial_long", 0)
                    cs = cot.get("commercial_short", 0)
                    total = cot.get("total", 1)
                    result[s] = (cl - cs) / max(total, 1)
            except Exception:
                continue
        return result
