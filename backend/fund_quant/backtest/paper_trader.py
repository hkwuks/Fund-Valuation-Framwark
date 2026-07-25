"""模拟交易模块 (PaperTrader) — 日频基金策略模拟交易

独立于回测引擎，复用 FundCostModel 和 RedemptionGate。
状态通过 Pickle 持久化，支持中断恢复。
"""

from __future__ import annotations

import logging
import os
import pickle
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .cost_model import FundCostModel
from .redemption_gate import RedemptionGate

logger = logging.getLogger(__name__)


@dataclass
class PaperTradeState:
    """模拟交易会话状态"""
    paper_trade_id: str
    strategy_name: str
    fund_codes: List[str]
    initial_capital: float
    cash: float
    positions: Dict[str, float]       # fund_code -> shares
    pending_orders: List[dict]
    equity_curve: List[dict]
    trade_log: List[dict]
    last_run_date: Optional[date]
    created_at: str
    status: str                        # "running" | "stopped"


@dataclass
class PaperTradeSummary:
    """模拟交易会话摘要"""
    paper_trade_id: str
    strategy_name: str
    status: str
    days_run: int
    total_return: float
    current_value: float
    sharpe: float
    last_run: Optional[str]


class FundPaperTrader:
    """日频基金模拟交易器

    daily_run() 的完整流程:
      1. 从磁盘加载状态
      2. 检查重复入日 (同一天跳过)
      3. 获取今日净值
      4. 确认 T+1 订单
      5. 调用策略生成信号
      6. 风控检查 → 下单
      7. 记录净值曲线 → 保存状态
    """

    def __init__(self, state_dir: str = "paper_trade_states",
                 strategy_func: Optional[Callable] = None):
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._cost_model = FundCostModel()
        self._redemption_gate = RedemptionGate()
        self._strategy_func = strategy_func

    # ── 生命周期 ──

    def start(self, strategy_name: str, fund_codes: List[str],
              initial_capital: float = 100000.0,
              params: dict = None) -> PaperTradeState:
        """启动一个新的模拟交易会话。"""
        if not fund_codes:
            raise ValueError("fund_codes must not be empty")

        paper_trade_id = uuid.uuid4().hex[:12]
        state = PaperTradeState(
            paper_trade_id=paper_trade_id,
            strategy_name=strategy_name,
            fund_codes=fund_codes,
            initial_capital=initial_capital,
            cash=initial_capital,
            positions={},
            pending_orders=[],
            equity_curve=[],
            trade_log=[],
            last_run_date=None,
            created_at=datetime.now().isoformat(),
            status="running",
        )
        self._save_state(state)
        return state

    def daily_run(self, paper_trade_id: str,
                  nav_data: Dict[str, List[dict]],
                  run_date: Optional[date] = None) -> Optional[PaperTradeState]:
        """执行一天的交易周期。

        参数:
            paper_trade_id: 会话 ID
            nav_data: {fund_code: [{date, nav, ...}, ...]}
            run_date: 运行日期, 默认 date.today()

        返回更新后的状态, 或 None (ID 不存在/已停止/无数据)。
        """
        state = self._load_state(paper_trade_id)
        if state is None or state.status != "running":
            return state

        today = run_date or date.today()

        # 2. 重复入日保护
        if state.last_run_date is not None and state.last_run_date >= today:
            logger.info("PaperTrade %s already run for %s", paper_trade_id, today)
            return state

        # 3. 获取今日净值
        today_navs = self._get_today_navs(state.fund_codes, nav_data, today)
        if not today_navs:
            logger.warning("PaperTrade %s: no NAV data for %s", paper_trade_id, today)
            return state

        state.last_run_date = today

        # 4. 确认 T+1 订单
        self._confirm_orders(state, today, today_navs)

        # 5. 调用策略生成信号
        if self._strategy_func:
            signals = self._strategy_func(paper_trade_id, self._state_to_dict(state))
        else:
            signals = []

        # 6. 风控检查 → 下单
        for sig in signals:
            code = sig["fund_code"]
            direction = sig["direction"]
            shares = sig["shares"]

            if code not in today_navs:
                logger.warning("Risk skip: %s has no NAV today", code)
                continue

            if direction == "sell":
                held = state.positions.get(code, 0.0)
                shares = min(shares, held)
                if shares <= 0:
                    continue
                # 巨额赎回检查
                total_shares = sum(state.positions.values()) or 1.0
                verdict = self._redemption_gate.check(code, shares, total_shares)
                if not verdict.passed and not (verdict.max_accepted or 0) > 0:
                    continue
                if verdict.max_accepted:
                    shares = min(shares, verdict.max_accepted)

            if direction == "buy":
                cost = shares * today_navs[code]
                if cost > state.cash:
                    logger.warning("Risk skip buy %s: insufficient cash", code)
                    continue

            order = {
                "fund_code": code,
                "direction": direction,
                "shares": shares,
                "submit_date": today.isoformat(),
                "status": "pending",
            }
            state.pending_orders.append(order)

        # 7. 记录净值曲线
        total_value = self._compute_total_value(state, today_navs)
        state.equity_curve.append({
            "date": today.isoformat(),
            "total_value": round(total_value, 2),
            "cash": round(state.cash, 2),
        })

        # 8. 保存
        self._save_state(state)
        return state

    def get_status(self, paper_trade_id: str) -> Optional[PaperTradeState]:
        """获取模拟交易会话的当前状态。"""
        return self._load_state(paper_trade_id)

    def list_sessions(self) -> List[PaperTradeSummary]:
        """列出所有模拟交易会话的摘要。"""
        summaries = []
        for fname in sorted(os.listdir(self._state_dir)):
            if not fname.endswith(".pkl"):
                continue
            trade_id = fname[:-4]
            state = self._load_state(trade_id)
            if state is None:
                continue
            days_run = len(state.equity_curve)
            initial = state.initial_capital
            last_value = state.equity_curve[-1]["total_value"] if state.equity_curve else initial
            total_return = (last_value - initial) / initial if initial > 0 else 0.0

            # 简易夏普 (日频 > 年化)
            sharpe = 0.0
            if len(state.equity_curve) >= 3:
                returns = []
                for i in range(1, len(state.equity_curve)):
                    pv = state.equity_curve[i - 1].get("total_value", 0)
                    cv = state.equity_curve[i].get("total_value", 0)
                    if pv > 0:
                        returns.append(cv / pv - 1)
                if returns:
                    avg_r = sum(returns) / len(returns)
                    var_r = sum((r - avg_r) ** 2 for r in returns) / len(returns)
                    std = var_r ** 0.5
                    if std > 0:
                        sharpe = round(avg_r / std * (252 ** 0.5), 4)

            summaries.append(PaperTradeSummary(
                paper_trade_id=trade_id,
                strategy_name=state.strategy_name,
                status=state.status,
                days_run=days_run,
                total_return=round(total_return, 4),
                current_value=round(last_value, 2),
                sharpe=sharpe,
                last_run=state.last_run_date.isoformat() if state.last_run_date else None,
            ))
        return summaries

    def stop(self, paper_trade_id: str) -> Optional[PaperTradeState]:
        """停止一个模拟交易会话。"""
        state = self._load_state(paper_trade_id)
        if state is None:
            return None
        state.status = "stopped"
        self._save_state(state)
        return state

    # ── 内部方法 ──

    def _confirm_orders(self, state: PaperTradeState, today: date,
                        navs: Dict[str, float]):
        """确认已到期的 T+1 订单。"""
        confirmed = []
        remaining = []
        for order in state.pending_orders:
            submit_str = order["submit_date"]
            submit_date = date.fromisoformat(submit_str)
            if today <= submit_date:
                remaining.append(order)
                continue

            code = order["fund_code"]
            direction = order["direction"]
            shares = order["shares"]
            nav = navs.get(code)
            if nav is None:
                remaining.append(order)
                continue

            if direction == "sell":
                held = state.positions.get(code, 0.0)
                actual = min(shares, held)
                if actual > 0:
                    proceeds = round(actual * nav, 2)
                    state.cash += proceeds
                    state.positions[code] = held - actual
                    if state.positions[code] <= 0:
                        del state.positions[code]
                    state.trade_log.append({
                        "fund_code": code,
                        "direction": "sell",
                        "shares": actual,
                        "nav": nav,
                        "amount": proceeds,
                        "date": today.isoformat(),
                        "status": "confirmed",
                    })
            elif direction == "buy":
                cost = round(shares * nav, 2)
                if cost <= state.cash:
                    state.cash -= cost
                    state.positions[code] = state.positions.get(code, 0.0) + shares
                    state.trade_log.append({
                        "fund_code": code,
                        "direction": "buy",
                        "shares": shares,
                        "nav": nav,
                        "amount": cost,
                        "date": today.isoformat(),
                        "status": "confirmed",
                    })
            confirmed.append(order)

        state.pending_orders = remaining

    def _get_today_navs(self, fund_codes: List[str],
                         nav_data: Dict[str, List[dict]],
                         today: date) -> Dict[str, float]:
        """从净值数据中提取今日 (或之前最近) 的净值。"""
        result: Dict[str, float] = {}
        for code in fund_codes:
            records = nav_data.get(code, [])
            if not records:
                continue
            best = None
            for r in records:
                raw_date = r.get("date")
                if isinstance(raw_date, date):
                    rdate = raw_date
                else:
                    rdate = date.fromisoformat(str(raw_date))
                rnav = r.get("nav") or r.get("adjusted_nav")
                if rnav is not None and rdate <= today:
                    if best is None or rdate > best[0]:
                        best = (rdate, float(rnav))
            if best:
                result[code] = best[1]
        return result

    @staticmethod
    def _compute_total_value(state: PaperTradeState,
                              navs: Dict[str, float]) -> float:
        total = state.cash
        for code, shares in state.positions.items():
            nav = navs.get(code)
            if nav:
                total += shares * nav
        return total

    @staticmethod
    def _state_to_dict(state: PaperTradeState) -> dict:
        return {
            "paper_trade_id": state.paper_trade_id,
            "strategy_name": state.strategy_name,
            "fund_codes": list(state.fund_codes),
            "cash": state.cash,
            "positions": dict(state.positions),
            "last_run_date": state.last_run_date.isoformat() if state.last_run_date else None,
            "status": state.status,
        }

    # ── 持久化 ──

    def _save_state(self, state: PaperTradeState):
        path = self._state_dir / f"{state.paper_trade_id}.pkl"
        with open(path, "wb") as f:
            pickle.dump(state, f)

    def _load_state(self, paper_trade_id: str) -> Optional[PaperTradeState]:
        path = self._state_dir / f"{paper_trade_id}.pkl"
        if not path.exists():
            return None
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            logger.warning("Corrupt pickle for %s", paper_trade_id)
            return None
