"""Paper trading engine — domain-agnostic base for live/paper trading"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
import os
import uuid

from .signal import Signal, Direction, Fill
from .strategy import Strategy, StrategyContext, StrategyRegistry
from .backtest import T1ExecutionEngine, CostModel, NoCost
from .risk import RiskPipeline, RiskContext, RiskLevel
from .data import FundNavPoint, Bar


@dataclass
class PaperTradeSession:
    """Paper trading session state — persisted to disk"""
    session_id: str
    strategy_name: str
    symbols: list[str]
    initial_capital: float
    cash: float
    positions: dict = field(default_factory=dict)  # symbol -> {shares, avg_price}
    equity_curve: list = field(default_factory=list)
    trade_log: list = field(default_factory=list)
    last_run_date: Optional[str] = None
    created_at: str = ""
    status: str = "running"
    params: dict = field(default_factory=dict)


class PaperTradeEngine(ABC):
    """Paper trading engine — domain-agnostic base class.

    Subclasses implement persistence (_load_session / _save_session / _list_sessions).
    Uses T1ExecutionEngine internally for order execution and position tracking.
    """

    def __init__(self, state_dir: str = "paper_trade_states"):
        self._state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self._risk_pipeline = RiskPipeline()
        self._cost_model: CostModel = NoCost()

    # ── Subclass hooks ──

    @abstractmethod
    def _load_session(self, session_id: str) -> Optional[PaperTradeSession]:
        ...

    @abstractmethod
    def _save_session(self, session: PaperTradeSession):
        ...

    @abstractmethod
    def _list_sessions(self) -> list[PaperTradeSession]:
        ...

    # ── Public API ──

    def start(self, strategy_name: str, symbols: list[str],
              initial_capital: float = 100000.0,
              params: dict = None) -> PaperTradeSession:
        if not symbols:
            raise ValueError("symbols must not be empty")
        session = PaperTradeSession(
            session_id=uuid.uuid4().hex[:12],
            strategy_name=strategy_name,
            symbols=list(symbols),
            initial_capital=initial_capital,
            cash=initial_capital,
            created_at=datetime.now().isoformat(),
            status="running",
            params=params or {},
        )
        self._save_session(session)
        return session

    def daily_run(self, session_id: str, nav_data: dict[str, list[dict]],
                  run_date: Optional[date] = None) -> Optional[PaperTradeSession]:
        """Execute one day of paper trading."""
        session = self._load_session(session_id)
        if session is None or session.status != "running":
            return session

        today = run_date or date.today()
        today_str = today.isoformat()

        if session.last_run_date is not None and session.last_run_date >= today_str:
            return session

        today_prices = self._get_today_prices(session.symbols, nav_data, today)
        if not today_prices:
            return session

        # Rebuild execution engine
        execution = T1ExecutionEngine(confirmation_delay=1)
        execution.set_capital(session.cash)
        for sym, pd in (session.positions or {}).items():
            shares = pd.get("shares", 0) if isinstance(pd, dict) else pd
            if shares > 0:
                from .signal import Position, Direction
                execution._positions[sym] = Position(
                    symbol=sym, direction=Direction.LONG,
                    volume=shares, avg_price=pd.get("avg_price", 0) if isinstance(pd, dict) else 0,
                )

        # Init strategy
        try:
            strategy_cls = StrategyRegistry.get(session.strategy_name)
        except KeyError:
            return session
        strategy = strategy_cls()
        strategy.params.update(session.params)

        from .event import EventBus
        bus = EventBus()
        ctx = StrategyContext(bus)
        ctx.execution = execution
        strategy.on_init(ctx)

        signals_published = []
        def _capture(event):
            signals_published.append(event.payload)
        bus._subscribers.setdefault("signal.generated", []).append(_capture)

        for sym in session.symbols:
            price = today_prices.get(sym)
            if price is None or price <= 0:
                continue
            strategy.on_data(FundNavPoint(fund_code=sym, date=today, nav=price))

        # Risk + submit
        risk_ctx = RiskContext(portfolio_value=execution.portfolio_value,
                               positions=list(execution.positions()))
        for signal in signals_published:
            if any(r.level == RiskLevel.REJECT for r in self._risk_pipeline.run_signal(signal, risk_ctx)):
                continue
            if signal.direction in (Direction.LONG,) and signal.price * signal.volume > execution._capital:
                continue
            execution.submit(signal)

        # Trigger T+1 confirmation
        bar_price = next(iter(today_prices.values()), 0)
        from datetime import datetime as dt
        bar = Bar(symbol=session.symbols[0] if session.symbols else "",
                  exchange="", timeframe="1d",
                  datetime=dt.combine(today, dt.min.time()),
                  open=bar_price, high=bar_price, low=bar_price, close=bar_price, volume=0)
        fills = execution.on_bar(bar)
        for fill in fills:
            cost = self._cost_model.calc(
                next((s for s in signals_published if f"o_{s.id}" == fill.order_id), None) or Signal(id="", strategy="", symbol="", direction=Direction.HOLD, price=0, volume=0),
                fill)
            if hasattr(execution, 'deduct_cost'):
                execution.deduct_cost(cost)

        # Persist state
        session.cash = execution._capital
        session.positions = {p.symbol: {"shares": p.volume, "avg_price": p.avg_price} for p in execution.positions()}
        session.equity_curve.append({"date": today_str, "equity": round(execution.portfolio_value, 2), "cash": round(session.cash, 2)})
        if hasattr(execution, '_trade_log'):
            for t in execution._trade_log:
                session.trade_log.append({**t, "date": today_str})
        session.last_run_date = today_str
        self._save_session(session)
        return session

    def stop(self, session_id: str) -> Optional[PaperTradeSession]:
        s = self._load_session(session_id)
        if s is None:
            return None
        s.status = "stopped"
        self._save_session(s)
        return s

    def get_status(self, session_id: str) -> Optional[PaperTradeSession]:
        return self._load_session(session_id)

    def list_sessions(self) -> list[dict]:
        sm = []
        for s in self._list_sessions():
            days = len(s.equity_curve)
            init = s.initial_capital
            lv = s.equity_curve[-1]["equity"] if s.equity_curve else init
            tr = (lv - init) / init if init > 0 else 0
            sharp = 0.0
            if len(s.equity_curve) >= 3:
                rets = []
                for i in range(1, len(s.equity_curve)):
                    pv = s.equity_curve[i-1].get("equity", 0)
                    cv = s.equity_curve[i].get("equity", 0)
                    if pv > 0:
                        rets.append(cv / pv - 1)
                if rets:
                    ar = sum(rets) / len(rets)
                    vr = sum((r - ar) ** 2 for r in rets) / len(rets)
                    std = vr ** 0.5
                    if std > 0:
                        sharp = round(ar / std * (252 ** 0.5), 4)
            sm.append({"session_id": s.session_id, "strategy_name": s.strategy_name,
                        "status": s.status, "days_run": days, "total_return": round(tr, 4),
                        "current_value": round(lv, 2), "sharpe": sharp, "last_run": s.last_run_date})
        return sm

    @staticmethod
    def _get_today_prices(symbols: list[str], nav_data: dict[str, list[dict]], today: date) -> dict[str, float]:
        result = {}
        for sym in symbols:
            for r in nav_data.get(sym, []):
                rd = r.get("date")
                if isinstance(rd, str):
                    rd = date.fromisoformat(rd)
                nav = r.get("nav") or r.get("adjusted_nav")
                if nav is not None and rd <= today:
                    prev = result.get(sym)
                    if prev is None or rd > prev[0]:
                        result[sym] = (rd, float(nav))
        return {k: v[1] for k, v in result.items()}

    def set_risk_pipeline(self, pipeline: RiskPipeline):
        self._risk_pipeline = pipeline

    def set_cost_model(self, cost_model: CostModel):
        self._cost_model = cost_model