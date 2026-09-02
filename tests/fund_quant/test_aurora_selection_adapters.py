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
    def test_refresh_uses_injected_screen(self, monkeypatch):
        """_refresh_scores 走 screen → 注入目标分数集。"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection

        monkeypatch.setattr(
            RatingEnhancedSelection,
            "screen",
            lambda *a, **k: {"rankings": [{"fund_code": "000001", "total_score": 0.8}]},
        )
        strategy = AuroraRatingEnhancedSelection()
        strategy._refresh_scores()
        assert strategy._scores == {"000001": 0.8}

    def test_default_uses_rating_enhanced_scorer(self, monkeypatch):
        """默认 fund_type=all 走评级增强评分器（非 MultiFactor）。"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection

        captured = {}

        def fake_screen(self_, fund_type="all", top_n=10):
            captured["fund_type"] = fund_type
            return {"rankings": []}

        monkeypatch.setattr(RatingEnhancedSelection, "screen", fake_screen)
        strategy = AuroraRatingEnhancedSelection()
        strategy._refresh_scores()
        assert captured["fund_type"] == "all"
        assert strategy._scores == {}
