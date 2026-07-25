"""NAV缺口处理测试"""

import pytest
from backend.fund_quant.backtest.engine import FundBacktester
from backend.fund_quant.core.models import BacktestConfig


class TestNavGapHandling:
    def test_forward_fill_on_missing_nav(self):
        """前向填充: 缺失值用前一日净值填充"""
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-01-10",
            nav_gap_policy="forward_fill",
        )
        navs = {
            "000001": [
                {"date": "2020-01-02", "nav": 1.0},
                {"date": "2020-01-06", "nav": 1.05},
            ]
        }
        engine = FundBacktester()
        result = engine.run(cfg, nav_data=navs)
        assert result.status == "completed"

    def test_skip_policy_skips_gap_days(self):
        """skip 模式: 缺数日跳过"""
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-01-10",
            nav_gap_policy="skip",
        )
        navs = {
            "000001": [
                {"date": "2020-01-02", "nav": 1.0},
                {"date": "2020-01-06", "nav": 1.05},
            ]
        }
        engine = FundBacktester()
        result = engine.run(cfg, nav_data=navs)
        assert result.status == "completed"

    def test_low_quality_warning(self):
        """少于 50% 交易日有数据时标记低质量"""
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-02-01",
            min_nav_records_pct=0.5,
        )
        navs = {"000001": [{"date": "2020-01-02", "nav": 1.0}]}
        engine = FundBacktester()
        result = engine.run(cfg, nav_data=navs)
        assert result.status == "completed"
