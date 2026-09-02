"""Selection 注入式重构 — 三个 legacy scorer screen(fund_data=...) 注入语义。

注入后必须保留现有 storage 读取回退（API/旧调用不传 fund_data 时行为不变），
并允许调用方传入预装载的候选基金截面（含 meta + nav_values），
使 Aurora 历史回测可按 as-of 日期注入，消除 selection 的全库未来读取。
"""
import sys
sys.path.insert(0, "backend")

import pytest

from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection
from backend.fund_quant.strategy.selection.multi_factor import MultiFactorSelection
from backend.fund_quant.strategy.selection.index_selection import IndexSelectionStrategy

# 与 test_selection_migration_semantics 一致的最小数据合约
FUND_DATA = [
    {"fund_code": "A", "fund_name": "FundA", "fund_type": "stock", "rating": 5,
     "nav_values": [1.0 + 0.001 * i for i in range(200)]},
    {"fund_code": "B", "fund_name": "FundB", "fund_type": "stock",
     "nav_values": [1.0 + 0.001 * i for i in range(200)]},
]


class TestRatingEnhancedInjection:
    def test_injected_fund_data_ranked_without_storage(self, monkeypatch):
        """传 fund_data 时完全不触碰 storage 读取。"""
        def fail(*_a, **_k):
            raise AssertionError("注入路径不得读取 storage")

        monkeypatch.setattr("backend.fund_quant.data.storage.get_all_fund_codes", fail)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_fund_meta", fail)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", fail)

        result = RatingEnhancedSelection().screen(fund_type="stock", top_n=5,
                                                  fund_data=FUND_DATA)
        assert result["total_candidates"] == 2
        codes = {r["fund_code"] for r in result["rankings"]}
        assert codes == {"A", "B"}

    def test_injected_data_missing_rating_neutral(self):
        result = RatingEnhancedSelection().screen(fund_type="stock", top_n=5,
                                                  fund_data=FUND_DATA)
        b = next(r for r in result["rankings"] if r["fund_code"] == "B")
        assert b["rating_score"] == pytest.approx(0.5)

    def test_fallback_to_storage_when_not_injected(self, monkeypatch):
        """不传 fund_data 时回退 storage（现有行为）。"""
        monkeypatch.setattr("backend.fund_quant.data.storage.get_all_fund_codes", lambda: [])
        result = RatingEnhancedSelection().screen(fund_type="stock", top_n=5)
        assert result["rankings"] == []


class TestMultiFactorInjection:
    def test_injected_data_skips_storage(self, monkeypatch):
        def fail(*_a, **_k):
            raise AssertionError("多因子注入路径不得读取 storage")

        monkeypatch.setattr("backend.fund_quant.data.storage.get_all_fund_codes", fail)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_fund_meta", fail)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", fail)

        # 注入 fund_data + 显式因子，避免依赖因子引擎
        result = MultiFactorSelection().screen(
            fund_type="stock", top_n=5,
            fund_data=FUND_DATA,
            factors=[],
        )
        # 空因子 → 等权回退（不触发 storage）
        assert "rankings" in result


class TestIndexSelectionInjection:
    def test_injected_data_skips_storage_and_uses_state(self, monkeypatch):
        def fail(*_a, **_k):
            raise AssertionError("指数选基注入路径不得读取 storage")

        monkeypatch.setattr("backend.fund_quant.data.storage.get_all_fund_codes", fail)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_fund_meta", fail)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", fail)

        index_data = [
            {"fund_code": "510300", "fund_type": "index",
             "management_fee": 0.005, "custody_fee": 0.001, "scale": 5e10,
             "nav_values": [1.0 + 0.001 * i for i in range(120)]},
        ]
        s = IndexSelectionStrategy()
        s._state["tracking_errors"] = {"510300": 0.003}
        result = s.screen(fund_type="index", top_n=5, fund_data=index_data)
        assert result["total_candidates"] == 1
        assert result["rankings"][0]["fund_code"] == "510300"
