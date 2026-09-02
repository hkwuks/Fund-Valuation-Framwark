"""Aurora selection adapters 行为审计测试（非迁移等价，仅约束当前语义）。"""
import sys
sys.path.insert(0, "backend")

import pytest

from backend.fund_quant.adapter import (
    AuroraMultiFactorSelection,
    AuroraIndexSelection,
    AuroraRatingEnhancedSelection,
)


class TestAuroraMultiFactorSelectionAdapter:
    def test_forwards_screen(self, monkeypatch):
        captured = {}

        def fake_screen(self_, fund_type="all", top_n=5, params=None):
            captured["fund_type"] = fund_type
            captured["top_n"] = top_n
            captured["params"] = params
            return {"rankings": []}

        monkeypatch.setattr(
            "backend.fund_quant.strategy.selection.multi_factor.MultiFactorSelection.screen",
            fake_screen,
        )
        strategy = AuroraMultiFactorSelection()
        strategy._state["x"] = 1
        result = strategy.screen(fund_type="equity", top_n=7, params={"p": 1})
        assert result == {"rankings": []}
        assert captured == {"fund_type": "equity", "top_n": 7, "params": {"p": 1}}

    def test_forwards_score(self, monkeypatch):
        captured = {}

        def fake_score(self_, fund_type="all", params=None):
            captured["fund_type"] = fund_type
            captured["params"] = params
            return {"rankings": []}

        monkeypatch.setattr(
            "backend.fund_quant.strategy.selection.multi_factor.MultiFactorSelection.score",
            fake_score,
        )
        strategy = AuroraMultiFactorSelection()
        result = strategy.score(fund_type="bond", params=None)
        assert result == {"rankings": []}
        assert captured == {"fund_type": "bond", "params": None}


class TestAuroraIndexSelectionAdapter:
    def test_forwards_state_and_screen(self, monkeypatch):
        captured = {}

        def fake_screen(self_, fund_type="index", top_n=5, params=None):
            captured["fund_type"] = fund_type
            captured["liquidity"] = self_._state.get("liquidity_data")
            return {"rankings": [{"fund_code": "510300", "total_score": 0.9}]}

        monkeypatch.setattr(
            "backend.fund_quant.strategy.selection.index_selection.IndexSelectionStrategy.screen",
            fake_screen,
        )
        strategy = AuroraIndexSelection()
        strategy._state["liquidity_data"] = {"510300": 1.0}
        result = strategy.screen(fund_type="index", top_n=5)
        assert result["rankings"][0]["total_score"] == 0.9
        assert captured["liquidity"] == {"510300": 1.0}


class TestAuroraRatingEnhancedSelectionRefresh:
    def test_rating_adapter_uses_rating_scorer(self):
        """评级增强 Aurora 适配器与 multi/index 共用统一 hist as-of 重放骨架。"""
        from backend.fund_quant.adapter import (
            _AuroraSelectionAdapter, AuroraRatingEnhancedSelection,
        )
        assert issubclass(AuroraRatingEnhancedSelection, _AuroraSelectionAdapter)
        # 统一骨架默认参数：fund_type/top_n/rebalance_days
        assert AuroraRatingEnhancedSelection.default_params["rebalance_days"] == 20
        from backend.fund_quant.strategy.selection.rating_enhanced import (
            RatingEnhancedSelection,
        )
        assert AuroraRatingEnhancedSelection().selection_cls is RatingEnhancedSelection

    def test_screen_forwards_fund_data_to_rating_scorer(self, monkeypatch):
        """screen(fund_data=...) 转发到评级增强评分器注入路径（不回退 storage）。"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection

        captured = {}

        def fake_screen(self_, fund_type="all", top_n=10, params=None, fund_data=None):
            captured["fund_type"] = fund_type
            captured["fund_data"] = fund_data
            return {"rankings": [{"fund_code": "000001", "total_score": 0.8}]}

        monkeypatch.setattr(RatingEnhancedSelection, "screen", fake_screen)
        strategy = AuroraRatingEnhancedSelection()
        section = [{"fund_code": "000001", "fund_type": "stock",
                    "nav_values": [1.0] * 80}]
        result = strategy.screen(fund_type="all", top_n=5, fund_data=section)
        assert result["rankings"][0]["fund_code"] == "000001"
        assert captured["fund_data"] is section
        assert captured["fund_type"] == "all"
