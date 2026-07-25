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
            "valuation_deviation", "momentum", "interest_rate",
            "fx_momentum", "smart_dca", "gold_momentum", "credit_spread", "multi_factor",
            "rating_enhanced", "index_selection", "risk_parity", "black_litterman",
            "etf_global_rotation", "all_weather",
        }
        assert names == expected, f"缺失: {expected - names}, 多余: {names - expected}"

    def test_strategy_types(self):
        strategies = self.registry.list_strategies()
        by_type = {}
        for s in strategies:
            by_type.setdefault(s["type"], []).append(s["name"])
        assert len(by_type.get("timing", [])) == 7, "择时策略应为7个"
        assert len(by_type.get("selection", [])) == 3, "选基策略应为3个"
        assert len(by_type.get("allocation", [])) == 4, "配置策略应为4个（risk_parity, black_litterman, etf_global_rotation, all_weather）"

    def test_get_strategy_returns_instance(self):
        s = self.registry.get_strategy("momentum")
        assert s is not None
        assert s.strategy_name == "momentum"
        assert s.strategy_type == "timing"

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

    def test_valuation_deviation(self, setup_strategy):
        s = setup_strategy("valuation_deviation")
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1
        sig = signals[0]
        assert sig.signal_type == SignalType.TIMING

    def test_momentum(self, setup_strategy):
        s = setup_strategy("momentum")
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1
        assert signals[0].strategy_name == "momentum"

    def test_interest_rate(self, setup_strategy):
        s = setup_strategy("interest_rate")
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1

    def test_fx_momentum(self, setup_strategy):
        s = setup_strategy("fx_momentum")
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1
        # 无汇率数据时应返回 hold
        assert signals[0].direction == Direction.HOLD

    def test_fx_momentum_with_fx_data(self, setup_strategy):
        s = setup_strategy("fx_momentum")
        # 注入汇率数据
        fx_data = {f"USDCNY": [7.0 + i * 0.001 for i in range(30)]}
        s._state["fx_rates_history"] = fx_data
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1

    def test_smart_dca(self, setup_strategy):
        """智能定投需要特定z-score触发信号, 使用下行趋势数据"""
        s = setup_strategy("smart_dca")
        # 构建下行趋势数据使z-score明显为负(低估)
        np.random.seed(1)
        vals = [1.0]
        for _ in range(180):
            vals.append(vals[-1] * (1 - abs(np.random.normal(0.001, 0.005))))
        s._state["nav_values"] = vals
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1

    def test_gold_momentum(self, setup_strategy):
        """黄金动量策略能生成信号"""
        s = setup_strategy("gold_momentum")
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1
        sig = signals[0]
        assert sig.signal_type == SignalType.TIMING

    def test_credit_spread_no_data(self, setup_strategy):
        """信用利差策略无数据时返回 HOLD"""
        s = setup_strategy("credit_spread")
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1
        assert signals[0].direction == Direction.HOLD

    def test_credit_spread_with_data(self, setup_strategy):
        """信用利差策略有数据时生成买卖信号"""
        s = setup_strategy("credit_spread")
        # 注入利差走阔数据（模拟信用恶化）
        spread_widening = [0.01 * i for i in range(30)]
        s._state["credit_spread_history"] = spread_widening
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1
        assert signals[0].direction == Direction.SELL

        # 利差收窄 → BUY
        s2 = setup_strategy("credit_spread")
        spread_narrowing = [0.03 - 0.001 * i for i in range(30)]
        s2._state["credit_spread_history"] = spread_narrowing
        signals2 = s2.on_evaluate(None, None)
        assert signals2[0].direction == Direction.BUY

    def test_credit_spread_curve_signal(self, setup_strategy):
        """收益率曲线信号独立工作"""
        s = setup_strategy("credit_spread")
        # 无利差数据，但有曲线数据
        curve_steepening = [0.005 * i for i in range(30)]
        s._state["yield_curve_history"] = curve_steepening
        signals = s.on_evaluate(None, None)
        assert len(signals) >= 1

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
        for name in ["valuation_deviation", "momentum"]:
            s = setup_strategy(name)
            signals = s.on_evaluate(None, None)
            assert len(signals) >= 1, f"{name} 未能生成信号"


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


class TestBlackLitterman:
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
        """无观点时降级为均值-方差优化"""
        from backend.fund_quant.strategy.allocation.black_litterman import BlackLittermanStrategy

        navs = self._make_nav_values(120)

        def mock_get_nav_history(code):
            return [{"nav": v} for v in navs]

        monkeypatch.setattr("backend.fund_quant.data.storage.get_nav_history", mock_get_nav_history)

        s = BlackLittermanStrategy()
        result = s.optimize(fund_codes=["000001", "110011"])
        assert result["status"] == "success"
        assert result["method"] in ("mean_variance",)
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
        s = registry.get_strategy("momentum")
        s._state = {"test_key": "test_value"}
        state = s.save_state()
        assert state["test_key"] == "test_value"

        s2 = registry.get_strategy("momentum")
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
