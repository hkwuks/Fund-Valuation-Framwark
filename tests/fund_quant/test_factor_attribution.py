"""因子归因单元测试"""

import numpy as np
import pytest

from backend.fund_quant.backtest.factor_attribution import (
    FactorAttribution,
    FactorAttributionReport,
)


class TestFactorAttribution:
    """FactorAttribution 单元测试"""

    @pytest.fixture
    def attr(self) -> FactorAttribution:
        return FactorAttribution()

    def test_known_beta(self, attr):
        """模拟已知 beta 的数据，验证估计值在 0.1 误差范围内。"""
        rng = np.random.RandomState(42)
        n = 200
        market = rng.normal(0.0005, 0.01, n)
        size = rng.normal(0.0002, 0.005, n)
        value = rng.normal(0.0001, 0.003, n)
        alpha_daily = 0.0003
        portfolio = (
            alpha_daily
            + 0.8 * market
            + 0.2 * size
            + 0.1 * value
            + rng.normal(0, 0.001, n)
        )
        factors = {"market": market, "size": size, "value": value}

        report = attr.run(portfolio, factors)

        assert isinstance(report, FactorAttributionReport)
        assert report.n_factors == 3
        assert abs(report.betas["market"] - 0.8) < 0.1
        assert abs(report.betas["size"] - 0.2) < 0.1
        assert abs(report.betas["value"] - 0.1) < 0.1
        assert 0.0 < report.r_squared < 1.0

    def test_zero_alpha(self, attr):
        """收益完全由因子解释，Alpha 不显著。"""
        rng = np.random.RandomState(123)
        n = 200
        market = rng.normal(0.0005, 0.01, n)
        portfolio = 1.0 * market + rng.normal(0, 0.005, n)
        factors = {"market": market}

        report = attr.run(portfolio, factors)

        assert not report.alpha_significant

    def test_positive_alpha(self, attr):
        """含 Alpha 的数据，Alpha 为正且显著。"""
        rng = np.random.RandomState(456)
        n = 200
        market = rng.normal(0.0005, 0.01, n)
        alpha_daily = 0.001
        portfolio = alpha_daily + 1.0 * market + rng.normal(0, 0.002, n)
        factors = {"market": market}

        report = attr.run(portfolio, factors)

        assert report.alpha > 0.0
        assert report.alpha_significant

    def test_newey_west_se_differs_from_ols(self, attr):
        """自相关残差下 Newey-West 标准误与 OLS 标准误不同。"""
        rng = np.random.RandomState(789)
        n = 100
        # 生成自相关扰动: AR(1) 噪声
        eps = np.empty(n)
        eps[0] = rng.normal(0, 0.005)
        for t in range(1, n):
            eps[t] = 0.7 * eps[t - 1] + rng.normal(0, 0.005)
        market = rng.normal(0.0005, 0.01, n)
        portfolio = 1.0 * market + eps
        factors = {"market": market}

        report = attr.run(portfolio, factors)
        portfolio_arr = np.asarray(portfolio, dtype=float)
        X = np.column_stack([np.ones(n), np.asarray(market, dtype=float)])
        beta = np.linalg.lstsq(X, portfolio_arr, rcond=None)[0]
        residuals = portfolio_arr - X @ beta

        # OLS 标准误
        n_obs, p = X.shape
        s2 = np.sum(residuals ** 2) / (n_obs - p)
        XX_inv = np.linalg.inv(X.T @ X)
        se_ols = np.sqrt(s2 * np.diag(XX_inv))

        # NYW 标准误
        se_nw = attr.newey_west_se(residuals, X)

        # 验证至少一个系数的两种标准误存在明显差异
        ratios = se_nw / se_ols
        assert np.any(np.abs(ratios - 1.0) > 0.05)

    def test_r_squared_perfect(self, attr):
        """收益精确等于因子收益线性组合，R² 应为 1.0。"""
        rng = np.random.RandomState(111)
        n = 50
        market = rng.normal(0.0005, 0.01, n)
        size = rng.normal(0.0002, 0.005, n)
        portfolio = 0.6 * market + 0.4 * size
        factors = {"market": market, "size": size}

        report = attr.run(portfolio, factors)

        assert report.r_squared == pytest.approx(1.0, abs=1e-10)

    def test_empty_factor_raises(self, attr):
        """空因子字典应抛出 ValueError。"""
        with pytest.raises(ValueError, match="因子收益率字典不能为空"):
            attr.run(np.array([0.1, 0.2, 0.3]), {})

    def test_mismatched_lengths(self, attr):
        """因子序列与组合收益率序列长度不匹配应抛出 ValueError。"""
        with pytest.raises(ValueError, match="不匹配"):
            attr.run(
                np.zeros(12),
                {"factor": np.zeros(10)},
            )
