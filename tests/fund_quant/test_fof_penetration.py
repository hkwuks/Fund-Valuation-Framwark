"""FOF 穿透分析测试"""
import pytest
from backend.fund_quant.analysis.fof_penetration import (
    _parse_subtype,
    _parse_benchmark,
    analyze_fof_penetration,
    PenetrationResult,
)


class TestParseSubtype:
    def test_wenjian(self):
        assert _parse_subtype("FOF-稳健型") == "稳健型"

    def test_junheng(self):
        assert _parse_subtype("FOF-均衡型") == "均衡型"

    def test_jinqu(self):
        assert _parse_subtype("FOF-进取型") == "进取型"

    def test_pianzhai(self):
        assert _parse_subtype("FOF-偏债混合") == "偏债混合"

    def test_piangu(self):
        assert _parse_subtype("FOF-偏股混合") == "偏股混合"

    def test_qdii_fof(self):
        assert _parse_subtype("QDII-FOF") == "QDII-FOF"

    def test_unknown(self):
        assert _parse_subtype("FOF") == "unknown"

    def test_empty(self):
        assert _parse_subtype("") == "unknown"


class TestParseBenchmark:
    def test_simple_equity(self):
        result = _parse_benchmark("沪深300指数收益率×80%+中债综合财富指数收益率×20%")
        assert result == 0.80

    def test_with_stock_keyword(self):
        result = _parse_benchmark("中证800股票指数收益率×20%+中债综合财富指数收益率×70%+Wind商品综合指数收益率×10%")
        assert result == 0.20

    def test_multi_equity(self):
        result = _parse_benchmark("沪深300指数收益率×50%+中证500指数收益率×20%+中债综合财富指数收益率×30%")
        assert result == pytest.approx(0.70, abs=0.01)

    def test_no_equity(self):
        result = _parse_benchmark("中债综合财富指数收益率×100%")
        assert result is None

    def test_no_benchmark(self):
        result = _parse_benchmark("")
        assert result is None

    def test_null(self):
        result = _parse_benchmark(None)
        assert result is None


class TestAnalyzeFofPenetration:
    def test_prior_wenjian(self):
        """稳健型 → 20% 权益"""
        r = analyze_fof_penetration("005156", subtype="稳健型")
        assert r.equity_ratio == 0.2
        assert r.bond_ratio == 0.8
        assert r.method == "prior"

    def test_prior_jinqu(self):
        """进取型 → 80% 权益"""
        r = analyze_fof_penetration("005220", subtype="进取型")
        assert r.equity_ratio == 0.8

    def test_prior_junheng(self):
        """均衡型 → 50% 权益"""
        r = analyze_fof_penetration("009161", subtype="均衡型")
        assert r.equity_ratio == 0.5

    def test_prior_unknown(self):
        """未知子类 → 50% 权益"""
        r = analyze_fof_penetration("000000", subtype="unknown")
        assert r.equity_ratio == 0.5

    def test_benchmark_only(self):
        """S2 基准可用（无 OLS）→ benchmark 模式"""
        r = analyze_fof_penetration("005156", subtype="稳健型", benchmark_eq=0.30)
        assert r.method == "benchmark"
        # S1 prior=0.2 × 0.3 + S2 benchmark=0.3 × 0.7 = 0.27
        assert r.equity_ratio == pytest.approx(0.27, abs=0.01)
        assert r.source_benchmark == 0.30

    def test_benchmark_with_ols(self):
        """S2 基准 + S4 OLS → hybrid 模式"""
        r = analyze_fof_penetration(
            "005156", subtype="稳健型", benchmark_eq=0.30,
            ols_result={"equity_ratio": 0.40, "bond_ratio": 0.60, "r_squared": 0.80},
        )
        assert r.method == "hybrid"
        assert r.source_benchmark == 0.30
        assert 0.27 < r.equity_ratio < 0.40  # 在基准与OLS之间

    def test_actual_holdings(self):
        """S3 实际持仓最高优先级 → actual 模式"""
        r = analyze_fof_penetration(
            "005156", subtype="稳健型", actual_eq=0.10,
            benchmark_eq=0.30,
            ols_result={"equity_ratio": 0.40, "bond_ratio": 0.60, "r_squared": 0.80},
        )
        assert r.method == "actual"
        assert r.equity_ratio == 0.10  # S3 直接覆盖
        assert r.confidence == pytest.approx(0.85)
        assert r.source_actual == 0.10

    def test_hybrid_high_r2(self):
        """高 R² OLS → hybrid 模式"""
        r = analyze_fof_penetration(
            "005156", subtype="稳健型",
            ols_result={"equity_ratio": 0.35, "bond_ratio": 0.65, "r_squared": 0.85},
        )
        assert r.method == "hybrid"
        assert 0.2 < r.equity_ratio < 0.35

    def test_low_r2_fallback(self):
        """低 R² OLS → 回退先验"""
        r = analyze_fof_penetration(
            "005156", subtype="稳健型",
            ols_result={"equity_ratio": 0.90, "bond_ratio": 0.10, "r_squared": 0.05},
        )
        assert r.method == "prior"
        assert r.equity_ratio == 0.2

    def test_no_ols(self):
        """无 OLS 结果 → 先验"""
        r = analyze_fof_penetration("005156", subtype="稳健型")
        assert r.method == "prior"
        assert r.ols_r_squared is None

    def test_returns_penetration_result(self):
        """返回值类型"""
        r = analyze_fof_penetration("005156", subtype="稳健型")
        assert isinstance(r, PenetrationResult)
        assert r.fund_code == "005156"
        assert 0 <= r.equity_ratio <= 1
        assert 0 <= r.bond_ratio <= 1
        assert abs(r.equity_ratio + r.bond_ratio - 1) < 0.01

    def test_confidence_range(self):
        """置信度在 [0, 1] 范围内"""
        r1 = analyze_fof_penetration("005156", subtype="稳健型")
        r2 = analyze_fof_penetration(
            "005156", subtype="稳健型",
            ols_result={"equity_ratio": 0.3, "bond_ratio": 0.7, "r_squared": 0.85},
        )
        assert 0 <= r1.confidence <= 1
        assert 0 <= r2.confidence <= 1
