"""分红再投集成测试 — 检查 DividendHandler 在回测引擎中的正确集成"""

import sys; sys.path.insert(0, 'backend/..')
import pytest
from datetime import date
from backend.fund_quant.backtest.engine import FundBacktester
from backend.fund_quant.core.models import BacktestConfig


class TestDividendIntegration:
    def test_reinvest_policy(self):
        """分红再投策略时引擎不报错"""
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-01-10",
            initial_capital=100000, dividend_policy="reinvest",
            dividend_calendar={"2020-01-06": {"000001": 0.05}},
        )
        navs = {
            "000001": [
                {"date": "2020-01-02", "nav": 1.0},
                {"date": "2020-01-03", "nav": 1.02},
                {"date": "2020-01-06", "nav": 1.01},
            ]
        }
        engine = FundBacktester()
        result = engine.run(cfg, nav_data=navs)
        assert result.status == "completed"

    def test_cash_dividend_policy(self):
        """现金分红策略时引擎不报错"""
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-01-10",
            initial_capital=100000, dividend_policy="cash",
            dividend_calendar={"2020-01-06": {"000001": 0.05}},
        )
        navs = {
            "000001": [
                {"date": "2020-01-02", "nav": 1.0},
                {"date": "2020-01-03", "nav": 1.02},
                {"date": "2020-01-06", "nav": 1.01},
            ]
        }
        engine = FundBacktester()
        result = engine.run(cfg, nav_data=navs)
        assert result.status == "completed"

    def test_run_without_dividend_calendar(self):
        """无分红日历时正常运行"""
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-01-10",
            initial_capital=100000,
        )
        navs = {
            "000001": [
                {"date": "2020-01-02", "nav": 1.0},
                {"date": "2020-01-03", "nav": 1.02},
            ]
        }
        engine = FundBacktester()
        result = engine.run(cfg, nav_data=navs)
        assert result.status == "completed"

    def test_dividend_with_position(self):
        """有持仓时分红事件被正确消费"""
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-01-10",
            initial_capital=100000, dividend_policy="reinvest",
            dividend_calendar={"2020-01-06": {"000001": 0.05}},
        )
        navs = {
            "000001": [
                {"date": "2020-01-02", "nav": 1.0},
                {"date": "2020-01-03", "nav": 1.02},
                {"date": "2020-01-06", "nav": 1.01},
            ]
        }
        engine = FundBacktester()
        # 手动添加持仓，模拟运行中的分红处理
        engine._config = cfg
        engine._cash = 50000
        from backend.fund_quant.backtest.engine import SimPosition
        engine._positions["000001"] = SimPosition("000001", 1000, date(2020, 1, 2), 1.0)
        engine._dividend_calendar = {"2020-01-06": {"000001": 0.05}}
        code_nav_map = {"000001": {"2020-01-06": {"date": "2020-01-06", "nav": 1.01}}}

        engine._process_dividends("2020-01-06", date(2020, 1, 6), code_nav_map)

        # 红利再投后份额应增加
        assert engine._positions["000001"].shares > 1000

    def test_cash_dividend_with_position(self):
        """现金分红后现金增加, 份额不变"""
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-01-10",
            initial_capital=100000, dividend_policy="cash",
            dividend_calendar={"2020-01-06": {"000001": 0.05}},
        )
        navs = {
            "000001": [
                {"date": "2020-01-02", "nav": 1.0},
                {"date": "2020-01-03", "nav": 1.02},
                {"date": "2020-01-06", "nav": 1.01},
            ]
        }
        engine = FundBacktester()
        engine._config = cfg
        engine._cash = 50000
        from backend.fund_quant.backtest.engine import SimPosition
        engine._positions["000001"] = SimPosition("000001", 1000, date(2020, 1, 2), 1.0)
        engine._dividend_calendar = {"2020-01-06": {"000001": 0.05}}
        code_nav_map = {"000001": {"2020-01-06": {"date": "2020-01-06", "nav": 1.01}}}

        engine._process_dividends("2020-01-06", date(2020, 1, 6), code_nav_map)

        assert engine._positions["000001"].shares == 1000  # 份额不变
        assert engine._cash > 50000  # 现金增加
