"""API集成测试 — FundQuant端点"""

import sys; sys.path.insert(0, 'backend/..')
import pytest
from fastapi.testclient import TestClient

# 导入主应用（需要先在backend启动）
try:
    from backend.main import app
    HAS_APP = True
except ImportError:
    HAS_APP = False


pytestmark = pytest.mark.skipif(not HAS_APP, reason="需要backend.main模块")


class TestFundQuantAPI:
    """FundQuant API 集成测试"""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_strategy_list(self, client):
        res = client.get("/api/fund-quant/strategy/list")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        strategies = data["data"]
        # timing 策略已废弃删除，剩余 selection(3) + allocation(4) = 7
        assert len(strategies) >= 7
        names = {s["name"] for s in strategies}
        assert "momentum" not in names, "废弃的 timing 策略不应在列表中"

    def test_strategy_params(self, client):
        res = client.get("/api/fund-quant/strategy/params/multi_factor")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["name"] == "multi_factor"
        assert "default_params" in data

    def test_strategy_params_expose_declared_choices(self, client):
        res = client.get("/api/fund-quant/strategy/params/all_weather_aurora")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["param_choices"]["mode"] == ["fixed", "risk_parity"]

    def test_strategy_params_not_found(self, client):
        res = client.get("/api/fund-quant/strategy/params/nonexistent")
        assert res.status_code == 404

    def test_risk_metrics_no_fund(self, client):
        res = client.get("/api/fund-quant/risk/metrics")
        assert res.status_code == 200

    def test_risk_metrics_with_fund(self, client):
        res = client.get("/api/fund-quant/risk/metrics?fund_code=000001")
        assert res.status_code == 200

    def test_data_quality_unknown(self, client):
        res = client.get("/api/fund-quant/data/quality/nonexistent")
        assert res.status_code == 200
        assert "data" in res.json()

    def test_walk_forward_endpoint(self, client, monkeypatch):
        import backend.api.fund_quant as fund_quant

        monkeypatch.setattr(
            "backend.fund_quant.backtest.validation.walk_forward_validator.validate",
            lambda *_args, **_kwargs: {"method": "walk_forward", "summary": {"valid_windows": 1}},
        )
        res = client.post("/api/fund-quant/backtest/walk-forward", json={
            "strategy_name": "all_weather_aurora",
            "fund_codes": ["510300", "518880"],
            "start_date": "2020-01-01",
            "end_date": "2024-01-01",
        })

        assert res.status_code == 200
        assert res.json()["data"]["summary"]["valid_windows"] == 1

        res = client.get("/api/fund-quant/backtest/list")
        assert res.status_code == 200

    def test_aurora_backtest_configures_risk_pipeline(self, monkeypatch):
        import backend.api.fund_quant as fund_quant
        from types import SimpleNamespace

        class SpyEngine:
            def __init__(self, *_args, **_kwargs):
                self.risk = None

            def set_strategy(self, _strategy):
                pass

            def set_executor(self, _executor):
                pass

            def set_cost_model(self, _model):
                pass

            def set_risk(self, pipeline):
                self.risk = pipeline

            def set_data(self, _data):
                pass

            def run(self):
                assert self.risk is not None
                return SimpleNamespace(
                    equity_curve=[{"equity": 100000}],
                    total_trades=0,
                )

        class Strategy:
            def __init__(self):
                self.params = {}

            def on_init(self, _ctx):
                pass

        monkeypatch.setattr("core.BacktestEngine", SpyEngine)
        monkeypatch.setattr(fund_quant, "get_nav_history", lambda *_args, **_kwargs: [
            {"date": "2024-01-01", "nav": 1.0},
        ])
        monkeypatch.setattr(fund_quant, "save_backtest_result", lambda _result: None)
        monkeypatch.setattr(
            "core.strategy.StrategyRegistry.get",
            staticmethod(lambda _name: Strategy),
        )

        fund_quant._run_backtest_sync({
            "backtest_id": "risk-pipeline-test",
            "strategy_name": "all_weather_aurora",
            "fund_codes": ["A"],
            "start_date": "2024-01-01",
            "end_date": "2024-01-01",
        })

    def test_selection_screen(self, client):
        res = client.post("/api/fund-quant/selection/score",
                          json={"fund_type": "stock"})
        assert res.status_code == 200

    def test_strategy_allocation_current(self, client):
        res = client.post("/api/fund-quant/strategy/allocation/current",
                          json={"fund_codes": ["518880", "513100", "510300"]})
        assert res.status_code == 200
        data = res.json()
        assert "strategies" in data.get("data", {})

    def test_allocation_current_includes_registered_aurora_strategies(self, client):
        res = client.post("/api/fund-quant/strategy/allocation/current",
                          json={"fund_codes": ["518880", "513100", "510300"]})
        assert res.status_code == 200
        names = {strategy["strategy"] for strategy in res.json()["data"]["strategies"]}
        assert {"dynamic_risk_parity_aurora", "vol_targeting_aurora",
                "trend_following_aurora", "gmv_aurora"}.issubset(names)

    def test_signal_history(self, client):
        res = client.get("/api/fund-quant/signal/history?limit=5")
        assert res.status_code == 200
        assert "data" in res.json()

    def test_data_status(self, client):
        res = client.get("/api/fund-quant/data/status")
        assert res.status_code == 200

    def test_factor_list(self, client):
        res = client.get("/api/fund-quant/factor/list")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        names = {factor["name"] for factor in data["data"]}
        assert {"calendar_return", "sharpe_ratio", "max_drawdown"}.issubset(names)

    @pytest.mark.parametrize("payload", [
        {"factor_name": "sharpe_ratio", "fund_codes": [],
         "start_date": "2022-01-01", "end_date": "2024-01-01"},
        {"factor_name": "sharpe_ratio", "fund_codes": ["000001"],
         "start_date": "2024-01-01", "end_date": "2022-01-01"},
    ])
    def test_factor_evaluate_validation(self, client, payload):
        res = client.post("/api/fund-quant/factor/evaluate", json=payload)
        assert res.status_code == 400

    def test_factor_evaluate_unknown_factor(self, client):
        res = client.post("/api/fund-quant/factor/evaluate", json={
            "factor_name": "does_not_exist",
            "fund_codes": ["000001"],
            "start_date": "2022-01-01",
            "end_date": "2024-01-01",
        })
        assert res.status_code == 404

    def test_factor_evaluate_success(self, client, monkeypatch):
        from backend.core.factor.evaluation import EvaluationEngine
        from backend.core.factor.models import FactorEvaluationReport
        from datetime import date

        def fake_run(self, factor, symbols, start, end):
            return FactorEvaluationReport(
                factor_name=factor.meta.name,
                domain="fund",
                category=factor.meta.category,
                evaluation_period=(start, end),
                n_periods=2,
                rank_ic_mean=0.25,
            )

        monkeypatch.setattr(EvaluationEngine, "run", fake_run)
        res = client.post("/api/fund-quant/factor/evaluate", json={
            "factor_name": "sharpe_ratio",
            "fund_codes": ["000001", "000002"],
            "start_date": date(2022, 1, 1).isoformat(),
            "end_date": date(2024, 1, 1).isoformat(),
        })
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["factor_name"] == "sharpe_ratio"
        assert data["evaluation_period"] == ["2022-01-01", "2024-01-01"]
        assert data["n_periods"] == 2
