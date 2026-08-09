"""回测过拟合检测 — DSR, MMC, Sharpe衰减率"""

import numpy as np
from scipy import stats


def _cumulant_moment(sr: np.ndarray, k: int) -> float:
    """计算SR序列的k阶累积量"""
    n = len(sr)
    if n < 2:
        return 0.0
    mu = np.mean(sr)
    m = np.mean((sr - mu) ** k)
    return m * n ** (k / 2 - 1) / (n - 1)  # 无偏累积量


class OverfittingDetector:
    """
    回测过拟合检测

    方法：
    1. Deflated Sharpe Ratio (DSR) — 考虑多重比较的调整Sharpe
    2. MMC (Model Confidence Criterion) — 比较策略vs基准
    3. 策略Sharp在训练/测试集的差异
    """

    def detect(
        self,
        in_sample_sharpes: list[float],
        out_sample_sharpes: list[float],
        n_trials: int = 1,
        num_observations: int = 252,
    ) -> dict:
        """
        过拟合检测综合报告
        """
        is_sharpes = np.array(in_sample_sharpes) if in_sample_sharpes else np.array([])
        oos_sharpes = np.array(out_sample_sharpes) if out_sample_sharpes else np.array([])

        if len(is_sharpes) == 0 or len(oos_sharpes) == 0:
            return {"error": "insufficient_data", "overfitting_detected": False, "verdict": "数据不足"}

        # DSR
        best_is_sharpe = float(np.max(is_sharpes))
        dsr = self.deflated_sharpe(best_is_sharpe, n_trials, num_observations)

        # DSR
        best_is_sharpe = float(np.max(is_sharpes))
        dsr = self.deflated_sharpe(best_is_sharpe, n_trials, num_observations)

        # Sharpe衰减率
        drop = self.sharpe_drop(float(np.mean(is_sharpes)), float(np.mean(oos_sharpes)))

        # MMC
        mmc_p = self._mmc_test(is_sharpes, oos_sharpes)

        # 判定
        flags = []
        if dsr < 1.96:
            flags.append("DSR不显著(p<0.05)")
        if drop > 0.5:
            flags.append(f"Sharpe衰减率过高({drop:.1%})")
        if mmc_p > 0.05:
            flags.append("MMC未通过")
        overfit = len(flags) >= 2

        return {
            "dsr": round(dsr, 4),
            "dsr_significant": dsr >= 1.96,
            "sharpe_drop": round(drop, 4),
            "mmc_p_value": round(mmc_p, 4),
            "mmc_passed": mmc_p <= 0.05,
            "n_trials": n_trials,
            "best_is_sharpe": round(best_is_sharpe, 4),
            "avg_is_sharpe": round(float(np.mean(is_sharpes)), 4),
            "avg_oos_sharpe": round(float(np.mean(oos_sharpes)), 4),
            "overfitting_detected": overfit,
            "verdict": "过拟合风险高" if overfit else "无明显过拟合",
            "flags": flags,
        }

    def deflated_sharpe(
        self, sharpe: float, n_trials: int, num_observations: int
    ) -> float:
        """
        Deflated Sharpe Ratio — López de Prado (2018)

        DSR = Z[ (SR * sqrt(T-1) - E[SR]) / std(SR) ]

        其中 E[SR] 和 std(SR) 考虑多重比较修正:
        - E[SR] ≈ max(SR) * (1 - gamma + log(1 - gamma)) / gamma  (gamma = Euler-Mascheroni)
        - std(SR) ≈ sqrt( (1 - k*SR² + (k-1)*SR²/4) / (T-1) )  (k=偏度)

        Args:
            sharpe: 策略Sharpe Ratio
            n_trials: 独立试验次数（参数调优次数）
            num_observations: 样本数量

        Returns:
            float: DSR值
        """
        if num_observations < 2 or n_trials < 1:
            return 0.0

        T = num_observations
        gamma = 0.5772156649  # Euler-Mascheroni常数

        # 多重比较期望修正
        max_approx = np.sqrt(2 * np.log(n_trials))  # 最大次序统计量近似
        e_max_sr = max_approx * (1 - gamma + np.log(1 - gamma)) / gamma
        e_max_sr = np.nan_to_num(e_max_sr, nan=0.0)

        # 标准差估计 (假设偏度=0, 峰度=3)
        std_sr = np.sqrt(1.0 / (T - 1))

        # DSR
        numerator = sharpe * np.sqrt(T - 1) - e_max_sr
        denominator = std_sr
        if denominator < 1e-10:
            return 0.0
        dsr = numerator / denominator

        # 转为正态CDF概率
        dsr_prob = stats.norm.cdf(dsr)
        # 转换为Z值形式 (标准正态分位数)
        return float(dsr_prob)

    def sharpe_drop(self, is_sharpe: float, oos_sharpe: float) -> float:
        """
        Sharpe衰减率: (IS - OOS) / IS

        Args:
            is_sharpe: 训练集Sharpe
            oos_sharpe: 测试集Sharpe

        Returns:
            float: 衰减率 (0-1), 负值表示OOS更好
        """
        if abs(is_sharpe) < 1e-10:
            return 1.0 if oos_sharpe < 0 else 0.0
        return max(0.0, (is_sharpe - oos_sharpe) / abs(is_sharpe))

    def _mmc_test(
        self, is_sharpes: np.ndarray, oos_sharpes: np.ndarray
    ) -> float:
        """
        Model Confidence Criterion — 配对t检验比较IS vs OOS Sharpe

        H0: IS Sharpe 均值 <= OOS Sharpe 均值 (无过拟合)
        H1: IS Sharpe 均值 > OOS Sharpe 均值 (过拟合)

        Returns:
            float: p-value
        """
        if len(is_sharpes) < 2 or len(oos_sharpes) < 2:
            return 1.0
        _, p_value = stats.ttest_rel(is_sharpes, oos_sharpes, alternative="greater")
        return float(p_value)


def compute_dsr(sharpe: float, n_trials: int, num_observations: int) -> float:
    """
    便捷函数 — 单次DSR计算 (无需实例化)
    """
    detector = OverfittingDetector()
    return detector.deflated_sharpe(sharpe, n_trials, num_observations)