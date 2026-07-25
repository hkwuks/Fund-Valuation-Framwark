"""测试巨额赎回限制"""

import pytest
from backend.fund_quant.backtest.redemption_gate import RedemptionGate


class TestRedemptionGate:
    def test_below_10pct_passes(self):
        gate = RedemptionGate(LARGE_REDEMPTION_PCT=0.10)
        verdict = gate.check("000001", sell_shares=500, total_shares=10000)
        assert verdict.passed is True

    def test_10_to_20pct_partial_accept(self):
        gate = RedemptionGate(LARGE_REDEMPTION_PCT=0.10)
        verdict = gate.check("000001", sell_shares=1500, total_shares=10000)
        assert verdict.passed is False
        assert verdict.max_accepted > 0

    def test_over_20pct_fully_rejected(self):
        gate = RedemptionGate(LARGE_REDEMPTION_PCT=0.10)
        verdict = gate.check("000001", sell_shares=3000, total_shares=10000)
        assert verdict.passed is False
        assert verdict.max_accepted == 0

    def test_consecutive_triggers_suspends(self):
        gate = RedemptionGate(LARGE_REDEMPTION_PCT=0.10, consecutive_limit=2)
        # First trigger
        gate.check("000002", sell_shares=1500, total_shares=10000)
        # Second trigger — should suspend
        verdict = gate.check("000002", sell_shares=1500, total_shares=10000)
        assert verdict.passed is False
        assert verdict.max_accepted == 0
        assert "暂停赎回" in verdict.reason
        # Subsequent check — still suspended
        verdict = gate.check("000002", sell_shares=1500, total_shares=10000)
        assert verdict.passed is False
        assert "暂停赎回" in verdict.reason

    def test_clear_suspension(self):
        gate = RedemptionGate(LARGE_REDEMPTION_PCT=0.10, consecutive_limit=2)
        gate.check("000003", sell_shares=1500, total_shares=10000)
        gate.check("000003", sell_shares=1500, total_shares=10000)
        assert "000003" in gate._suspended
        gate.clear_suspension("000003")
        assert "000003" not in gate._suspended
        verdict = gate.check("000003", sell_shares=500, total_shares=10000)
        assert verdict.passed is True

    def test_zero_total_shares_passes(self):
        gate = RedemptionGate(LARGE_REDEMPTION_PCT=0.10)
        verdict = gate.check("000004", sell_shares=0, total_shares=0)
        assert verdict.passed is True
