"""基金清盘/合并检测测试"""

import sys; sys.path.insert(0, 'backend/..')
import pytest
from datetime import date
from backend.fund_quant.backtest.liquidation import LiquidationHandler


class TestLiquidation:
    def test_no_liquidation_returns_none(self):
        handler = LiquidationHandler()
        event = handler.check("000001", date(2024, 6, 1))
        assert event is None

    def test_liquidation_date_detected(self):
        handler = LiquidationHandler()
        handler.set_liquidation("000001", date(2024, 7, 1), reason="基金清盘")
        event = handler.check("000001", date(2024, 7, 1))
        assert event is not None
        assert event.reason == "基金清盘"

    def test_after_liquidation_removed(self):
        handler = LiquidationHandler()
        handler._active_funds.add("000001")
        handler.set_liquidation("000001", date(2024, 7, 1))
        handler.check("000001", date(2024, 7, 1))  # triggers
        assert "000001" not in handler._active_funds

    def test_merger_event_detected(self):
        handler = LiquidationHandler()
        handler._active_funds.add("000001")
        handler.set_merger("000001", "000002", 0.8, date(2024, 8, 1))
        event = handler.check("000001", date(2024, 8, 1))
        assert event is not None
        assert event.reason == "基金合并"
        assert event.merge_target == "000002"
        assert event.merge_ratio == 0.8

    def test_non_matching_date_returns_none(self):
        handler = LiquidationHandler()
        handler._active_funds.add("000001")
        handler.set_liquidation("000001", date(2024, 7, 1))
        event = handler.check("000001", date(2024, 7, 2))
        assert event is None

    def test_merger_removes_old_fund_after_check(self):
        handler = LiquidationHandler()
        handler._active_funds.add("000001")
        handler.set_merger("000001", "000002", 0.8, date(2024, 8, 1))
        handler.check("000001", date(2024, 8, 1))
        assert "000001" not in handler._active_funds
