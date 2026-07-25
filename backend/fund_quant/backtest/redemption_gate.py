"""巨额赎回限制 — 基于基金总份额的赎回比例检查"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RedemptionVerdict:
    passed: bool
    reason: str = ""
    max_accepted: Optional[float] = None  # shares accepted if partial


class RedemptionGate:
    """巨额赎回限制 — 基于基金总份额的赎回比例检查"""

    def __init__(self, LARGE_REDEMPTION_PCT: float = 0.10,
                 FULL_REJECTION_PCT: float = 0.20,
                 deferred_days: int = 5,
                 consecutive_limit: int = 3):
        self.LARGE_REDEMPTION_PCT = LARGE_REDEMPTION_PCT
        self.FULL_REJECTION_PCT = FULL_REJECTION_PCT
        self.DEFERRED_DAYS = deferred_days
        self.CONSECUTIVE_LIMIT = consecutive_limit
        self._consecutive_triggers: dict[str, int] = {}
        self._suspended: set[str] = set()

    def check(self, fund_code: str, sell_shares: float,
              total_shares: float) -> RedemptionVerdict:
        """检查是否触发巨额赎回限制"""
        if fund_code in self._suspended:
            return RedemptionVerdict(
                passed=False, reason="基金处于暂停赎回状态", max_accepted=0,
            )
        if total_shares <= 0:
            return RedemptionVerdict(passed=True)
        ratio = sell_shares / total_shares
        if ratio < self.LARGE_REDEMPTION_PCT:
            self._consecutive_triggers[fund_code] = 0
            return RedemptionVerdict(passed=True)
        self._consecutive_triggers[fund_code] = self._consecutive_triggers.get(fund_code, 0) + 1
        if self._consecutive_triggers[fund_code] >= self.CONSECUTIVE_LIMIT:
            self._suspended.add(fund_code)
            return RedemptionVerdict(
                passed=False, reason=f"连续 {self.CONSECUTIVE_LIMIT} 日触发巨额赎回, 暂停赎回",
                max_accepted=0,
            )
        if ratio < self.FULL_REJECTION_PCT:
            max_acc = sell_shares * 0.5  # accept 50%
            return RedemptionVerdict(
                passed=False, reason=f"赎回比例 {ratio:.1%} ≥ {self.LARGE_REDEMPTION_PCT:.0%}, 部分接受",
                max_accepted=max_acc,
            )
        return RedemptionVerdict(
            passed=False, reason=f"赎回比例 {ratio:.1%} ≥ {self.FULL_REJECTION_PCT:.0%}, 全部拒绝",
            max_accepted=0,
        )

    def clear_suspension(self, fund_code: str):
        """解除暂停赎回状态（连续 N 日无巨额赎回时外部调用）"""
        self._suspended.discard(fund_code)
        self._consecutive_triggers[fund_code] = 0
