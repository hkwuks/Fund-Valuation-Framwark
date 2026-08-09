"""策略层 — Strategy 基类 + Registry + Context"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .data import Bar, FundNavPoint
    from .event import EventBus
    from .signal import Signal, Fill


class StrategyContext:
    """策略运行上下文 — 策略与外界的唯一接口"""

    def __init__(self, event_bus: EventBus, mode: str = "backtest"):
        self._event_bus = event_bus
        self._mode = mode
        self._data: dict[str, Any] = {}  # 策略私有状态
        self._portfolio_value: float = 0.0  # 组合总权益（引擎每 bar 更新）
        self.execution: Any = None  # 执行引擎引用（策略可查询持仓）

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def portfolio_value(self) -> float:
        """当前组合总权益（含持仓市值 + 可用资金）"""
        return self._portfolio_value

    @portfolio_value.setter
    def portfolio_value(self, value: float):
        self._portfolio_value = value

    def emit(self, signal: Signal) -> str:
        """发布信号 → EventBus"""
        from .event import Event, EventType

        signal.id = f"{signal.strategy}_{signal.symbol}_{id(signal)}"
        self._event_bus.publish(Event(
            type=EventType.SIGNAL_GENERATED,
            payload=signal,
            source=signal.strategy,
        ))
        return signal.id


class Strategy(ABC):
    """策略基类 — 所有策略的统一抽象"""

    name: str = ""
    strategy_type: str = ""
    description: str = ""
    default_params: dict = {}
    param_ranges: dict = {}

    def __init__(self):
        self.ctx: StrategyContext | None = None
        self.params: dict = {}

    def on_init(self, ctx: StrategyContext):
        """初始化 — 子类重写"""
        self.ctx = ctx

    @abstractmethod
    def on_data(self, data: Bar | FundNavPoint):
        """数据回调 — 子类实现"""
        ...

    def on_fill(self, signal_id: str, fill: Fill) -> None:
        """成交回调 — 默认空操作"""
        pass


class StrategyRegistry:
    """统一注册表 — 装饰器注册 + importlib 自动发现"""
    _strategies: dict[str, type[Strategy]] = {}
    _scanned: set[str] = set()

    @classmethod
    def register(cls, name: str):
        """装饰器注册

        >>> @StrategyRegistry.register("demo")
        ... class DemoStrategy(Strategy):
        ...     def on_data(self, data): pass
        >>> StrategyRegistry.get("demo") is DemoStrategy
        True
        """
        def _(clazz):
            cls._strategies[name] = clazz
            return clazz
        return _

    @classmethod
    def discover(cls, package: str):
        """自动扫描包 — 确保所有装饰器被触发"""
        if package in cls._scanned:
            return
        import importlib, pkgutil
        pkg = importlib.import_module(package)
        for _, modname, _ in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            importlib.import_module(modname)
        cls._scanned.add(package)

    @classmethod
    def get(cls, name: str) -> type[Strategy]:
        return cls._strategies[name]

    @classmethod
    def list_all(cls) -> dict:
        return dict(cls._strategies)


def demo():
    """注册 + 发现自检"""
    # 装饰器注册
    @StrategyRegistry.register("demo_ma")
    class MaStrategy(Strategy):
        def on_data(self, data): pass

    assert "demo_ma" in StrategyRegistry.list_all()
    assert StrategyRegistry.get("demo_ma") is MaStrategy

    # Context 自检
    from .event import EventBus, Event, EventType
    bus = EventBus()
    ctx = StrategyContext(bus, mode="backtest")
    assert ctx.mode == "backtest"

    from .signal import Signal, Direction
    signals_seen = []
    bus.subscribe(EventType.SIGNAL_GENERATED, lambda e: signals_seen.append(e.payload))
    ctx.emit(Signal(id="", strategy="test", symbol="AU0",
                    direction=Direction.LONG, price=600, volume=1))
    assert len(signals_seen) == 1
    assert signals_seen[0].symbol == "AU0"

    print("[strategy] ✅ 注册 + Context 通过")


if __name__ == "__main__":
    demo()
