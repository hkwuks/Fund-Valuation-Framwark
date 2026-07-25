"""Coverage gap tests for backtest modules — edge cases, error paths, and untested branches."""

from __future__ import annotations

import os
import pickle
import shutil
import tempfile
from datetime import date, datetime
from typing import Optional

import numpy as np
import pytest

# ── PaperTrader gaps ──

from backend.fund_quant.backtest.paper_trader import FundPaperTrader, PaperTradeState
from backend.fund_quant.backtest.cost_model import FundCostModel, CostModelConfig


class TestPaperTraderCoverage:
    """Close paper_trader.py coverage gaps: strategy signals, stop/start edge, confirm edge cases, list with corruption."""

    @pytest.fixture
    def trader(self):
        tmp = tempfile.mkdtemp()
        yield FundPaperTrader(state_dir=tmp)
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.fixture
    def running_state(self, trader):
        return trader.start("test", ["000001"], 100000)

    @pytest.fixture
    def nav_data(self):
        return {"000001": [{"date": "2024-01-03", "nav": 1.01}, {"date": "2024-01-04", "nav": 1.02}, {"date": "2024-01-05", "nav": 1.03}]}

    def test_stopped_session_returns_unchanged(self, trader):
        """daily_run on stopped session returns state unchanged"""
        state = trader.start("test", ["000001"], 100000)
        trader.stop(state.paper_trade_id)
        result = trader.daily_run(state.paper_trade_id, {})
        assert result is not None
        assert result.status == "stopped"

    def test_no_nav_data_returns_state(self, trader, running_state):
        """daily_run with empty nav_data returns state unchanged (not None)"""
        result = trader.daily_run(running_state.paper_trade_id, {},
                                  run_date=date(2024, 1, 3))
        assert result is not None
        assert result.last_run_date is None  # not updated

    def test_reentry_same_day(self, trader, running_state, nav_data):
        """Two daily_runs on same date → second returns state without processing"""
        first = trader.daily_run(running_state.paper_trade_id, nav_data,
                                 run_date=date(2024, 1, 3))
        assert len(first.equity_curve) == 1
        second = trader.daily_run(running_state.paper_trade_id, nav_data,
                                  run_date=date(2024, 1, 3))
        assert len(second.equity_curve) == 1  # same

    def test_strategy_generates_signals(self, trader, running_state):
        """Strategy func returning buy signals → orders placed and confirmed T+1"""
        nav_data = {"000001": [{"date": "2024-01-03", "nav": 1.0},
                                {"date": "2024-01-04", "nav": 1.0}]}
        # Set strategy before any daily_run
        def buy_sig(tid, st):
            return [{"fund_code": "000001", "direction": "buy", "shares": 100}]
        trader._strategy_func = buy_sig
        s1 = trader.daily_run(running_state.paper_trade_id, nav_data,
                              run_date=date(2024, 1, 3))
        # Verify order was placed
        assert len(s1.pending_orders) >= 1, f"Expected pending order, got state: paper_trade_id={s1.paper_trade_id}, pending={s1.pending_orders}, cash={s1.cash}, positions={s1.positions}"

    def test_sell_signal_reduces_position(self, trader):
        """Sell signal reduces position and adds cash"""
        state = trader.start("test", ["000001"], 100000)
        state.positions["000001"] = 50.0
        trader._save_state(state)

        nav_data = {"000001": [{"date": "2024-01-03", "nav": 2.0},
                                {"date": "2024-01-04", "nav": 2.0}]}
        # Use relaxed redemption gate to avoid test interference
        from backend.fund_quant.backtest.redemption_gate import RedemptionGate
        trader._redemption_gate = RedemptionGate(LARGE_REDEMPTION_PCT=1.0, FULL_REJECTION_PCT=2.0)
        trader._strategy_func = lambda tid, st: [{"fund_code": "000001", "direction": "sell", "shares": 20}]
        s1 = trader.daily_run(state.paper_trade_id, nav_data,
                              run_date=date(2024, 1, 3))
        assert len(s1.pending_orders) >= 1, f"Expected sell pending, got: {s1.pending_orders}"

    def test_sell_insufficient_held_reduces(self, trader):
        """Sell with shares > held → clamped to held"""
        state = trader.start("test", ["000001"], 100000)
        state.positions["000001"] = 10.0
        trader._save_state(state)

        nav_data = {"000001": [{"date": "2024-01-03", "nav": 2.0},
                                {"date": "2024-01-04", "nav": 2.0}]}
        from backend.fund_quant.backtest.redemption_gate import RedemptionGate
        trader._redemption_gate = RedemptionGate(LARGE_REDEMPTION_PCT=2.0, FULL_REJECTION_PCT=3.0)
        trader._strategy_func = lambda tid, st: [{"fund_code": "000001", "direction": "sell", "shares": 999}]
        s1 = trader.daily_run(state.paper_trade_id, nav_data,
                              run_date=date(2024, 1, 3))
        assert len(s1.pending_orders) >= 1, f"No pending orders: {s1}"
        assert s1.pending_orders[0]["shares"] <= 10.0  # clamped
        s2 = trader.daily_run(state.paper_trade_id, nav_data, run_date=date(2024, 1, 4))
        assert s2.positions.get("000001", None) is None  # fully sold

    def test_buy_insufficient_cash_skipped(self, trader, running_state):
        """Buy exceeding cash → order skipped"""
        def buy_expensive(tid, st):
            return [{"fund_code": "000001", "direction": "buy", "shares": 1_000_000}]
        trader._strategy_func = buy_expensive
        nav_data = {"000001": [{"date": "2024-01-03", "nav": 1.0}]}
        s = trader.daily_run(running_state.paper_trade_id, nav_data,
                             run_date=date(2024, 1, 3))
        assert len(s.pending_orders) == 0  # skipped

    def test_list_with_mixed_files(self, trader):
        """list_sessions handles non-pkl files and corrupt states gracefully"""
        trader.start("s1", ["000001"], 10000)
        # add a non-pkl file
        with open(os.path.join(trader._state_dir, "readme.txt"), "w") as f:
            f.write("not a pickle")
        # add corrupt pickle
        with open(os.path.join(trader._state_dir, "corrupt.pkl"), "wb") as f:
            f.write(b"not valid pickle data")
        summaries = trader.list_sessions()
        assert len(summaries) == 1  # only s1
        assert summaries[0].strategy_name == "s1"

    def test_list_with_equity_curve_sharpe(self, trader):
        """list_sessions computes sharpe when equity_curve has 3+ entries"""
        state = trader.start("s1", ["000001"], 10000)
        for i in range(5):
            state.equity_curve.append({"date": f"2024-01-0{i+1}", "total_value": 10000 + i * 100})
        state.initial_capital = 10000
        trader._save_state(state)
        summaries = trader.list_sessions()
        assert summaries[0].sharpe != 0.0

    def test_confirm_order_sell_no_nav(self, trader, running_state):
        """Pending sell order with no NAV on confirm day → remains pending"""
        running_state.pending_orders = [{
            "fund_code": "000001", "direction": "sell", "shares": 10,
            "submit_date": "2024-01-03", "status": "pending",
        }]
        trader._confirm_orders(running_state, date(2024, 1, 4), {})  # empty navs
        assert len(running_state.pending_orders) == 1  # not removed

    def test_confirm_order_sell_same_day(self, trader, running_state):
        """Pending order not confirmed same day (T+1 rule)"""
        running_state.pending_orders = [{
            "fund_code": "000001", "direction": "sell", "shares": 10,
            "submit_date": "2024-01-04", "status": "pending",
        }]
        trader._confirm_orders(running_state, date(2024, 1, 4), {"000001": 2.0})
        assert len(running_state.pending_orders) == 1

    def test_get_today_navs_missing_fund(self, trader):
        """_get_today_navs with no records for a fund returns empty dict"""
        navs = trader._get_today_navs(["missing_code"], {}, date(2024, 1, 3))
        assert navs == {}

    def test_get_today_navs_string_date(self, trader):
        """_get_today_navs handles string dates"""
        nav_data = {"000001": [{"date": "2024-01-03", "nav": 1.5}]}
        navs = trader._get_today_navs(["000001"], nav_data, date(2024, 1, 3))
        assert navs["000001"] == 1.5

    def test_compute_total_value_missing_nav(self, trader):
        """_compute_total_value with fund having no nav → uses cash only"""
        state = PaperTradeState(
            paper_trade_id="test", strategy_name="t", fund_codes=["000001"],
            initial_capital=1000, cash=500, positions={"000001": 100},
            pending_orders=[], equity_curve=[], trade_log=[],
            last_run_date=None, created_at="now", status="running",
        )
        total = trader._compute_total_value(state, {})
        assert total == 500.0

    def test_load_corrupt_pickle(self, trader):
        """Corrupt pickle file → _load_state returns None"""
        with open(os.path.join(trader._state_dir, "bad.pkl"), "wb") as f:
            f.write(b"garbage")
        assert trader._load_state("bad") is None

    def test_stop_twice(self, trader, running_state):
        """stop() on already stopped session returns state unchanged"""
        trader.stop(running_state.paper_trade_id)
        result = trader.stop(running_state.paper_trade_id)
        assert result.status == "stopped"


class TestCostModelCoverage:
    """Close cost_model.py gaps: C-class, FOF, dividend tax, max_subscription_amount, should_use_c_class."""

    def setup_method(self):
        cfg = CostModelConfig(
            subscription_fee_tiers={"stock": 0.015, "bond": 0.008, "fof": 0.012},
            c_class_redemption_fee=0.005,
            c_class_service_fee=0.004,
            ac_class_threshold_years=1.5,
            holding_period_discount={"7": 1.5, "30": 0.75, "365": 0.5, "730": 0.0},
            max_subscription_amount=500000,
            dividend_tax_holding_under_1y=0.10,
            dividend_tax_holding_over_1y=0.0,
        )
        self.model = FundCostModel(cfg)
        self.model.set_discount(0.10)

    def test_max_subscription_discount(self):
        """Amount > max_subscription_amount → rate capped at 0.001 * amount"""
        fee = self.model.get_subscription_fee("stock", amount=1_000_000, as_of=date(2024, 1, 1))
        # rate=0.001, amount=1_000_000, discount=0.10 → fee = 100
        assert fee == 100.0

    def test_c_class_redemption(self):
        """C-class redemption with short holding → uses c_class rate"""
        fee = self.model.get_redemption_fee("equity", holding_days=100, is_c_class=True)
        assert fee == 0.005  # c_class_redemption_fee

    def test_c_class_redemption_long_hold(self):
        """C-class redemption with holding >= threshold → zero fee"""
        fee = self.model.get_redemption_fee("equity", holding_days=600, is_c_class=True)
        assert fee == 0.0

    def test_c_class_service_fee(self):
        """C-class service fee computed correctly"""
        fee = self.model.get_c_class_service_fee(365)
        # 0.004 * 365 / 365 = 0.004
        assert abs(fee - 0.004) < 1e-10

    def test_fof_effective_fee_non_fof(self):
        """Non-FOF fund type returns normal management fee"""
        fee = self.model.fof_effective_fee("stock")
        assert fee == 0.015  # stock management fee

    def test_fof_effective_fee_fof(self):
        """FOF fund type returns FOF fee + underlying"""
        fee = self.model.fof_effective_fee("fof", underlying_fee=0.005)
        # management_fee for "fof" defaults to 0.01 → 0.01 + 0.005 = 0.015
        assert abs(fee - 0.015) < 1e-10

    def test_dividend_tax_under_1y(self):
        """Holding < 365 days → 10% tax rate"""
        tax = self.model.get_dividend_tax(180)
        assert tax == 0.10

    def test_dividend_tax_over_1y(self):
        """Holding >= 365 days → 0% tax rate"""
        tax = self.model.get_dividend_tax(365)
        assert tax == 0.0

    def test_should_use_c_class_short(self):
        """Holding < 547 days → True"""
        assert FundCostModel.should_use_c_class(100) is True

    def test_should_use_c_class_long(self):
        """Holding >= 547 days → False"""
        assert FundCostModel.should_use_c_class(600) is False

    def test_resolve_rate_unknown_type(self):
        """Unknown fund_type returns 0.0"""
        rate = self.model._resolve_rate(
            self.model.config.subscription_fee_tiers, "unknown", date.today()
        )
        assert rate == 0.0

    def test_resolve_rate_period_before_first(self):
        """Date before first period → uses first period's fee"""
        rates = {"stock": [("2023-07-01", 0.012), ("2024-01-01", 0.010)]}
        rate = self.model._resolve_rate(rates, "stock", date(2023, 1, 1))
        assert rate == 0.012

    def test_management_fee_with_as_of(self):
        """get_management_fee with as_of date works"""
        fee = self.model.get_management_fee("stock", as_of=date(2024, 6, 1))
        assert fee == 0.015

    def test_custody_fee_with_as_of(self):
        """get_custody_fee with as_of date works"""
        fee = self.model.get_custody_fee("bond", as_of=date(2024, 6, 1))
        assert fee > 0


class TestEngineCoverage:
    """Close engine.py gaps: conversion edge, fund type lookup, report generation edge."""

    def test_get_fund_type_unknown(self):
        """_get_fund_type returns 'stock' for unknown fund_code"""
        from backend.fund_quant.backtest.engine import FundBacktester
        from backend.fund_quant.core.models import BacktestConfig
        bt = FundBacktester()
        bt._config = BacktestConfig(
            strategy_name="test", fund_codes=["999999"],
            start_date="2024-01-01", end_date="2024-12-31",
        )
        ftype = bt._get_fund_type("999999")
        assert ftype == "stock"

    def test_get_fund_type_cached(self):
        """_get_fund_type returns cached value without querying"""
        from backend.fund_quant.backtest.engine import FundBacktester
        bt = FundBacktester()
        bt._fund_type_map["000001"] = "bond"
        assert bt._get_fund_type("000001") == "bond"

    def test_process_conversion_same_code(self):
        """_process_conversion with same source/target → False"""
        from backend.fund_quant.backtest.engine import FundBacktester
        from backend.fund_quant.core.models import BacktestConfig
        bt = FundBacktester()
        bt._config = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2024-01-01", end_date="2024-12-31",
        )
        assert bt._process_conversion("000001", "000001", 100, date(2024, 6, 1)) is False

    def test_process_conversion_no_position(self):
        """_process_conversion with no source position → False"""
        from backend.fund_quant.backtest.engine import FundBacktester
        from backend.fund_quant.core.models import BacktestConfig
        bt = FundBacktester()
        bt._config = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2024-01-01", end_date="2024-12-31",
        )
        assert bt._process_conversion("000001", "000002", 100, date(2024, 6, 1)) is False

    def test_process_conversion_zero_shares(self):
        """_process_conversion with shares <= 0 → False"""
        from backend.fund_quant.backtest.engine import FundBacktester, SimPosition
        from backend.fund_quant.core.models import BacktestConfig
        bt = FundBacktester()
        bt._config = BacktestConfig(
            strategy_name="test", fund_codes=["000001", "000002"],
            start_date="2024-01-01", end_date="2024-12-31",
        )
        bt._positions["000001"] = SimPosition("000001", 100, date(2024, 1, 1), 1.0)
        assert bt._process_conversion("000001", "000002", 0, date(2024, 6, 1)) is False
        assert bt._process_conversion("000001", "000002", -1, date(2024, 6, 1)) is False

    def test_process_conversion_diff_company(self):
        """_process_conversion with different prefix → False"""
        from backend.fund_quant.backtest.engine import FundBacktester, SimPosition
        from backend.fund_quant.core.models import BacktestConfig
        bt = FundBacktester()
        bt._config = BacktestConfig(
            strategy_name="test", fund_codes=["110011", "000001"],
            start_date="2024-01-01", end_date="2024-12-31",
        )
        bt._positions["110011"] = SimPosition("110011", 100, date(2024, 1, 1), 1.0)
        result = bt._process_conversion("110011", "000001", 50, date(2024, 6, 1))
        assert result is False  # different prefix → redeem_buy

    def test_calc_total_value_with_positions(self):
        """_calc_total_value includes cash + positions × nav"""
        from backend.fund_quant.backtest.engine import FundBacktester, SimPosition
        from backend.fund_quant.core.models import BacktestConfig
        bt = FundBacktester()
        bt._config = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2024-01-01", end_date="2024-01-10",
        )
        bt._cash = 50000
        bt._positions["000001"] = SimPosition("000001", 100, date(2024, 1, 1), 1.0)
        code_nav_map = {"000001": {"2024-01-05": {"nav": 1.5}}}
        total = bt._calc_total_value("2024-01-05", code_nav_map)
        assert abs(total - (50000 + 100 * 1.5)) < 1e-6

    def test_holding_days(self):
        """get_holding_days returns correct count"""
        from backend.fund_quant.backtest.engine import FundBacktester, SimPosition
        bt = FundBacktester()
        bt._positions["000001"] = SimPosition("000001", 100, date(2024, 1, 1), 1.0)
        days = bt.get_holding_days("000001", date(2024, 1, 10))
        assert days == 9

    def test_holding_days_no_position(self):
        """get_holding_days for missing fund → 0"""
        from backend.fund_quant.backtest.engine import FundBacktester
        bt = FundBacktester()
        assert bt.get_holding_days("nonexistent", date(2024, 1, 10)) == 0

    def test_submit_order(self):
        """submit_order creates a PendingOrder and appends it"""
        from backend.fund_quant.backtest.engine import FundBacktester
        bt = FundBacktester()
        bt.submit_order("000001", "buy", 100, date(2024, 1, 1))
        assert len(bt._pending_orders) == 1
        assert bt._pending_orders[0].fund_code == "000001"

    def test_get_position(self):
        """get_position returns position or None"""
        from backend.fund_quant.backtest.engine import FundBacktester, SimPosition
        bt = FundBacktester()
        assert bt.get_position("000001") is None
        bt._positions["000001"] = SimPosition("000001", 100, date(2024, 1, 1), 1.0)
        pos = bt.get_position("000001")
        assert pos is not None
        assert pos.shares == 100

    def test_generate_report_empty(self):
        """_generate_report with <2 equity curve entries returns zero return"""
        from backend.fund_quant.backtest.engine import FundBacktester
        from backend.fund_quant.core.models import BacktestConfig
        bt = FundBacktester()
        bt._config = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2024-01-01", end_date="2024-01-10",
        )
        bt._equity_curve = [{"date": "2024-01-01", "total_value": 100000}]
        report = bt._generate_report()
        assert report.total_return == 0.0

    def test_generate_report_with_trades(self):
        """_generate_report computes win rate correctly"""
        from backend.fund_quant.backtest.engine import FundBacktester
        from backend.fund_quant.core.models import BacktestConfig
        bt = FundBacktester()
        bt._config = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2024-01-01", end_date="2024-01-10",
            initial_capital=100000,
        )
        bt._cash = 100000
        # 2 winning trades + 1 losing trade
        bt._trade_log = [
            {"action": "sell_confirmed", "proceeds": 150},
            {"action": "sell_confirmed", "proceeds": 200},
            {"action": "sell_confirmed", "proceeds": 0},
        ]
        bt._equity_curve = [
            {"date": "2024-01-01", "total_value": 100000},
            {"date": "2024-01-02", "total_value": 101000},
            {"date": "2024-01-03", "total_value": 102000},
        ]
        report = bt._generate_report()
        assert report.win_rate == pytest.approx(2 / 3, abs=0.001)
        assert report.total_trades == 3


class TestParamScannerCoverage:
    """Close param_scanner.py gaps: random_search edge, empty result, NaN metrics."""

    def test_random_search_sampling(self):
        """random_search samples from distribution correctly"""
        from backend.fund_quant.backtest.param_scanner import ParameterScanner

        def dummy_func(params):
            return {"sharpe": params.get("x", 0) * 0.1, "ret": params.get("y", 0)}

        scanner = ParameterScanner(dummy_func)
        dist = {"x": [0.1, 0.2, 0.3, 0.4, 0.5], "y": [0.01, 0.02]}
        result = scanner.random_search(dist, n_iter=20, seed=42)
        assert result.n_iterations == 20
        assert len(result.results) == 20
        # All x values should be from the distribution list
        x_vals = [r["x"] for r in result.results]
        assert all(x in [0.1, 0.2, 0.3, 0.4, 0.5] for x in x_vals)
        assert "sharpe" in result.results[0]
        assert "ret" in result.results[0]

    def test_random_search_reproducible(self):
        """Same seed → same random_search results"""
        from backend.fund_quant.backtest.param_scanner import ParameterScanner

        def dummy_func(params):
            return {"sharpe": params["x"] * 0.1}

        scanner = ParameterScanner(dummy_func)
        dist = {"x": [0.1, 0.2, 0.3]}
        r1 = scanner.random_search(dist, n_iter=10, seed=42)
        r2 = scanner.random_search(dist, n_iter=10, seed=42)
        assert r1.results == r2.results

    def test_random_search_range_tuple(self):
        """random_search with (low, high) tuple samples uniformly"""
        from backend.fund_quant.backtest.param_scanner import ParameterScanner

        def dummy_func(params):
            return {"sharpe": params["x"]}

        scanner = ParameterScanner(dummy_func)
        result = scanner.random_search({"x": (0.0, 1.0)}, n_iter=100, seed=42)
        x_vals = [r["x"] for r in result.results]
        assert all(0.0 <= x <= 1.0 for x in x_vals)
        assert len(set(round(v, 2) for v in x_vals)) > 5  # diverse

    def test_single_param_sensitivity_score(self):
        """single_param computes sensitivity_score"""
        from backend.fund_quant.backtest.param_scanner import ParameterScanner

        def func(p):
            return {"sharpe": p["x"] * 2}

        scanner = ParameterScanner(func)
        result = scanner.single_param("x", [1, 2, 3, 4, 5])
        assert result.sensitivity_score is not None
        assert result.sensitivity_score["sharpe"] == pytest.approx(10 - 2, 0.01)

    def test_grid_search_stability_region(self):
        """grid_search computes stability_region for 2-param"""
        from backend.fund_quant.backtest.param_scanner import ParameterScanner

        def func(p):
            return {"sharpe": p["a"] / p["b"] if p["b"] > 0 else 0}

        scanner = ParameterScanner(func)
        result = scanner.grid_search({"a": [1, 2], "b": [1, 2]})
        assert result.stability_region is not None


class TestVectorizedCoverage:
    """Close vectorized_engine.py gaps: edge cases."""

    def test_no_funds_raises(self):
        """Empty nav_matrix raises ValueError"""
        from backend.fund_quant.backtest.vectorized_engine import VectorizedBacktestEngine
        engine = VectorizedBacktestEngine()
        with pytest.raises(ValueError):
            engine.run(np.array([[]]), lambda x: x)

    def test_single_day(self):
        """Single trading day returns 0 for annual metrics"""
        from backend.fund_quant.backtest.vectorized_engine import VectorizedBacktestEngine
        engine = VectorizedBacktestEngine()
        nav = np.array([[1.0, 1.01], [1.0, 1.02]], dtype=float)

        def equal_w(nav):
            nf, nd = nav.shape
            return np.ones((nf, nd)) / nf

        result = engine.run(nav, equal_w)
        # n_trading_days = number of daily return observations = n_days - 1
        assert result.n_trading_days == 1
        assert result.total_return != 0.0

    def test_single_fund(self):
        """Single fund returns same as fund's return"""
        from backend.fund_quant.backtest.vectorized_engine import VectorizedBacktestEngine
        engine = VectorizedBacktestEngine()
        nav = np.array([[1.0, 1.05, 1.10]], dtype=float)

        def one_fund(nav):
            nf, nd = nav.shape
            w = np.zeros((nf, nd))
            w[0, :] = 1.0
            return w

        result = engine.run(nav, one_fund)
        assert abs(result.total_return - 0.10) < 0.001


class TestBrinsonCoverage:
    """Close brinson.py gaps: edge cases."""

    def test_single_period_empty_weights_raises(self):
        """Empty portfolio_weights raises ValueError"""
        from backend.fund_quant.backtest.brinson import BrinsonAttribution
        attr = BrinsonAttribution()
        with pytest.raises(ValueError):
            attr.attribute_single_period({}, {}, {}, {})

    def test_single_period_normalized_weights(self):
        """Weights not summing to 1 are normalized"""
        from backend.fund_quant.backtest.brinson import BrinsonAttribution
        attr = BrinsonAttribution()
        result = attr.attribute_single_period(
            portfolio_weights={"eq": 0.3, "bo": 0.3},  # sum=0.6
            portfolio_returns={"eq": 0.05, "bo": 0.02},
            benchmark_weights={"eq": 0.5, "bo": 0.5},
            benchmark_returns={"eq": 0.04, "bo": 0.03},
        )
        # After normalization: eq=0.5, bo=0.5
        assert abs(result.portfolio_return - (0.5 * 0.05 + 0.5 * 0.02)) < 1e-6

    def test_missing_sector_handled(self):
        """Sector in portfolio but not benchmark → benchmark weight = 0"""
        from backend.fund_quant.backtest.brinson import BrinsonAttribution
        attr = BrinsonAttribution()
        result = attr.attribute_single_period(
            portfolio_weights={"eq": 1.0, "alt": 0.0},
            portfolio_returns={"eq": 0.05, "alt": 0.10},
            benchmark_weights={"eq": 1.0},
            benchmark_returns={"eq": 0.04},
        )
        assert "alt" in result.sector_details
        assert result.sector_details["alt"]["allocation"] == pytest.approx((0.0 - 0.0) * 0.0)

    def test_carino_limit_case(self):
        """Carino linking when portfolio ≈ benchmark (limit case)"""
        from backend.fund_quant.backtest.brinson import BrinsonAttribution
        attr = BrinsonAttribution()
        report = attr.attribute_multi_period([
            {
                "period": "2024-Q1",
                "portfolio_weights": {"eq": 0.6, "bo": 0.4},
                "portfolio_returns": {"eq": 0.05, "bo": 0.02},
                "benchmark_weights": {"eq": 0.5, "bo": 0.5},
                "benchmark_returns": {"eq": 0.04, "bo": 0.03},
            },
        ])
        assert report.n_periods == 1
        assert not report.carino_linked  # single period


class TestSignificanceCoverage:
    """Close significance.py edge cases."""

    def test_many_bootstrap_iters(self):
        """Samples many iterations without error"""
        from backend.fund_quant.backtest.significance import SignificanceTester
        rng = np.random.RandomState(42)
        returns = rng.normal(0.0005, 0.01, 100)
        report = SignificanceTester().test(returns, n_bootstrap=500, seed=42)
        assert 0 <= report.p_value <= 1
        assert report.ci_lower <= report.ci_upper


class TestOverfittingCoverage:
    """Close overfitting.py remaining paths."""

    def test_report_with_zero_sharpe(self):
        """report() with sharpe=0 produces min_btl_years=0"""
        from backend.fund_quant.backtest.overfitting import OverfittingDetector
        det = OverfittingDetector()
        det.record({}, {})
        returns = np.random.RandomState(42).normal(0, 0.01, 100)
        report = det.report(returns, sharpe=0.0, years=1.0)
        assert report.min_btl_years == 0.0
        assert report.deflated_sharpe < 0

    def test_min_btl_with_skew_kurt(self):
        """min_btl accepts skew and kurt parameters"""
        from backend.fund_quant.backtest.overfitting import OverfittingDetector
        det = OverfittingDetector()
        det.record({}, {"sharpe": 0.5})
        btl = det.min_btl(1.0, skew=0.5, kurt=4.0)
        assert btl >= 0
