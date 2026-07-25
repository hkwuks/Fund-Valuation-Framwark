"""黄金期货因子（展期收益、基差）"""
from datetime import date
from typing import Any

from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class RollYieldFactor(Factor):
    """展期收益因子——期限结构倾斜度"""
    meta = FactorMeta(
        name="roll_yield", display_name="展期收益",
        category="futures", domain="gold",
        description="(近月 - 远月) / 近月。升贴水结构转换有预测力",
        direction=1, params={"lookback": 20},
        formula="(near_price - far_price) / near_price",
        reference="商品期货展期收益率的因子定义和测试, 2021",
        fund_types=["commodity"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                prices = data.get_factor_input([s], as_of, 1)
                if isinstance(prices, dict):
                    near = prices.get("near")
                    far = prices.get("far")
                    if near and far and near > 0:
                        result[s] = (near - far) / near
            except Exception:
                continue
        return result


class BasisFactor(Factor):
    """基差因子——期货-现货价差"""
    meta = FactorMeta(
        name="basis", display_name="基差",
        category="futures", domain="gold",
        description="(期货价 - 现货价) / 现货价, 极端基差有均值回归",
        direction=1, params={"lookback": 1},
        formula="(futures_price - spot_price) / spot_price",
        fund_types=["commodity"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                prices = data.get_factor_input([s], as_of, 1)
                if isinstance(prices, dict):
                    fut = prices.get("futures")
                    spot = prices.get("spot")
                    if fut and spot and spot > 0:
                        result[s] = (fut - spot) / spot
            except Exception:
                continue
        return result
