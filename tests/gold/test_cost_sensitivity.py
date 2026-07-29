"""测试成本敏感度分析"""
import numpy as np
import pandas as pd
from backend.gold.ml.cost_sensitivity import CostSensitivityAnalyzer


class TestCostSensitivityAnalyzer:
    def test_init(self):
        csa = CostSensitivityAnalyzer(base_commission=10.0, base_slippage=20.0)
        assert csa.base_commission == 10.0
        assert csa.base_slippage == 20.0

    def test_analyze_structure(self):
        csa = CostSensitivityAnalyzer()
        result = csa.analyze(None, multipliers=[0, 1, 2])  # 无数据，走默认策略
        assert "results" in result
        assert "breakeven" in result
        assert len(result["results"]) == 3

    def test_breakeven_analysis(self):
        csa = CostSensitivityAnalyzer()
        results = [
            {"cost_multiplier": 0, "total_return_pct": 20, "sharpe_ratio": 2.0},
            {"cost_multiplier": 1, "total_return_pct": 10, "sharpe_ratio": 1.0},
            {"cost_multiplier": 2, "total_return_pct": -5, "sharpe_ratio": -0.5},
        ]
        be = csa.breakeven_analysis(results)
        assert be["breakeven_multiplier"] is not None
        assert be["breakeven_multiplier"] > 1.0
        assert be["breakeven_multiplier"] < 2.0