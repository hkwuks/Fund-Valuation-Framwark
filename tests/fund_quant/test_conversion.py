"""基金转换费用计算测试 (P3-3)"""

from datetime import date

import pytest
from backend.fund_quant.backtest.cost_model import FundCostModel
from backend.fund_quant.core.models import CostModelConfig


class TestConversionCost:
    """FundCostModel.calc_conversion_cost"""

    def setup_method(self):
        config = CostModelConfig(
            subscription_fee_tiers={"stock": 0.015, "bond": 0.008},
        )
        self.model = FundCostModel(config)
        self.model.set_discount(0.10)

    def test_same_company_conversion(self):
        """Same first 6 digits -> path='conversion', fee diff calculated"""
        # "00000100"[:6] == "00000101"[:6] == "000001" -> same company
        result = self.model.calc_conversion_cost(
            "00000100", "00000101", "stock", "stock", 10000,
        )
        assert result["path"] == "conversion"
        # source_rate == target_rate == 0.015 -> fee_diff = 0
        assert result["fee_diff"] == 0.0
        # redemption_fee = 1.5% (holding_days=0) * 10000 = 150
        assert result["redemption_fee"] == 150.0
        assert result["conversion_fee"] == 150.0

    def test_diff_company_no_conversion(self):
        """Different 6-char prefix -> path='redeem_buy'"""
        result = self.model.calc_conversion_cost(
            "11001100", "00000101", "stock", "bond", 10000,
        )
        assert result["path"] == "redeem_buy"
        # redemption (stock 1.5% * 10000) + subscription (bond 0.8% * 10000 * 0.1)
        redemption = 0.015 * 10000  # 150
        subscription = 0.008 * 10000 * 0.10  # 8
        assert result["conversion_fee"] == pytest.approx(redemption + subscription, 0.01)
        assert result["fee_diff"] == 0.0

    def test_source_rate_higher(self):
        """source_rate >= target_rate -> fee_diff = 0"""
        # Same prefix "000001", source stock (0.015) >= target bond (0.008)
        result = self.model.calc_conversion_cost(
            "00000100", "00000101", "stock", "bond", 10000,
        )
        assert result["path"] == "conversion"
        assert result["fee_diff"] == 0.0
        assert result["source_rate"] == 0.015
        assert result["target_rate"] == 0.008

    def test_source_rate_lower(self):
        """source_rate < target_rate -> fee_diff = (target-source)*amount*discount"""
        # Same prefix "000001", source bond (0.008) < target stock (0.015)
        result = self.model.calc_conversion_cost(
            "00000100", "00000101", "bond", "stock", 10000,
        )
        assert result["path"] == "conversion"
        expected_diff = (0.015 - 0.008) * 10000 * 0.10  # 7.0
        assert result["fee_diff"] == pytest.approx(expected_diff, 0.01)
        # redemption_fee uses common holding_period_discount, same for all types
        # holding_days=0 -> rate = 1.5% -> 0.015 * 10000 = 150
        assert result["redemption_fee"] == 150.0
        assert result["conversion_fee"] == pytest.approx(150.0 + expected_diff, 0.01)

    def test_amount_edge(self):
        """amount=0 -> zero cost, path='redeem_buy'"""
        result = self.model.calc_conversion_cost(
            "00000100", "00000101", "stock", "stock", 0,
        )
        assert result["path"] == "redeem_buy"
        assert result["conversion_fee"] == 0.0
        assert result["redemption_fee"] == 0.0

    def test_same_fund_raises(self):
        """Same fund codes -> ValueError"""
        with pytest.raises(ValueError, match="Cannot convert to same fund"):
            self.model.calc_conversion_cost(
                "000001", "000001", "stock", "stock", 10000,
            )

    def test_conversion_cost_less_than_redeem_buy(self):
        """Conversion is cheaper than redeem+buy for same-company funds"""
        # Same company (000001 prefix): conversion path
        conv_result = self.model.calc_conversion_cost(
            "00000100", "00000101", "stock", "bond", 10000,
        )
        assert conv_result["path"] == "conversion"

        # Different company (110011 vs 000001): redeem+buy path
        redeem_buy_result = self.model.calc_conversion_cost(
            "11001100", "00000101", "stock", "bond", 10000,
        )
        assert redeem_buy_result["path"] == "redeem_buy"

        # Conversion should cost less than redeem+buy
        assert conv_result["conversion_fee"] < redeem_buy_result["conversion_fee"]
        # Conversion: 150 (redemption only, no fee_diff)
        # Redeem+buy: 150 (redemption) + 8 (subscription) = 158


class TestConversionEngine:
    """FundBacktester conversion routing"""

    def test_process_conversion_different_company(self):
        """Different company -> _process_conversion returns False"""
        from backend.fund_quant.backtest.engine import FundBacktester
        from backend.fund_quant.core.models import BacktestConfig

        bt = FundBacktester()
        bt._config = BacktestConfig(
            strategy_name="test", fund_codes=["11001100", "00000101"],
            start_date="2025-01-01", end_date="2025-03-01",
        )
        bt._positions["11001100"] = type("Pos", (), {
            "shares": 1000, "buy_nav": 1.5, "cost": 1500,
        })()
        result = bt._process_conversion("11001100", "00000101", 100, date(2025, 1, 10))
        assert result is False

    def test_process_conversion_same_fund(self):
        """Same fund_code -> returns False"""
        from backend.fund_quant.backtest.engine import FundBacktester
        from backend.fund_quant.core.models import BacktestConfig

        bt = FundBacktester()
        bt._config = BacktestConfig(
            strategy_name="test", fund_codes=["00000100"],
            start_date="2025-01-01", end_date="2025-03-01",
        )
        result = bt._process_conversion("00000100", "00000100", 100, date(2025, 1, 10))
        assert result is False
