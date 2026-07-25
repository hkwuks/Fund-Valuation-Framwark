"""Brinson 绩效归因单元测试"""

import math

import pytest

from backend.fund_quant.backtest.brinson import BrinsonAttribution


class TestBrinsonAttribution:
    """BrinsonAttribution 单元测试"""

    @pytest.fixture
    def attribution(self) -> BrinsonAttribution:
        return BrinsonAttribution()

    def test_single_period_allocation_only(self, attribution):
        """等收益、不同权重 → 仅有配置效应，选股和交互为零。"""
        result = attribution.attribute_single_period(
            portfolio_weights={"equity": 0.6, "bond": 0.4},
            portfolio_returns={"equity": 0.05, "bond": 0.02},
            benchmark_weights={"equity": 0.5, "bond": 0.5},
            benchmark_returns={"equity": 0.05, "bond": 0.02},
        )
        # 配置效应: (0.6-0.5)*0.05 + (0.4-0.5)*0.02 = 0.005 - 0.002 = 0.003
        assert result.allocation_effect == pytest.approx(0.003, abs=1e-10)
        assert result.selection_effect == pytest.approx(0.0, abs=1e-10)
        assert result.interaction_effect == pytest.approx(0.0, abs=1e-10)
        # 组合: 0.6*0.05 + 0.4*0.02 = 0.038
        # 基准: 0.5*0.05 + 0.5*0.02 = 0.035
        # 超额: 0.003
        assert result.excess_return == pytest.approx(0.003, abs=1e-10)

    def test_single_period_selection_only(self, attribution):
        """等权重、不同收益 → 仅有选股效应。"""
        result = attribution.attribute_single_period(
            portfolio_weights={"equity": 0.5, "bond": 0.5},
            portfolio_returns={"equity": 0.06, "bond": 0.03},
            benchmark_weights={"equity": 0.5, "bond": 0.5},
            benchmark_returns={"equity": 0.05, "bond": 0.02},
        )
        # 选股: 0.5*(0.06-0.05) + 0.5*(0.03-0.02) = 0.005 + 0.005 = 0.01
        assert result.allocation_effect == pytest.approx(0.0, abs=1e-10)
        assert result.selection_effect == pytest.approx(0.01, abs=1e-10)
        assert result.interaction_effect == pytest.approx(0.0, abs=1e-10)
        # 组合: 0.5*0.06 + 0.5*0.03 = 0.045
        # 基准: 0.5*0.05 + 0.5*0.02 = 0.035
        # 超额: 0.01
        assert result.excess_return == pytest.approx(0.01, abs=1e-10)

    def test_single_period_all_effects(self, attribution):
        """不同权重和收益 → 三个效应均非零。"""
        result = attribution.attribute_single_period(
            portfolio_weights={"equity": 0.7, "bond": 0.3},
            portfolio_returns={"equity": 0.08, "bond": 0.01},
            benchmark_weights={"equity": 0.5, "bond": 0.5},
            benchmark_returns={"equity": 0.05, "bond": 0.02},
        )
        # equity: alloc=(0.7-0.5)*0.05=0.01, sel=0.5*(0.08-0.05)=0.015, inter=(0.7-0.5)*(0.08-0.05)=0.006
        # bond:   alloc=(0.3-0.5)*0.02=-0.004, sel=0.5*(0.01-0.02)=-0.005, inter=(0.3-0.5)*(0.01-0.02)=0.002
        # 总和: alloc=0.006, sel=0.01, inter=0.008
        # 组合: 0.7*0.08+0.3*0.01=0.059
        # 基准: 0.5*0.05+0.5*0.02=0.035
        # 超额: 0.024 = 0.006+0.01+0.008
        assert result.allocation_effect == pytest.approx(0.006, abs=1e-10)
        assert result.selection_effect == pytest.approx(0.01, abs=1e-10)
        assert result.interaction_effect == pytest.approx(0.008, abs=1e-10)
        assert result.excess_return == pytest.approx(0.024, abs=1e-10)
        assert result.sector_details["equity"]["allocation"] == pytest.approx(0.01, abs=1e-10)
        assert result.sector_details["equity"]["selection"] == pytest.approx(0.015, abs=1e-10)
        assert result.sector_details["equity"]["interaction"] == pytest.approx(0.006, abs=1e-10)
        assert result.sector_details["bond"]["allocation"] == pytest.approx(-0.004, abs=1e-10)
        assert result.sector_details["bond"]["selection"] == pytest.approx(-0.005, abs=1e-10)
        assert result.sector_details["bond"]["interaction"] == pytest.approx(0.002, abs=1e-10)

    def test_excess_decomposition_sum(self, attribution):
        """验证配置+选股+交互 ≈ 超额收益（精度 1e-6）。"""
        result = attribution.attribute_single_period(
            portfolio_weights={"A": 0.5, "B": 0.3, "C": 0.2},
            portfolio_returns={"A": 0.10, "B": -0.02, "C": 0.04},
            benchmark_weights={"A": 0.4, "B": 0.4, "C": 0.2},
            benchmark_returns={"A": 0.08, "B": 0.01, "C": 0.03},
        )
        effects_sum = (
            result.allocation_effect
            + result.selection_effect
            + result.interaction_effect
        )
        assert effects_sum == pytest.approx(result.excess_return, abs=1e-6)

    def test_multi_period_carino_linking(self, attribution):
        """多周期 Carino 链接后效应可加。"""
        periods = [
            {
                "period": "2024-Q1",
                "portfolio_weights": {"equity": 0.6, "bond": 0.4},
                "portfolio_returns": {"equity": 0.05, "bond": 0.02},
                "benchmark_weights": {"equity": 0.5, "bond": 0.5},
                "benchmark_returns": {"equity": 0.04, "bond": 0.03},
            },
            {
                "period": "2024-Q2",
                "portfolio_weights": {"equity": 0.7, "bond": 0.3},
                "portfolio_returns": {"equity": -0.02, "bond": 0.01},
                "benchmark_weights": {"equity": 0.6, "bond": 0.4},
                "benchmark_returns": {"equity": -0.03, "bond": 0.02},
            },
        ]
        report = attribution.attribute_multi_period(periods)

        assert report.carino_linked is True
        assert report.n_periods == 2
        assert len(report.periods) == 2
        assert report.periods[0].period == "2024-Q1"
        assert report.periods[1].period == "2024-Q2"

        # 验证 Carino 系数
        for p in report.periods:
            k_t = BrinsonAttribution._carino_k(p.portfolio_return, p.benchmark_return)
            expected_k = (
                (math.log(1 + p.portfolio_return) - math.log(1 + p.benchmark_return))
                / (p.portfolio_return - p.benchmark_return)
            )
            assert k_t == pytest.approx(expected_k, abs=1e-10)

        # 效应之和应等于 total_excess（本质上去掉了交互效应的时间不可加性）
        total = report.total_allocation + report.total_selection + report.total_interaction
        assert total == pytest.approx(report.total_excess, abs=1e-10)

    def test_multi_period_single(self, attribution):
        """仅有一个周期 → carino_linked 为 False，结果与单周期一致。"""
        periods = [
            {
                "period": "2024-Q1",
                "portfolio_weights": {"equity": 0.6, "bond": 0.4},
                "portfolio_returns": {"equity": 0.05, "bond": 0.02},
                "benchmark_weights": {"equity": 0.5, "bond": 0.5},
                "benchmark_returns": {"equity": 0.05, "bond": 0.02},
            },
        ]
        report = attribution.attribute_multi_period(periods)
        assert report.carino_linked is False
        assert report.n_periods == 1
        assert report.total_allocation == pytest.approx(0.003, abs=1e-10)
        assert report.total_selection == pytest.approx(0.0, abs=1e-10)
        assert report.total_interaction == pytest.approx(0.0, abs=1e-10)

    def test_empty_weights_raises(self, attribution):
        """空权重 → ValueError。"""
        with pytest.raises(ValueError, match="权重字典不能为空"):
            attribution.attribute_single_period(
                portfolio_weights={},
                portfolio_returns={"equity": 0.05},
                benchmark_weights={"equity": 0.5},
                benchmark_returns={"equity": 0.04},
            )
        with pytest.raises(ValueError, match="权重字典不能为空"):
            attribution.attribute_single_period(
                portfolio_weights={"equity": 0.6},
                portfolio_returns={"equity": 0.05},
                benchmark_weights={},
                benchmark_returns={"equity": 0.04},
            )

    def test_missing_sector_handled(self, attribution):
        """行业仅在组合中存在（基准中无）→ 处理为基准权重=0。"""
        result = attribution.attribute_single_period(
            portfolio_weights={"equity": 0.6, "bond": 0.4},
            portfolio_returns={"equity": 0.08, "bond": 0.02},
            benchmark_weights={"equity": 0.5},
            benchmark_returns={"equity": 0.05},
        )
        # bond 在基准中不存在, w_b=0, r_b=0
        # equity: alloc=(0.6-0.5)*0.05=0.005, sel=0.5*(0.08-0.05)=0.015, inter=0.1*0.03=0.003
        # bond:   alloc=(0.4-0)*0=0, sel=0*(0.02-0)=0, inter=(0.4-0)*0.02=0.008
        # 注意：基准总收益 = 0.5*0.05 = 0.025（归一化 equity 权重为 1.0）
        # 组合总收益 = 0.6*0.08 + 0.4*0.02 = 0.056（归一化 equity/bond 权重 0.6/0.4）
        # 超额 = 0.056 - 0.025 = 0.031
        # 效应: 0.005+0.015+0.003+0+0+0.008 = 0.031 ✓
        assert result.sector_details["bond"]["benchmark_return"] == pytest.approx(0.0, abs=1e-10)
        assert result.sector_details["bond"]["benchmark_weight"] == pytest.approx(0.0, abs=1e-10)
        effects_sum = (
            result.allocation_effect
            + result.selection_effect
            + result.interaction_effect
        )
        assert effects_sum == pytest.approx(result.excess_return, abs=1e-6)

    def test_weights_normalized(self, attribution):
        """权重之和不足 1.0 → 自动归一化。"""
        result = attribution.attribute_single_period(
            portfolio_weights={"equity": 0.5, "bond": 0.4},  # 总和 0.9
            portfolio_returns={"equity": 0.05, "bond": 0.02},
            benchmark_weights={"equity": 0.45, "bond": 0.45},  # 总和 0.9
            benchmark_returns={"equity": 0.04, "bond": 0.03},
        )
        # 归一化后 equity=0.5/0.9, bond=0.4/0.9
        # 基准 equity=0.45/0.9=0.5, bond=0.45/0.9=0.5
        # 与标准 50/50 等权相同，但组合权重与基准权重相同 → 只有选股效应
        assert result.portfolio_weight["equity"] == pytest.approx(0.5 / 0.9, abs=1e-10)
        assert result.portfolio_weight["bond"] == pytest.approx(0.4 / 0.9, abs=1e-10)
        assert result.benchmark_weight["equity"] == pytest.approx(0.5, abs=1e-10)
        assert result.benchmark_weight["bond"] == pytest.approx(0.5, abs=1e-10)
        # 选股效应: 0.5*(0.05-0.04) + 0.5*(0.02-0.03) = 0.005 - 0.005 = 0.0
        assert result.selection_effect == pytest.approx(0.0, abs=1e-10)

    def test_carino_limit_case(self, attribution):
        """R_t ≈ B_t 时 Carino k 使用极限近似。"""
        # 当 r_t 和 b_t 几乎相等时，用 1/(1 + R_t)
        k = BrinsonAttribution._carino_k(0.05, 0.05 + 1e-12)
        assert k == pytest.approx(1.0 / 1.05, abs=1e-10)

    def test_carino_multi_period_three_sectors(self, attribution):
        """三个行业、两个周期的 Carino 链接完整性验证。"""
        periods = [
            {
                "period": "2024-Q1",
                "portfolio_weights": {"A": 0.4, "B": 0.35, "C": 0.25},
                "portfolio_returns": {"A": 0.10, "B": 0.05, "C": -0.02},
                "benchmark_weights": {"A": 0.3, "B": 0.4, "C": 0.3},
                "benchmark_returns": {"A": 0.08, "B": 0.04, "C": 0.01},
            },
            {
                "period": "2024-Q2",
                "portfolio_weights": {"A": 0.5, "B": 0.3, "C": 0.2},
                "portfolio_returns": {"A": -0.03, "B": 0.06, "C": 0.04},
                "benchmark_weights": {"A": 0.4, "B": 0.35, "C": 0.25},
                "benchmark_returns": {"A": -0.01, "B": 0.05, "C": 0.03},
            },
        ]
        report = attribution.attribute_multi_period(periods)
        assert report.carino_linked is True
        assert report.n_periods == 2
        total = report.total_allocation + report.total_selection + report.total_interaction
        assert total == pytest.approx(report.total_excess, abs=1e-10)
