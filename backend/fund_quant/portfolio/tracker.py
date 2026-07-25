"""模拟组合跟踪"""

from datetime import datetime, date
from typing import Optional, Dict, List
from ..core.models import Portfolio


class PortfolioTracker:
    """模拟组合跟踪器"""

    def __init__(self, initial_capital: float = 100000.0):
        self._portfolio = Portfolio(total_value=initial_capital, cash=initial_capital)
        self._history: List[dict] = []
        self._initial_capital = initial_capital

    def update(self, fund_code: str, shares: float, nav: float):
        """更新持仓"""
        self._portfolio.nav_values[fund_code] = nav
        current_value = sum(
            self._portfolio.nav_values.get(code, 0) * shares
            for code, shares in self._portfolio.positions.items()
        ) + self._portfolio.cash
        self._portfolio.total_value = current_value

    def buy(self, fund_code: str, amount: float, nav: float):
        """买入操作"""
        if amount > self._portfolio.cash:
            amount = self._portfolio.cash
        shares = amount / nav if nav > 0 else 0
        self._portfolio.positions[fund_code] = self._portfolio.positions.get(fund_code, 0) + shares
        self._portfolio.cash -= amount
        self._portfolio.nav_values[fund_code] = nav
        self._portfolio.total_value = sum(
            self._portfolio.nav_values.get(c, 0) * s
            for c, s in self._portfolio.positions.items()
        ) + self._portfolio.cash
        self._snapshot(f"买入 {fund_code} 金额 {amount:.2f}")

    def sell(self, fund_code: str, pct: float, nav: float):
        """卖出操作"""
        shares = self._portfolio.positions.get(fund_code, 0)
        sell_shares = shares * pct
        amount = sell_shares * nav
        self._portfolio.positions[fund_code] = shares - sell_shares
        self._portfolio.cash += amount
        self._portfolio.nav_values[fund_code] = nav
        self._portfolio.total_value = sum(
            self._portfolio.nav_values.get(c, 0) * s
            for c, s in self._portfolio.positions.items()
        ) + self._portfolio.cash
        self._snapshot(f"卖出 {fund_code} 比例 {pct:.1%}")

    def _snapshot(self, action: str = ""):
        self._history.append({
            "timestamp": datetime.now().isoformat(),
            "total_value": self._portfolio.total_value,
            "cash": self._portfolio.cash,
            "positions": dict(self._portfolio.positions),
            "action": action,
        })

    def get_status(self) -> dict:
        """获取当前组合状态"""
        return {
            "initial_capital": self._initial_capital,
            "total_value": self._portfolio.total_value,
            "cash": self._portfolio.cash,
            "return_pct": ((self._portfolio.total_value - self._initial_capital) / self._initial_capital * 100) if self._initial_capital > 0 else 0,
            "position_count": len(self._portfolio.positions),
            "positions": {
                code: {
                    "shares": shares,
                    "nav": self._portfolio.nav_values.get(code, 0),
                    "value": shares * self._portfolio.nav_values.get(code, 0),
                }
                for code, shares in self._portfolio.positions.items()
            },
            "history_count": len(self._history),
        }


    def get_extended_status(self, nav_history: Optional[Dict[str, list]] = None) -> dict:
        """扩展组合状态：在 get_status 基础上增加 KPI 指标"""
        base = self.get_status()
        base["annual_return"] = 0.0
        base["max_drawdown"] = 0.0
        base["sharpe_ratio"] = 0.0
        base["volatility"] = 0.0
        base["benchmark_return"] = 0.0
        base["signal_count"] = {"buy": 0, "sell": 0, "hold": 0}

        # 如果有净值历史，计算年化收益和最大回撤
        if nav_history and self._portfolio.nav_values:
            all_navs: list[float] = []
            for code in self._portfolio.positions:
                navs = nav_history.get(code, [])
                all_navs.extend(navs)
            if len(all_navs) > 20:
                # 年化收益（按日频计算，252 个交易日）
                daily_returns = [(all_navs[i] - all_navs[i-1]) / all_navs[i-1]
                                 for i in range(1, len(all_navs))]
                if daily_returns:
                    mean_daily = sum(daily_returns) / len(daily_returns)
                    base["annual_return"] = round(mean_daily * 252 * 100, 2)
                    base["volatility"] = round(
                        (sum((r - mean_daily) ** 2 for r in daily_returns) / len(daily_returns)) ** 0.5 * (252 ** 0.5) * 100,
                        2,
                    )
                    if base["volatility"] > 0:
                        base["sharpe_ratio"] = round(mean_daily / (sum((r - mean_daily) ** 2 for r in daily_returns) / len(daily_returns)) ** 0.5 * (252 ** 0.5), 2)

                # 最大回撤
                peak = -float("inf")
                max_dd = 0.0
                for nav in all_navs:
                    peak = max(peak, nav)
                    dd = (nav - peak) / peak
                    max_dd = min(max_dd, dd)
                base["max_drawdown"] = round(max_dd * 100, 2)

        return base


portfolio_tracker = PortfolioTracker()
