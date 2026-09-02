"""Aurora 选基迁移第一步 — 数据注入/降级语义测试（迁移后持续约束）。

三个 legacy selection scorer（multi_factor / index_selection / rating_enhanced）
都通过 storage 层全库读取基金池/元数据/净值。Aurora 迁移必须保留：
  1) 空池 / 无元数据 → 空 rankings + total_candidates=0（不崩溃、不误报）
  2) 每基金缺失评级/规模等可选字段 → 中性降级（不因单字段缺失丢基金）
  3) storage 调用契约（get_all_fund_codes / get_fund_meta / get_nav_history）
     从 legacy screen 转移到 Aurora 实现后，行为不得改变。
"""
import sys
sys.path.insert(0, "backend")

import pytest

from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection


@pytest.fixture
def empty_storage(monkeypatch):
    monkeypatch.setattr(
        "backend.fund_quant.data.storage.get_all_fund_codes", lambda: []
    )
    monkeypatch.setattr(
        "backend.fund_quant.data.storage.get_fund_meta", lambda code: None
    )
    monkeypatch.setattr(
        "backend.fund_quant.data.storage.get_nav_history", lambda code: []
    )


@pytest.fixture
def rich_storage(monkeypatch):
    """2 只基金：A 完整字段（评级 5），B 缺评级/规模（应中性降级而非丢弃）。"""
    funds = ["A", "B"]
    metas = {
        "A": {"fund_code": "A", "fund_name": "FundA", "fund_type": "stock", "rating": 5},
        "B": {"fund_code": "B", "fund_name": "FundB", "fund_type": "stock"},  # 缺 rating
    }
    monkeypatch.setattr(
        "backend.fund_quant.data.storage.get_all_fund_codes", lambda: funds
    )
    monkeypatch.setattr(
        "backend.fund_quant.data.storage.get_fund_meta", lambda code: metas.get(code)
    )
    monkeypatch.setattr(
        "backend.fund_quant.data.storage.get_nav_history",
        lambda code: [{"nav": 1.0 + i * 0.001} for i in range(200)],
    )


class TestSelectionDegradationSemantics:
    def test_empty_pool_returns_empty_rankings(self, empty_storage):
        result = RatingEnhancedSelection().screen(fund_type="stock", top_n=5)
        assert result["rankings"] == []
        assert result["total_candidates"] == 0

    def test_missing_optional_fields_degrades_neutrally(self, rich_storage):
        """缺评级基金 B 仍参与排名，评级中性化 0.5，不因单字段缺失被丢。"""
        result = RatingEnhancedSelection().screen(fund_type="stock", top_n=5)
        codes = {r["fund_code"] for r in result["rankings"]}
        assert {"A", "B"} <= codes
        b = next(r for r in result["rankings"] if r["fund_code"] == "B")
        assert b["rating_score"] == pytest.approx(0.5)  # (5-1)/4=1.0 for A；B 中性
        a = next(r for r in result["rankings"] if r["fund_code"] == "A")
        assert a["rating_score"] == pytest.approx(1.0)

    def test_score_smoke(self, rich_storage):
        result = RatingEnhancedSelection().score(fund_type="stock")
        assert set(result["scores"]) >= {"A", "B"}

    def test_storage_contract_functions_used(self, rich_storage):
        """迁移后必须仍通过这些 storage 入口取数（不引入新数据源）。"""
        import backend.fund_quant.data.storage as storage
        result = RatingEnhancedSelection().screen(fund_type="stock", top_n=5)
        assert result["total_candidates"] == 2
