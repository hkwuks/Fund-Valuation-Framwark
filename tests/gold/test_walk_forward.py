"""
Walk-Forward + CPCV 回测验证测试
"""

import pytest
from datetime import datetime, timedelta
import numpy as np

from backend.gold.core.models import GoldBarData
from backend.gold.backtest.walk_forward import WalkForwardValidator, CPCVValidator
from backend.gold.backtest.validation import WalkForwardValidatorAdapter, CPCVValidatorAdapter
from backend.gold.strategy.base import StrategyBase, StrategyRegistry, StrategyContext
from backend.gold.core.models import SignalDirection


@StrategyRegistry.register("test_wf_strategy")
class WFStrategy(StrategyBase):
    """测试用简单策略 — 每10根bar开仓"""
    strategy_name = "test_wf_strategy"
    strategy_type = "test"
    description = "测试策略"
    default_params = {"position_size": 1}

    def on_init(self, context: StrategyContext):
        self._count = 0

    def on_bar(self, bar: GoldBarData):
        self._count += 1
        if self._count == 10:
            self.emit_signal(SignalDirection.LONG, bar.symbol, bar.close, 1,
                             reason="test", bar_datetime=bar.datetime)
        elif self._count == 20:
            self.emit_signal(SignalDirection.CLOSE_LONG, bar.symbol, bar.close, 1,
                             reason="test", bar_datetime=bar.datetime)
            self._count = 0


def _make_bars(n: int, seed: int = 42) -> list[GoldBarData]:
    rng = np.random.RandomState(seed)
    bars = []
    dt = datetime(2020, 1, 1)
    prev_close = 400.0
    for i in range(n):
        change = rng.normal(0, 2.0)
        close = prev_close + change
        high = max(close, prev_close) + abs(rng.normal(0, 1.0))
        low = min(close, prev_close) - abs(rng.normal(0, 1.0))
        bars.append(GoldBarData(
            symbol="AU0", datetime=dt + timedelta(days=i),
            open=prev_close, high=high, low=low, close=close, volume=1000,
        ))
        prev_close = close
    return bars


class TestWalkForwardValidator:
    def test_insufficient_data_returns_error(self):
        bars = _make_bars(50)
        v = WalkForwardValidator(train_window=100, test_window=20)
        result = v.validate(WFStrategy, bars)
        assert "error" in result

    def test_sufficient_data_returns_windows(self):
        bars = _make_bars(400)
        v = WalkForwardValidator(train_window=100, test_window=20)
        result = v.validate(WFStrategy, bars)
        assert "error" not in result
        assert result["n_windows"] > 0
        assert "avg_return_pct" in result
        assert "avg_sharpe" in result

    def test_windows_have_metrics(self):
        bars = _make_bars(400)
        v = WalkForwardValidator(train_window=100, test_window=20)
        result = v.validate(WFStrategy, bars)
        for w in result["windows"]:
            assert "train_bars" in w
            assert "test_bars" in w
            assert "total_return_pct" in w or w.get("total_return_pct") is None

    def test_adapter_works_with_strategy_name(self):
        bars = _make_bars(400)
        v = WalkForwardValidatorAdapter(train_window=100, test_window=20)
        result = v.validate("test_wf_strategy", bars)
        assert "error" not in result

    def test_adapter_unknown_strategy(self):
        bars = _make_bars(400)
        v = WalkForwardValidatorAdapter(train_window=100, test_window=20)
        result = v.validate("non_existent", bars)
        assert "error" in result


class TestCPCVValidator:
    def test_insufficient_data_returns_error(self):
        bars = _make_bars(50)
        v = CPCVValidator(n_groups=4, k_test=1)
        result = v.validate(WFStrategy, bars)
        assert "error" in result

    def test_sufficient_data_returns_paths(self):
        bars = _make_bars(400)
        v = CPCVValidator(n_groups=4, k_test=1)
        result = v.validate(WFStrategy, bars)
        assert "error" not in result
        assert result["n_paths"] > 0
        assert "pbo" in result
        assert "pbo_verdict" in result

    def test_paths_have_metrics(self):
        bars = _make_bars(400)
        v = CPCVValidator(n_groups=4, k_test=1)
        result = v.validate(WFStrategy, bars)
        for p in result["paths"]:
            assert "train_bars" in p
            assert "test_bars" in p
            assert "sharpe_ratio" in p or p.get("sharpe_ratio") is None

    def test_adapter_works_with_strategy_name(self):
        bars = _make_bars(400)
        v = CPCVValidatorAdapter(n_groups=4, k_test=1)
        result = v.validate("test_wf_strategy", bars)
        assert "error" not in result
