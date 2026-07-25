"""
统计显著性检验 — Bootstrap 法检验 Sharpe 比率是否显著异于零
"""

import math
from dataclasses import dataclass

import numpy as np
from loguru import logger

__all__ = ["SignificanceReport", "SignificanceTester"]


@dataclass
class SignificanceReport:
    """Bootstrap 显著性检验报告"""

    sharpe: float
    p_value: float
    ci_lower: float
    ci_upper: float
    is_significant: bool
    n_bootstrap: int


class SignificanceTester:
    """Bootstrap 法 Sharpe 比率显著性检验

    通过重采样日收益率序列构建零分布（H0: true Sharpe = 0），
    计算观测 Sharpe 在零分布中的位置作为 p-value。
    """

    def test(
        self,
        daily_returns: np.ndarray,
        n_bootstrap: int = 1000,
        seed: int = 42,
    ) -> SignificanceReport:
        """Bootstrap 检验 Sharpe 比率显著性

        Args:
            daily_returns: 日收益率序列（小数形式）
            n_bootstrap: Bootstrap 重采样次数
            seed: 随机种子

        Returns:
            SignificanceReport: 包含观测 Sharpe、p-value、95% 置信区间

        Raises:
            ValueError: 序列长度不足或 n_bootstrap 无效
        """
        returns = np.asarray(daily_returns, dtype=float)

        if len(returns) < 2:
            raise ValueError(
                f"daily_returns 长度不足 ({len(returns)}), 至少需要 2 个样本"
            )
        if n_bootstrap <= 0:
            raise ValueError(f"n_bootstrap 必须为正整数, 得到 {n_bootstrap}")

        observed = self._sharpe(returns)
        rng = np.random.RandomState(seed)
        n = len(returns)
        ann_factor = math.sqrt(252)

        # 步骤 1: 构建零分布（H0: mean=0，重采样后中心化）
        null_sharpes = np.empty(n_bootstrap)
        for i in range(n_bootstrap):
            sampled = rng.choice(returns, size=n, replace=True)
            centered = sampled - np.mean(returns)
            mean_c = float(np.mean(centered))
            std_c = float(np.std(centered, ddof=1))
            null_sharpes[i] = mean_c / std_c * ann_factor if std_c > 1e-10 else 0.0

        # p-value = P(null >= observed) — 单侧检验 Sharpe > 0
        p_value = float(np.mean(null_sharpes >= observed))

        # 步骤 2: 95% CI — 从非中心化 bootstrap 分布计算百分位数
        boot_sharpes = np.empty(n_bootstrap)
        for i in range(n_bootstrap):
            sampled = rng.choice(returns, size=n, replace=True)
            mean_s = float(np.mean(sampled))
            std_s = float(np.std(sampled, ddof=1))
            boot_sharpes[i] = mean_s / std_s * ann_factor if std_s > 1e-10 else 0.0

        ci_lower = float(np.percentile(boot_sharpes, 2.5))
        ci_upper = float(np.percentile(boot_sharpes, 97.5))

        logger.debug(
            f"SignificanceTest: sharpe={observed:.4f}, "
            f"p={p_value:.4f}, CI=[{ci_lower:.4f}, {ci_upper:.4f}]"
        )

        return SignificanceReport(
            sharpe=observed,
            p_value=p_value,
            ci_lower=ci_lower,
            ci_upper=ci_upper,
            is_significant=p_value < 0.05,
            n_bootstrap=n_bootstrap,
        )

    @staticmethod
    def _sharpe(returns: np.ndarray) -> float:
        """计算年化 Sharpe 比率（与 overfitting 模块一致）"""
        if len(returns) < 2:
            return 0.0
        mean_ret = float(np.mean(returns))
        std_ret = float(np.std(returns, ddof=1))
        if std_ret < 1e-10:
            return 0.0
        return mean_ret / std_ret * math.sqrt(252)
