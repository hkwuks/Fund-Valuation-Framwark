"""FundQuant 核心模块测试：config/enums/errors/models/storage"""

import sys; sys.path.insert(0, 'backend/..')
import pytest
from datetime import date, datetime
from backend.fund_quant.core.config import fund_quant_settings
from backend.fund_quant.core.enums import SignalType, Direction, FundType, StrategyType, DataQuality
from backend.fund_quant.core.errors import (
    FundQuantError, DataCollectionError, DataQualityError,
    StrategyNotFoundError, StrategyParamError, RiskCheckFailed,
    BacktestConfigError, LookAheadBiasError,
)
from backend.fund_quant.core.models import (
    FundSignal, RiskMetrics, BacktestConfig, BacktestResult,
    FundDataPoint, InformationSet, Portfolio, FusionSignal,
    NavPoint, FundHolding, HoldingItem,
)
from backend.fund_quant.data.storage import (
    init_db, save_nav_points, get_nav_history, get_latest_nav,
    save_signal, get_signals, save_backtest_result, get_backtest_result,
    upsert_fund_meta, get_fund_meta,
)
from backend.fund_quant.data.quality import data_quality_checker


class TestEnums:
    def test_signal_type_values(self):
        assert SignalType.TIMING.value == "timing"
        assert SignalType.SELECTION.value == "selection"
        assert SignalType.ALLOCATION.value == "allocation"

    def test_direction_values(self):
        assert Direction.BUY.value == "buy"
        assert Direction.SELL.value == "sell"
        assert Direction.HOLD.value == "hold"
        assert Direction.REBALANCE.value == "rebalance"

    def test_fund_type_coverage(self):
        types = {e.value for e in FundType}
        required = {"equity", "index", "balanced", "bond", "money", "qdii", "commodity", "fof"}
        assert required.issubset(types), f"缺少基金类型: {required - types}"


class TestErrors:
    def test_base_error(self):
        e = FundQuantError("test", "CODE")
        assert str(e) == "test"
        assert e.error_code == "CODE"

    def test_data_collection_error(self):
        e = DataCollectionError("网络超时", "000001")
        assert "000001" in str(e)
        assert e.error_code == "DATA_COLLECTION_ERROR"

    def test_strategy_not_found(self):
        e = StrategyNotFoundError("nonexistent")
        assert "nonexistent" in str(e)

    def test_look_ahead_bias(self):
        e = LookAheadBiasError("用了未来数据")
        assert e.error_code == "LOOK_AHEAD_BIAS"


class TestModels:
    def test_fund_signal_minimal(self):
        s = FundSignal(signal_id="s1", fund_code="000001",
                       signal_type=SignalType.TIMING, direction=Direction.BUY,
                       confidence=0.85, reason="测试信号")
        assert s.signal_id == "s1"
        assert s.confidence == 0.85
        assert s.risk_check_passed is True

    def test_fund_signal_confidence_validates(self):
        """置信度必须 ≤1.0 (Pydantic验证)"""
        with pytest.raises(Exception):
            FundSignal(signal_id="s2", fund_code="000001",
                       signal_type=SignalType.TIMING, direction=Direction.BUY,
                       confidence=1.5, reason="x")

    def test_risk_metrics_defaults(self):
        m = RiskMetrics()
        assert m.var_95 == 0.0
        assert m.sharpe_ratio is None

    def test_backtest_config_defaults(self):
        c = BacktestConfig(strategy_name="test", fund_codes=["000001"],
                           start_date="2020-01-01", end_date="2020-12-31")
        assert c.initial_capital == 100000.0
        assert c.rebalance_freq == "monthly"

    def test_information_set(self):
        info = InformationSet(
            nav_available_up_to=date(2024, 1, 1),
            intraday_quotes_available=date(2024, 1, 1),
            holdings_disclosed_up_to=date(2023, 12, 1),
            holdings_effective_date=date(2023, 12, 1),
        )
        assert info.nav_available_up_to == date(2024, 1, 1)

    def test_nav_point(self):
        p = NavPoint(fund_code="000001", date=date(2024, 1, 1), nav=1.5)
        assert p.adjusted_nav is None
        assert p.source == "eastmoney"

    def test_fusion_signal_defaults(self):
        f = FusionSignal(fund_code="000001", direction=Direction.HOLD, confidence=0.0)
        assert f.conflict is False


class TestStorage:
    @pytest.fixture(autouse=True)
    def setup_db(self):
        init_db()
        # 使用独立表隔离测试数据
        from backend.fund_quant.data.storage import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM nav_history WHERE fund_code='pytest_test'")
            conn.execute("DELETE FROM signals WHERE fund_code='pytest_test'")
            conn.execute("DELETE FROM backtest_results WHERE backtest_id='pytest_bt'")
            conn.commit()
        yield
        with get_conn() as conn:
            conn.execute("DELETE FROM nav_history WHERE fund_code='pytest_test'")
            conn.execute("DELETE FROM signals WHERE fund_code='pytest_test'")
            conn.execute("DELETE FROM backtest_results WHERE backtest_id='pytest_bt'")
            conn.commit()

    def test_save_and_get_nav(self):
        points = [
            NavPoint(fund_code="pytest_test", date=date(2024, 1, 1), nav=1.0, adjusted_nav=1.0),
            NavPoint(fund_code="pytest_test", date=date(2024, 1, 2), nav=1.01, adjusted_nav=1.02),
        ]
        save_nav_points(points)
        history = get_nav_history("pytest_test")
        assert len(history) == 2
        assert history[0]["nav"] == 1.0

    def test_get_latest_nav(self):
        points = [
            NavPoint(fund_code="pytest_test", date=date(2024, 1, 1), nav=1.0),
            NavPoint(fund_code="pytest_test", date=date(2024, 1, 3), nav=1.05),
        ]
        save_nav_points(points)
        latest = get_latest_nav("pytest_test")
        assert latest is not None and latest["nav"] == 1.05
        assert latest["date"] == "2024-01-03"

    def test_save_and_get_signal(self):
        s = FundSignal(signal_id="pytest_sig", fund_code="pytest_test",
                       fund_name="测试基金", signal_type=SignalType.TIMING,
                       direction=Direction.BUY, confidence=0.8, reason="测试")
        save_signal(s)
        signals = get_signals(fund_code="pytest_test")
        assert len(signals) >= 1
        assert signals[0]["direction"] == "buy"

    def test_backtest_crud(self):
        config = BacktestConfig(strategy_name="pytest", fund_codes=["000001"],
                                start_date="2020-01-01", end_date="2020-12-31")
        result = BacktestResult(backtest_id="pytest_bt", config=config,
                                total_return=0.1, status="completed")
        save_backtest_result(result)
        loaded = get_backtest_result("pytest_bt")
        assert loaded is not None
        assert loaded["backtest_id"] == "pytest_bt"

    def test_get_nav_empty(self):
        assert get_nav_history("nonexistent_fund") == []


class TestDataQuality:
    def test_empty_fund(self):
        issues = data_quality_checker.check_nav_quality("nonexistent")
        assert len(issues) == 1
        assert issues[0]["severity"] == "critical"
        assert issues[0]["issue"] == "no_data"

    def test_quality_summary(self):
        summary = data_quality_checker.get_quality_summary("nonexistent")
        assert summary["quality"] == "error"
        assert summary["total_issues"] == 1
        assert summary["critical_issues"] == 1
