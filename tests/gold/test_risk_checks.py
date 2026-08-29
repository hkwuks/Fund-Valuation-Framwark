"""风控检查测试"""

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from datetime import datetime, date
import pytest

from gold.core.models import GoldSignal, SignalDirection, RiskLevel
from gold.risk.checks import RiskChecker
from gold.core.config import GoldSettings


class TestRiskChecker:
    def test_passes_clean_signal(self):
        signal = GoldSignal(
            signal_id="t1", strategy_id="test", strategy_name="test",
            symbol="AU0", direction=SignalDirection.LONG,
            price=500, volume=1, created_at=datetime.now(),
        )
        checker = RiskChecker()
        result = checker.check(signal, current_equity=1_000_000, initial_capital=1_000_000)
        assert result.passed is True

    def test_drawdown_rejects(self):
        signal = GoldSignal(
            signal_id="t1", strategy_id="test", strategy_name="test",
            symbol="AU0", direction=SignalDirection.LONG,
            price=500, volume=1, created_at=datetime.now(),
        )
        cfg = GoldSettings(max_drawdown_pct=0.10)
        checker = RiskChecker(config=cfg)
        result = checker.check(signal, current_equity=800_000, initial_capital=1_000_000)
        assert result.passed is False
        assert result.risk_level == RiskLevel.REJECT

    def test_drawdown_warning_at_80pct(self):
        signal = GoldSignal(
            signal_id="t2", strategy_id="test", strategy_name="test",
            symbol="AU0", direction=SignalDirection.LONG,
            price=500, volume=1, created_at=datetime.now(),
        )
        cfg = GoldSettings(max_drawdown_pct=0.10)
        checker = RiskChecker(config=cfg)
        # 8.1% drawdown is 81% of 10% limit → WARNING (8.0% exactly is not > 80%)
        result = checker.check(signal, current_equity=919_000, initial_capital=1_000_000)
        assert result.passed is True  # WARNING doesn't reject
        assert result.risk_level == RiskLevel.WARNING

    def test_skip_check_when_no_equity(self):
        signal = GoldSignal(
            signal_id="t3", strategy_id="test", strategy_name="test",
            symbol="AU0", direction=SignalDirection.LONG,
            price=500, volume=1, created_at=datetime.now(),
        )
        checker = RiskChecker()
        result = checker.check(signal)  # no equity params
        assert result.passed is True

    def test_signal_frequency_warns(self):
        cfg = GoldSettings(max_daily_signals=20)
        checker = RiskChecker(config=cfg)
        freq_result = checker._check_signal_frequency(
            GoldSignal(signal_id="f1", strategy_id="test", strategy_name="test",
                       symbol="AU0", direction=SignalDirection.LONG,
                       price=500, volume=1, created_at=datetime.now()),
            checker._get_daily(date.today().isoformat()),
        )
        # With no prior signals today, should pass
        assert freq_result["passed"] is True
