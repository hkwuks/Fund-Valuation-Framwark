"""
因子归因 — OLS 回归分解组合收益为因子暴露和 Alpha。
使用 Newey-West 标准误处理自相关和异方差。
"""

import logging
from dataclasses import dataclass
from typing import Dict, List

import numpy as np
from scipy import stats as scipy_stats

logger = logging.getLogger(__name__)

__all__ = ["FactorAttributionReport", "FactorAttribution"]


@dataclass
class FactorAttributionReport:
    """因子归因报告。"""

    alpha: float
    alpha_tstat: float
    alpha_pvalue: float
    alpha_significant: bool
    betas: Dict[str, float]
    beta_tstats: Dict[str, float]
    beta_pvalues: Dict[str, float]
    r_squared: float
    adj_r_squared: float
    n_observations: int
    n_factors: int
    factor_names: List[str]


class FactorAttribution:
    """因子归因分析器。

    将组合收益率序列对因子收益率序列做 OLS 回归，通过 Newey-West 标准误
    计算 t 统计量，并给出年化 Alpha。
    """

    FACTOR_NAMES = ["market", "size", "value", "momentum", "bond"]

    @staticmethod
    def newey_west_se(residuals: np.ndarray, X: np.ndarray,
                      lag: int = None) -> np.ndarray:
        """Newey-West 异方差自相关一致标准误。

        Args:
            residuals: 回归残差 (n,)
            X: 设计矩阵 (n, p)，含常数项
            lag: 滞后阶数，自动选择时使用 Newey-West (1994) 公式

        Returns:
            SE 数组 (p,)
        """
        n, p = X.shape
        if lag is None:
            lag = int(4 * (n / 100) ** (2 / 9))
        lag = max(lag, 1)

        # White 估计量基础项
        Xe = X * residuals[:, np.newaxis]
        omega = Xe.T @ Xe

        # Bartlett 核加权自协方差
        for l in range(1, lag + 1):
            w = 1 - l / (lag + 1)
            Xe_l = X[:-l] * residuals[l:, np.newaxis]   # X_{t-l} * e_t
            Xe_r = X[l:] * residuals[:-l, np.newaxis]   # X_t * e_{t-l}
            omega += w * (Xe_l.T @ Xe_r + Xe_r.T @ Xe_l)

        XX_inv = np.linalg.inv(X.T @ X)
        cov = XX_inv @ omega @ XX_inv
        return np.sqrt(np.diag(cov))

    def run(self, portfolio_returns: np.ndarray,
            factor_returns: Dict[str, np.ndarray]) -> FactorAttributionReport:
        """OLS 回归分解组合收益为 Alpha 和因子暴露。

        Args:
            portfolio_returns: 组合日收益率 (n,)
            factor_returns: 因子名 -> 日收益率 (n,)

        Returns:
            FactorAttributionReport

        Raises:
            ValueError: 因子为空、序列长度不匹配、观测值不足
        """
        portfolio_returns = np.asarray(portfolio_returns, dtype=float)

        if not factor_returns:
            raise ValueError("因子收益率字典不能为空")

        factor_names = sorted(factor_returns.keys())
        n = len(portfolio_returns)

        if n < 10:
            raise ValueError(f"观测值不足 ({n}), 至少需要 10 个样本")

        factor_arrays = []
        valid_names = []
        for name in factor_names:
            arr = np.asarray(factor_returns[name], dtype=float)
            if len(arr) != n:
                raise ValueError(
                    f"因子 '{name}' 长度 ({len(arr)}) "
                    f"与组合收益率长度 ({n}) 不匹配"
                )
            factor_arrays.append(arr)
            valid_names.append(name)

        if not valid_names:
            raise ValueError("没有有效的因子序列")

        p = len(valid_names)

        # 设计矩阵 X: 常数项 + 因子收益率
        factor_matrix = np.column_stack(factor_arrays)
        X = np.column_stack([np.ones(n), factor_matrix])

        # OLS: beta = (X'X)^{-1} X'y
        beta = np.linalg.lstsq(X, portfolio_returns, rcond=None)[0]

        # 残差
        residuals = portfolio_returns - X @ beta

        # Newey-West 标准误
        se = self.newey_west_se(residuals, X)

        # t 统计量
        t_stats = beta / se

        # 自由度
        df = n - p - 1

        # p 值 (t 分布双尾)
        p_values = scipy_stats.t.sf(np.abs(t_stats), df) * 2

        # R²
        ss_res = np.sum(residuals ** 2)
        ss_tot = np.sum((portfolio_returns - np.mean(portfolio_returns)) ** 2)
        r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        adj_r_squared = (
            1.0 - (1.0 - r_squared) * (n - 1) / (n - p - 1)
            if n > p + 1
            else 0.0
        )

        # 拆解
        alpha_daily = beta[0]
        alpha = alpha_daily * 252
        alpha_tstat = t_stats[0]
        alpha_pvalue = p_values[0]
        alpha_significant = abs(alpha_tstat) > 1.96

        betas = {valid_names[i]: float(beta[i + 1]) for i in range(p)}
        beta_tstats = {valid_names[i]: float(t_stats[i + 1]) for i in range(p)}
        beta_pvalues = {valid_names[i]: float(p_values[i + 1]) for i in range(p)}

        logger.debug(
            f"FactorAttribution: alpha={alpha:.6f}, "
            f"R²={r_squared:.4f}, adj_R²={adj_r_squared:.4f}, "
            f"n={n}, p={p}"
        )

        return FactorAttributionReport(
            alpha=float(alpha),
            alpha_tstat=float(alpha_tstat),
            alpha_pvalue=float(alpha_pvalue),
            alpha_significant=alpha_significant,
            betas=betas,
            beta_tstats=beta_tstats,
            beta_pvalues=beta_pvalues,
            r_squared=float(r_squared),
            adj_r_squared=float(adj_r_squared),
            n_observations=n,
            n_factors=p,
            factor_names=valid_names,
        )
