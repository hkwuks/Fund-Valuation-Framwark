"""基金清盘/合并检测器"""

from dataclasses import dataclass
from datetime import date
from typing import Optional, Dict, Set


@dataclass
class LiquidationEvent:
    fund_code: str
    date: date
    reason: str  # "基金清盘" / "基金合并"
    merge_target: Optional[str] = None  # 合并后的基金代码
    merge_ratio: Optional[float] = None  # 合并换股比例


class LiquidationHandler:
    """基金清盘/合并检测器"""

    def __init__(self):
        self._liquidation_dates: Dict[str, date] = {}
        self._mergers: Dict[str, tuple[str, float]] = {}  # old_code -> (new_code, ratio)
        self._active_funds: Set[str] = set()

    def set_liquidation(self, fund_code: str, date: date, reason: str = "基金清盘"):
        self._liquidation_dates[fund_code] = date
        self._active_funds.add(fund_code)

    def set_merger(self, old_code: str, new_code: str, ratio: float, date: date):
        self._liquidation_dates[old_code] = date
        self._mergers[old_code] = (new_code, ratio)
        self._active_funds.add(old_code)

    def check(self, fund_code: str, current_date: date) -> Optional[LiquidationEvent]:
        """检查当日是否触发清盘/合并事件"""
        if fund_code not in self._active_funds:
            return None
        liq_date = self._liquidation_dates.get(fund_code)
        if liq_date and liq_date == current_date:
            if fund_code in self._mergers:
                new_code, ratio = self._mergers[fund_code]
                event = LiquidationEvent(
                    fund_code=fund_code, date=current_date,
                    reason="基金合并", merge_target=new_code, merge_ratio=ratio,
                )
            else:
                event = LiquidationEvent(
                    fund_code=fund_code, date=current_date, reason="基金清盘",
                )
            self._active_funds.discard(fund_code)
            return event
        return None
