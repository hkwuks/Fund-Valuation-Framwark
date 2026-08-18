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

    def test_backtest_list(self, client):
        res = client.get("/api/fund-quant/backtest/list")
        assert res.status_code == 200

    def test_selection_screen(self, client):
        res = client.post("/api/fund-quant/selection/screen",
                          json={"fund_type": "stock", "top_n": 5})
        assert res.status_code == 200
        data = res.json()
        assert "rankings" in data.get("data", {})

    def test_selection_score(self, client):
        res = client.post("/api/fund-quant/selection/score",
                          json={"fund_type": "stock"})
        assert res.status_code == 200

    def test_strategy_allocation_current(self, client):
        res = client.post("/api/fund-quant/strategy/allocation/current",
                          json={"fund_codes": ["518880", "513100", "510300"]})
        assert res.status_code == 200
        data = res.json()
        assert "strategies" in data.get("data", {})

    def test_signal_history(self, client):
        res = client.get("/api/fund-quant/signal/history?limit=5")
        assert res.status_code == 200
        assert "data" in res.json()

    def test_data_status(self, client):
        res = client.get("/api/fund-quant/data/status")
        assert res.status_code == 200
