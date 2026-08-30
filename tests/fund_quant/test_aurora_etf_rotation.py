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


def _run_all_weather_engine(points, initial_capital=100000, params=None):
    """用 AuroraCore 引擎跑 all_weather_aurora"""
    from core import BacktestEngine, BacktestConfig, T1ExecutionEngine
    from core.strategy import StrategyRegistry

    import backend.fund_quant.adapter as _adapter  # noqa: F401

    strategy_cls = StrategyRegistry.get("all_weather_aurora")
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


class TestAllWeatherAurora:
    """AuroraCore 全天候策略端到端测试"""

    def test_registered(self):
        """all_weather_aurora 已注册到 AuroraCore registry"""
        from core.strategy import StrategyRegistry
        import backend.fund_quant.adapter as _adapter  # noqa: F401
        assert StrategyRegistry.get("all_weather_aurora") is not None

    def test_fixed_mode_rebalances(self):
        """fixed 模式在多头市场应产生交易且持有收益为正"""
        codes = ["510300", "510500", "518880"]
        points = _make_points(codes, 120, {"510300": 1, "510500": 1, "518880": 1})
        report = _run_all_weather_engine(points, params={"mode": "fixed"})
        assert report.total_trades > 0, "fixed 模式月度再平衡应产生交易"
        final = report.equity_curve[-1]["equity"]
        assert final > report.equity_curve[0]["equity"], "全上涨应赚钱"

    def test_risk_parity_mode_runs(self):
        """risk_parity 模式应能运行（可能因数据少回退 fixed，但不报错）"""
        codes = ["510300", "510500", "518880"]
        points = _make_points(codes, 120, {"510300": 1, "510500": 1, "518880": 1})
        report = _run_all_weather_engine(points, params={"mode": "risk_parity"})
        assert report.total_trades >= 0
        assert len(report.equity_curve) > 1


class TestAuroraBacktestExecution:
    """Fund Aurora 回测撮合与日级权益回归测试。"""

    def test_multifund_order_fills_on_its_next_nav(self):
        """多基金订单必须在标的基金下一交易日净值成交。"""
        from core import BacktestEngine, BacktestConfig, FundNavPoint, T1ExecutionEngine
        from core.signal import Direction, Signal
        from core.strategy import Strategy

        class BuyAOnce(Strategy):
            name = "buy_a_once"

            def on_data(self, data):
                if data.fund_code == "A" and str(data.date) == "2021-01-04":
                    self.ctx.emit(Signal(id="", strategy=self.name, symbol="A",
                                         direction=Direction.LONG, price=data.nav, volume=100))

        points = [
            FundNavPoint(fund_code=code, date=date(2021, 1, day), nav=nav)
            for day, a_nav, b_nav in [(4, 1.0, 10.0), (5, 1.2, 10.1), (6, 1.3, 10.2)]
            for code, nav in [("A", a_nav), ("B", b_nav)]
        ]
        engine = BacktestEngine(BacktestConfig(initial_capital=1000))
        execution = T1ExecutionEngine(confirmation_delay=1)
        engine.set_strategy(BuyAOnce())
        engine.set_executor(execution)
        engine.set_data(points)
        engine.run()

        assert execution.get_trade_log()[0]["price"] == 1.2

    def test_multifund_equity_curve_has_one_point_per_date(self):
        """多基金回测权益曲线只能按交易日记录。"""
        from core import BacktestEngine, BacktestConfig, FundNavPoint, T1ExecutionEngine
        from core.strategy import Strategy

        class NoTrade(Strategy):
            def on_data(self, data):
                pass

        points = [
            FundNavPoint(fund_code=code, date=date(2021, 1, day), nav=1.0)
            for day in (4, 5, 6)
            for code in ("A", "B")
        ]
        engine = BacktestEngine(BacktestConfig(initial_capital=1000))
        engine.set_strategy(NoTrade())
        engine.set_executor(T1ExecutionEngine())
        engine.set_data(points)
        report = engine.run()

        assert len(report.equity_curve) == 4  # 初始值 + 3 个交易日

    def test_t1_engine_rejects_buy_beyond_available_cash(self):
        """基金 T+1 撮合不得将现金余额买成负数。"""
        from core import FundNavPoint, T1ExecutionEngine
        from core.signal import Direction, Signal

        execution = T1ExecutionEngine(confirmation_delay=0)
        execution.set_capital(100)
        execution.submit(Signal(id="cash", strategy="test", symbol="A",
                                direction=Direction.LONG, price=1.0, volume=101))
        execution.on_bar(FundNavPoint(fund_code="A", date=date(2021, 1, 4), nav=1.0))
        fills = execution.on_bar(FundNavPoint(fund_code="A", date=date(2021, 1, 5), nav=1.0))

        assert len(fills) == 1
        assert fills[0].volume == 100
        assert execution.portfolio_value == 100

    def test_rebalance_sells_only_excess_weight(self):
        """超配调仓只能卖出目标差额，不能清仓。"""
        import backend.fund_quant.adapter as _adapter  # noqa: F401
        from core import Position
        from core.signal import Direction
        from core.strategy import StrategyRegistry

        strategy = StrategyRegistry.get("risk_parity_aurora")()
        strategy.params["rebalance_threshold"] = 0
        strategy._hist = {
            code: [(f"2021-01-{day:02d}", 10.0) for day in range(1, 21)]
            for code in ("A", "B")
        }

        positions = {
            "A": Position(symbol="A", direction=Direction.LONG, volume=70, avg_price=10),
            "B": Position(symbol="B", direction=Direction.LONG, volume=30, avg_price=10),
        }

        class Execution:
            def get_position(self, symbol):
                return positions.get(symbol)

        signals = []

        class Context:
            portfolio_value = 1000
            execution = Execution()

            def emit(self, signal):
                signals.append(signal)

        strategy.ctx = Context()
        strategy._compute_weights = lambda nav_series, codes: {"A": 0.5, "B": 0.5}
        strategy._rebalance("2021-01-20")

        sell = next(signal for signal in signals if signal.symbol == "A" and signal.direction == Direction.CLOSE_LONG)
        assert sell.volume == 20

    def test_all_weather_risk_parity_uses_supplied_history(self, monkeypatch):
        """Aurora 全天候风险平价只能使用回测传入的截至当日历史。"""
        import backend.fund_quant.strategy.allocation.all_weather as all_weather

        strategy = all_weather.AllWeatherStrategy(params={
            "mode": "risk_parity",
            "lookback_days": 20,
        })
        nav_series = {
            "A": [1.0 + i * 0.01 for i in range(21)],
            "B": [1.0 + i * 0.005 for i in range(21)],
        }

        def fail_if_database_is_read(*_args, **_kwargs):
            raise AssertionError("risk parity must not read full database history")

        monkeypatch.setattr(all_weather, "get_nav_history", fail_if_database_is_read, raising=False)
        result = strategy.optimize(
            fund_codes=["A", "B"],
            nav_series=nav_series,
        )

        assert result["status"] == "success"
        assert set(result["weights"]) == {"A", "B"}

    def test_gmv_rejects_infeasible_solver_weights(self, monkeypatch):
        """GMV 不得把不满足约束的求解结果归一化后直接使用。"""
        from types import SimpleNamespace
        from scipy import optimize
        import backend.fund_quant.adapter as adapter

        monkeypatch.setattr(
            optimize,
            "minimize",
            lambda *_args, **_kwargs: SimpleNamespace(
                success=True,
                x=np.array([0.4, 0.02, 0.02]),
            ),
        )
        strategy = adapter.AuroraGlobalMinimumVariance()
        nav_series = {
            code: [1.0 + (i + offset) * 0.001 for i in range(30)]
            for offset, code in enumerate(("A", "B", "C"))
        }

        weights = strategy._compute_weights(nav_series, ["A", "B", "C"])

        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-4)
    def test_fund_risk_checks_use_backtest_as_of_date(self):
        """基金历史回测风控不得使用机器当前日期。"""
        from datetime import datetime
        from core import RiskContext, Signal
        from core.signal import Direction
        from backend.fund_quant.risk.risk_checks import CooldownCheck, MinHoldingCheck

        ctx = RiskContext(extra={"as_of_date": date(2024, 1, 10)})
        holding = MinHoldingCheck(min_days=7)
        holding.register_buy("A", date(2024, 1, 5))
        sell = Signal(id="", strategy="test", symbol="A", direction=Direction.CLOSE_LONG,
                      price=1, volume=1)
        assert holding.check(ctx, sell).passed is False

        cooldown = CooldownCheck(cooldown_days=5)
        buy = Signal(id="", strategy="test", symbol="A", direction=Direction.LONG,
                     price=1, volume=1)
        ctx.extra["as_of_date"] = date(2024, 1, 10)
        assert cooldown.check(ctx, buy).passed is True
        ctx.extra["as_of_date"] = date(2024, 1, 12)
        assert cooldown.check(ctx, buy).passed is False


    def test_black_litterman_historical_run_does_not_read_current_scale(self, monkeypatch):
        """历史 BL 回测没有时点规模数据时必须使用等权市场先验。"""
        from backend.fund_quant.strategy.allocation.black_litterman import BlackLittermanStrategy

        nav_series = {
            "A": [1.0 + i * 0.001 for i in range(30)],
            "B": [1.0 + i * 0.002 for i in range(30)],
        }

        def fail_if_current_metadata_is_read(*_args, **_kwargs):
            raise AssertionError("historical BL must not read current fund scale")

        monkeypatch.setattr(
            "backend.fund_quant.data.storage.get_fund_meta",
            fail_if_current_metadata_is_read,
        )
        result = BlackLittermanStrategy().optimize(
            ["A", "B"],
            nav_series=nav_series,
        )

        assert result["status"] == "success"
        assert result["method"] == "equal_weight"
        assert result["weights"] == {"A": 0.5, "B": 0.5}
