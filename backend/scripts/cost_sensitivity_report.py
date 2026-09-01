"""Aurora 策略成本敏感度报告 — zero/current/high 三档成本对比。

用法:
    PYTHONPATH=backend:. python -m backend.scripts.cost_sensitivity_report
    PYTHONPATH=backend:. python -m backend.scripts.cost_sensitivity_report etf_rotation_aurora all_weather_aurora
"""

from __future__ import annotations

import sys
from datetime import date, timedelta

from core import BacktestEngine, BacktestConfig, FundNavPoint, T1ExecutionEngine
from core.backtest import NoCost
from core.strategy import StrategyRegistry

import backend.fund_quant.adapter as _adapter  # noqa: F401  # trigger strategy registration
from backend.fund_quant.adapter import FundCostModelAdapter
from backend.fund_quant.backtest.cost_model import FundCostModel
from backend.fund_quant.core.models import CostModelConfig

POOL = ["510300", "510500", "518880"]


def _make_points(codes: list[str], n_days: int, trend_map: dict[str, float]) -> list[FundNavPoint]:
    """构造确定性净值序列，供不依赖网络/数据库的成本敏感度比较。"""
    points = []
    start = date(2021, 1, 4)
    navs = {code: 1.0 for code in codes}
    for offset in range(n_days):
        for code in codes:
            navs[code] *= 1 + trend_map[code] * 0.005
            points.append(FundNavPoint(
                fund_code=code,
                date=start + timedelta(days=offset),
                nav=round(navs[code], 4),
            ))
    return points


def run_strategy(name: str, points: list[FundNavPoint], cost_model, initial_capital: float = 100000) -> dict:
    """通过现有 AuroraCore 引擎运行一个策略和一档成本。"""
    strategy = StrategyRegistry.get(name)()
    execution = T1ExecutionEngine(confirmation_delay=1)
    execution.set_capital(initial_capital)
    engine = BacktestEngine(BacktestConfig(initial_capital=initial_capital))
    engine.set_strategy(strategy)
    engine.set_executor(execution)
    engine.set_data(points)
    engine.set_cost_model(cost_model)
    report = engine.run()
    total_cost = sum((fill.commission or 0) for fill in execution._all_fills)
    return {
        "final_equity": report.final_equity,
        "total_return_pct": report.total_return * 100,
        "total_trades": report.total_trades,
        "total_cost": total_cost,
    }


def high_cost_model() -> FundCostModelAdapter:
    """高成本档：申购费翻倍，赎回费固定为 1%。"""
    config = CostModelConfig(
        subscription_fee_tiers={
            "stock": 0.03, "hybrid": 0.03, "bond": 0.016, "index": 0.02,
            "qdii": 0.03, "money": 0.0, "fof": 0.024,
        },
        holding_period_discount={7: 100.0, 30: 100.0, 9999: 100.0},
    )
    return FundCostModelAdapter(FundCostModel(config))


SCENARIOS = [
    ("zero", NoCost),
    ("current", FundCostModelAdapter),
    ("high", high_cost_model),
]


def main(strategy_names: list[str] | None = None) -> None:
    names = strategy_names or ["etf_rotation_aurora", "all_weather_aurora"]
    points = _make_points(POOL, 120, {code: 1 for code in POOL})
    header = f"{'strategy':<24}" + "".join(
        f"{f'{scenario}_equity':>14}{f'{scenario}_ret%':>10}{f'{scenario}_trades':>10}{f'{scenario}_cost':>12}"
        for scenario, _ in SCENARIOS
    )
    print(header)
    print("-" * len(header))
    for name in names:
        results = {scenario: run_strategy(name, points, factory()) for scenario, factory in SCENARIOS}
        row = f"{name:<24}"
        for scenario, _ in SCENARIOS:
            result = results[scenario]
            row += (f"{result['final_equity']:>14.2f}{result['total_return_pct']:>10.2f}"
                    f"{result['total_trades']:>10d}{result['total_cost']:>12.2f}")
        print(row)


if __name__ == "__main__":
    main(sys.argv[1:] or None)
