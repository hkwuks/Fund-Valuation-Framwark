"""
过拟合检测 — Deflated Sharpe Ratio, MinBTL, 标签洗牌检验

基于 Bailey & Lopez de Prado (2014) 方法检测回测过拟合。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger
from scipy import stats

__all__ = ["OverfittingReport", "OverfittingDetector"]


@dataclass
class OverfittingReport:
    """过拟合检测报告"""

    total_attempts: int
    latest_sharpe: float
    deflated_sharpe: float
    min_btl_years: float
    actual_years: float
    min_btl_warning: str  # "" if ok, "WARNING: ..." if actual < min_btl
    shuffle_p_value: float
    is_significant: bool  # p < 0.05
    n_trials: int  # same as total_attempts


class OverfittingDetector:
    """Bailey & Lopez de Prado (2014) 过拟合检测器

    功能:
      - Deflated Sharpe Ratio (收缩夏普比)
      - Minimum Backtest Length (最小回测长度)
      - Label shuffling permutation test (标签洗牌检验)
      - 试验日志记录
    """

    def __init__(self) -> None:
        self._history: List[Dict] = []

    # ── 公共方法 ─────────────────────────────────────

    def record(self, params: dict, metrics: dict) -> None:
        """记录一次回测尝试的参数与指标"""
        self._history.append({"params": params, "metrics": metrics})
        logger.debug(f"OverfittingDetector: recorded attempt #{len(self._history)}")

    def adjusted_sharpe(self, sharpe: float) -> float:
        """Deflated Sharpe Ratio (简化版): SR - E[max Sharpe | N]"""
        n_trials = len(self._history)
        if n_trials == 0:
            return sharpe  # 无试验记录, 无需 deflate
        return sharpe - self._e_max_sharpe(n_trials)

    def min_btl(
        self,
        sharpe: float,
        skew: float = 0.0,
        kurt: float = 3.0,
    ) -> float:
        """Minimum backtest length in years (Bailey & Lopez de Prado, 2014)

        基于 N 次独立试验的预期最大 Sharpe 和目标 Sharpe 计算所需年数。

        Args:
            sharpe: 目标年化 Sharpe
            skew: 收益率偏度 (默认 0.0)
            kurt: 收益率峰度 (默认 3.0, 正态)

        Returns:
            min_btl_years: 所需最小回测年数, sharpe <= 0 时返回 0
        """
        n_trials = max(len(self._history), 1)
        if sharpe <= 0:
            return 0.0
        return (self._e_max_sharpe(n_trials) / sharpe) ** 2

    def shuffle_test(
        self,
        daily_returns: np.ndarray,
        n_shuffles: int = 100,
    ) -> float:
        """标签洗牌置换检验 — 返回 p-value

        使用符号置换 (randomization test): 对每个日收益率独立随机翻转符号,
        生成零分布 (原假设: 收益率分布对称于 0), 比较观测 Sharpe 与零分布。
        p = (置换后 Sharpe >= 观测 Sharpe 的次数) / 总次数。
        """
        returns_arr = np.asarray(daily_returns, dtype=float)
        observed_sharpe = self._sharpe_from_returns(returns_arr)
        rng = np.random.RandomState(42)
        n = len(returns_arr)
        count = 0

        for _ in range(n_shuffles):
            signs = rng.choice([-1, 1], size=n)
            perm_sharpe = self._sharpe_from_returns(returns_arr * signs)
            if perm_sharpe >= observed_sharpe:
                count += 1

        return count / n_shuffles

    def report(
        self,
        daily_returns: np.ndarray,
        sharpe: float,
        years: float,
    ) -> OverfittingReport:
        """生成完整过拟合检测报告"""
        returns_arr = np.asarray(daily_returns, dtype=float)
        n_trials = len(self._history)
        deflated = self.adjusted_sharpe(sharpe)
        btl_years = self.min_btl(sharpe)

        # MinBTL 警告
        warning = ""
        if btl_years > years:
            warning = (
                f"WARNING: actual backtest period ({years:.1f}y) "
                f"< MinBTL ({btl_years:.1f}y)"
            )

        p = self.shuffle_test(returns_arr)

        return OverfittingReport(
            total_attempts=n_trials,
            latest_sharpe=sharpe,
            deflated_sharpe=deflated,
            min_btl_years=btl_years,
            actual_years=years,
            min_btl_warning=warning,
            shuffle_p_value=p,
            is_significant=p < 0.05,
            n_trials=n_trials,
        )

    # ── 内部方法 ─────────────────────────────────────

    @staticmethod
    def _e_max_sharpe(n_trials: int) -> float:
        """E[max Sharpe | N] — N 次独立试验的预期最大 Sharpe"""
        gamma = 0.5772  # Euler-Mascheroni constant
        n = max(n_trials, 2)
        inv1 = stats.norm.ppf(1 - 1 / n)
        inv2 = stats.norm.ppf(1 - 1 / n * math.exp(-1))
        return (1 - gamma) * inv1 + gamma * inv2

    @staticmethod
    def _sharpe_from_returns(returns: np.ndarray) -> float:
        """从日收益率序列计算年化 Sharpe"""
        if len(returns) < 2:
            return 0.0
        mean_ret = float(np.mean(returns))
        std_ret = float(np.std(returns, ddof=1))
        if std_ret < 1e-10:
            return 0.0
        ann_factor = math.sqrt(252)
        return mean_ret / std_ret * ann_factor


# 单例实例 (可选, 与代码库其他模块保持一致)
overfitting_detector = OverfittingDetector()
