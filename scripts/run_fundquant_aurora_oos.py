#!/usr/bin/env python3
"""Regenerate the persisted Aurora Walk-Forward OOS report from local NAV data."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from backend.api import fund_quant
from backend.fund_quant.backtest.significance import SignificanceTester
from backend.fund_quant.backtest.validation import walk_forward_validator


DEFAULT_STRATEGIES = [
    "etf_rotation_aurora",
    "all_weather_aurora",
    "bl_quadrant_aurora",
    "black_litterman_aurora",
    "risk_parity_aurora",
    "dynamic_risk_parity_aurora",
    "vol_targeting_aurora",
    "trend_following_aurora",
    "gmv_aurora",
    "hrp_aurora",
    "max_diversification_aurora",
    # 选基 as-of 注入后可在 Aurora 回测中重放：真实截面下每基金历史 NAV + 预载 meta
    "multi_factor_aurora",
    "index_selection_aurora",
    "rating_enhanced_aurora",
]
DEFAULT_FUND_CODES = ["510300", "510500", "518880"]
DEFAULT_VALIDATION = {
    "train_window_days": 252,
    "test_window_days": 126,
    "step_size_days": 126,
    "min_train_trades": 1,
}


def _result_for_window(strategy_name: str, fund_codes: list[str], initial_capital: float) -> Callable[[dict], dict]:
    def run_window(config: dict) -> dict:
        backtest_id = fund_quant._run_backtest_sync({
            **config,
            "strategy_name": strategy_name,
            "fund_codes": fund_codes,
            "initial_capital": initial_capital,
            "params": config.get("params", {}),
        })
        stored = fund_quant.get_backtest_result(backtest_id) or {}
        result_json = stored.get("result_json")
        if result_json:
            try:
                stored = {**stored, **json.loads(result_json)}
            except (TypeError, json.JSONDecodeError):
                pass
        return {
            "total_return": stored.get("total_return", 0),
            "sharpe_ratio": stored.get("sharpe_ratio", 0),
            "max_drawdown": stored.get("max_drawdown", 0),
            "total_trades": stored.get("total_trades", 0),
            "benchmark_return": stored.get("benchmark_return"),
        }

    return run_window


def build_report(
    strategies: list[str],
    fund_codes: list[str],
    start_date: str,
    end_date: str,
    validation: dict,
    validate: Callable = walk_forward_validator.validate,
    run_window: Callable | None = None,
    n_bootstrap: int = 500,
    initial_capital: float = 100000.0,
    benchmark: str | None = None,
) -> dict:
    """Run each strategy and serialize strategy, benchmark, and excess-return OOS data."""
    results = []
    for strategy_name in strategies:
        window_runner = run_window or _result_for_window(strategy_name, fund_codes, initial_capital)
        result = validate(window_runner, fund_codes, start_date, end_date, validation, benchmark)
        windows = result.get("windows", [])
        excess_returns = [
            window["excess_return"]
            for window in windows
            if isinstance(window.get("excess_return"), (int, float))
        ]
        significance = None
        if len(excess_returns) >= 2:
            significance = asdict(SignificanceTester().test(
                excess_returns, n_bootstrap=n_bootstrap, seed=42,
            ))
        results.append({
            "strategy": strategy_name,
            "oos": result.get("summary", {}),
            "windows": windows,
            "significance_excess": significance,
            "n_excess_windows": len(excess_returns),
        })

    return {
        "generated_at": datetime.now().isoformat(),
        "period": [start_date, end_date],
        "fund_codes": fund_codes,
        "validation": validation,
        "benchmark": benchmark or "fund_pool_equal_weight",
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reports/fundquant_aurora_oos.json")
    parser.add_argument("--start-date", default="2022-01-01")
    parser.add_argument("--end-date", default="2026-08-28")
    parser.add_argument("--bootstrap", type=int, default=500)
    parser.add_argument("--benchmark", choices=["csi300", "cbi"], default=None)
    args = parser.parse_args()

    report = build_report(
        strategies=DEFAULT_STRATEGIES,
        fund_codes=DEFAULT_FUND_CODES,
        start_date=args.start_date,
        end_date=args.end_date,
        validation=DEFAULT_VALIDATION,
        n_bootstrap=args.bootstrap,
        benchmark=args.benchmark,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"saved {output}")


if __name__ == "__main__":
    main()
