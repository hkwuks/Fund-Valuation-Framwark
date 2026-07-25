"""
Brinson 绩效归因 — 将超额收益分解为配置效应、选股效应和交互效应。

参考资料:
    Brinson, Hood & Beebower (1986) "Determinants of Portfolio Performance"
    Carino (1999) "Combining Attribution Effects Over Time"
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List

__all__ = ["BrinsonPeriod", "BrinsonReport", "BrinsonAttribution"]


@dataclass
class BrinsonPeriod:
    """单个归因周期的结果。"""

    period: str
    portfolio_weight: Dict[str, float]
    portfolio_return: float
    benchmark_weight: Dict[str, float]
    benchmark_return: float
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    excess_return: float
    sector_details: Dict[str, Dict] = field(default_factory=dict)


@dataclass
class BrinsonReport:
    """多周期归因报告。"""

    periods: List[BrinsonPeriod]
    total_excess: float
    total_allocation: float
    total_selection: float
    total_interaction: float
    carino_linked: bool
    n_periods: int


class BrinsonAttribution:
    """Brinson 绩效归因分析器。"""

    @staticmethod
    def _normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
        """将权重归一化，使其总和为 1.0。"""
        total = sum(weights.values())
        if total == 0.0:
            return {}
        return {k: v / total for k, v in weights.items()}

    @staticmethod
    def _all_sectors(
        portfolio_weights: Dict[str, float],
        benchmark_weights: Dict[str, float],
    ) -> List[str]:
        """取两市的所有行业并集。"""
        return list(set(list(portfolio_weights.keys()) + list(benchmark_weights.keys())))

    def attribute_single_period(
        self,
        portfolio_weights: Dict[str, float],
        portfolio_returns: Dict[str, float],
        benchmark_weights: Dict[str, float],
        benchmark_returns: Dict[str, float],
    ) -> BrinsonPeriod:
        """单周期 Brinson 分解。

        - 配置效应: (w_p - w_b) * R_b
        - 选股效应: w_b * (R_p - R_b)
        - 交互效应: (w_p - w_b) * (R_p - R_b)
        - 超额收益 = 配置 + 选股 + 交互 ≈ 组合收益 - 基准收益
        """
        if not portfolio_weights or not benchmark_weights:
            raise ValueError("权重字典不能为空")

        pw = self._normalize_weights(portfolio_weights)
        bw = self._normalize_weights(benchmark_weights)
        sectors = self._all_sectors(pw, bw)

        total_portfolio = 0.0
        total_benchmark = 0.0
        allocation_effect = 0.0
        selection_effect = 0.0
        interaction_effect = 0.0
        sector_details = {}

        for s in sectors:
            w_p = pw.get(s, 0.0)
            w_b = bw.get(s, 0.0)
            r_p = portfolio_returns.get(s, 0.0)
            r_b = benchmark_returns.get(s, 0.0)

            alloc = (w_p - w_b) * r_b
            sel = w_b * (r_p - r_b)
            inter = (w_p - w_b) * (r_p - r_b)

            total_portfolio += w_p * r_p
            total_benchmark += w_b * r_b
            allocation_effect += alloc
            selection_effect += sel
            interaction_effect += inter

            sector_details[s] = {
                "allocation": alloc,
                "selection": sel,
                "interaction": inter,
                "portfolio_weight": w_p,
                "portfolio_return": r_p,
                "benchmark_weight": w_b,
                "benchmark_return": r_b,
            }

        excess = total_portfolio - total_benchmark
        effects_sum = allocation_effect + selection_effect + interaction_effect

        # 验证分解的可加性
        if abs(effects_sum - excess) > 1e-6:
            raise ValueError(
                f"分解不一致: 效应之和 {effects_sum:.8f} != 超额收益 {excess:.8f}"
            )

        return BrinsonPeriod(
            period="",
            portfolio_weight=pw,
            portfolio_return=total_portfolio,
            benchmark_weight=bw,
            benchmark_return=total_benchmark,
            allocation_effect=allocation_effect,
            selection_effect=selection_effect,
            interaction_effect=interaction_effect,
            excess_return=excess,
            sector_details=sector_details,
        )

    @staticmethod
    def _carino_k(r_t: float, b_t: float) -> float:
        """计算 Carino 链接系数 k_t。

        k_t = (ln(1+R_t) - ln(1+B_t)) / (R_t - B_t)
        若 R_t ≈ B_t，用极限近似 1 / (1 + R_t)。
        """
        if abs(r_t - b_t) < 1e-10:
            return 1.0 / (1.0 + r_t)
        return (math.log(1.0 + r_t) - math.log(1.0 + b_t)) / (r_t - b_t)

    def attribute_multi_period(
        self,
        periods_data: List[Dict],
    ) -> BrinsonReport:
        """多周期归因（含 Carino 链接）。

        periods_data 每项:
            {
                "period": "2024-Q1",
                "portfolio_weights": {...},
                "portfolio_returns": {...},
                "benchmark_weights": {...},
                "benchmark_returns": {...},
            }
        """
        if not periods_data:
            raise ValueError("周期数据不能为空")

        periods = []
        for p in periods_data:
            period_result = self.attribute_single_period(
                portfolio_weights=p["portfolio_weights"],
                portfolio_returns=p["portfolio_returns"],
                benchmark_weights=p["benchmark_weights"],
                benchmark_returns=p["benchmark_returns"],
            )
            period_result.period = p["period"]
            periods.append(period_result)

        if len(periods) == 1:
            period = periods[0]
            return BrinsonReport(
                periods=periods,
                total_excess=period.excess_return,
                total_allocation=period.allocation_effect,
                total_selection=period.selection_effect,
                total_interaction=period.interaction_effect,
                carino_linked=False,
                n_periods=1,
            )

        # Carino 链接
        k_values = []
        for p in periods:
            k_t = self._carino_k(p.portfolio_return, p.benchmark_return)
            k_values.append(k_t)

        K = sum(k_values)

        total_allocation = 0.0
        total_selection = 0.0
        total_interaction = 0.0

        for i, p in enumerate(periods):
            weight = k_values[i] / K
            total_allocation += weight * p.allocation_effect
            total_selection += weight * p.selection_effect
            total_interaction += weight * p.interaction_effect

        # 链接后超额收益 = 链接后效应之和
        total_excess = total_allocation + total_selection + total_interaction

        return BrinsonReport(
            periods=periods,
            total_excess=total_excess,
            total_allocation=total_allocation,
            total_selection=total_selection,
            total_interaction=total_interaction,
            carino_linked=True,
            n_periods=len(periods),
        )
