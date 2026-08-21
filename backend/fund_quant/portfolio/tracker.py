"""模拟组合跟踪 — 基于 T1ExecutionEngine"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, List

from core import T1ExecutionEngine, Position, Direction


class PortfolioTracker:
    """模拟组合跟踪器 — 基于 T1ExecutionEngine"""

    def __init__(self, initial_capital: float = 100000.0):
        self._engine = T1ExecutionEngine(confirmation_delay=1)
        self._engine.set_capital(initial_capital)
        self._initial_capital = initial_capital
        self._history: List[dict] = []

    def update(self, fund_code: str, shares: float, nav: float):
        self._engine._positions[fund_code] = Position(
            symbol=fund_code, direction=Direction.LONG,
            volume=shares, avg_price=nav,
        )
        self._snapshot(f"update {fund_code}")

    def buy(self, fund_code: str, amount: float, nav: float):
        if amount > self._engine._capital:
            amount = self._engine._capital
        shares = amount / nav if nav > 0 else 0
        self._engine._capital -= amount
        pos = self._engine._positions.get(fund_code)
        if pos:
            total = pos.avg_price * pos.volume + nav * shares
            pos.volume += shares
            pos.avg_price = total / pos.volume
        else:
            self._engine._positions[fund_code] = Position(
                symbol=fund_code, direction=Direction.LONG,
                volume=shares, avg_price=nav,
            )
        self._snapshot(f"买入 {fund_code} 金额 {amount:.2f}")

    def sell(self, fund_code: str, pct: float, nav: float):
        pos = self._engine._positions.get(fund_code)
        if not pos:
            return
        sell_shares = pos.volume * pct
        amount = sell_shares * nav
        self._engine._capital += amount
        pos.volume -= sell_shares
        if pos.volume <= 1e-9:
            self._engine._positions.pop(fund_code, None)
        self._snapshot(f"卖出 {fund_code} 比例 {pct:.1%}")

    def _snapshot(self, action: str = ""):
        self._history.append({
            "timestamp": datetime.now().isoformat(),
            "total_value": self._engine.portfolio_value,
            "cash": self._engine._capital,
            "positions": {s: p.volume for s, p in self._engine._positions.items()},
            "action": action,
        })

    def get_status(self) -> dict:
        positions = {}
        for sym, pos in self._engine._positions.items():
            if pos.volume > 0:
                positions[sym] = {
                    "shares": pos.volume,
                    "nav": pos.avg_price,
                    "value": pos.volume * pos.avg_price,
                }
        return {
            "initial_capital": self._initial_capital,
            "total_value": self._engine.portfolio_value,
            "cash": self._engine._capital,
            "return_pct": ((self._engine.portfolio_value - self._initial_capital) / self._initial_capital * 100) if self._initial_capital > 0 else 0,
            "position_count": len(self._engine._positions),
            "positions": positions,
            "history_count": len(self._history),
        }

    def get_extended_status(self, nav_history: Optional[Dict[str, list]] = None) -> dict:
        base = self.get_status()
        base["annual_return"] = 0.0
        base["max_drawdown"] = 0.0
        base["sharpe_ratio"] = 0.0
        base["volatility"] = 0.0
        base["benchmark_return"] = 0.0
        base["signal_count"] = {"buy": 0, "sell": 0, "hold": 0}
        if nav_history and self._engine._positions:
            all_navs: list[float] = []
            for code in self._engine._positions:
                navs = nav_history.get(code, [])
                all_navs.extend(navs)
            if len(all_navs) > 20:
                daily_returns = [(all_navs[i] - all_navs[i-1]) / all_navs[i-1] for i in range(1, len(all_navs))]
                if daily_returns:
                    mean_daily = sum(daily_returns) / len(daily_returns)
                    base["annual_return"] = round(mean_daily * 252 * 100, 2)
                    variance = sum((r - mean_daily) ** 2 for r in daily_returns) / len(daily_returns)
                    vol = variance ** 0.5
                    base["volatility"] = round(vol * (252 ** 0.5) * 100, 2)
                    if vol > 0:
                        base["sharpe_ratio"] = round(mean_daily / vol * (252 ** 0.5), 2)
                peak = -float("inf")
                max_dd = 0.0
                for nav in all_navs:
                    peak = max(peak, nav)
                    dd = (nav - peak) / peak
                    max_dd = min(max_dd, dd)
                base["max_drawdown"] = round(max_dd * 100, 2)
        return base


portfolio_tracker = PortfolioTracker()