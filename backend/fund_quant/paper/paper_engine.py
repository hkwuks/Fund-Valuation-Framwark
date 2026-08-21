"""基金域模拟交易引擎 — 继承 core.PaperTradeEngine + 基金特有设置"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from core import PaperTradeEngine, PaperTradeSession, RiskPipeline
from ..risk.risk_checks import (
    ConfidenceCheck, CooldownCheck, MinHoldingCheck,
    FundPositionLimitCheck, ConcentrationCheck,
    LiquidityCheck, CashReserveCheck,
)
from ..adapter import FundCostModelAdapter


class FundPaperEngine(PaperTradeEngine):
    """基金域模拟交易引擎 — JSON 持久化 + 基金特有风控/成本"""

    def __init__(self, state_dir: str = "paper_trade_states"):
        super().__init__(state_dir)
        pipeline = RiskPipeline()
        pipeline.add(ConfidenceCheck(min_confidence=0.6))
        pipeline.add(CooldownCheck(cooldown_days=5))
        pipeline.add(MinHoldingCheck(min_days=7))
        pipeline.add(FundPositionLimitCheck(max_position_pct=0.3))
        pipeline.add(ConcentrationCheck(max_pct=0.4))
        pipeline.add(LiquidityCheck(max_redemption_pct=0.2))
        pipeline.add(CashReserveCheck(min_cash_pct=0.05))
        self.set_risk_pipeline(pipeline)
        self.set_cost_model(FundCostModelAdapter())

    def _session_path(self, session_id: str) -> Path:
        return Path(self._state_dir) / f"{session_id}.json"

    def _load_session(self, session_id: str) -> Optional[PaperTradeSession]:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return PaperTradeSession(**data)
        except Exception:
            return None

    def _save_session(self, session: PaperTradeSession):
        path = self._session_path(session.session_id)
        data = {
            "session_id": session.session_id,
            "strategy_name": session.strategy_name,
            "symbols": session.symbols,
            "initial_capital": session.initial_capital,
            "cash": session.cash,
            "positions": session.positions,
            "equity_curve": session.equity_curve,
            "trade_log": session.trade_log,
            "last_run_date": session.last_run_date,
            "created_at": session.created_at,
            "status": session.status,
            "params": session.params,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _list_sessions(self) -> list[PaperTradeSession]:
        sessions: list[PaperTradeSession] = []
        if not os.path.isdir(self._state_dir):
            return sessions
        for fname in sorted(os.listdir(self._state_dir)):
            if not fname.endswith(".json"):
                continue
            sid = fname[:-5]
            s = self._load_session(sid)
            if s:
                sessions.append(s)
        return sessions


# Singleton
fund_paper_engine = FundPaperEngine()
