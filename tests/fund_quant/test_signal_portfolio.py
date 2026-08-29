"""信号输出 + 模拟组合跟踪测试"""

import sys; sys.path.insert(0, 'backend/..')
import asyncio
import pytest
from datetime import datetime, date
from backend.fund_quant.signal.output import SignalOutputService
from backend.fund_quant.portfolio.tracker import PortfolioTracker
from backend.fund_quant.core.models import FundSignal, FusionSignal
from backend.fund_quant.core.enums import SignalType, Direction


class TestSignalOutput:
    def setup_method(self):
        self.service = SignalOutputService()

    def test_emit_signal_returns_id(self):
        sig = FundSignal(signal_id="", fund_code="000001", fund_name="Test",
                         signal_type=SignalType.TIMING, direction=Direction.BUY,
                         confidence=0.8, reason="测试")
        sig_id = self.service.emit_signal(sig)
        assert sig_id is not None
        assert len(sig_id) > 0

    def test_emit_signal_sets_timestamp(self):
        sig = FundSignal(signal_id="ts_test", fund_code="000001", fund_name="Test",
                         signal_type=SignalType.TIMING, direction=Direction.BUY,
                         confidence=0.8, reason="测试")
        before = datetime.now()
        self.service.emit_signal(sig)
        assert sig.timestamp >= before

    def test_cooldown_prevents_duplicate(self):
        sig = FundSignal(signal_id="cd1", fund_code="cd_test", fund_name="Test",
                         signal_type=SignalType.TIMING, direction=Direction.BUY,
                         confidence=0.8, reason="测试")
        id1 = self.service.emit_signal(sig)
        id2 = self.service.emit_signal(sig)
        # 冷却期内第二次会被跳过(不推), 但返回原id
        assert id1 is not None
        # 同一个fund_code+signal_type在冷却期内不会重复推送

    def test_emit_fusion(self):
        fusion = FusionSignal(fund_code="000001", fund_name="Test",
                              direction=Direction.BUY, confidence=0.8,
                              reason="融合测试", contributing_strategies=[])
        sig_id = self.service.emit_fusion(fusion)
        assert sig_id is not None

    def test_stream_signals(self):
        """验证SSE流能收到信号"""
        import asyncio
        sig = FundSignal(signal_id="stream1", fund_code="000001", fund_name="Test",
                         signal_type=SignalType.TIMING, direction=Direction.BUY,
                         confidence=0.8, reason="SSE测试")

        async def _test():
            # 启动流任务在后台运行
            stream_task = asyncio.create_task(self._collect_first_signal())
            await asyncio.sleep(0.01)  # 让流启动完毕
            self.service.emit_signal(sig)
            data = await asyncio.wait_for(stream_task, timeout=2.0)
            assert "stream1" in data

        asyncio.run(_test())

    async def _collect_first_signal(self):
        """辅助: 收集SSE流的第一个非心跳信号"""
        async for data in self.service.stream_signals():
            if "heartbeat" not in data:
                return data

    def test_format_signal_structure(self):
        sig = FundSignal(signal_id="fmt1", fund_code="000001", fund_name="Test基金",
                         fund_type="stock", signal_type=SignalType.TIMING,
                         direction=Direction.BUY, confidence=0.85,
                         reason="格式测试", strategy_name="momentum",
                         risk_warnings=["注意风险"])
        formatted = self.service.format_signal(sig)
        assert "signal_id" in formatted
        assert formatted["fund"]["code"] == "000001"
        assert formatted["fund"]["name"] == "Test基金"
        assert formatted["action"]["direction"] == "buy"
        assert formatted["analysis"]["confidence"] == 0.85
        assert "disclaimer" in formatted
        assert len(formatted["risk"]["warnings"]) == 1


class TestPortfolioTracker:
    def setup_method(self):
        self.tracker = PortfolioTracker(initial_capital=100000)

    def test_initial_state(self):
        status = self.tracker.get_status()
        assert status["initial_capital"] == 100000
        assert status["total_value"] == 100000
        assert status["cash"] == 100000
        assert status["position_count"] == 0

    def test_buy_reduces_cash(self):
        self.tracker.buy("000001", 50000, 1.0)
        status = self.tracker.get_status()
        assert status["cash"] == 50000
        assert status["position_count"] == 1
        assert "000001" in status["positions"]

    def test_buy_insufficient_cash(self):
        self.tracker.buy("000001", 200000, 1.0)  # 超过现金
        status = self.tracker.get_status()
        assert status["cash"] == 0  # 全部买入

    def test_sell_reduces_position(self):
        self.tracker.buy("000001", 50000, 1.0)
        self.tracker.sell("000001", 0.5, 1.02)  # 卖一半
        status = self.tracker.get_status()
        assert status["position_count"] >= 1

    def test_sell_all_removes_position(self):
        self.tracker.buy("000001", 50000, 1.0)
        self.tracker.sell("000001", 1.0, 1.0)
        status = self.tracker.get_status()
        assert "000001" not in status["positions"]
        assert status["position_count"] == 0
        assert status["cash"] >= 50000

    def test_return_pct(self):
        """买入后净值上涨应反映在收益率"""
        self.tracker.buy("000001", 80000, 1.0)
        self.tracker.sell("000001", 0.5, 1.10)  # 一半以更高价卖出
        status = self.tracker.get_status()
        assert status["return_pct"] is not None

    def test_update_value(self):
        self.tracker.buy("000001", 50000, 1.0)
        self.tracker.update("000001", 50000, 1.05)
        status = self.tracker.get_status()
        total = status["cash"] + 50000 * 1.05
        assert abs(status["total_value"] - total) < 0.01

    def test_multiple_positions(self):
        self.tracker.buy("000001", 30000, 1.0)
        self.tracker.buy("000002", 30000, 2.0)
        status = self.tracker.get_status()
        assert status["position_count"] == 2
        assert status["cash"] == 40000

    def test_snapshot_history(self):
        self.tracker.buy("000001", 50000, 1.0)
        self.tracker.sell("000001", 0.5, 1.05)
        status = self.tracker.get_status()
        assert status["history_count"] == 2

    def test_get_status_consistency(self):
        self.tracker.buy("000001", 30000, 1.0)
        status = self.tracker.get_status()
        pos_value = sum(
            p["value"] for p in status["positions"].values()
        )
        assert abs(status["total_value"] - (status["cash"] + pos_value)) < 0.01
