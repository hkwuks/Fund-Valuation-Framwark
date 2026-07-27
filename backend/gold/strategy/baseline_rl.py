"""
动量+ATR通道突破 — RL基线对比策略

逻辑:
  1. 计算 N 日移动平均线 (MA)
  2. 计算 ATR(14) 作为波动率通道
  3. 价格突破 MA + K*ATR → 做多, 突破 MA - K*ATR → 做空
  4. ATR 止损 (2 * ATR)
  5. 波动率平价仓位 (calc_position_size)
"""

from backend.gold.strategy.base import StrategyBase, StrategyRegistry, StrategyContext
from backend.gold.core.models import GoldBarData, SignalDirection


@StrategyRegistry.register("baseline_rl")
class BaselineRLStrategy(StrategyBase):
    """动量+ATR通道突破 — RL基线对比策略"""

    strategy_name = "baseline_rl"
    strategy_type = "baseline_rl"
    description = "动量+ATR通道突破 — RL基线对比策略"
    default_params = {
        "ma_periods": 20,
        "atr_period": 14,
        "atr_channel_multiplier": 2.0,
        "atr_stop_multiplier": 2.0,
        "position_size": 1,
        "target_vol_pct": 0.10,
    }
    param_ranges = {
        "ma_periods": [10, 15, 20, 30, 40, 60],
        "atr_period": [7, 10, 14, 20, 30],
        "atr_channel_multiplier": [1.0, 1.5, 2.0, 2.5, 3.0],
        "atr_stop_multiplier": [1.0, 1.5, 2.0, 2.5, 3.0],
    }

    def on_init(self, context: StrategyContext):
        self._bars: list[GoldBarData] = []
        self._ma_value: float = 0.0
        self._atr_value: float = 0.0
        self._position: int = 0   # 1=long, -1=short, 0=flat
        self._entry_price: float = 0.0

    def on_bar(self, bar: GoldBarData):
        self._bars.append(bar)
        max_history = max(self.ma_periods, self.atr_period) + 10
        if len(self._bars) > max_history:
            self._bars = self._bars[-max_history:]

        self._calculate_indicators()

        if len(self._bars) < max(self.ma_periods, self.atr_period + 1):
            return

        self._check_signals(bar)

    def _calculate_indicators(self):
        closes = [b.close for b in self._bars]

        # MA
        if len(closes) >= self.ma_periods:
            self._ma_value = sum(closes[-self.ma_periods:]) / self.ma_periods

        # ATR
        if len(self._bars) >= self.atr_period + 1:
            trs = []
            for i in range(1, len(self._bars)):
                bar, prev = self._bars[i], self._bars[i - 1]
                tr = max(bar.high - bar.low,
                         abs(bar.high - prev.close),
                         abs(bar.low - prev.close))
                trs.append(tr)
            self._atr_value = sum(trs[-self.atr_period:]) / self.atr_period

    def _check_signals(self, bar: GoldBarData):
        if self._ma_value <= 0 or self._atr_value <= 0:
            return

        price = bar.close
        dt = bar.datetime
        upper_band = self._ma_value + self.atr_channel_multiplier * self._atr_value
        lower_band = self._ma_value - self.atr_channel_multiplier * self._atr_value

        # 进场
        if self._position == 0:
            if price > upper_band:
                vol = self.calc_position_size(price, self._atr_value)
                sl = price - self._atr_value * self.atr_stop_multiplier
                self.emit_signal(SignalDirection.LONG, bar.symbol, price,
                                 vol, stop_loss=sl,
                                 confidence=0.6,
                                 reason=f"MA+ATR通道突破做多 price={price:.2f} MA={self._ma_value:.2f} "
                                        f"upper={upper_band:.2f} ATR={self._atr_value:.2f}",
                                 bar_datetime=dt)
                self._position = 1
                self._entry_price = price

            elif price < lower_band:
                vol = self.calc_position_size(price, self._atr_value)
                sl = price + self._atr_value * self.atr_stop_multiplier
                self.emit_signal(SignalDirection.SHORT, bar.symbol, price,
                                 vol, stop_loss=sl,
                                 confidence=0.6,
                                 reason=f"MA-ATR通道突破做空 price={price:.2f} MA={self._ma_value:.2f} "
                                        f"lower={lower_band:.2f} ATR={self._atr_value:.2f}",
                                 bar_datetime=dt)
                self._position = -1
                self._entry_price = price

        # 出场
        elif self._position == 1:
            if price < self._entry_price - self._atr_value * self.atr_stop_multiplier:
                self.emit_signal(SignalDirection.CLOSE_LONG, bar.symbol, price,
                                 self.position_size, reason="ATR止损",
                                 bar_datetime=dt)
                self._position = 0
            elif price < self._ma_value:
                self.emit_signal(SignalDirection.CLOSE_LONG, bar.symbol, price,
                                 self.position_size, reason="价格跌破MA出场",
                                 bar_datetime=dt)
                self._position = 0

        elif self._position == -1:
            if price > self._entry_price + self._atr_value * self.atr_stop_multiplier:
                self.emit_signal(SignalDirection.CLOSE_SHORT, bar.symbol, price,
                                 self.position_size, reason="ATR止损",
                                 bar_datetime=dt)
                self._position = 0
            elif price > self._ma_value:
                self.emit_signal(SignalDirection.CLOSE_SHORT, bar.symbol, price,
                                 self.position_size, reason="价格突破MA出场",
                                 bar_datetime=dt)
                self._position = 0