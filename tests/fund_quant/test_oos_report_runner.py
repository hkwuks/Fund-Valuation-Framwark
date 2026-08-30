"""Regression tests for the reproducible Aurora OOS report runner."""

import importlib.util
from pathlib import Path


def _load_runner():
    path = Path("scripts/run_fundquant_aurora_oos.py")
    spec = importlib.util.spec_from_file_location("run_fundquant_aurora_oos", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_report_keeps_benchmark_and_excess_significance():
    runner = _load_runner()

    def validate(_run_window, _codes, _start, _end, _validation):
        return {
            "summary": {"avg_excess_return": 0.015},
            "windows": [
                {"benchmark_return": 0.01, "excess_return": 0.01},
                {"benchmark_return": 0.02, "excess_return": 0.02},
            ],
        }

    report = runner.build_report(
        strategies=["test_aurora"],
        fund_codes=["A"],
        start_date="2024-01-01",
        end_date="2024-12-31",
        validation={"test_window_days": 1},
        validate=validate,
        run_window=lambda _config: {},
        n_bootstrap=20,
    )

    result = report["results"][0]
    assert result["oos"]["avg_excess_return"] == 0.015
    assert result["windows"][0]["benchmark_return"] == 0.01
    assert result["significance_excess"]["n_bootstrap"] == 20
    assert result["n_excess_windows"] == 2
