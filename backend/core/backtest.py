"""回测引擎 — BacktestEngine + ExecutionEngine + CostModel + SlippageModel"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from .signal import Direction, Order, OrderStatus, Fill, Position, Signal
from .strategy import Strategy, StrategyContext
from .event import Event, EventType, EventBus

if TYPE_CHECKING:
    from .event import EventBus, EventType
    from .data import Bar, FundNavPoint
    from .strategy import Strategy, StrategyContext
    from .config import BacktestConfig


# ═══════════════════════════════════════════
# 滑点模型
# ═══════════════════════════════════════════

class SlippageModel(ABC):
    @abstractmethod
    def apply(self, signal: Signal, bar: Bar) -> tuple[float, float]:
        """返回 (成交价, 滑点成本)"""
        ...


class NoSlippage(SlippageModel):
    def apply(self, signal: Signal, bar: Bar) -> tuple[float, float]:
        return bar.close, 0.0


class PercentageSlippage(SlippageModel):
    def __init__(self, pct: float = 0.001):
        self.pct = pct

    def apply(self, signal: Signal, bar: Bar) -> tuple[float, float]:
        if signal.direction in (Direction.LONG, Direction.CLOSE_SHORT):
            price = bar.close * (1 + self.pct)
        else:
            price = bar.close * (1 - self.pct)
        return price, abs(price - bar.close)


# ═══════════════════════════════════════════
# 成本模型
# ═══════════════════════════════════════════

class CostModel(ABC):
    @abstractmethod
    def calc(self, signal: Signal, fill: Fill) -> float:
        """返回交易成本"""
        ...


class NoCost(CostModel):
    def calc(self, signal: Signal, fill: Fill) -> float:
        return 0.0


# ═══════════════════════════════════════════
# 执行引擎
# ═══════════════════════════════════════════

class ExecutionEngine(ABC):
    """执行引擎 — 订单生命周期管理 + 持仓追踪"""

    @abstractmethod
    def submit(self, signal: Signal) -> Order: ...
    @abstractmethod
    def cancel(self, order_id: str) -> bool: ...
    @abstractmethod
    def get_order(self, order_id: str) -> Order | None: ...
    @abstractmethod
    def get_position(self, symbol: str) -> Position | None: ...
    @abstractmethod
    def positions(self) -> list[Position]: ...
    @abstractmethod
    def on_bar(self, bar: Bar) -> list[Fill]:
        """处理待成交订单，返回本根 bar 产生的成交"""
        ...


class SimExecutionEngine(ExecutionEngine):
    """回测执行引擎 — bar close 成交 + 滑点模型"""

    def __init__(self, slippage: SlippageModel | None = None,
                 fill_ratio: float = 1.0):
        self._slippage = slippage or NoSlippage()
        self._fill_ratio = fill_ratio
        self._orders: dict[str, Order] = {}
        self._pending: dict[str, Signal] = {}
        self._positions: dict[str, Position] = {}
        self._fills: list[Fill] = []

    def submit(self, signal: Signal) -> Order:
        oid = f"o_{signal.id}"
        order = Order(
            id=oid, signal_id=signal.id, symbol=signal.symbol,
            direction=signal.direction, price=signal.price,
            volume=signal.volume, status=OrderStatus.ACCEPTED,
        )
        self._orders[oid] = order
        self._pending[oid] = signal
        return order

    def cancel(self, order_id: str) -> bool:
        if order_id in self._pending:
            self._orders[order_id].status = OrderStatus.CANCELLED
            del self._pending[order_id]
            return True
        return False

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def on_bar(self, bar: Bar) -> list[Fill]:
        fills: list[Fill] = []
        for oid, signal in list(self._pending.items()):
            order = self._orders[oid]
            vol = signal.volume * self._fill_ratio
            if vol < 0.01:
                order.status = OrderStatus.REJECTED
                del self._pending[oid]
                continue

            price, slippage = self._slippage.apply(signal, bar)
            fill = Fill(
                order_id=oid, price=round(price, 4), volume=vol,
                slippage=round(slippage, 4),
            )
            order.filled_volume = vol
            order.status = OrderStatus.FILLED
            fills.append(fill)
            self._fills.append(fill)

            # 更新持仓
            self._apply_fill(signal, fill)
            del self._pending[oid]

        return fills

    def _apply_fill(self, signal: Signal, fill: Fill):
        pos = self._positions.get(signal.symbol)
        direction = signal.direction

        # 平仓方向 → 减少对应持仓
        if direction in (Direction.CLOSE_LONG, Direction.CLOSE_SHORT):
            close_dir = Direction.LONG if direction == Direction.CLOSE_LONG else Direction.SHORT
            if pos and pos.direction == close_dir and pos.volume > 0:
                avg_cost = pos.avg_price * pos.volume
                fill_cost = fill.price * fill.volume
                pos.realized_pnl += avg_cost - fill_cost if direction == Direction.CLOSE_LONG else fill_cost - avg_cost
                pos.volume -= fill.volume
                if pos.volume <= 0:
                    self._positions.pop(signal.symbol, None)
            return

        # 开仓方向
        if direction in (Direction.LONG, Direction.SHORT):
            if pos is None:
                self._positions[signal.symbol] = Position(
                    symbol=signal.symbol, direction=direction,
                    volume=fill.volume, avg_price=fill.price,
                )
            else:
                # 加仓
                total = pos.avg_price * pos.volume + fill.price * fill.volume
                pos.volume += fill.volume
                pos.avg_price = total / pos.volume


class T1ExecutionEngine(ExecutionEngine):
    """T+1 确认执行引擎（基金）— 持仓成本平均 + 持有天数跟踪 + 交易日志 + 权益追踪"""

    def __init__(self, confirmation_delay: int = 1):
        self._delay = confirmation_delay
        self._orders: dict[str, Order] = {}
        self._pending: dict[str, tuple[Signal, int]] = {}  # signal -> bars_waited
        self._positions: dict[str, Position] = {}
        self._all_fills: list[Fill] = []
        self._entry_bars: dict[str, int] = {}  # fund_code -> bar_index opened
        self._trade_log: list[dict] = []
        self._bar_index: int = 0
        self._capital: float = 0.0
        self._current_prices: dict[str, float] = {}

    def set_capital(self, capital: float):
        self._capital = capital

    def deduct_cost(self, cost: float):
        """从可用资金中扣除交易成本（申购/赎回/管理费）"""
        self._capital -= cost

    @property
    def portfolio_value(self) -> float:
        """总权益 = 可用资金 + 持仓市值 + 未实现盈亏"""
        total = self._capital
        for sym, pos in self._positions.items():
            price = self._current_prices.get(sym, pos.avg_price)
            total += pos.volume * price
        return total

    def submit(self, signal: Signal) -> Order:
        oid = f"o_{signal.id}"
        order = Order(id=oid, signal_id=signal.id, symbol=signal.symbol,
                      direction=signal.direction, price=signal.price,
                      volume=signal.volume, status=OrderStatus.ACCEPTED)
        self._orders[oid] = order
        self._pending[oid] = (signal, 0)
        return order

    def cancel(self, order_id: str) -> bool:
        if order_id in self._pending:
            self._orders[order_id].status = OrderStatus.CANCELLED
            del self._pending[order_id]
            return True
        return False

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_holding_days(self, fund_code: str) -> int:
        """返回指定基金当前持仓已持有 bar 数（用于赎回费率计算）"""
        entry_bar = self._entry_bars.get(fund_code)
        if entry_bar is None:
            return 0
        return self._bar_index - entry_bar

    def get_trade_log(self) -> list[dict]:
        """返回详细交易日志"""
        return list(self._trade_log)

    def on_bar(self, bar: Bar) -> list[Fill]:
        # 记录当前价格用于 portfolio_value
        sym = getattr(bar, "symbol", getattr(bar, "fund_code", ""))
        self._current_prices[sym] = getattr(bar, "close", getattr(bar, "nav", 0))

        fills: list[Fill] = []
        ready: list[tuple[str, Signal]] = []
        remaining: dict[str, tuple[Signal, int]] = {}

        for oid, (signal, waited) in self._pending.items():
            if waited >= self._delay:
                ready.append((oid, signal))
            else:
                remaining[oid] = (signal, waited + 1)

        self._pending = remaining

        for oid, signal in ready:
            order = self._orders[oid]
            price = getattr(bar, "close", getattr(bar, "nav", 0))
            fill = Fill(order_id=oid, price=price, volume=signal.volume)
            order.filled_volume = signal.volume
            order.status = OrderStatus.FILLED
            fills.append(fill)
            self._all_fills.append(fill)
            self._apply_fill(signal, fill, price)

        self._bar_index += 1
        return fills

    def _apply_fill(self, signal: Signal, fill: Fill, price: float | None = None):
        fill_price = price or fill.price
        pos = self._positions.get(signal.symbol)

        # 平仓方向
        if signal.direction in (Direction.CLOSE_LONG, Direction.CLOSE_SHORT):
            close_dir = Direction.LONG if signal.direction == Direction.CLOSE_LONG else Direction.SHORT
            if pos and pos.direction == close_dir and pos.volume > 0:
                # 卖出 → 增加可用资金
                proceeds = fill_price * signal.volume
                self._capital += proceeds
                realized = proceeds - pos.avg_price * signal.volume
                pos.realized_pnl += realized if signal.direction == Direction.CLOSE_LONG else -realized
                pos.volume -= signal.volume
                self._trade_log.append({
                    "action": "sell", "symbol": signal.symbol,
                    "bar": self._bar_index, "price": fill_price,
                    "volume": signal.volume, "holding_bars": self.get_holding_days(signal.symbol),
                })
                if pos.volume <= 0:
                    self._positions.pop(signal.symbol, None)
                    self._entry_bars.pop(signal.symbol, None)
            return

        # 开仓方向
        if signal.direction in (Direction.LONG, Direction.SHORT):
            # 买入 → 减少可用资金
            investment = fill_price * signal.volume
            self._capital -= investment
            if pos is None:
                self._positions[signal.symbol] = Position(
                    symbol=signal.symbol, direction=signal.direction,
                    volume=signal.volume, avg_price=fill_price,
                )
                self._entry_bars[signal.symbol] = self._bar_index
                self._trade_log.append({
                    "action": "buy", "symbol": signal.symbol,
                    "bar": self._bar_index, "price": fill_price,
                    "volume": signal.volume,
                })
            else:
                # 成本平均法（多次买入合并）
                total = pos.avg_price * pos.volume + fill_price * signal.volume
                pos.volume += signal.volume
                pos.avg_price = total / pos.volume
                self._trade_log.append({
                    "action": "add", "symbol": signal.symbol,
                    "bar": self._bar_index, "price": fill_price,
                    "volume": signal.volume,
                })


class FuturesExecutionEngine(ExecutionEngine):
    """期货执行引擎 — 保证金计算 + 平今区分 + ATR动态滑点 + 延迟成交 + 部分成交

    旧 gold/backtest/engine.py BacktestStrategyContext 的完整移植。
    滑点嵌入成交价，手续费留给 cost_model。
    """

    def __init__(self, multiplier: int = 1000, margin_rate: float = 0.08,
                 fill_ratio: float = 1.0, execution_delay: int = 0,
                 close_today_commission: float = 0.0,
                 slippage_per_lot: float = 20.0,
                 dynamic_slippage: bool = True,
                 slippage_atr_ratio: float = 0.5):
        self._multiplier = multiplier
        self._margin_rate = margin_rate
        self._fill_ratio = fill_ratio
        self._execution_delay = execution_delay
        self._close_today_commission = close_today_commission
        self._fixed_slippage = slippage_per_lot
        self._dynamic_slippage = dynamic_slippage
        self._slippage_atr_ratio = slippage_atr_ratio

        self._orders: dict[str, Order] = {}
        self._pending: list[dict] = []  # [{remaining, signal, order_id}]
        self._positions: dict[str, Position] = {}
        self._fills: list[Fill] = []
        self._capital: float = 0.0       # 可用资金（不含保证金）
        self._bar_index: int = 0
        self._open_bars: dict[str, int] = {}   # symbol -> bar_index opened
        self._current_prices: dict[str, float] = {}
        self._current_atr: float = 0.0
        self._trades: list[dict] = []

    def set_capital(self, capital: float):
        self._capital = capital

    def set_atr(self, atr: float):
        self._current_atr = atr

    def deduct_cost(self, cost: float):
        """从可用资金中扣除交易成本（BacktestEngine 在 cost_model.calc() 后调用）"""
        self._capital -= cost

    def submit(self, signal: Signal) -> Order:
        oid = f"o_{signal.id}"
        order = Order(id=oid, signal_id=signal.id, symbol=signal.symbol,
                      direction=signal.direction, price=signal.price,
                      volume=signal.volume, status=OrderStatus.ACCEPTED)
        self._orders[oid] = order
        self._pending.append({
            "remaining": self._execution_delay,
            "signal": signal, "order_id": oid,
        })
        return order

    def cancel(self, order_id: str) -> bool:
        for i, entry in enumerate(self._pending):
            if entry["order_id"] == order_id:
                self._orders[order_id].status = OrderStatus.CANCELLED
                self._pending.pop(i)
                return True
        return False

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_trades(self) -> list[dict]:
        return list(self._trades)

    @property
    def portfolio_value(self) -> float:
        """总权益 = 可用资金 + 保证金总额 + 未实现盈亏"""
        margin_total = 0.0
        unrealized = 0.0
        for sym, pos in self._positions.items():
            price = self._current_prices.get(sym, pos.avg_price)
            notional = price * self._multiplier * pos.volume
            margin_total += notional * self._margin_rate
            if pos.direction == Direction.LONG:
                unrealized += (price - pos.avg_price) * self._multiplier * pos.volume
            else:
                unrealized += (pos.avg_price - price) * self._multiplier * pos.volume
        return self._capital + margin_total + unrealized

    def _calc_slippage_per_lot(self) -> float:
        """单手滑点成本（元/手）"""
        if self._dynamic_slippage and self._current_atr > 0:
            return self._slippage_atr_ratio * self._current_atr * self._multiplier
        return self._fixed_slippage


    def on_bar(self, bar: Bar) -> list[Fill]:
        self._current_prices[bar.symbol] = bar.close
        import random

        fills: list[Fill] = []
        remaining: list[dict] = []

        for entry in self._pending:
            entry["remaining"] -= 1
            if entry["remaining"] > 0:
                remaining.append(entry)
                continue

            signal = entry["signal"]
            oid = entry["order_id"]
            order = self._orders[oid]

            # 部分成交
            effective_vol = signal.volume
            if self._fill_ratio < 1.0:
                if random.random() > self._fill_ratio:
                    effective_vol = max(1, int(signal.volume * random.uniform(0.1, 0.9)))

            if effective_vol < 0.5:
                order.status = OrderStatus.REJECTED
                continue

            # 滑点计算 + 成交价
            slip_per_lot = self._calc_slippage_per_lot()
            slip_total = slip_per_lot * effective_vol
            slip_price_adj = slip_total / (self._multiplier * effective_vol) if effective_vol > 0 else 0

            if signal.direction in (Direction.LONG, Direction.CLOSE_SHORT):
                fill_price = bar.close + slip_price_adj
            else:
                fill_price = bar.close - slip_price_adj

            # 开仓：检查保证金 + 滑点成本
            if signal.direction in (Direction.LONG, Direction.SHORT):
                notional = fill_price * self._multiplier * effective_vol
                margin = notional * self._margin_rate
                # ponytail: 预估总成本 = 保证金 + 滑点，手续费之后由 deduct_cost 补充扣除
                if margin + slip_total > self._capital:
                    order.status = OrderStatus.REJECTED
                    continue

                self._capital -= margin  # 占用保证金

                pos = self._positions.get(signal.symbol)
                if pos:
                    total = pos.avg_price * pos.volume + fill_price * effective_vol
                    pos.volume += effective_vol
                    pos.avg_price = total / pos.volume
                else:
                    self._positions[signal.symbol] = Position(
                        symbol=signal.symbol, direction=signal.direction,
                        volume=effective_vol, avg_price=fill_price,
                    )
                self._open_bars[signal.symbol] = self._bar_index

                self._trades.append({
                    "type": "open", "symbol": signal.symbol,
                    "price": fill_price, "volume": effective_vol,
                    "slippage": round(slip_total, 2),
                    "bar": self._bar_index,
                })

            # 平仓：释放保证金 + 计算盈亏
            else:
                pos = self._positions.get(signal.symbol)
                if not pos or pos.volume < 0.5:
                    order.status = OrderStatus.REJECTED
                    continue

                effective_vol = min(effective_vol, pos.volume)
                is_close_today = self._open_bars.get(signal.symbol) == self._bar_index
                signal.extra["is_close_today"] = is_close_today
                open_bar = self._open_bars.get(signal.symbol, self._bar_index)

                # PnL = (平仓价 - 开仓价) × 乘数 × 手数
                if pos.direction == Direction.LONG:
                    pnl = (fill_price - pos.avg_price) * self._multiplier * effective_vol
                else:
                    pnl = (pos.avg_price - fill_price) * self._multiplier * effective_vol

                # 释放保证金（按当前价重新计算）
                close_notional = fill_price * self._multiplier * effective_vol
                margin_released = close_notional * self._margin_rate
                self._capital += margin_released + pnl

                # 减去持仓
                pos.volume -= effective_vol
                if pos.volume <= 0:
                    self._positions.pop(signal.symbol, None)
                    self._open_bars.pop(signal.symbol, None)

                self._trades.append({
                    "type": "close", "symbol": signal.symbol,
                    "price": fill_price, "volume": effective_vol,
                    "pnl": round(pnl, 2),
                    "slippage": round(slip_total, 2),
                    "is_close_today": is_close_today,
                    "holding_bars": self._bar_index - open_bar,
                    "bar": self._bar_index,
                })

            fill = Fill(order_id=oid, price=round(fill_price, 4),
                        volume=effective_vol, slippage=round(slip_total, 4))
            order.filled_volume = effective_vol
            order.status = OrderStatus.FILLED
            fills.append(fill)
            self._fills.append(fill)

        self._pending = remaining
        self._bar_index += 1
        return fills


# ═══════════════════════════════════════════
# 回测引擎
# ═══════════════════════════════════════════

@dataclass
class BacktestReport:
    """回测报告（Phase 1 简易版）"""
    total_return: float = 0.0
    total_trades: int = 0
    final_equity: float = 0.0
    equity_curve: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)


class BacktestEngine:
    """回测引擎内核 — EventBus 驱动的事件循环"""

    def __init__(self, config: BacktestConfig):
        self.config = config
        self._event_bus: EventBus | None = None
        self._strategy: Strategy | None = None
        self._data: Any = None  # DataFeed — Phase 1 先接受 list[Bar]
        self._execution: ExecutionEngine | None = None
        self._cost_model: CostModel | None = None
        self._risk: Any = None  # RiskPipeline — Phase 1 暂不严格依赖

    def set_event_bus(self, bus: EventBus):
        self._event_bus = bus

    def set_strategy(self, strategy: Strategy):
        self._strategy = strategy

    def set_data(self, data: Any):
        self._data = data

    def set_executor(self, execution: ExecutionEngine):
        self._execution = execution

    def set_cost_model(self, cost_model: CostModel):
        self._cost_model = cost_model

    def set_risk(self, risk: Any):
        self._risk = risk

    def run(self) -> BacktestReport:
        """事件循环 — 驱动策略回测"""
        from .risk import RiskPipeline, RiskLevel, RiskContext

        bus = self._event_bus or EventBus()
        strategy = self._strategy
        execution = self._execution
        cost_model = self._cost_model or NoCost()
        risk_pipeline = self._risk  # Optional[RiskPipeline]
        bars = self._data or []
        if not strategy:
            raise RuntimeError("Strategy not set")

        # ATR 序列（OHLC 数据可用时计算）
        atr_values = self._calc_atr_series(bars) if bars and hasattr(bars[0], 'high') else None

        # 期货引擎初始化
        if hasattr(execution, 'set_capital'):
            execution.set_capital(self.config.initial_capital)

        ctx = StrategyContext(bus)
        strategy.on_init(ctx)

        _signals_published: list[Signal] = []

        def _capture(event: Event):
            _signals_published.append(event.payload)

        # ponytail: subscribe by value string to avoid circular import on EventType
        bus._subscribers["signal.generated"].append(_capture)

        # 所有待成交订单的信号映射（跨 bar 积累，T+1 撮合需要）
        _pending_signal_map: dict[str, Signal] = {}

        risk_ctx = RiskContext(portfolio_value=self.config.initial_capital)
        equity = self.config.initial_capital
        report = BacktestReport()
        report.equity_curve.append({"bar": 0, "equity": equity})

        for i, bar in enumerate(bars):
            # 设置 ATR（期货引擎动态滑点用）
            if hasattr(execution, 'set_atr') and atr_values:
                execution.set_atr(atr_values[i] if i < len(atr_values) else 0.0)

            # 发布 BAR_RECEIVED
            bus.publish(Event(EventType.BAR_RECEIVED, bar, source="engine"))

            # 组合级风控（每日一次）
            if risk_pipeline:
                # 使用期货引擎的总权益（如有）
                if hasattr(execution, 'portfolio_value'):
                    risk_ctx.portfolio_value = execution.portfolio_value
                else:
                    risk_ctx.portfolio_value = equity
                risk_ctx.positions = [p.symbol for p in (execution.positions() if execution else [])]
                portfolio_results = risk_pipeline.run_portfolio(risk_ctx)
                reject = any(r.level == RiskLevel.REJECT for r in portfolio_results)
                if reject:
                    report.equity_curve.append({
                        "bar": i + 1, "equity": equity,
                        "close": getattr(bar, "close", getattr(bar, "nav", 0)),
                        "risk_blocked": True,
                    })
                    continue

            # Strategy 处理数据
            strategy.on_data(bar)

            # 信号级风控 + 执行
            allowed_signals: list[Signal] = []
            for signal in _signals_published:
                # 信号级风控
                if risk_pipeline:
                    risk_ctx.daily_signal_count += 1
                    signal_results = risk_pipeline.run_signal(signal, risk_ctx)
                    if any(r.level == RiskLevel.REJECT for r in signal_results):
                        bus.publish(Event(EventType.SIGNAL_RISK_REJECT, signal, source="risk"))
                        continue
                    bus.publish(Event(EventType.SIGNAL_RISK_PASS, signal, source="risk"))
                allowed_signals.append(signal)

            _signals_published.clear()

            # 提交信号到执行引擎（积累到跨 bar 映射）
            for signal in allowed_signals:
                order = execution.submit(signal) if execution else None
                if order:
                    _pending_signal_map[order.id] = signal

            # ExecutionEngine 撮合
            if execution:
                fills = execution.on_bar(bar)
                for fill in fills:
                    # 从跨 bar 映射中找对应的 signal
                    orig_signal = _pending_signal_map.get(fill.order_id)
                    if orig_signal:
                        cost = cost_model.calc(orig_signal, fill)
                        fill.commission = cost
                        # 通知执行引擎扣除交易成本（有 deduct_cost 的引擎才扣）
                        if hasattr(execution, 'deduct_cost'):
                            execution.deduct_cost(cost)
                        strategy.on_fill(fill.order_id, fill)
                        report.total_trades += 1
                        bus.publish(Event(EventType.ORDER_FILLED, fill, source="execution"))

            # 记录权益曲线
            if hasattr(execution, 'portfolio_value'):
                # 期货/内部算权益的引擎
                equity_ts = execution.portfolio_value
            else:
                equity_ts = report.equity_curve[-1]["equity"]
                bar_price = getattr(bar, "close", getattr(bar, "nav", 0))
                if execution and execution.positions():
                    for pos in execution.positions():
                        if pos.volume > 0:
                            mk_val = pos.volume * bar_price
                            cost_val = pos.volume * pos.avg_price
                            pos.unrealized_pnl = round(mk_val - cost_val, 4)
                            equity_ts += pos.unrealized_pnl

            report.equity_curve.append({
                "bar": i + 1, "equity": round(equity_ts, 2),
                "close": getattr(bar, "close", getattr(bar, "nav", 0)),
            })

        report.final_equity = report.equity_curve[-1]["equity"]
        report.total_return = (report.final_equity / self.config.initial_capital) - 1
        self._captured_signals = _signals_published
        bus.publish(Event(EventType.ENGINE_STOP, {"reason": "completed"}, source="engine"))
        return report

    @staticmethod
    def _calc_atr_series(bars, period: int = 14) -> list[float] | None:
        """计算 ATR 序列 — 从 bars 提取 OHLC，每根 bar 的 ATR = 前 period 根 TR 均值

        需要 bars[0] 有 .high/.low/.close 属性，否则返回 None。
        """
        if not bars:
            return None
        for attr in ('high', 'low', 'close'):
            if not hasattr(bars[0], attr):
                return None
        trs = []
        for i in range(1, len(bars)):
            h = float(bars[i].high)
            l = float(bars[i].low)
            pc = float(bars[i - 1].close)
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        if not trs:
            return None
        atr_values = [0.0] * len(bars)
        for i in range(1, len(bars)):
            window = trs[max(0, i - period):i]
            atr_values[i] = sum(window) / len(window) if window else 0.0
        return atr_values


def demo():
    """端到端回测自检"""
    from .data import Bar
    from .config import BacktestConfig
    from .strategy import Strategy, StrategyRegistry
    from datetime import datetime

    bus = EventBus()
    cfg = BacktestConfig(initial_capital=100_000)

    # 一个简单策略：close > 100 做多
    @StrategyRegistry.register("demo_backtest")
    class DemoStrategy(Strategy):
        def on_data(self, data):
            if data.close > 100:
                self.ctx.emit(Signal(
                    id="", strategy="demo_backtest", symbol=data.symbol,
                    direction=Direction.LONG, price=data.close, volume=1,
                    reason=f"close={data.close} > 100",
                ))

    # 构造 10 根 bar
    bars = [Bar(symbol="TEST", exchange="DEMO", timeframe="1d",
                datetime=datetime(2026, 7, i + 1),
                open=float(100 + i), high=float(101 + i),
                low=float(99 + i), close=float(100 + i),
                volume=1000) for i in range(10)]

    engine = BacktestEngine(cfg)
    engine.set_event_bus(bus)
    engine.set_strategy(DemoStrategy())
    engine.set_executor(SimExecutionEngine())
    engine.set_data(bars)
    report = engine.run()

    assert report.total_trades > 0, f"expected trades, got {report.total_trades}"
    assert report.total_return != 0 or report.total_trades > 0
    assert len(report.equity_curve) == len(bars) + 1  # 初始 + 每根 bar

    print(f"[backtest] ✅ SimExecutionEngine 通过 — {report.total_trades} 笔交易, "
          f"return={report.total_return:.2%}, equity={report.final_equity:.0f}")

    # ── FuturesExecutionEngine 自检 ──
    cfg2 = BacktestConfig(initial_capital=200_000)
    bus2 = EventBus()

    @StrategyRegistry.register("demo_futures")
    class DemoFuturesStrategy(Strategy):
        def on_data(self, data):
            if data.close > 100:
                self.ctx.emit(Signal(
                    id="", strategy="demo_futures", symbol=data.symbol,
                    direction=Direction.LONG, price=data.close, volume=1,
                ))
            elif data.close < 104:
                self.ctx.emit(Signal(
                    id="", strategy="demo_futures", symbol=data.symbol,
                    direction=Direction.CLOSE_LONG, price=data.close, volume=1,
                ))

    engine2 = BacktestEngine(cfg2)
    engine2.set_event_bus(bus2)
    engine2.set_strategy(DemoFuturesStrategy())
    engine2.set_executor(FuturesExecutionEngine(
        multiplier=1000, margin_rate=0.08, execution_delay=0,
        slippage_per_lot=20.0, dynamic_slippage=True, slippage_atr_ratio=0.5,
    ))
    engine2.set_data(bars)
    report2 = engine2.run()

    assert report2.total_trades > 0, f"期货引擎无交易，got {report2.total_trades}"
    # 期货用 portfolio_value 算权益，曲线长度应正确
    assert len(report2.equity_curve) == len(bars) + 1
    print(f"[backtest] ✅ FuturesExecutionEngine 通过 — {report2.total_trades} 笔交易, "
          f"return={report2.total_return:.2%}, equity={report2.final_equity:.0f}")

    # ── T1ExecutionEngine 自检（含持仓天数跟踪 + 交易日志） ──
    cfg3 = BacktestConfig(initial_capital=100_000)
    bus3 = EventBus()

    @StrategyRegistry.register("demo_t1")
    class DemoT1Strategy(Strategy):
        def on_data(self, data):
            nav = getattr(data, "nav", getattr(data, "close", 0))
            code = getattr(data, "fund_code", getattr(data, "symbol", ""))
            if nav > 1.1 and not hasattr(self, "_bought"):
                self._bought = True
                self.ctx.emit(Signal(
                    id="", strategy="demo_t1", symbol=code,
                    direction=Direction.LONG, price=nav, volume=10000,
                ))

    from .data import FundNavPoint
    navs = [FundNavPoint(fund_code="000001", date=datetime(2026, 7, i + 1).date(),
                          nav=round(1.0 + i * 0.02, 4)) for i in range(15)]

    engine3 = BacktestEngine(cfg3)
    engine3.set_event_bus(bus3)
    engine3.set_strategy(DemoT1Strategy())
    engine3.set_executor(T1ExecutionEngine(confirmation_delay=1))
    engine3.set_data(navs)
    report3 = engine3.run()

    assert report3.total_trades > 0, f"T+1引擎无交易"
    t1_exec = engine3._execution
    if hasattr(t1_exec, 'get_trade_log'):
        trade_log = t1_exec.get_trade_log()
        assert len(trade_log) > 0, "交易日志应为空"
        buy_entries = [t for t in trade_log if t["action"] in ("buy", "add")]
        assert len(buy_entries) > 0, "应有买入记录"
    print(f"[backtest] ✅ T1ExecutionEngine 通过 — {report3.total_trades} 笔交易, "
          f"return={report3.total_return:.2%}, equity={report3.final_equity:.0f}")

    print("[backtest] ✅ 所有执行引擎通过自检")


if __name__ == "__main__":
    demo()
