"""成本敏感度脚本回归测试。"""

from backend.scripts.cost_sensitivity_report import SCENARIOS, _make_points, run_strategy


CODES = ["510300", "510500", "518880"]


def _run(name):
    points = _make_points(CODES, 60, {code: 1 for code in CODES})
    return {scenario: run_strategy(name, points, factory()) for scenario, factory in SCENARIOS}


def test_cost_monotonic_increasing():
    for name in ["etf_rotation_aurora", "all_weather_aurora"]:
        result = _run(name)
        costs = [result[scenario]["total_cost"] for scenario, _ in SCENARIOS]
        assert costs[0] <= costs[1] <= costs[2]


def test_equity_monotonic_decreasing():
    for name in ["etf_rotation_aurora", "all_weather_aurora"]:
        result = _run(name)
        equities = [result[scenario]["final_equity"] for scenario, _ in SCENARIOS]
        assert equities[0] >= equities[1] >= equities[2]


def test_zero_cost_no_fees():
    assert _run("etf_rotation_aurora")["zero"]["total_cost"] == 0.0
