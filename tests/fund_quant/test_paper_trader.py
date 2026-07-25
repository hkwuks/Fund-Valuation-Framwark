"""测试模拟交易模块 (PaperTrader)"""

import os
import pickle
import tempfile
from datetime import date
from pathlib import Path

import pytest

from backend.fund_quant.backtest.paper_trader import (
    FundPaperTrader,
    PaperTradeState,
    PaperTradeSummary,
)


class TestFundPaperTrader:
    """FundPaperTrader 单元测试 — 状态管理 + 持久化 + 幂等性"""

    @pytest.fixture
    def trader(self):
        tmpdir = tempfile.mkdtemp()
        yield FundPaperTrader(state_dir=tmpdir)

    # ── 启动 ──

    def test_start_new_session(self, trader):
        """start() 创建正确初始状态。"""
        state = trader.start("strat_a", ["000001", "000002"], initial_capital=200000.0)

        assert state.paper_trade_id is not None
        assert state.strategy_name == "strat_a"
        assert state.fund_codes == ["000001", "000002"]
        assert state.initial_capital == 200000.0
        assert state.cash == 200000.0
        assert state.positions == {}
        assert state.status == "running"
        assert state.last_run_date is None

    # ── 每日运行 (无策略) ──

    def test_daily_run_no_strategy(self, trader):
        """无策略时只确认订单并记录净值曲线。"""
        state = trader.start("noop", ["000001"])
        navs = {
            "000001": [
                {"date": date(2025, 1, 2), "nav": 1.0},
                {"date": date(2025, 1, 3), "nav": 1.01},
            ],
        }
        result = trader.daily_run(state.paper_trade_id, navs, run_date=date(2025, 1, 2))
        assert result is not None
        assert len(result.equity_curve) == 1
        assert result.equity_curve[0]["total_value"] == 100000.0  # 纯现金

    # ── 持久化 ──

    def test_persistence_save_load(self, trader):
        """状态保存后能完整加载。"""
        orig = trader.start("persist", ["000001", "000002"], initial_capital=50000.0)
        # 模拟一次运行
        navs = {
            "000001": [{"date": date(2025, 1, 2), "nav": 1.0}],
            "000002": [{"date": date(2025, 1, 2), "nav": 2.0}],
        }
        trader.daily_run(orig.paper_trade_id, navs, run_date=date(2025, 1, 2))

        loaded = trader.get_status(orig.paper_trade_id)
        assert loaded is not None
        assert loaded.paper_trade_id == orig.paper_trade_id
        assert loaded.strategy_name == orig.strategy_name
        assert loaded.fund_codes == orig.fund_codes
        assert loaded.initial_capital == orig.initial_capital
        assert loaded.cash == orig.cash
        assert loaded.status == "running"
        assert len(loaded.equity_curve) == 1

    # ── 停止 ──

    def test_stop_session(self, trader):
        """stop() 标记 status=stopped 并持久化。"""
        state = trader.start("stop_me", ["000001"])
        stopped = trader.stop(state.paper_trade_id)
        assert stopped.status == "stopped"

        loaded = trader.get_status(state.paper_trade_id)
        assert loaded.status == "stopped"

    # ── 重复入日保护 ──

    def test_reentry_protection(self, trader):
        """同一天两次 daily_run 只处理一次。"""
        state = trader.start("reentry", ["000001"])
        navs = {
            "000001": [{"date": date(2025, 1, 2), "nav": 1.0}],
        }
        trader.daily_run(state.paper_trade_id, navs, run_date=date(2025, 1, 2))
        trader.daily_run(state.paper_trade_id, navs, run_date=date(2025, 1, 2))

        loaded = trader.get_status(state.paper_trade_id)
        assert len(loaded.equity_curve) == 1

    # ── 空 fund_codes ──

    def test_empty_fund_codes_raises(self, trader):
        """空 fund_codes 抛出 ValueError。"""
        with pytest.raises(ValueError, match="fund_codes"):
            trader.start("empty", [])

    # ── 列示会话 ──

    def test_list_sessions(self, trader):
        """多个会话出现在 list_sessions 中。"""
        s1 = trader.start("a", ["000001"])
        s2 = trader.start("b", ["000002"])
        s3 = trader.start("c", ["000003"])

        sessions = trader.list_sessions()
        ids = [s.paper_trade_id for s in sessions]
        assert s1.paper_trade_id in ids
        assert s2.paper_trade_id in ids
        assert s3.paper_trade_id in ids

    # ── 无效 ID ──

    def test_invalid_trade_id(self, trader):
        """不存在的 ID 返回 None。"""
        assert trader.get_status("nonexistent") is None
        assert trader.stop("nonexistent") is None

    # ── 损坏的 pickle ──

    def test_corrupt_pickle(self, trader):
        """损坏的 pickle 文件被优雅忽略 (返回 None)。"""
        state = trader.start("corrupt", ["000001"])
        path = Path(trader._state_dir) / f"{state.paper_trade_id}.pkl"
        with open(path, "wb") as f:
            f.write(b"not a valid pickle at all")

        assert trader.get_status(state.paper_trade_id) is None
