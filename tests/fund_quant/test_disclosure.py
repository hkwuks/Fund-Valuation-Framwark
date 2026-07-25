"""DisclosureCalendar 单元测试"""

import sys; sys.path.insert(0, 'backend/..')
import pytest
from datetime import date
from backend.fund_quant.backtest.disclosure import DisclosureCalendar


class TestDisclosureCalendar:
    """P3-1: 持仓报告披露滞后建模"""

    def test_q1_report_available_april(self):
        """Q1 季报 3/31 结束 + 15 工作日 ≈ 4/21 可用"""
        cal = DisclosureCalendar(end_year=2024)
        q1_avail = cal._add_business_days(date(2024, 3, 31), 15)
        assert q1_avail >= date(2024, 4, 19)
        assert q1_avail.weekday() < 5  # 工作日

        # on 4/15 应该还拿不到 Q1 报告
        assert cal.get_available_as_of(date(2024, 4, 15)) < date(2024, 3, 31)
        # on 4/25 应该已经能看到 Q1 报告
        avail = cal.get_available_as_of(date(2024, 4, 25))
        assert avail >= date(2024, 3, 31)

    def test_annual_report_available_march(self):
        """年报 12/31 结束 + 90 自然日 ≈ 次年 3/31"""
        cal = DisclosureCalendar(end_year=2024)
        annual_avail = date(2024, 12, 31) + __import__("datetime").timedelta(days=90)
        assert annual_avail == date(2025, 3, 31)

        avail = cal.get_available_as_of(date(2025, 4, 1))
        assert avail == date(2024, 12, 31)

    def test_no_report_available(self):
        """年初尚无任何报告可用时返回 date.min"""
        cal = DisclosureCalendar(end_year=2024)
        # 用 2024-01-02 已有 2023Q3 报告可用 (available ~2023-10-21)
        # 真正无报告要回到 2020 年之前
        result = cal.get_available_as_of(date(2018, 1, 2))
        assert result == date.min

    def test_latest_report_selected(self):
        """多个报告可用时返回最晚的 end_date"""
        cal = DisclosureCalendar(end_year=2024)
        # 次年 4 月 1 日: Q1-2024 和年报-2023 都已可用
        avail = cal.get_available_as_of(date(2025, 4, 1))
        # 应该返回 2024-12-31（年报），因为 Q1-2025 要到 4 月下旬
        assert avail == date(2024, 12, 31)

    def test_semi_annual_lag(self):
        """H1 半年报 6/30 结束 + 60 自然日 ≈ 8/29"""
        cal = DisclosureCalendar(end_year=2024)
        h1_avail = date(2024, 6, 30) + __import__("datetime").timedelta(days=60)
        assert h1_avail == date(2024, 8, 29)

        avail = cal.get_available_as_of(date(2024, 9, 1))
        # H1 半年报 end=6/30 应可用
        assert avail >= date(2024, 6, 30)

    def test_business_days_skip_weekends(self):
        """加 1 个工作日：周五 → 下周一"""
        fri = date(2024, 1, 5)  # Friday
        result = DisclosureCalendar._add_business_days(fri, 1)
        assert result == date(2024, 1, 8)  # Monday

        # 0 天不加
        assert DisclosureCalendar._add_business_days(fri, 0) == fri

    def test_get_report_dates_structure(self):
        """get_report_dates 返回包含预期字段的列表"""
        cal = DisclosureCalendar(end_year=2024)
        reports = cal.get_report_dates(2024)
        assert len(reports) == 5  # Q1, Q2, H1, Q3, Annual
        periods = [r["period"] for r in reports]
        assert "Q1" in periods
        assert "H1" in periods
        assert "Annual" in periods
        for r in reports:
            assert "start_date" in r
            assert "end_date" in r
            assert "available_date" in r
            assert "type" in r
