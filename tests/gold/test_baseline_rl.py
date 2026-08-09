"""基线规则策略 (baseline_rl) 单元测试"""

from datetime import datetime, timedelta
import numpy as np

from backend.gold.strategy.baseline_rl import BaselineRLStrategy
from backend.gold.strategy.base import StrategyRegistry
from backend.gold.core.models import GoldBarData, GoldPosition, SignalDirection


class _MockContext:
    """模拟回测上下文，捕获信号"""
    def __init__(self):
        self.signals = []
        self._position = None
        self._balance = 1_000_000

    @property
    def mode(self):
        return "backtest"

    def on_signal(self, signal):
        self.signals.append(signal)

    def get_position(self, symbol):
        return self._position

    def get_balance(self):
        return self._balance


def _make_bar(close: float, high: float = None, low: float = None,
              dt: datetime = None, symbol: str = "AU0") -> GoldBarData:
    h = high or close * 1.005
    l = low or close * 0.995
    return GoldBarData(
        symbol=symbol, exchange="SHFE", period="1d",
        datetime=dt or datetime.now(),
        open=close, high=h, low=l, close=close, volume=100,
    )


def _make_trend_bars(n: int, start_price: float, trend: float = 0.0,
                     vol: float = 0.005) -> list[GoldBarData]:
    """生成趋势或震荡K线序列"""
    bars = []
    price = start_price
    base_dt = datetime(2024, 1, 1)
    for i in range(n):
        noise = np.random.randn() * vol * price
        price = price * (1 + trend) + noise
        price = max(price, 1)
        bars.append(_make_bar(
            round(price, 2),
            dt=base_dt + timedelta(days=i),
        ))
    return bars


# ===== 测试用例 =====

def test_registration():
    """策略应通过 @StrategyRegistry.register("baseline_rl") 注册"""
    cls = StrategyRegistry.get("baseline_rl")
    assert cls is not None, "baseline_rl 未注册"
    assert cls == BaselineRLStrategy


def test_default_params():
    """默认参数应包含所有必需的参数"""
    s = BaselineRLStrategy()
    assert s.ma_periods == 20
    assert s.atr_period == 14
    assert s.atr_channel_multiplier == 2.0
    assert s.atr_stop_multiplier == 2.0
    assert s.position_size == 1


def test_custom_params():
    """构造函数应覆盖默认参数"""
    s = BaselineRLStrategy(ma_periods=10, atr_period=7, atr_channel_multiplier=1.5)
    assert s.ma_periods == 10
    assert s.atr_period == 7
    assert s.atr_channel_multiplier == 1.5


def test_long_signal_on_uptrend():
    """
    强上升趋势中，价格突破 MA+2*ATR 上轨 → 应产生做多信号
    """
    ctx = _MockContext()
    s = BaselineRLStrategy()
    s.set_context(ctx)
    s.on_init(ctx)

    # 先走一段温和行情积累指标，然后拉出突破
    bars = _make_trend_bars(40, 400, trend=0.002)  # 温和上升

    # 最后几根大幅拉升制造突破
    for bar in bars[:-1]:
        s.on_bar(bar)

    # 最后一根大幅拉升
    final_bar = bars[-1]
    final_bar.close = final_bar.close * 1.03  # 3% 突破
    final_bar.high = final_bar.close * 1.005
    s.on_bar(final_bar)

    signals = ctx.signals
    long_signals = [sg for sg in signals if sg.direction == SignalDirection.LONG]
    assert len(long_signals) >= 1, (
        f"应产生做多信号，实际信号: {[(sg.direction, sg.reason) for sg in signals]}"
    )


def test_short_signal_on_downtrend():
    """
    强下降趋势中，价格跌破 MA-2*ATR 下轨 → 应产生做空信号
    """
    np.random.seed(42)
    ctx = _MockContext()
    s = BaselineRLStrategy()
    s.set_context(ctx)
    s.on_init(ctx)

    bars = _make_trend_bars(40, 450, trend=-0.002)  # 温和下降

    for bar in bars[:-1]:
        s.on_bar(bar)

    # 确保最后一根大幅跌破下轨
    final_bar = bars[-1]
    lower_band = s._ma_value - s.atr_channel_multiplier * s._atr_value
    final_bar.close = round(lower_band * 0.98, 2)  # 跌破下轨 2%
    final_bar.low = round(final_bar.close * 0.995, 2)
    s.on_bar(final_bar)

    signals = ctx.signals
    short_signals = [sg for sg in signals if sg.direction == SignalDirection.SHORT]
    assert len(short_signals) >= 1, (
        f"应产生做空信号，实际信号: {[(sg.direction, sg.reason) for sg in signals]}"
    )


def test_flat_range_no_signal():
    """
    窄幅震荡，价格在通道内 → 不应产生信号
    """
    ctx = _MockContext()
    s = BaselineRLStrategy()
    s.set_context(ctx)
    s.on_init(ctx)

    # 平稳震荡
    bars = _make_trend_bars(50, 400, trend=0.0, vol=0.001)

    for bar in bars:
        s.on_bar(bar)

    signals = ctx.signals
    entry_signals = [sg for sg in signals if sg.direction in (SignalDirection.LONG, SignalDirection.SHORT)]
    # 窄幅震荡不应触发通道突破
    assert len(entry_signals) == 0, (
        f"窄幅震荡不应产生开仓信号，实际: {[(sg.direction, sg.reason) for sg in entry_signals]}"
    )


def test_atr_stop_loss():
    """
    持仓后价格反向运动超过 ATR 止损 → 应触发止损出场信号
    """
    ctx = _MockContext()
    s = BaselineRLStrategy()
    s.set_context(ctx)
    s.on_init(ctx)

    # 先造上升趋势开多
    bars = _make_trend_bars(40, 400, trend=0.002)
    for bar in bars[:-1]:
        s.on_bar(bar)

    # 拉升突破
    final_bar = bars[-1]
    final_bar.close = final_bar.close * 1.03
    final_bar.high = final_bar.close * 1.005
    s.on_bar(final_bar)
    assert len(ctx.signals) >= 1
    assert ctx.signals[-1].direction == SignalDirection.LONG

    # 反向暴跌触发止损
    drop_bar = _make_bar(
        s._entry_price - s._atr_value * s.atr_stop_multiplier * 0.99,
        dt=final_bar.datetime + timedelta(days=1),
    )
    s.on_bar(drop_bar)

    close_signals = [sg for sg in ctx.signals if sg.direction == SignalDirection.CLOSE_LONG]
    assert len(close_signals) >= 1, "应触发止损出场"


def test_ma_reverse_exit():
    """
    多头持仓后价格跌回 MA → 应触发MA出场
    """
    ctx = _MockContext()
    s = BaselineRLStrategy()
    s.set_context(ctx)
    s.on_init(ctx)

    bars = _make_trend_bars(40, 400, trend=0.002)
    for bar in bars[:-1]:
        s.on_bar(bar)

    final_bar = bars[-1]
    final_bar.close = final_bar.close * 1.03
    final_bar.high = final_bar.close * 1.005
    s.on_bar(final_bar)
    assert ctx.signals[-1].direction == SignalDirection.LONG
    ma_value = s._ma_value

    # 价格跌回MA以下
    reverse_bar = _make_bar(ma_value * 0.995, dt=final_bar.datetime + timedelta(days=1))
    s.on_bar(reverse_bar)

    close_signals = [sg for sg in ctx.signals if sg.direction == SignalDirection.CLOSE_LONG]
    assert len(close_signals) >= 1, "应触发MA出场"


def test_param_sensitivity():
    """
    参数敏感性：更敏感的通道（更小ma_periods, 更小atr_channel_multiplier）
    应在相同行情下产生更多信号
    """
    bars = _make_trend_bars(60, 400, trend=0.003)

    ctx_sensitive = _MockContext()
    sensitive = BaselineRLStrategy(ma_periods=10, atr_channel_multiplier=1.0)
    sensitive.set_context(ctx_sensitive)
    sensitive.on_init(ctx_sensitive)

    ctx_conservative = _MockContext()
    conservative = BaselineRLStrategy(ma_periods=30, atr_channel_multiplier=3.0)
    conservative.set_context(ctx_conservative)
    conservative.on_init(ctx_conservative)

    for bar in bars:
        sensitive.on_bar(bar)
        conservative.on_bar(bar)

    sensitive_entries = len([sg for sg in ctx_sensitive.signals
                            if sg.direction in (SignalDirection.LONG, SignalDirection.SHORT)])
    conservative_entries = len([sg for sg in ctx_conservative.signals
                                if sg.direction in (SignalDirection.LONG, SignalDirection.SHORT)])

    assert sensitive_entries >= conservative_entries, (
        f"敏感参数应产生更多信号: sensitive={sensitive_entries} < conservative={conservative_entries}"
    )


if __name__ == "__main__":
    test_registration()
    test_default_params()
    test_custom_params()
    test_long_signal_on_uptrend()
    test_short_signal_on_downtrend()
    test_flat_range_no_signal()
    test_atr_stop_loss()
    test_ma_reverse_exit()
    test_param_sensitivity()
    print("所有测试通过")