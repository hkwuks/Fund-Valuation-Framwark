"""黄金领域适配 — GoldDomainAdapter + FuturesCostModel + 旧策略包装器"""
from __future__ import annotations

from typing import Optional
from datetime import datetime

from core import (
    DomainAdapter, ExecutionEngine, CostModel,
    RiskCheck, Strategy, StrategyRegistry,
    FuturesExecutionEngine, PercentageSlippage,
    Signal, Direction, Fill,
    DataFeed, Bar,
)


class FuturesCostModel(CostModel):
    """期货手续费模型 — 区分开/平/平今

    开仓10元/手，平仓10元/手，平今免费（SHFE AU）。
    滑点由 FuturesExecutionEngine 独立处理，此处只算手续费。

    平今判定：通过 signal.extra["is_close_today"] 传递（由 FuturesExecutionEngine 设置）。
    """

    def __init__(self, open_commission: float = 10.0,
                 close_commission: float = 10.0,
                 close_today_commission: float = 0.0):
        self._open = open_commission
        self._close = close_commission
        self._close_today = close_today_commission

    def calc(self, signal: Signal, fill: Fill) -> float:
        """按手数计算手续费

        开仓: open_commission × volume
        平仓: close_commission × volume（默认）
        平今: close_today_commission × volume（signal.extra.is_close_today=True 时）
        """
        vol = fill.volume
        if signal.direction in (Direction.LONG, Direction.SHORT):
            return round(self._open * vol, 4)

        is_close_today = signal.extra.get("is_close_today", False)
        rate = self._close_today if is_close_today else self._close
        return round(rate * vol, 4)


# ── 旧策略通用包装器 ──

class GoldStrategyAdapter(Strategy):
    """桥接 AuroraCore → 旧 StrategyBase 接口

    让旧 MeanReversionStrategy、MLPredictorStrategy 等直接跑在
    AuroraCore BacktestEngine 上，无需重写策略逻辑。
    """

    def __init__(self):
        super().__init__()
        self._old = None  # 子类在初始化时设置
        self._prev_signal_count: int = 0

    def on_init(self, ctx):
        super().on_init(ctx)
        if self._old and hasattr(self._old, 'on_init'):
            self._old.on_init(None)

    def on_data(self, data):
        """AuroraCore on_data → 转成 GoldBarData → 调旧策略 on_bar"""
        if self._old is None:
            return
        gbd = _bar_to_goldbar(data)
        self._prev_signal_count = len(self._old._signals)
        self._old.on_bar(gbd)
        self._emit_new_signals()

    def _emit_new_signals(self):
        """把旧策略新生成的信号转成 AuroraCore Signal"""
        signals = self._old._signals
        for i in range(self._prev_signal_count, len(signals)):
            gs = signals[i]
            self.ctx.emit(Signal(
                id=gs.signal_id, strategy=self.name, symbol=gs.symbol,
                direction=_map_direction(gs.direction),
                price=gs.price, volume=gs.volume,
                stop_loss=gs.stop_loss, take_profit=gs.take_profit,
                confidence=gs.confidence, reason=gs.reason,
            ))


def _bar_to_goldbar(bar) -> Optional[object]:
    """Bar → GoldBarData 转换（pydantic 模型）"""
    try:
        from backend.gold.core.models import GoldBarData
        return GoldBarData(
            symbol=bar.symbol,
            exchange=getattr(bar, "exchange", "SHFE"),
            period=getattr(bar, "timeframe", "d"),
            datetime=bar.datetime,
            open=float(bar.open), high=float(bar.high),
            low=float(bar.low), close=float(bar.close),
            volume=float(getattr(bar, "volume", 0)),
        )
    except Exception:
        return bar


_DIR_MAP = {
    "long": Direction.LONG, "short": Direction.SHORT,
    "close_long": Direction.CLOSE_LONG, "close_short": Direction.CLOSE_SHORT,
    "LONG": Direction.LONG, "SHORT": Direction.SHORT,
    "buy": Direction.LONG, "sell": Direction.SHORT,
}


def _map_direction(d):
    if isinstance(d, Direction):
        return d
    return _DIR_MAP.get(str(d).lower(), Direction.LONG)



class GoldDomainAdapter(DomainAdapter):
    """黄金领域适配器"""

    @property
    def name(self) -> str:
        return "gold"

    def create_data_feed(self, config: dict) -> DataFeed:
        raise NotImplementedError("使用 gold 现有数据层，Phase 3 迁移")

    def create_executor(self, config: dict) -> ExecutionEngine:
        return FuturesExecutionEngine(
            multiplier=config.get("multiplier", 1000),
            margin_rate=config.get("margin_rate", 0.08),
            fill_ratio=config.get("fill_ratio", 1.0),
            execution_delay=config.get("execution_delay", 0),
            slippage_per_lot=config.get("slippage_per_lot", 20.0),
            dynamic_slippage=config.get("dynamic_slippage", True),
            slippage_atr_ratio=config.get("slippage_atr_ratio", 0.5),
        )

    def create_cost_model(self, config: dict) -> CostModel:
        return FuturesCostModel(
            open_commission=config.get("open_commission", 10.0),
            close_commission=config.get("close_commission", 10.0),
            close_today_commission=config.get("close_today_commission", 0.0),
        )

    def default_risk_checks(self) -> list[RiskCheck]:
        from .risk.gold_risk_checks import (
            GoldDrawdownCheck, GoldDailyLossCheck,
            GoldConsecutiveLossCheck, GoldVarCheck,
            AtrVolatilityCheck, GoldPositionLimitCheck,
        )
        from core.risk import SignalFrequencyCheck
        return [
            GoldDrawdownCheck(drawdown_limit=0.25),
            GoldDailyLossCheck(max_loss_pct=0.03),
            SignalFrequencyCheck(max_per_day=10),
            GoldConsecutiveLossCheck(max_losses=5),
            GoldVarCheck(var_limit=0.10),
            AtrVolatilityCheck(reject_ratio=0.10, warn_ratio=0.05),
            GoldPositionLimitCheck(max_lots=10, max_margin_ratio=0.3),
        ]

    def get_available_strategies(self) -> dict[str, type[Strategy]]:
        return {
            "trend_following": AdaptedTrendFollowing,
            "mean_reversion": _make_wrapper("mean_reversion"),
            "ml_predictor": _make_wrapper("ml_predictor"),
        }

    def register_factors(self):
        """注册黄金域因子"""
        from backend.core.factor.registry import FactorRegistry
        from backend.gold.factors.futures import RollYieldFactor, BasisFactor
        from backend.gold.factors.momentum import MomentumMultiFactor
        from backend.gold.factors.sentiment import (
            OpenInterestChangeFactor, COTSignalFactor,
        )
        from backend.gold.factors.risk import VolatilityRegimeFactor
        from backend.gold.factors.fundamental import InventoryChangeFactor

        FactorRegistry.register_factors([
            (RollYieldFactor, RollYieldFactor.meta),
            (BasisFactor, BasisFactor.meta),
            (MomentumMultiFactor, MomentumMultiFactor.meta),
            (OpenInterestChangeFactor, OpenInterestChangeFactor.meta),
            (COTSignalFactor, COTSignalFactor.meta),
            (VolatilityRegimeFactor, VolatilityRegimeFactor.meta),
            (InventoryChangeFactor, InventoryChangeFactor.meta),
        ])


_WRAPPER_CACHE: dict[str, type] = {}


def _make_wrapper(name: str) -> type:
    """运行时生成包装器类，避免 import 层级依赖（结果缓存）"""
    cached = _WRAPPER_CACHE.get(name)
    if cached is not None:
        return cached

    from backend.gold.strategy.base import StrategyRegistry as OldReg
    old_cls = OldReg.get(name)
    if old_cls is None:
        raise RuntimeError(f"旧策略未注册: {name}")
    wrapper_cls = type(
        f"Adapted{old_cls.__name__}",
        (GoldStrategyAdapter,),
        {
            "name": name,
            "strategy_type": getattr(old_cls, "strategy_type", ""),
            "description": getattr(old_cls, "description", ""),
            "default_params": getattr(old_cls, "default_params", {}),
            "param_ranges": getattr(old_cls, "param_ranges", {}),
            "__init__": lambda self, oc=old_cls: [
                GoldStrategyAdapter.__init__(self),
                setattr(self, '_old', oc()),
            ][-1],
        },
    )
    _WRAPPER_CACHE[name] = wrapper_cls
    return wrapper_cls


# ── 适配后的黄金趋势跟踪策略 ──

@StrategyRegistry.register("trend_following")
class AdaptedTrendFollowing(Strategy):
    """多周期均线突破 + Donchian通道 + ATR止损
    移植自 gold.strategy.trend_following.TrendFollowingStrategy
    """
    name = "trend_following"
    strategy_type = "trend_following"
    description = "MA排列 + Donchian突破 + ATR止损"
    default_params = {
        "ma_periods": [5, 20, 60],
        "atr_period": 14,
        "atr_stop_multiplier": 2.0,
        "donchian_entry": 20,
        "donchian_exit": 10,
        "position_size": 1,
    }
    param_ranges = {
        "ma_periods": [[3, 5, 8, 10], [5, 10, 20, 30], [5, 20, 60], [10, 20, 50, 100]],
        "atr_period": [7, 10, 14, 20, 30],
        "atr_stop_multiplier": [1.0, 1.5, 2.0, 2.5, 3.0, 4.0],
        "donchian_entry": [10, 15, 20, 25, 30],
        "donchian_exit": [5, 10, 15, 20],
        "position_size": [1, 2, 3, 5],
    }

    def __init__(self):
        super().__init__()
        self._bars: list[Bar] = []
        self._position: int = 0   # 0=空仓, 1=多, -1=空
        self._entry_price: float = 0.0

    def on_data(self, bar: Bar):
        self._bars.append(bar)
        max_period = max(self.params.get("ma_periods", [60])) + 10
        if len(self._bars) > max_period:
            self._bars = self._bars[-max_period:]

        if len(self._bars) < max(self.params.get("ma_periods", [60])):
            return

        closes = [b.close for b in self._bars]

        # MA
        periods = self.params.get("ma_periods", [5, 20, 60])
        mas = {}
        for p in periods:
            if len(closes) >= p:
                mas[p] = sum(closes[-p:]) / p

        # ATR
        atr = self._calc_atr(closes)

        # Donchian
        entry_p = self.params.get("donchian_entry", 20)
        exit_p = self.params.get("donchian_exit", 10)
        dh = max(b.high for b in self._bars[-entry_p:-1]) if len(self._bars) >= entry_p else 0
        dl = min(b.low for b in self._bars[-entry_p:-1]) if len(self._bars) >= entry_p else 0

        price = bar.close
        pos_size = self.params.get("position_size", 1)
        atr_stop = self.params.get("atr_stop_multiplier", 2.0)

        if self._position == 0:
            ma_f = mas.get(periods[0], 0)
            ma_m = mas.get(periods[1], 0)
            ma_s = mas.get(periods[2], 0)
            if ma_f > ma_m > ma_s and bar.high > dh:
                sl = price - atr * atr_stop
                self.ctx.emit(Signal(
                    id="", strategy=self.name, symbol=bar.symbol,
                    direction=Direction.LONG, price=price, volume=pos_size,
                    stop_loss=sl, confidence=0.7,
                    reason=f"MA多头+Donchian突破 ATR={atr:.2f}",
                ))
                self._position = 1
                self._entry_price = price
            elif ma_f < ma_m < ma_s and bar.low < dl:
                sl = price + atr * atr_stop
                self.ctx.emit(Signal(
                    id="", strategy=self.name, symbol=bar.symbol,
                    direction=Direction.SHORT, price=price, volume=pos_size,
                    stop_loss=sl, confidence=0.7,
                    reason=f"MA空头+Donchian突破 ATR={atr:.2f}",
                ))
                self._position = -1
                self._entry_price = price

        elif self._position == 1:
            sl = self._entry_price - atr * atr_stop
            if price < sl:
                self.ctx.emit(Signal(id="", strategy=self.name, symbol=bar.symbol,
                    direction=Direction.CLOSE_LONG, price=price, volume=pos_size,
                    reason="ATR止损"))
                self._position = 0
            elif price < dl:
                self.ctx.emit(Signal(id="", strategy=self.name, symbol=bar.symbol,
                    direction=Direction.CLOSE_LONG, price=price, volume=pos_size,
                    reason="Donchian出场"))
                self._position = 0

        elif self._position == -1:
            sl = self._entry_price + atr * atr_stop
            if price > sl:
                self.ctx.emit(Signal(id="", strategy=self.name, symbol=bar.symbol,
                    direction=Direction.CLOSE_SHORT, price=price, volume=pos_size,
                    reason="ATR止损"))
                self._position = 0
            elif price > dh:
                self.ctx.emit(Signal(id="", strategy=self.name, symbol=bar.symbol,
                    direction=Direction.CLOSE_SHORT, price=price, volume=pos_size,
                    reason="Donchian出场"))
                self._position = 0

    def _calc_atr(self, closes: list[float]) -> float:
        atr_p = self.params.get("atr_period", 14)
        if len(self._bars) < atr_p + 1:
            return 0.0
        trs = []
        for i in range(1, len(self._bars)):
            b, p = self._bars[i], self._bars[i - 1]
            tr = max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close))
            trs.append(tr)
        return sum(trs[-atr_p:]) / atr_p


def demo():
    """黄金领域适配自检"""
    adapter = GoldDomainAdapter()
    assert adapter.name == "gold"
    assert len(adapter.default_risk_checks()) == 7

    cost = adapter.create_cost_model({})
    signal = Signal(id="", strategy="test", symbol="AU0",
                    direction=Direction.LONG, price=600, volume=2)
    fill = Fill(order_id="o1", price=600, volume=2)
    c = cost.calc(signal, fill)
    assert c == 20.0, f"open 2 lots × 10 = 20, got {c}"
    print(f"[gold_adapter] ✅ FuturesCostModel: 开仓2手 = {c} 元")

    strategies = adapter.get_available_strategies()
    assert "trend_following" in strategies
    print("[gold_adapter] ✅ GoldDomainAdapter 接口通过")

    # ── FuturesExecutionEngine 集成自检 ──
    executor = adapter.create_executor({
        "multiplier": 1000, "margin_rate": 0.08, "fill_ratio": 1.0,
        "slippage_per_lot": 20.0, "dynamic_slippage": True, "slippage_atr_ratio": 0.5,
    })
    executor.set_capital(1_000_000)
    executor.set_atr(5.0)  # ATR=5 元/克
    sig = Signal(id="s1", strategy="test", symbol="AU0",
                 direction=Direction.LONG, price=600, volume=2)
    executor.submit(sig)

    from core import Bar
    from datetime import datetime
    bar = Bar(symbol="AU0", exchange="SHFE", timeframe="1d",
              datetime=datetime(2026, 7, 17), open=600, high=605,
              low=598, close=602, volume=10000)
    fills = executor.on_bar(bar)
    assert len(fills) > 0, "FuturesExecutionEngine 应成交"
    pos = executor.get_position("AU0")
    assert pos is not None, "应有持仓"
    assert pos.volume == 2, f"持仓量应为2，got {pos.volume}"
    assert pos.avg_price > 600, f"成交价应含滑点 >600, got {pos.avg_price}"
    assert executor.portfolio_value < 1_000_000, "占用保证金后总权益应减少"
    print(f"[gold_adapter] ✅ FuturesExecutionEngine 集成通过 — "
          f"开仓价={pos.avg_price:.2f}, 占用保证金={1_000_000 - executor.portfolio_value:.0f}")


if __name__ == "__main__":
    demo()
