"""ETF全球资产轮动 + 桥水全天候策略 测试"""
import pytest
import numpy as np
from datetime import date, timedelta
from unittest.mock import patch, MagicMock


# ── ETF全球资产轮动 ──

class TestEtfGlobalRotationStrategy:
    """ETF全球资产轮动策略测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from backend.fund_quant.strategy.allocation.etf_rotation import EtfGlobalRotationStrategy
        self.strategy = EtfGlobalRotationStrategy()

    def test_strategy_metadata(self):
        """元数据正确性"""
        assert self.strategy.strategy_name == "etf_global_rotation"
        assert self.strategy.strategy_type == "allocation"
        assert "etf_pool" in self.strategy.default_params
        assert self.strategy.default_params["top_n"] == 1
        assert self.strategy.default_params["momentum_days"] == 25

    def test_empty_result_on_no_data(self):
        """无净值数据时返回空权重"""
        import backend.fund_quant.data.storage as storage
        orig = storage.get_nav_history
        storage.get_nav_history = lambda *a, **kw: []
        try:
            result = self.strategy.optimize()
            assert result["status"] == "insufficient_data"
            for c, w in result["weights"].items():
                assert w == 0.0
        finally:
            storage.get_nav_history = orig

    def test_single_fund_pool(self):
        """单只ETF池也能正常工作"""
        self.strategy.params["etf_pool"] = {"510300": "沪深300ETF"}
        self.strategy.params["enable_market_timing"] = False

        import backend.fund_quant.data.storage as storage
        orig_nav = storage.get_nav_history
        orig_idx = storage.get_index_nav_prices

        def mock_nav(*args, **kwargs):
            import random
            n = 50
            base = 1.0
            navs = []
            for i in range(n):
                base *= (1 + random.uniform(-0.015, 0.015))
                navs.append({"date": f"2025-01-{i+1:02d}", "nav": round(base, 4), "fund_name": "测试"})
            return navs

        storage.get_nav_history = mock_nav
        storage.get_index_nav_prices = lambda *a, **kw: [1.0 + i * 0.001 for i in range(60)]

        try:
            result = self.strategy.optimize()
            assert result["status"] in ("success", "all_sell")
        finally:
            storage.get_nav_history = orig_nav
            storage.get_index_nav_prices = orig_idx

    def test_registered(self):
        """已注册到策略注册表"""
        from backend.fund_quant.strategy.base import StrategyRegistry
        registry = StrategyRegistry()
        s = registry.get_strategy_class("etf_global_rotation")
        assert s is not None
        assert s.strategy_name == "etf_global_rotation"


# ── 桥水全天候策略 ──

class TestAllWeatherStrategy:
    """全天候策略测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        from backend.fund_quant.strategy.allocation.all_weather import AllWeatherStrategy
        self.strategy = AllWeatherStrategy()

    def test_strategy_metadata(self):
        """元数据正确性"""
        assert self.strategy.strategy_name == "all_weather"
        assert self.strategy.strategy_type == "allocation"
        assert self.strategy.default_params["mode"] in ("fixed", "risk_parity")
        assert "asset_template" in self.strategy.default_params

    def test_fixed_mode_allocation(self):
        """固定权重模式：返回正确的资产类别配置"""
        result = self.strategy.optimize(params={"mode": "fixed"})
        assert result["status"] == "success"
        assert result["mode"] == "fixed"
        assert "weights" in result
        assert "asset_allocation" in result
        # 黄金 + 商品 配置约15%
        alloc = result["asset_allocation"]
        assert "gold" in alloc
        assert "commodity" in alloc

    def test_fixed_mode_weights_sum_to_approx_one(self):
        """固定权重模式下权重之和应约为1（无杠杆）"""
        result = self.strategy.optimize(params={"mode": "fixed", "leverage": 1.0})
        weights = result["weights"]
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01, f"权重之和 {total} 偏离1"

    def test_fixed_mode_with_leverage(self):
        """杠杆有效放大权重"""
        result_1x = self.strategy.optimize(params={"mode": "fixed", "leverage": 1.0})
        result_2x = self.strategy.optimize(params={"mode": "fixed", "leverage": 2.0})

        for c in result_1x["weights"]:
            if result_1x["weights"][c] > 0:
                assert abs(result_2x["weights"][c] - 2 * result_1x["weights"][c]) < 0.001

    def test_risk_parity_mode_falls_back_on_no_data(self):
        """风险平价模式数据不足时回退固定权重"""
        import backend.fund_quant.data.storage as storage
        orig = storage.get_nav_history
        storage.get_nav_history = lambda *a, **kw: []
        try:
            result = self.strategy.optimize(params={"mode": "risk_parity"})
            # 应回退到 fixed 模式
            assert result["status"] == "success"
        finally:
            storage.get_nav_history = orig

    def test_risk_parity_mode_with_data(self):
        """风险平价模式有数据时正常求解"""
        import random
        import backend.fund_quant.data.storage as storage
        orig = storage.get_nav_history

        def mock_nav(*args, **kwargs):
            n = 800
            base = 1.0
            navs = []
            for i in range(n):
                base *= (1 + random.uniform(-0.015, 0.015))
                navs.append({"date": f"2023-01-{(i % 365) + 1:03d}",
                              "nav": round(base, 4)})
            return navs

        storage.get_nav_history = mock_nav
        try:
            result = self.strategy.optimize(params={"mode": "risk_parity"})
            assert result["status"] == "success"
            weights = result["weights"]
            total = sum(weights.values())
            assert abs(total - 1.0) < 0.02, f"权重之和 {total} 偏离1"
        finally:
            storage.get_nav_history = orig

    def test_custom_asset_template(self):
        """自定义资产模板"""
        custom_template = {
            "159915": {"name": "创业板ETF", "asset_class": "equity", "fixed_weight": 0.5},
            "511260": {"name": "10年国债ETF","asset_class": "bond_long", "fixed_weight": 0.5},
        }
        result = self.strategy.optimize(params={
            "mode": "fixed",
            "asset_template": custom_template,
        })
        assert result["status"] == "success"
        weights = result["weights"]
        assert "159915" in weights
        assert "511260" in weights
        total = sum(weights.values())
        assert abs(total - 1.0) < 0.01

    def test_registered(self):
        """已注册到策略注册表"""
        from backend.fund_quant.strategy.base import StrategyRegistry
        registry = StrategyRegistry()
        s = registry.get_strategy_class("all_weather")
        assert s is not None
        assert s.strategy_name == "all_weather"
