"""
Monte Carlo 模拟 -- 对基金日收益率序列重采样，评估策略稳定性。

原理: bootstrap 日收益率序列，生成 N 条模拟路径，计算置信区间。
"""

import numpy as np
from dataclasses import dataclass
from typing import Dict, List

__all__ = ["MonteCarloReport", "MonteCarloEngine"]


@dataclass
class MonteCarloReport:
    """Monte Carlo 模拟结果报告"""

    n_simulations: int
    return_pct: Dict[str, float]         # mean, median, std, p5, p25, p75, p95, min, max, positive_ratio
    sharpe_ratio: Dict[str, float]       # mean, p5, p95, positive_ratio
    max_drawdown_pct: Dict[str, float]   # mean, p5, p95
    ulcer_index: Dict[str, float]        # mean, p5, p95
    probability_of_loss: float


class MonteCarloEngine:
    """Monte Carlo 模拟引擎 -- bootstrap 日收益率序列"""

    def run(
        self,
        daily_returns: List[float],
        n_simulations: int = 1000,
        n_periods: int = 252,
        seed: int = 42,
    ) -> MonteCarloReport:
        """
        对日收益率序列做 bootstrap 重采样，生成 N 条模拟路径并计算分布统计。

        Args:
            daily_returns: 日收益率序列（小数形式，如 0.01 表示 1%）
            n_simulations: 模拟路径数
            n_periods: 每条路径的采样天数
            seed: 随机种子

        Returns:
            MonteCarloReport: 包含各指标的百分位分布
        """
        if not daily_returns:
            raise ValueError("daily_returns 为空，无法进行 Monte Carlo 模拟")

        returns_arr = np.array(daily_returns, dtype=float)
        rng = np.random.RandomState(seed)
        rf = 0.025  # 年化无风险利率 2.5%，与 gold 版一致

        path_returns: List[float] = []
        path_sharpes: List[float] = []
        path_drawdowns: List[float] = []
        path_ulcers: List[float] = []

        for _ in range(n_simulations):
            # bootstrap: 有放回重采样日收益率
            sampled = rng.choice(returns_arr, size=n_periods, replace=True)

            # 净值曲线：初始资本为 1.0，累积复利
            equity = np.empty(n_periods + 1)
            equity[0] = 1.0
            np.multiply.accumulate(1.0 + sampled, out=equity[1:])

            total_ret = equity[-1] / equity[0] - 1

            # 年化收益率（对数法）
            ann_ret = (
                np.exp(np.log1p(total_ret) * 252 / n_periods) - 1
                if total_ret > -1
                else -1.0
            )

            # 年化波动率
            vol = float(np.std(sampled, ddof=1) * np.sqrt(252))
            sharpe = (ann_ret - rf) / vol if vol > 1e-10 else 0.0

            # 最大回撤
            peak = np.maximum.accumulate(equity)
            drawdown = (equity - peak) / peak  # 非正值
            max_dd = float(np.min(drawdown)) * 100

            # Ulcer Index = sqrt(mean(drawdown^2))
            ulcer = float(np.sqrt(np.mean(drawdown ** 2))) * 100

            path_returns.append(total_ret * 100)
            path_sharpes.append(sharpe)
            path_drawdowns.append(max_dd)
            path_ulcers.append(ulcer)

        arr_ret = np.array(path_returns)
        arr_sharpe = np.array(path_sharpes)
        arr_dd = np.array(path_drawdowns)
        arr_ulcer = np.array(path_ulcers)

        return MonteCarloReport(
            n_simulations=len(path_returns),
            return_pct={
                "mean": round(float(np.mean(arr_ret)), 2),
                "median": round(float(np.median(arr_ret)), 2),
                "std": round(float(np.std(arr_ret, ddof=1)), 2),
                "p5": round(float(np.percentile(arr_ret, 5)), 2),
                "p25": round(float(np.percentile(arr_ret, 25)), 2),
                "p75": round(float(np.percentile(arr_ret, 75)), 2),
                "p95": round(float(np.percentile(arr_ret, 95)), 2),
                "min": round(float(np.min(arr_ret)), 2),
                "max": round(float(np.max(arr_ret)), 2),
                "positive_ratio": round(float(np.mean(arr_ret > 0) * 100), 1),
            },
            sharpe_ratio={
                "mean": round(float(np.mean(arr_sharpe)), 2),
                "p5": round(float(np.percentile(arr_sharpe, 5)), 2),
                "p95": round(float(np.percentile(arr_sharpe, 95)), 2),
                "positive_ratio": round(float(np.mean(arr_sharpe > 0) * 100), 1),
            },
            max_drawdown_pct={
                "mean": round(float(np.mean(arr_dd)), 2),
                "p5": round(float(np.percentile(arr_dd, 5)), 2),
                "p95": round(float(np.percentile(arr_dd, 95)), 2),
            },
            ulcer_index={
                "mean": round(float(np.mean(arr_ulcer)), 2),
                "p5": round(float(np.percentile(arr_ulcer, 5)), 2),
                "p95": round(float(np.percentile(arr_ulcer, 95)), 2),
            },
            probability_of_loss=round(float(np.mean(arr_ret < 0) * 100), 1),
        )
