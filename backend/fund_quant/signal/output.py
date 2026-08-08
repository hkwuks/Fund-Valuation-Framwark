"""FundQuant 信号输出与SSE推送"""

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import AsyncGenerator, Optional, List, Dict
from loguru import logger

from ..core.enums import Direction
from ..core.models import FundSignal, FusionSignal
from ..data.storage import save_signal


class SignalOutputService:
    """信号输出服务"""

    # 信号优先级排序: 风控 > 择时 > 配置 > 选基
    PRIORITY_MAP = {
        "risk": 0,
        "timing": 1,
        "allocation": 2,
        "selection": 3,
    }

    def __init__(self):
        self._subscribers: List[asyncio.Queue] = []
        self._recent_signals: Dict[str, datetime] = {}  # fund_code -> 上次推送时间
        self._cooldown_seconds = 300  # 5分钟冷却

    def emit_signal(self, signal: FundSignal) -> str:
        """发射信号（持久化+推送）"""
        if not signal.signal_id:
            signal.signal_id = f"sig_{uuid.uuid4().hex[:12]}"
        signal.timestamp = datetime.now()

        # 冷却去重（单基金+策略+类型 5分钟，避免不同策略互相拦截）
        key = f"{signal.fund_code}:{signal.signal_type}:{signal.strategy_name}"
        last = self._recent_signals.get(key)
        if last and (datetime.now() - last).total_seconds() < self._cooldown_seconds:
            return signal.signal_id

        self._recent_signals[key] = datetime.now()

        # 预估交易成本
        cost = self._estimate_cost(signal)
        if cost:
            signal.suggested_amount = cost.get("total_cost", 0)

        # 持久化
        try:
            save_signal(signal)
        except Exception as e:
            logger.warning(f"信号持久化失败: {e}")

        # 推送到所有订阅者
        payload = signal.model_dump_json()
        dead_queues = []
        for q in self._subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead_queues.append(q)
        for q in dead_queues:
            self._subscribers.remove(q)

        return signal.signal_id

    def emit_fusion(self, fusion: FusionSignal) -> Optional[str]:
        """发射融合信号"""
        signal = FundSignal(
            signal_id=f"fusion_{uuid.uuid4().hex[:12]}",
            fund_code=fusion.fund_code,
            fund_name=fusion.fund_name,
            signal_type="allocation",
            direction=fusion.direction,
            confidence=fusion.confidence,
            reason=fusion.reason,
            strategy_name="signal_fusion",
            risk_check_passed=fusion.risk_check_passed,
            risk_warnings=fusion.risk_warnings,
        )
        return self.emit_signal(signal)

    async def stream_signals(self) -> AsyncGenerator[str, None]:
        """SSE信号流"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield payload
                except asyncio.TimeoutError:
                    yield json.dumps({"type": "heartbeat", "timestamp": datetime.now().isoformat()})
        except asyncio.CancelledError:
            pass
        finally:
            if queue in self._subscribers:
                self._subscribers.remove(queue)

    @staticmethod
    def _estimate_cost(signal: FundSignal) -> Optional[Dict]:
        """预估信号交易成本"""
        try:
            from ..backtest.cost_model import fund_cost_model
            amt = signal.suggested_amount or 100000.0
            cost = fund_cost_model.estimate_trade_cost(
                fund_type=signal.fund_type or "stock",
                amount=amt,
                holding_days=30,
                fund_code=signal.fund_code,
            )
            return cost
        except Exception:
            return None

    @staticmethod
    def _signal_priority(signal_type) -> int:
        """信号优先级数值（越小越优先）"""
        st = signal_type.value if hasattr(signal_type, 'value') else str(signal_type)
        return SignalOutputService.PRIORITY_MAP.get(st, 99)

    def sort_by_priority(self, signals: List[FundSignal]) -> List[FundSignal]:
        """按优先级排序: 风控>择时>配置>选基"""
        return sorted(signals, key=lambda s: self._signal_priority(s.signal_type))

    def format_signal(self, signal: FundSignal) -> dict:
        """格式化信号（含预估费率/优先级/免责声明）"""
        cost = self._estimate_cost(signal)
        priority = self._signal_priority(signal.signal_type)
        priority_labels = {0: "high", 1: "high", 2: "medium", 3: "low"}
        return {
            "signal_id": signal.signal_id,
            "timestamp": signal.timestamp.isoformat(),
            "fund": {
                "code": signal.fund_code,
                "name": signal.fund_name,
                "type": signal.fund_type,
            },
            "action": {
                "direction": signal.direction.value if hasattr(signal.direction, 'value') else signal.direction,
                "suggested_pct": signal.suggested_pct,
                "urgency": priority_labels.get(priority, "medium"),
                "priority": priority,
            },
            "analysis": {
                "strategy": signal.strategy_name,
                "confidence": signal.confidence,
                "reason": signal.reason,
                "estimated_cost": cost,
                "valid_until": (signal.timestamp + timedelta(days=3)).isoformat(),
            },
            "risk": {
                "check_passed": signal.risk_check_passed,
                "warnings": signal.risk_warnings,
            },
            "disclaimer": "本信号仅供参考，不构成投资建议。",
        }


# 全局单例
signal_output_service = SignalOutputService()
