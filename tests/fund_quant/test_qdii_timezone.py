"""QDII 净值时差处理测试 (P2-6)"""

import sys; sys.path.insert(0, 'backend/..')
from datetime import date
from unittest.mock import patch
import pytest
from backend.fund_quant.core.models import BacktestConfig, InformationSet
from backend.fund_quant.backtest.engine import FundBacktester


class TestQdiiTimezoneModels:
    """Model-level field existence tests"""

    def test_qdii_field_exists(self):
        """qdii_nav_available_up_to 和 qdii_fund_codes 字段存在"""
        # InformationSet
        info = InformationSet(
            nav_available_up_to=date(2024, 1, 10),
            intraday_quotes_available=date(2024, 1, 10),
            holdings_disclosed_up_to=date(2024, 1, 10),
            holdings_effective_date=date(2024, 1, 10),
        )
        assert hasattr(info, 'qdii_nav_available_up_to')
        assert info.qdii_nav_available_up_to is None  # 默认值

        # BacktestConfig
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2024-01-01", end_date="2024-12-31",
        )
        assert hasattr(cfg, 'qdii_fund_codes')
        assert cfg.qdii_fund_codes == []  # 默认空列表


class TestQdiiTimezoneEngine:
    """Engine-level QDII offset logic tests"""

    NAV_DATA = {
        "000001": [
            {"date": "2020-01-02", "nav": 1.0},
            {"date": "2020-01-03", "nav": 1.01},
            {"date": "2020-01-06", "nav": 1.02},
        ],
    }

    def test_qdii_nav_delayed(self):
        """有 QDII 基金时 qdii_nav_available_up_to < nav_available_up_to"""
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-01-10",
            qdii_fund_codes=["000001"],
        )
        engine = FundBacktester()
        with patch(
            "backend.fund_quant.backtest.engine.InformationSet",
            wraps=InformationSet,
        ) as mock_is:
            engine.run(cfg, nav_data=self.NAV_DATA)
            calls = mock_is.call_args_list
            has_delayed = any(
                call[1].get("qdii_nav_available_up_to") is not None
                and call[1]["qdii_nav_available_up_to"] < call[1]["nav_available_up_to"]
                for call in calls
            )
            assert has_delayed, (
                "应存在迭代中 qdii_nav_available_up_to < nav_available_up_to"
            )

    def test_non_qdii_no_delay(self):
        """无 QDII 基金时 qdii_nav_available_up_to 为 None"""
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-01-10",
        )
        engine = FundBacktester()
        with patch(
            "backend.fund_quant.backtest.engine.InformationSet",
            wraps=InformationSet,
        ) as mock_is:
            engine.run(cfg, nav_data=self.NAV_DATA)
            calls = mock_is.call_args_list
            all_none = all(
                call[1].get("qdii_nav_available_up_to") is None
                for call in calls
            )
            assert all_none, (
                "无 QDII 基金时所有 qdii_nav_available_up_to 应为 None"
            )

    def test_qdii_edge_at_start(self):
        """首个交易日 QDII 延后退化为当天"""
        day_navs = {
            "000001": [
                {"date": "2020-01-02", "nav": 1.0},
            ],
        }
        cfg = BacktestConfig(
            strategy_name="test", fund_codes=["000001"],
            start_date="2020-01-01", end_date="2020-01-05",
            qdii_fund_codes=["000001"],
        )
        engine = FundBacktester()
        with patch(
            "backend.fund_quant.backtest.engine.InformationSet",
            wraps=InformationSet,
        ) as mock_is:
            engine.run(cfg, nav_data=day_navs)
            calls = mock_is.call_args_list
            for call in calls:
                kwargs = call[1]
                qdii = kwargs.get("qdii_nav_available_up_to")
                nav = kwargs["nav_available_up_to"]
                if qdii is not None:
                    # qdii 不应早于首个交易日
                    assert qdii >= date(2020, 1, 2), (
                        "qdii_nav_available_up_to 不应在首个交易日之前"
                    )
                    assert qdii <= nav, (
                        "qdii_nav_available_up_to 不应大于 nav_available_up_to"
                    )
