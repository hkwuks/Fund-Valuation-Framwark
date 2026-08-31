"""
统计显著性检验 — Bootstrap 法检验 Sharpe 比率是否显著异于零
"""

import math
from dataclasses import dataclass, field

import numpy as np
from loguru import logger

__all__ = ["SignificanceReport", "SignificanceTester"]

# 稀疏样本阈值 — 低于此样本数时 bootstrap 零分布不可靠
MIN_BOOTSTRAP_SAMPLES = 30
# 单次检验默认显著性水平
ALPHA = 0.05


@dataclass
class SignificanceReport:
    """Bootstrap 显著性检验报告"""

    sharpe: float
    p_value: float
    ci_lower: float
    ci_upper: float
    is_significant: bool
    n_bootstrap: int
    n_returns: int = 0
    insufficient: bool = False
    insufficiency_reason: str = ""
    adjusted_p_value: float = 0.0
    alpha: float = ALPHA
    is_significant_adjusted: bool = False
    multiple_comparison: str = "none"
    n_comparisons: int = 1
    method_notes: list = field(default_factory=list)


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
        n_comparisons: int = 1,
        method: str = "bonferroni",
    ) -> SignificanceReport:
        """Bootstrap 检验 Sharpe 比率显著性

        Args:
            daily_returns: 日收益率序列（小数形式）
            n_bootstrap: Bootstrap 重采样次数
            seed: 随机种子
            n_comparisons: 多重比较次数（>=1）。>1 时对 p-value 做校正，
                避免多个策略/窗口同时检验时出现虚假显著性。
            method: 多重比较校正方法 — "bonferroni" | "holm" | "none"

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
        if n_comparisons < 1:
            raise ValueError(f"n_comparisons 必须 >= 1, 得到 {n_comparisons}")
        if method not in ("bonferroni", "holm", "none"):
            raise ValueError(f"未知多重比较校正方法: {method}")

        observed = self._sharpe(returns)
        n = len(returns)
        rng = np.random.RandomState(seed)
        ann_factor = math.sqrt(252)

        # 稀疏样本保护: 样本太少时 bootstrap 零分布不可靠
        # p-value 直接置 1（拒绝显著性声明），并给出明确的不足元数据
        notes: list[str] = []
        if n < MIN_BOOTSTRAP_SAMPLES:
            logger.warning(
                f"SignificanceTest: 样本数 {n} < {MIN_BOOTSTRAP_SAMPLES}, "
                f"检验结果不可靠, 标记为 insufficient"
            )
            p_value = 1.0
            boot_sharpes = rng.choice(returns, size=(n_bootstrap, n), replace=True)
            boot_sharpes = boot_sharpes.mean(axis=1) / np.maximum(
                boot_sharpes.std(axis=1, ddof=1), 1e-10
            ) * ann_factor
            ci_lower = float(np.percentile(boot_sharpes, 2.5))
            ci_upper = float(np.percentile(boot_sharpes, 97.5))
            insufficient = True
            reason = (
                f"样本数 {n} 少于最小可靠样本数 {MIN_BOOTSTRAP_SAMPLES}, "
                f"bootstrap 零分布不可靠, 显著性判断不可信"
            )
            notes.append("样本不足, p-value 置 1")
        else:
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
            insufficient = False
            reason = ""

        # 多重比较校正 — 只在同时检验多个策略/窗口时生效
        is_significant = p_value < ALPHA
        adjusted_p = p_value
        if n_comparisons > 1 and method != "none":
            if method == "bonferroni":
                adjusted_p = min(p_value * n_comparisons, 1.0)
            elif method == "holm":
                adjusted_p = self._holm_adjust(p_value, n_comparisons)
            notes.append(f"多重比较校正: {method}, n_comparisons={n_comparisons}")

        is_significant_adjusted = adjusted_p < ALPHA

        logger.debug(
            f"SignificanceTest: sharpe={observed:.4f}, "
            f"p={p_value:.4f}, CI=[{ci_lower:.4f}, {ci_upper:.4f}]"
        )

        return SignificanceReport(
            sharpe=observed,
            p_value=p_value,
            ci_lower=ci_lower,
            ci_upper=ci_upper,
            is_significant=is_significant,
            n_bootstrap=n_bootstrap,
            n_returns=n,
            insufficient=insufficient,
            insufficiency_reason=reason,
            adjusted_p_value=adjusted_p,
            alpha=ALPHA,
            is_significant_adjusted=is_significant_adjusted,
            multiple_comparison=method if n_comparisons > 1 else "none",
            n_comparisons=n_comparisons,
            method_notes=notes,
        )

    @staticmethod
    def _holm_adjust(p_value: float, n_comparisons: int) -> float:
        """Holm-Bonferroni 逐步校正（单 p-value 场景退化为 Bonferroni）"""
        # 只有一个 p-value 时 Holm 与 Bonferroni 相同:
        # 最小秩 = 1 → adjusted = p * n
        return min(p_value * n_comparisons, 1.0)

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
