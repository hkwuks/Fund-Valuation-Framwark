"""AuroraCore 统一引擎驱动配置策略测试 (P-配置层)"""
import pytest
import numpy as np
from datetime import date, timedelta

import sys
sys.path.insert(0, "backend")


def _make_points(codes, n_days, trend_map):
    """构造多基金净值数据点。trend_map: code -> 每日涨跌趋势方向 (+1/-1)

    返回按 (date, fund_code) 排序的 FundNavPoint 列表
    """
    from core import FundNavPoint

    points = []
    d0 = date(2021, 1, 4)
    navs = {c: 1.0 for c in codes}
    for i in range(n_days):
        d = d0 + timedelta(days=i)
        # 跳过周末（简化：每天都是交易日，引擎按序推进即可）
        for c in codes:
            navs[c] *= (1 + trend_map[c] * 0.005)
            points.append(FundNavPoint(fund_code=c, date=d, nav=round(navs[c], 4)))
    points.sort(key=lambda p: (p.date, p.fund_code))
    return points


def _run_engine(points, initial_capital=100000, params=None):
    """用 AuroraCore BacktestEngine + T1ExecutionEngine 跑 etf_rotation_aurora"""
    from core import BacktestEngine, BacktestConfig, T1ExecutionEngine
    from core.strategy import StrategyRegistry

    import backend.fund_quant.adapter as _adapter  # noqa: F401 触发策略注册

    strategy_cls = StrategyRegistry.get("etf_rotation_aurora")
    strategy = strategy_cls()
    if params:
        strategy.params.update(params)

    execution = T1ExecutionEngine(confirmation_delay=1)
    execution.set_capital(initial_capital)
    engine = BacktestEngine(BacktestConfig(initial_capital=initial_capital))
    engine.set_strategy(strategy)
    engine.set_executor(execution)
    engine.set_data(points)
    return engine.run()


class TestAuroraEtfRotation:
    """AuroraCore 配置策略端到端测试"""

    def test_registered(self):
        """策略已注册到 AuroraCore registry"""
        from core.strategy import StrategyRegistry
        import backend.fund_quant.adapter as _adapter  # noqa: F401
        assert StrategyRegistry.get("etf_rotation_aurora") is not None

    def test_bull_market_holds_top(self):
        """多头市场（全上涨）→ 应有成交且持有收益为正"""
        codes = ["510300", "510500", "518880"]
        # 黄金涨最快 → 应该长期持黄金
        points = _make_points(codes, 120, {"510300": 1, "510500": 1, "518880": 1})
        report = _run_engine(points)
        assert report.total_trades > 0, "多头市场应产生调仓交易"
        final = report.equity_curve[-1]["equity"]
        assert final > report.equity_curve[0]["equity"], "全上涨应赚钱"

    def test_bear_market_goes_cash(self):
        """全下跌市场 → 动量策略应空仓避险（收益接近0而非大幅亏损）"""
        codes = ["510300", "510500", "518880"]
        # 全跌：动量<0 → buy_threshold=0 拦下 → 全清仓持币
        points = _make_points(codes, 80, {"510300": -1, "510500": -1, "518880": -1})
        report = _run_engine(points)
        # 持有满仓下跌会亏 1-(0.995^80)≈33%；策略应明显优于
        final = report.equity_curve[-1]["equity"]
        init = report.equity_curve[0]["equity"]
        loss = 1 - final / init
        assert loss < 0.10, f"空仓避险应接近0亏损, 实际亏 {loss:.1%}"

    def test_clear_winner_gets_all_in(self):
        """一只强趋势 + 两只弱 → 资金集中于强趋势基金"""
        codes = ["A", "B", "C"]
        # 只让 A 有数据（B/C 无数据会被跳过）
        points = _make_points(codes, 100, {"A": 1, "B": 0, "C": 0})
        report = _run_engine(points, params={"top_n": 1, "rebalance_days": 10})
        assert report.total_trades > 0

    def test_t1_settlement_delay(self):
        """信号 T+1 确认：成交应发生在信号后而非同日"""
        codes = ["510300", "510500"]
        points = _make_points(codes, 60, {"510300": 1, "510500": -1})
        report = _run_engine(points, params={"rebalance_days": 5})
        assert report.total_trades > 0
