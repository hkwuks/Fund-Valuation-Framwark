"""参数敏感性扫描测试 — ParameterScanner"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict

import pytest

from backend.fund_quant.backtest.param_scanner import ParameterScanner, ScanResult


def _linear_func(params: Dict[str, Any]) -> Dict[str, float]:
    """Simple mock: sharpe = threshold / 10 - cost"""
    threshold = params.get("threshold", 0.5)
    cost = params.get("cost", 0.0)
    sharpe = threshold / 10 - cost
    return {"sharpe": sharpe, "return_pct": sharpe * 10}


class TestParameterScanner:
    def test_single_param_varying(self):
        """one param, 5 values -> 5 results, sensitivity_score computed"""
        scanner = ParameterScanner(_linear_func)
        result = scanner.single_param(
            "threshold", [0.1, 0.3, 0.5, 0.7, 0.9], fixed_params={"cost": 0.02}
        )
        assert result.mode == "single_param"
        assert len(result.results) == 5
        assert result.sensitivity_score is not None
        assert "sharpe" in result.sensitivity_score
        # sharpe values: 0.1/10-0.02=-0.01, 0.3/10-0.02=0.01, 0.5/10-0.02=0.03,
        #                0.7/10-0.02=0.05, 0.9/10-0.02=0.07
        # max-min = 0.07 - (-0.01) = 0.08
        assert result.sensitivity_score["sharpe"] == 0.08
        assert result.sensitivity_score["return_pct"] == 0.8

    def test_single_param_sorted(self):
        """param values out of order -> results sorted"""
        scanner = ParameterScanner(_linear_func)
        result = scanner.single_param(
            "threshold", [0.9, 0.1, 0.5], fixed_params={"cost": 0.01}
        )
        values = [r["threshold"] for r in result.results]
        assert values == [0.1, 0.5, 0.9]

    def test_grid_search_product(self):
        """2 params x 3 values each -> 6 results"""
        scanner = ParameterScanner(_linear_func)
        result = scanner.grid_search(
            param_grid={"threshold": [0.1, 0.5, 0.9], "cost": [0.01, 0.05]},
        )
        assert result.mode == "grid_search"
        assert len(result.results) == 6
        assert result.param_names == ["threshold", "cost"]

    def test_grid_search_stability(self):
        """2 params, stability_region computed for sharpe > 0.5"""
        scanner = ParameterScanner(_linear_func)
        result = scanner.grid_search(
            param_grid={"threshold": [5.0, 6.0, 7.0], "cost": [0.0, 0.05]},
        )
        assert result.stability_region is not None
        # 4 pairs where sharpe > 0.5:
        # (6.0, 0.0)=0.6, (6.0, 0.05)=0.55, (7.0, 0.0)=0.7, (7.0, 0.05)=0.65
        assert len(result.stability_region) == 4
        for p1, p2 in result.stability_region:
            sharpe = p1 / 10 - p2
            assert sharpe > 0.5

    def test_random_search_n_iter(self):
        """n_iter=50 -> 50 results, params within distribution ranges"""
        scanner = ParameterScanner(_linear_func)
        result = scanner.random_search(
            param_dist={"threshold": [1.0, 2.0, 3.0], "cost": (0.0, 0.1)},
            n_iter=50,
        )
        assert result.mode == "random_search"
        assert len(result.results) == 50
        for r in result.results:
            assert r["threshold"] in [1.0, 2.0, 3.0]
            assert 0.0 <= r["cost"] <= 0.1

    def test_random_search_reproducible(self):
        """same seed -> same results"""
        scanner = ParameterScanner(_linear_func)
        r1 = scanner.random_search(
            param_dist={"threshold": [1.0, 2.0], "cost": (0.0, 0.1)},
            n_iter=10,
            seed=42,
        )
        r2 = scanner.random_search(
            param_dist={"threshold": [1.0, 2.0], "cost": (0.0, 0.1)},
            n_iter=10,
            seed=42,
        )
        assert r1.results == r2.results

    def test_to_csv_creates_file(self):
        """export to temp CSV, file exists and has headers"""
        scanner = ParameterScanner(_linear_func)
        result = scanner.single_param(
            "threshold", [0.1, 0.5, 0.9], fixed_params={"cost": 0.02}
        )
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            tmp_path = f.name
        try:
            scanner.to_csv(result, tmp_path)
            assert os.path.exists(tmp_path)
            with open(tmp_path, encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 4  # header + 3 data rows
            assert "threshold" in lines[0]
            assert "sharpe" in lines[0]
        finally:
            os.unlink(tmp_path)

    def test_empty_values(self):
        """empty values list -> empty ScanResult"""
        scanner = ParameterScanner(_linear_func)
        result = scanner.single_param("threshold", [])
        assert result.n_iterations == 0
        assert len(result.results) == 0

    def test_empty_metrics(self):
        """func returns {} -> metric values are NaN"""
        def _empty_func(params: Dict[str, Any]) -> Dict[str, float]:
            return {}

        scanner = ParameterScanner(_empty_func)
        result = scanner.single_param("threshold", [0.1, 0.5])
        assert len(result.results) == 2
        for r in result.results:
            # only param key in the row, no metric keys
            assert list(r.keys()) == ["threshold"]

    def test_invalid_n_iter(self):
        """n_iter=-1 -> ValueError"""
        scanner = ParameterScanner(_linear_func)
        with pytest.raises(ValueError, match="n_iter must be > 0"):
            scanner.random_search(
                param_dist={"threshold": [1.0, 2.0]},
                n_iter=-1,
            )
