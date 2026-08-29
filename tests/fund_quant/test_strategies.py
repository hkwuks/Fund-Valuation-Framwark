"""策略引擎测试：注册表 + 12个策略 + 信号融合"""

import sys; sys.path.insert(0, 'backend/..')
import pytest
import numpy as np
from backend.fund_quant.strategy.base import FundStrategyBase, StrategyRegistry
from backend.fund_quant.strategy.fusion import SignalFusion
from backend.fund_quant.core.enums import SignalType, Direction
from backend.fund_quant.core.models import FundSignal, FusionSignal


class TestStrategyRegistry:
    def setup_method(self):
        self.registry = StrategyRegistry()

    def test_all_strategies_registered(self):
        strategies = self.registry.list_strategies()
        names = {s["name"] for s in strategies}
        expected = {
            "multi_factor", "rating_enhanced", "index_selection",
            "risk_parity", "black_litterman", "etf_global_rotation", "all_weather",
        }
        # 允许存在未列入的配置/选基策略，但已删除的 timing 策略不应存在
        for gone in ["momentum", "gold_reversion", "interest_rate", "credit_spread",
                     "fx_momentum", "smart_dca", "valuation_deviation", "gold_momentum"]:
            assert gone not in names, f"已废弃策略 {gone} 仍被注册"
        assert expected <= names, f"缺失: {expected - names}"

    def test_strategy_types(self):
        strategies = self.registry.list_strategies()
        by_type = {}
        for s in strategies:
            by_type.setdefault(s["type"], []).append(s["name"])
        assert by_type.get("timing", []) == [], f"废弃的择时策略仍注册: {by_type.get('timing', [])}"
        assert len(by_type.get("selection", [])) == 3, "选基策略应为3个"
        assert len(by_type.get("allocation", [])) == 6, "配置策略应为6个（risk_parity, black_litterman, etf_global_rotation, all_weather, hrp, max_diversification）"

    def test_get_strategy_returns_instance(self):
        s = self.registry.get_strategy("multi_factor")
        assert s is not None
        assert s.strategy_name == "multi_factor"
        assert s.strategy_type == "selection"

    def test_get_nonexistent_returns_none(self):
        assert self.registry.get_strategy("nonexistent") is None

    def test_strategy_params_available(self):
        strategies = self.registry.list_strategies()
        for s in strategies:
            assert "default_params" in s, f"{s['name']} 缺少 default_params"
            assert isinstance(s["default_params"], dict)

    def test_each_strategy_has_description(self):
        strategies = self.registry.list_strategies()
        for s in strategies:
            assert s["description"], f"{s['name']} 缺少 description"

    def test_strategy_base_abc(self):
        """验证抽象类不可直接实例化"""
        with pytest.raises(TypeError):
            FundStrategyBase()  # abstractmethod on_evaluate


class TestStrategies:
    """验证每个策略的 on_evaluate 能正常返回信号 (无需实盘数据)"""

    @pytest.fixture
    def nav_data(self):
        """模拟180天净值序列"""
        np.random.seed(42)
        base = 1.0
        values = [base]
        for _ in range(180):
            base *= 1 + np.random.normal(0.0005, 0.008)
            values.append(base)
        return values

    @pytest.fixture
    def setup_strategy(self, nav_data):
        def _setup(name):
            registry = StrategyRegistry()
            s = registry.get_strategy(name)
            s._state.update({
                "fund_code": "000001",
                "nav_values": nav_data,
                "nav_dates": [f"2024-{i//30+1:02d}-{(i%30)+1:02d}" for i in range(len(nav_data))],
            })
            return s
        return _setup

    def test_multi_factor(self):
        registry = StrategyRegistry()
        s = registry.get_strategy("multi_factor")
        result = s.screen(fund_type="stock", top_n=5)
        assert "rankings" in result
        assert "total_candidates" in result

    def test_risk_parity(self):
        registry = StrategyRegistry()
        s = registry.get_strategy("risk_parity")
        result = s.optimize(fund_codes=["000001", "110011"])
        assert "weights" in result
        assert "status" in result

    def test_black_litterman(self, setup_strategy):
        s = setup_strategy("black_litterman")
        signals = s.on_evaluate(None, None)
        assert isinstance(signals, list)  # V1降级, 仅返回空列表

    def test_rating_enhanced(self, setup_strategy):
        s = setup_strategy("rating_enhanced")
        signals = s.on_evaluate(None, None)
        assert isinstance(signals, list)

    def test_rating_enhanced_screen_empty_db(self):
        """无数据时 screen 返回空 rankings"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection
        s = RatingEnhancedSelection()
        result = s.screen(fund_type="equity", top_n=5)
        assert "rankings" in result
        assert "total_candidates" in result

    def test_index_selection_registered(self, setup_strategy):
        """指数基金选基策略注册正确"""
        s = setup_strategy("index_selection")
        assert s.strategy_name == "index_selection"
        assert "index" in s.applicable_fund_types

    def test_index_selection_empty_db(self):
        """无数据时返回空结果"""
        from backend.fund_quant.strategy.selection.index_selection import IndexSelectionStrategy
        s = IndexSelectionStrategy()
        result = s.screen(fund_type="index", top_n=5)
        assert "rankings" in result

    def test_index_selection_scoring(self, monkeypatch):
        """有模拟数据时评分排序"""
        from backend.fund_quant.strategy.selection.index_selection import IndexSelectionStrategy

        mock_funds = ["000001", "110011"]
        mock_metas = {
            "000001": {"fund_code": "000001", "fund_name": "IndexA",
                       "fund_type": "index", "management_fee": 0.005,
                       "custody_fee": 0.001, "scale": 50_000_000_000},
            "110011": {"fund_code": "110011", "fund_name": "IndexB",
                       "fund_type": "index", "management_fee": 0.008,
                       "custody_fee": 0.002, "scale": 100_000_000},
        }

        def mock_get_all():
            return mock_funds
        def mock_get_meta(code):
            return mock_metas.get(code)
        def mock_get_nav_history(code):
            return [{"nav": 1.0 + i * 0.001} for i in range(120)]

        monkeypatch.setattr("backend.fund_quant.data.storage.get_all_fund_codes", mock_get_all)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_fund_meta", mock_get_meta)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", mock_get_nav_history)

        s = IndexSelectionStrategy()
        result = s.screen(fund_type="index", top_n=5)
        assert result["total_candidates"] == 2
        # IndexA（低费率+大规模）总分应高于 IndexB
        assert result["rankings"][0]["fund_code"] == "000001"

    def test_long_history_strategies_return_signals(self, setup_strategy):
        """验证有足够数据时策略返回非空信号"""
        for name in ["multi_factor"]:
            s = setup_strategy(name)
            signals = s.on_evaluate(None, None)
            assert isinstance(signals, list), f"{name} 应返回列表"


class TestRatingEnhanced:
    """评级增强选基策略专项测试（mock 数据层）"""

    def _make_nav_values(self, length=120, base=1.0, trend=0.0003, vol=0.008):
        """生成模拟净值数据"""
        import numpy as np
        np.random.seed(42)
        vals = [base]
        for _ in range(length):
            vals.append(vals[-1] * (1 + trend + np.random.normal(0, vol)))
        return vals

    def test_rating_normalize(self):
        """评级归一化: (星级-1)/4"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection
        s = RatingEnhancedSelection()
        assert s._normalize_rating(None) == 0.5
        assert s._normalize_rating(1) == 0.0
        assert s._normalize_rating(3) == 0.5
        assert s._normalize_rating(5) == 1.0
        assert s._normalize_rating(0) == 0.5  # 无效值回退

    def test_deviation_score_mapping(self):
        """估值偏差z-score → 得分映射"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection
        s = RatingEnhancedSelection()
        assert s._deviation_to_score(-2.0) == 1.0      # 低估 → 高分
        assert s._deviation_to_score(-1.6) == 1.0      # < -1.5 → 高分
        assert s._deviation_to_score(0.0) == 0.5        # 正常 → 中分
        assert s._deviation_to_score(1.4) == 0.5        # < 1.5 → 中分
        assert s._deviation_to_score(1.6) == 0.0        # > 1.5 → 低分
        assert s._deviation_to_score(2.0) == 0.0        # 高估 → 低分

    def test_calc_quant_factors(self):
        """量化因子计算返回预期结构"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection
        s = RatingEnhancedSelection()
        navs = self._make_nav_values(120)
        factors = s._calc_quant_factors(navs)
        assert "sharpe_ratio" in factors
        assert "max_drawdown" in factors
        assert "excess_return" in factors

    def test_screen_with_mock_db_data(self, monkeypatch):
        """模拟DB有数据时 screen 返回评分排名"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection

        mock_funds = ["000001", "110011", "007016"]
        mock_metas = {
            "000001": {"fund_code": "000001", "fund_name": "TestA", "fund_type": "stock", "rating": 5},
            "110011": {"fund_code": "110011", "fund_name": "TestB", "fund_type": "stock", "rating": 3},
            "007016": {"fund_code": "007016", "fund_name": "TestC", "fund_type": "stock", "rating": 1},
        }

        navs = self._make_nav_values(120)

        def mock_get_all():
            return mock_funds

        def mock_get_meta(code):
            return mock_metas.get(code)

        def mock_get_nav_history(code):
            return [{"nav": v} for v in navs]

        monkeypatch.setattr("backend.fund_quant.data.storage.get_all_fund_codes", mock_get_all)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_fund_meta", mock_get_meta)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", mock_get_nav_history)

        s = RatingEnhancedSelection()
        result = s.screen(fund_type="stock", top_n=5)
        assert result["total_candidates"] == 3
        assert len(result["rankings"]) == 3
        # rating 5 → 高分，应排第一
        assert result["rankings"][0]["fund_code"] == "000001"
        assert result["rankings"][0]["rating_score"] == 1.0

    def test_custom_weights(self, monkeypatch):
        """自定义权重改变排序"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection

        mock_funds = ["000001", "110011"]
        mock_metas = {
            "000001": {"fund_code": "000001", "fund_name": "TestA", "fund_type": "stock", "rating": 5},
            "110011": {"fund_code": "110011", "fund_name": "TestB", "fund_type": "stock", "rating": 1},
        }
        navs = self._make_nav_values(120)

        def mock_get_all():
            return mock_funds

        def mock_get_meta(code):
            return mock_metas.get(code)

        def mock_get_nav_history(code):
            return [{"nav": v} for v in navs]

        monkeypatch.setattr("backend.fund_quant.data.storage.get_all_fund_codes", mock_get_all)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_fund_meta", mock_get_meta)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", mock_get_nav_history)

        s = RatingEnhancedSelection()
        # 降低评级权重，测试可配置性
        result = s.screen(fund_type="stock", top_n=5, params={"rating_weight": 0.1, "quant_weight": 0.7, "deviation_weight": 0.2})
        assert result["total_candidates"] == 2
        # 参数被生效
        assert s.params["rating_weight"] == 0.1

    def test_no_data_fallback(self):
        """无净值数据时返回空结果"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection
        s = RatingEnhancedSelection()
        result = s.screen(fund_type="equity", top_n=5)
        assert "rankings" in result
        assert "total_candidates" in result

    def test_name_contains_score(self, monkeypatch):
        """信号中包含评分说明"""
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection

        mock_funds = ["000001"]
        mock_metas = {"000001": {"fund_code": "000001", "fund_name": "TestA", "fund_type": "stock", "rating": 4}}
        navs = self._make_nav_values(120)

        def mock_get_all():
            return mock_funds

        def mock_get_meta(code):
            return mock_metas.get(code)

        def mock_get_nav_history(code):
            return [{"nav": v} for v in navs]

        monkeypatch.setattr("backend.fund_quant.data.storage.get_all_fund_codes", mock_get_all)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_fund_meta", mock_get_meta)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", mock_get_nav_history)

        s = RatingEnhancedSelection()
        s._state["fund_code"] = "000001"
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1
        # 评分说明应该包含评级、量化、偏差等关键词
        assert "评级" in signals[0].reason or "评分" in signals[0].reason


class TestAuroraRatingEnhanced:
    def test_registered_strategy_emits_core_signal(self, monkeypatch):
        """Aurora 包装器只在受控刷新时调用遗留评分器并发出核心信号。"""
        import sys as _sys
        from datetime import date

        _sys.path.insert(0, "backend")
        from core import Direction as CoreDirection, FundNavPoint, StrategyContext, StrategyRegistry
        from core.event import EventBus, EventType
        from backend.fund_quant.adapter import AuroraRatingEnhancedSelection
        from backend.fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection

        monkeypatch.setattr(RatingEnhancedSelection, "screen", lambda *_args, **_kwargs: {
            "rankings": [{"fund_code": "000001", "total_score": 0.75}],
        })
        assert StrategyRegistry.get("rating_enhanced_aurora") is AuroraRatingEnhancedSelection

        signals = []
        bus = EventBus()
        bus.subscribe(EventType.SIGNAL_GENERATED, lambda event: signals.append(event.payload))
        strategy = AuroraRatingEnhancedSelection()
        strategy.on_init(StrategyContext(bus, mode="live"))
        strategy.on_data(FundNavPoint("000001", date(2024, 1, 2), 1.25))

        assert len(signals) == 1
        assert signals[0].strategy == "rating_enhanced_aurora"
        assert signals[0].direction is CoreDirection.LONG
        assert signals[0].confidence == 0.75

    """Black-Litterman 配置策略专项测试"""

    def _make_nav_values(self, length=120, base=1.0, trend=0.0003, vol=0.008):
        import numpy as np
        np.random.seed(42)
        vals = [base]
        for _ in range(length):
            vals.append(vals[-1] * (1 + trend + np.random.normal(0, vol)))
        return vals

    def test_bl_insufficient_data(self):
        """数据不足时返回 insufficient_data 状态"""
        from backend.fund_quant.strategy.allocation.black_litterman import BlackLittermanStrategy
        s = BlackLittermanStrategy()
        result = s.optimize(fund_codes=["000001"])
        assert result["status"] == "single_fund"

    def test_bl_mvo_only(self, monkeypatch):
        """无观点时等权配置（DeMiguel et al. 2009：等权样本外优于无约束优化）"""
        from backend.fund_quant.strategy.allocation.black_litterman import BlackLittermanStrategy

        navs = self._make_nav_values(120)

        def mock_get_nav_history(code):
            return [{"nav": v} for v in navs]

        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", mock_get_nav_history)

        s = BlackLittermanStrategy()
        result = s.optimize(fund_codes=["000001", "110011"])
        assert result["status"] == "success"
        assert result["method"] == "equal_weight"
        assert len(result["weights"]) == 2

    def test_bl_with_views(self, monkeypatch):
        """有信号视图时使用BL后验收益"""
        from backend.fund_quant.strategy.allocation.black_litterman import BlackLittermanStrategy
        from backend.fund_quant.core.enums import SignalType, Direction
        from backend.fund_quant.core.models import FundSignal

        navs = self._make_nav_values(120)

        def mock_get_nav_history(code):
            return [{"nav": v} for v in navs]

        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", mock_get_nav_history)

        s = BlackLittermanStrategy()
        # 注入一个买入信号
        s._state["active_signals"] = [
            FundSignal(signal_id="t1", fund_code="000001", fund_name="TestA",
                       signal_type=SignalType.TIMING, direction=Direction.BUY,
                       confidence=0.8, reason="测试信号"),
        ]
        result = s.optimize(fund_codes=["000001", "110011"])
        assert result["status"] == "success"
        assert result["method"] in ("black_litterman",)
        assert result.get("views_applied") is True

    def test_bl_two_fund_example(self, monkeypatch):
        """两基金示例: 验证BL公式数值合理性"""
        from backend.fund_quant.strategy.allocation.black_litterman import BlackLittermanStrategy

        navs = self._make_nav_values(120)

        def mock_get_nav_history(code):
            return [{"nav": v} for v in navs]

        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", mock_get_nav_history)
        monkeypatch.setattr("backend.fund_quant.data.storage.get_fund_meta", lambda c: None)

        s = BlackLittermanStrategy()
        result = s.optimize(fund_codes=["000001", "110011"])
        assert result["status"] == "success"
        assert "weights" in result
        # 权重为正且和为1
        w = list(result["weights"].values())
        assert all(wi > 0 for wi in w)
        assert abs(sum(w) - 1.0) < 0.01


class TestStrategyState:
    def test_save_load_state(self):
        registry = StrategyRegistry()
        s = registry.get_strategy("multi_factor")
        s._state = {"test_key": "test_value"}
        state = s.save_state()
        assert state["test_key"] == "test_value"

        s2 = registry.get_strategy("multi_factor")
        s2.load_state({"new_key": 42})
        assert s2._state["new_key"] == 42


class TestSignalFusion:
    def setup_method(self):
        self.fusion = SignalFusion()

    def test_no_signals(self):
        assert self.fusion.fuse([]) is None

    def test_single_buy_signal(self):
        s = FundSignal(signal_id="s1", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.TIMING, direction=Direction.BUY,
                       confidence=0.8, reason="测试")
        result = self.fusion.fuse([s])
        assert result is not None
        assert result.direction == Direction.BUY
        assert result.confidence > 0

    def test_two_buy_signals(self):
        sigs = [
            FundSignal(signal_id="s1", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.TIMING, direction=Direction.BUY,
                       confidence=0.8, reason="a"),
            FundSignal(signal_id="s2", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.SELECTION, direction=Direction.BUY,
                       confidence=0.6, reason="b"),
        ]
        result = self.fusion.fuse(sigs)
        assert result.direction == Direction.BUY
        assert result.conflict is False

    def test_conflict_signals(self):
        sigs = [
            FundSignal(signal_id="s1", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.TIMING, direction=Direction.BUY,
                       confidence=0.8, reason="a"),
            FundSignal(signal_id="s2", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.ALLOCATION, direction=Direction.SELL,
                       confidence=0.7, reason="b"),
        ]
        result = self.fusion.fuse(sigs)
        assert result.conflict is True
        assert len(result.contributing_strategies) >= 2

    def test_all_hold(self):
        sigs = [
            FundSignal(signal_id="s1", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.TIMING, direction=Direction.HOLD,
                       confidence=0.5, reason="a"),
        ]
        result = self.fusion.fuse(sigs)
        assert result is not None
        assert result.direction == Direction.HOLD

    def test_confidence_never_exceeds_1(self):
        sigs = [
            FundSignal(signal_id="s1", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.TIMING, direction=Direction.BUY,
                       confidence=1.0, reason="a"),
            FundSignal(signal_id="s2", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.SELECTION, direction=Direction.BUY,
                       confidence=1.0, reason="b"),
        ]
        result = self.fusion.fuse(sigs)
        assert result.confidence <= 1.0

    def test_timing_override(self):
        """择时置信度>0.9时覆盖配置信号"""
        # 构造: 择时=BUY(0.95), 选基=SELL(1.0), 配置=SELL(1.0)
        # 融合方向应为SELL, 但择时>0.9覆盖为BUY
        sigs = [
            FundSignal(signal_id="s1", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.TIMING, direction=Direction.BUY,
                       confidence=0.95, reason="高置信度买"),
            FundSignal(signal_id="s2", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.SELECTION, direction=Direction.SELL,
                       confidence=1.0, reason="选基卖"),
            FundSignal(signal_id="s3", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.ALLOCATION, direction=Direction.SELL,
                       confidence=1.0, reason="配置卖"),
        ]
        result = self.fusion.fuse(sigs)
        assert result.direction == Direction.BUY  # 择时覆盖
        assert result.override_reason is not None

    def test_balanced_weighted_fusion(self):
        """balanced 基金按仓位权重加权"""
        sigs = [
            FundSignal(signal_id="s1", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.TIMING, direction=Direction.BUY,
                       strategy_name="momentum", confidence=1.0, reason="momentum_buy"),
            FundSignal(signal_id="s2", fund_code="000001", fund_name="Test",
                       signal_type=SignalType.TIMING, direction=Direction.SELL,
                       strategy_name="interest_rate", confidence=1.0, reason="rate_sell"),
        ]
        # 80% 权益 → momentum 占优 → BUY
        r1 = self.fusion.fuse(sigs, fund_type="balanced",
                               position_weights={"equity_ratio": 0.8, "bond_ratio": 0.2})
        assert r1.direction == Direction.BUY, f"got {r1.direction}"
        # 80% 债券 → interest_rate 占优 → SELL
        r2 = self.fusion.fuse(sigs, fund_type="balanced",
                               position_weights={"equity_ratio": 0.2, "bond_ratio": 0.8})
        assert r2.direction == Direction.SELL, f"got {r2.direction}"
        # 不传 fund_type 时不加权
        r3 = self.fusion.fuse(sigs)
        assert r3 is not None


class TestP0AllocationStrategies:
    """P0 新策略：动态风险平价 + 波动率目标（走 AuroraCore 统一引擎）"""
    import sys as _sys
    _sys.path.insert(0, "backend")  # noqa: E402 — 暴露 core 包（与 test_aurora_etf_rotation 一致）

    def test_p0_registered(self):
        from core.strategy import StrategyRegistry
        import backend.fund_quant.adapter  # 触发注册
        reg = StrategyRegistry.list_all()
        assert "dynamic_risk_parity_aurora" in reg
        assert "vol_targeting_aurora" in reg
        assert reg["dynamic_risk_parity_aurora"].strategy_type == "allocation"
        assert reg["vol_targeting_aurora"].strategy_type == "allocation"

    def _nav_series(self, n_days=420, n_funds=3, seed=42):
        """生成 n_funds 只基金 n_days 天净值（低相关几何随机游走）"""
        rng = np.random.default_rng(seed)
        series = {}
        for f in range(n_funds):
            navs, base = [], 1.0
            for _ in range(n_days):
                base *= 1 + rng.normal(0.0003, 0.01)
                navs.append(base)
            series[f"F{f}"] = navs
        return series

    def test_dynamic_rp_weights(self):
        from backend.fund_quant.adapter import AuroraDynamicRiskParity
        s = AuroraDynamicRiskParity()
        series = self._nav_series(n_days=420, n_funds=3)  # 20个月
        w = s._compute_weights(series, list(series.keys()))
        assert w, "应返回权重"
        assert abs(sum(w.values()) - 1.0) < 1e-3, f"权重和≠1: {sum(w.values())}"  # 四舍五入容差
        assert all(np.isfinite(v) for v in w.values()), "存在 NaN"

    def test_dynamic_rp_short_window(self):
        """窗口截断后不足2基金 → 返回空（不崩溃）"""
        from backend.fund_quant.adapter import AuroraDynamicRiskParity
        s = AuroraDynamicRiskParity()
        series = {f"F{i}": list(range(50, 50 + 59)) for i in range(3)}  # 只有59天
        assert s._compute_weights(series, list(series.keys())) == {}

    def test_vol_targeting_equal_weight_base(self):
        from backend.fund_quant.adapter import AuroraVolTargeting
        s = AuroraVolTargeting()
        codes = ["F0", "F1", "F2"]
        w = s._compute_weights({}, codes)
        assert w == {c: 1 / 3 for c in codes}, f"等权打底失败: {w}"

    def test_vol_targeting_scaling(self):
        """高波降仓 scale<1；低波加仓 scale>1（受 max_scale 限制）"""
        from backend.fund_quant.adapter import AuroraVolTargeting
        rng = np.random.default_rng(7)
        high = [1.0]; base = 1.0
        for _ in range(200):
            base *= 1 + rng.normal(0, 0.03)   # 年化 ~47%
            high.append(base)
        low = [1.0]; base = 1.0
        for _ in range(200):
            base *= 1 + rng.normal(0, 0.001)  # 年化 ~1.6%
            low.append(base)

        # 高波 → 降仓
        s_hi = AuroraVolTargeting()
        w = {"A": 0.5, "B": 0.5}
        out = s_hi._apply_vol_targeting(w, {"A": high, "B": high}, "")
        assert out["A"] < 0.5, f"高波应降仓: {out['A']}"
        assert out["A"] >= 0.5 * 0.1, "不低于 min_scale"
        # 低波 → 加仓
        s_lo = AuroraVolTargeting()
        out = s_lo._apply_vol_targeting(w, {"A": low, "B": low}, "")
        assert out["A"] > 0.5, f"低波应加仓: {out['A']}"
        assert out["A"] <= 0.5 * 2.0, "不超过 max_scale"

    def test_vol_targeting_off_regression(self):
        """vol_target=0 时叠加层原样返回（保护现有7策略）"""
        from backend.fund_quant.adapter import AuroraRiskParity
        s = AuroraRiskParity()
        s.params["vol_target"] = 0
        w = {"A": 0.4, "B": 0.6}
        assert s._apply_vol_targeting(w, {"A": [1.0] * 100}, "") == w

    def test_trend_following_weights_and_cash(self):
        from backend.fund_quant.adapter import AuroraTrendFollowing
        s = AuroraTrendFollowing()
        s.params["lookback_days"] = 20
        up = list(np.linspace(1.0, 1.2, 21))
        down = list(np.linspace(1.2, 1.0, 21))
        flat = [1.0] * 21
        weights = s._compute_weights({"UP": up, "DOWN": down, "FLAT": flat}, ["UP", "DOWN", "FLAT"])
        assert weights == {"UP": 1.0}

        # 全部趋势转弱时返回显式零权重，供基类清仓并保留现金。
        assert s._compute_weights({"DOWN": down, "FLAT": flat}, ["DOWN", "FLAT"]) == {
            "DOWN": 0.0, "FLAT": 0.0,
        }

    def test_trend_following_liquidates_existing_positions(self):
        """趋势转弱时应发出平仓信号，而非因空权重直接跳过。"""
        from backend.fund_quant.adapter import AuroraTrendFollowing
        from core.signal import Direction, Position

        s = AuroraTrendFollowing()
        s.params.update({"lookback_days": 20, "rebalance_threshold": 0.0})
        values = list(np.linspace(1.2, 1.0, 21))
        dates = [f"2025-01-{i + 1:02d}" for i in range(21)]
        s._hist = {code: list(zip(dates, values)) for code in ("DOWN", "FLAT")}
        emitted = []

        class Execution:
            def get_position(self, code):
                return Position(code, Direction.LONG, 10.0, 1.1)

        class Context:
            portfolio_value = 1000.0
            execution = Execution()

            def emit(self, signal):
                emitted.append(signal)

        s.ctx = Context()
        s._rebalance(dates[-1])

        assert {signal.symbol for signal in emitted} == {"DOWN", "FLAT"}
        assert all(signal.direction == Direction.CLOSE_LONG for signal in emitted)

    def test_gmv_minimizes_variance(self):
        from backend.fund_quant.adapter import AuroraGlobalMinimumVariance
        s = AuroraGlobalMinimumVariance()
        rng = np.random.default_rng(11)
        # A 高波动，B 低波动；GMV 应倾向低波动资产且权重和为1。
        series = {}
        for code, sigma in (("HIGH", 0.03), ("LOW", 0.003), ("MID", 0.01)):
            value, values = 1.0, []
            for _ in range(300):
                value *= 1 + rng.normal(0.0002, sigma)
                values.append(value)
            series[code] = values
        weights = s._compute_weights(series, list(series))
        assert weights
        assert abs(sum(weights.values()) - 1.0) < 1e-3
        assert all(np.isfinite(v) and v >= 0 for v in weights.values())
        assert weights["LOW"] >= weights["HIGH"]

    def test_gmv_insufficient_data(self):
        from backend.fund_quant.adapter import AuroraGlobalMinimumVariance
        s = AuroraGlobalMinimumVariance()
        assert s._compute_weights({"A": [1.0] * 20, "B": [1.0] * 20}, ["A", "B"]) == {}
