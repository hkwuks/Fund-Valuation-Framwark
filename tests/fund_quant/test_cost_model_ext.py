"""申购费打折测试"""

from datetime import date

import pytest
from backend.fund_quant.backtest.cost_model import FundCostModel
from backend.fund_quant.core.models import CostModelConfig


class TestSubscriptionDiscount:
    def test_default_discount_slash_cost(self):
        """默认 1 折 (0.10), 1.5% 申购费按 1 万计算应为 15 元 (非 150 元)"""
        config = CostModelConfig(subscription_fee_tiers={"stock": 0.015})
        model = FundCostModel(config)
        model.set_discount(0.10)
        fee = model.get_subscription_fee("stock", amount=10000)
        assert fee == 15.0  # 10000 * 0.015 * 0.1

    def test_no_discount_full_rate(self):
        """discount=1.0 时恢复全额费率"""
        config = CostModelConfig(subscription_fee_tiers={"stock": 0.015})
        model = FundCostModel(config)
        model.set_discount(1.0)
        fee = model.get_subscription_fee("stock", amount=10000)
        assert fee == 150.0  # 10000 * 0.015 * 1.0

    def test_discount_default_on_instance(self):
        """未 set_discount 时默认 0.10"""
        config = CostModelConfig(subscription_fee_tiers={"stock": 0.015})
        model = FundCostModel(config)
        fee = model.get_subscription_fee("stock", amount=10000)
        assert fee == 15.0


class TestHistoricalFees:
    def test_single_value_still_works(self):
        """旧写法 Dict[str, float] 仍可用"""
        config = CostModelConfig(
            management_fee_rate={"stock": 0.015},
        )
        model = FundCostModel(config)
        assert model.get_management_fee("stock") == 0.015

    def test_period_lookup_picks_correct_segment(self):
        """时间段列表: 2022 年取改革前费率 1.5%, 2024 年取改革后 1.2%"""
        config = CostModelConfig(
            management_fee_rate={
                "stock": [("2005-01-01", 0.015), ("2023-07-01", 0.012)]
            },
        )
        model = FundCostModel(config)
        assert model.get_management_fee("stock", as_of=date(2022, 6, 1)) == 0.015
        assert model.get_management_fee("stock", as_of=date(2024, 6, 1)) == 0.012

    def test_period_lookup_before_first(self):
        """早于第一段的日期取第一段费率"""
        config = CostModelConfig(
            management_fee_rate={"stock": [("2010-01-01", 0.015)]},
        )
        model = FundCostModel(config)
        assert model.get_management_fee("stock", as_of=date(2005, 1, 1)) == 0.015

    def test_unknown_type_returns_fallback(self):
        """未配置的基金类型返回 0"""
        config = CostModelConfig(
            management_fee_rate={"stock": 0.015},
        )
        model = FundCostModel(config)
        assert model.get_management_fee("unknown_type") == 0.0
