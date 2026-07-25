"""DisclosureCalendar — 基金持仓报告披露日历（前视偏差防护）"""

from datetime import date, timedelta
from typing import Dict, List


class DisclosureCalendar:
    """模拟基金持仓报告的披露滞后。

    季报（季度结束后 15 个工作日）、半年报（60 个自然日）、年报（90 个自然日）。
    H1 半年报与 Q2 季报共用 end=Jun 30，但 H1 的 60d 滞后 > Q2 的 15bd 滞后，
    因此 H1 的 available_date 更晚、自动覆盖 Q2。年报覆盖 H1。
    """

    REPORT_LAGS = {
        "quarterly": 15,    # 15 个工作日
        "semi_annual": 60,  # 60 个自然日
        "annual": 90,       # 90 个自然日
    }

    def __init__(self, end_year: int | None = None):
        # ponytail: scan 5 back-years, enough for typical backtests
        now = date.today()
        end = end_year or now.year
        reports: list[dict] = []
        for y in range(end - 5, end + 1):
            reports.extend(self._generate_year(y))
        reports.sort(key=lambda r: r["available_date"])
        self._reports = reports

    # ── public API ──

    def get_available_as_of(self, as_of_date: date) -> date:
        """返回 `as_of_date` 时已可获取的最新报告对应区间结束日。

        若无任何报告可用返回 date.min。
        """
        if not self._reports:
            return date.min
        lo, hi = 0, len(self._reports) - 1
        best = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._reports[mid]["available_date"] <= as_of_date:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return self._reports[best]["end_date"] if best >= 0 else date.min

    def get_report_dates(self, year: int) -> List[Dict[str, date]]:
        """返回某一年所有报告期及对应披露可用日期。"""
        return [
            r for r in self._reports
            if r["start_date"].year == year or r["end_date"].year == year
        ]

    # ── 内部方法 ──

    @staticmethod
    def _add_business_days(start: date, n_days: int) -> date:
        """加上 n_days 个工作日（跳过周六周日）。"""
        current = start
        added = 0
        while added < n_days:
            current += timedelta(days=1)
            if current.weekday() < 5:  # Mon=0 .. Fri=4
                added += 1
        return current

    def _generate_year(self, year: int) -> List[Dict]:
        """生成某一年的全部报告条目。"""
        return [
            # Q1 季报：1/1-3/31，15 工作日
            {
                "period": "Q1",
                "type": "quarterly",
                "start_date": date(year, 1, 1),
                "end_date": date(year, 3, 31),
                "available_date": self._add_business_days(
                    date(year, 3, 31), self.REPORT_LAGS["quarterly"]
                ),
            },
            # Q2 季报：4/1-6/30，15 工作日
            {
                "period": "Q2",
                "type": "quarterly",
                "start_date": date(year, 4, 1),
                "end_date": date(year, 6, 30),
                "available_date": self._add_business_days(
                    date(year, 6, 30), self.REPORT_LAGS["quarterly"]
                ),
            },
            # H1 半年报：1/1-6/30，60 自然日
            {
                "period": "H1",
                "type": "semi_annual",
                "start_date": date(year, 1, 1),
                "end_date": date(year, 6, 30),
                "available_date": date(year, 6, 30)
                + timedelta(days=self.REPORT_LAGS["semi_annual"]),
            },
            # Q3 季报：7/1-9/30，15 工作日
            {
                "period": "Q3",
                "type": "quarterly",
                "start_date": date(year, 7, 1),
                "end_date": date(year, 9, 30),
                "available_date": self._add_business_days(
                    date(year, 9, 30), self.REPORT_LAGS["quarterly"]
                ),
            },
            # 年报：1/1-12/31，90 自然日
            {
                "period": "Annual",
                "type": "annual",
                "start_date": date(year, 1, 1),
                "end_date": date(year, 12, 31),
                "available_date": date(year, 12, 31)
                + timedelta(days=self.REPORT_LAGS["annual"]),
            },
        ]
