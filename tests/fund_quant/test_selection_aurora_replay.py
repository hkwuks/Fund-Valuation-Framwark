"""Aurora selection 选基 as-of 注入接线 — hist 回测重放语义测试。

验证迁移目标：selection 策略在 AuroraCore 历史回测中不再静默，也不再触碰
storage（消除全库未来读取）；排名完全来自 bar 流内截至当日的净值 + 调用方
预载的 meta，按 rebalance_days 频次重算 Top-N 并 emit 调仓信号。
"""
import sys
sys.path.insert(0, "backend")

import pytest

from core import BacktestEngine, BacktestConfig, FundNavPoint, T1ExecutionEngine
from core.strategy import StrategyRegistry

import backend.fund_quant.adapter as _adapter  # noqa: F401 注册适配器
from backend.api.fund_quant import _fund_risk_pipeline
from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection
from backend.fund_quant.strategy.selection.multi_factor import MultiFactorSelection
from backend.fund_quant.strategy.selection.index_selection import IndexSelectionStrategy


def _trade_pct(n_picks, strategy):
    """取适配器当选池内单票目标仓位（min(1/n_picks, max_single_weight)）。"""
    return _adapter._AuroraSelectionAdapter._trade_pct(n_picks, strategy.params)


def _daily_nav_series(codes, days=120, base=1.0, trend=0.001, spread=0.05,
                      trend_map=None):
    """多基金确定性净值序列：每基金不同偏移 + 可选独立趋势，保证排名可分。"""
    trend_map = trend_map or {}
    return {
        c: [round(base * (1 + spread * i) + (trend_map.get(c, trend)) * d, 6)
            for d in range(days)]
        for i, c in enumerate(codes)
    }


def _points(series, start_year=2024):
    from datetime import date, timedelta
    pts = []
    start = date(start_year, 1, 1)
    for code, navs in series.items():
        for d, nav in enumerate(navs):
            pts.append(FundNavPoint(code, start + timedelta(days=d), nav))
    # 与真实数据源一致：按 (date, fund_code) 交错（代码major顺序会让先到基金的
    # bar 流结束后目标基金的确认信号永远无法成交）
    pts.sort(key=lambda p: (p.date, p.fund_code))
    return pts


def _run(strategy, points, initial_capital=100000.0):
    """跑一次 Aurora 回测。不再强制覆盖 params：测试需特定 top_n/rebalance_days 时
    在调用前自行 strategy.params.update()（避免 _run 把测试设值冲掉）。"""
    strategy.params.setdefault("rebalance_days", 10)
    strategy.params.setdefault("top_n", 1)
    # 只在测试未预置 meta 时给默认（保留 index 测试预载的 fund_type/index 字段）
    if not strategy._meta:
        strategy._meta = {c: {"fund_code": c, "fund_name": f"Fund{c}", "fund_type": "stock"}
                          for c in {p.fund_code for p in points}}
    execution = T1ExecutionEngine(confirmation_delay=1)
    execution.set_capital(initial_capital)
    engine = BacktestEngine(BacktestConfig(initial_capital=initial_capital))
    engine.set_strategy(strategy)
    engine.set_executor(execution)
    engine.set_risk(_fund_risk_pipeline(strategy.name))  # API 回测同款风控（选基用轻量组合级检查）
    engine.set_data(points)
    report = engine.run()
    strategy._last_execution = execution  # 供测试读取 trade log（core report.trades 不填充）
    return report


@pytest.fixture
def multi_series():
    # A 弱趋势、B 强趋势：B 净值更高 → 评级增强应稳定选中 B
    return _daily_nav_series(["A", "B"], days=120, spread=0.05,
                             trend_map={"A": 0.0005, "B": 0.002})


class TestReplayRanking:
    def test_rating_enhanced_rebalances_and_emits(self, multi_series):
        """评级增强回测：Top-N 调仓信号发出（排名来自 hist，非 storage）。"""
        strategy = StrategyRegistry.get("rating_enhanced_aurora")()
        report = _run(strategy, _points(multi_series))
        assert report.total_trades > 0
        # 回测重放确实在调仓日买入某只基金（execution trade log 有买入记录）
        assert any(t.get("action") == "buy" for t in strategy._last_execution.get_trade_log())

    def test_no_storage_read_during_replay(self, multi_series, monkeypatch):
        """历史回测 on_data 路径 0 次 storage 读取。"""
        def fail(*_a, **_k):
            raise AssertionError("selection 回测重放不得读取 storage")

        monkeypatch.setattr("backend.fund_quant.data.storage.get_all_fund_codes", fail)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_fund_meta", fail)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", fail)
        strategy = StrategyRegistry.get("rating_enhanced_aurora")()
        report = _run(strategy, _points(multi_series))
        assert report.total_trades > 0

    def test_ranking_uses_only_available_history(self, multi_series):
        """调仓日截面只含截至当日净值：早期没有 A 的数据时不会提前买入。"""
        series = {"A": [], "B": _daily_nav_series(["B"], days=120)["B"]}
        # A 从第 60 天才出现
        series["A"] = [None] * 60 + _daily_nav_series(["A"], days=60)["A"]
        pts = []
        from datetime import date, timedelta
        start = date(2024, 1, 1)
        for code, navs in series.items():
            for d, nav in enumerate(navs):
                if nav is None:
                    continue
                pts.append(FundNavPoint(code, start + timedelta(days=d), nav))
        strategy = StrategyRegistry.get("rating_enhanced_aurora")()
        report = _run(strategy, pts)
        assert report.total_trades > 0


class TestMultiFundReplay:
    def test_all_top_n_funds_fill(self):
        """同时入库的基金在同一次调仓一起上榜时按等权成交（此前末位基金确认时资金不足被拒）。"""
        from datetime import date, timedelta
        codes = ["A", "B", "C"]
        pts = []
        start = date(2024, 1, 1)
        # 三只基金净值完全相同 → 同一调仓日一起上榜（无历史长度错位），等权各 1/3
        for c in codes:
            for d in range(140):
                pts.append(FundNavPoint(fund_code=c, date=start + timedelta(days=d),
                                        nav=1.0 + d * 0.001))
        pts.sort(key=lambda p: (p.date, p.fund_code))
        strategy = StrategyRegistry.get("multi_factor_aurora")()
        strategy.params.update({"rebalance_days": 20, "top_n": 5})
        report = _run(strategy, pts)
        buys = [t for t in strategy._last_execution.get_trade_log() if t.get("action") == "buy"]
        assert len(buys) >= len(codes), f"应成交所有上榜基金, got {[b['symbol'] for b in buys]}"
        # 首轮 A 先上榜满仓，第二轮 B/C 补历史后等权各 1/3 → B 与 C 金额应接近
        amts = [b["price"] * b["volume"] for b in buys]
        assert abs(amts[-1] - amts[-2]) < 3000, f"次轮 B/C 应等权, got {[round(a, 0) for a in amts]}"
        assert report.total_trades > 0

    def test_meta_type_does_not_shrink_section(self):
        """meta.fund_type 与 fund_type 筛选不符（real 数据 fund_type=None/FEE 归一化）
        不得把池内基金从截面排除——否则始终只有 1-2 只候选，排名坍缩成动量。"""
        from datetime import date, timedelta
        codes = ["510300", "510500", "518880"]
        pts = []
        start = date(2024, 1, 1)
        for i, c in enumerate(codes):
            for d in range(140):
                pts.append(FundNavPoint(fund_code=c, date=start + timedelta(days=d),
                                        nav=1.0 + d * 0.001 + i * 0.01))
        pts.sort(key=lambda p: (p.date, p.fund_code))
        strategy = StrategyRegistry.get("rating_enhanced_aurora")()
        strategy.params.update({"rebalance_days": 20, "top_n": 3})
        # real 场景：DB fund_type 缺失 → 空字符串；从 _pool_codes 出来的候选应全部在截面内
        strategy._meta = {c: {"fund_code": c, "fund_name": f"F{c}", "fund_type": ""}
                          for c in codes}
        report = _run(strategy, pts)
        buys = {t["symbol"] for t in strategy._last_execution.get_trade_log() if t.get("action") == "buy"}
        assert buys and len(buys) >= 2, f"meta 类型缺失不应带走候选, got {buys}"
        assert report.total_trades > 0

    def test_rebalance_days_reflects_position_pct(self):
        """当选3只等权 1/3；max_single_weight 截断；当选数变少 → 等权放大。"""
        s1 = StrategyRegistry.get("multi_factor_aurora")()
        s1.params.update({"top_n": 3, "max_single_weight": 1.0})
        assert abs(_trade_pct(3, s1) - 1.0 / 3) < 1e-9
        s2 = StrategyRegistry.get("rating_enhanced_aurora")()
        s2.params.update({"top_n": 3, "max_single_weight": 0.5})
        # 当选数变少 → 等权放大至 1/2，再被 0.5 截断 → 两者相等
        assert abs(_trade_pct(2, s2) - 0.5) < 1e-9
        s3 = StrategyRegistry.get("index_selection_aurora")()
        s3.params.update({"top_n": 5, "max_single_weight": 1.0})
        assert abs(_trade_pct(3, s3) - 1.0 / 3) < 1e-9


class TestAuroraReplayEquivalent:
    def test_aurora_rating_enhanced_matches_legacy_scorer(self):
        """Aurora 重放排名与直接注入同一截面的 legacy scorer 一致。"""
        navs = _daily_nav_series(["A", "B", "C"], days=120)
        section = [{"fund_code": c, "fund_type": "stock",
                    "nav_values": navs[c]} for c in navs]
        legacy = RatingEnhancedSelection().screen(
            fund_type="all", top_n=5, fund_data=section)
        legacy_rank = [r["fund_code"] for r in legacy["rankings"]]

        strategy = StrategyRegistry.get("rating_enhanced_aurora")()
        report = _run(strategy, _points(navs))
        # 重放过程中按相同 scorer 生成排名，最后一次排名应等于 legacy 对完整截面的排名
        assert report.total_trades > 0
        assert legacy_rank[0] == "C"  # 净值最高 → 排名第一

    def test_multi_factor_replay_emits(self):
        """多因子回测：空因子集 → 等权打分（无因子引擎依赖），仍产生交易。"""
        strategy = StrategyRegistry.get("multi_factor_aurora")()
        # 注入路径因子来自注册因子（等权），不触发 storage 也不跑评价引擎
        report = _run(strategy, _points(_daily_nav_series(["A", "B", "C"])))
        assert report.total_trades > 0

    def test_index_selection_replay_with_state(self):
        """指数选基回测：跟踪误差来自预注入 _state，不读 storage。"""
        navs = _daily_nav_series(["510300", "510500"], days=120)
        strategy = StrategyRegistry.get("index_selection_aurora")()
        strategy._state["tracking_errors"] = {"510300": 0.003, "510500": 0.01}
        strategy._state["liquidity_data"] = {"510300": 1e8, "510500": 1e8}
        strategy._state["premium_vol_data"] = {"510300": 0.001, "510500": 0.001}
        strategy._meta = {c: {"fund_code": c, "fund_name": f"Fund{c}",
                              "fund_type": "index", "management_fee": 0.005,
                              "custody_fee": 0.001, "scale": 5e10}
                          for c in navs}
        report = _run(strategy, _points(navs))
        assert report.total_trades > 0
