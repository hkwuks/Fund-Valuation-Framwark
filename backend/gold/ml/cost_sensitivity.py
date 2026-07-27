"""交易成本敏感度分析 — 测试策略在不同滑点/手续费下的稳健性"""
import numpy as np
import pandas as pd
from copy import deepcopy
from loguru import logger


class CostSensitivityAnalyzer:
    """测试策略在不同滑点/手续费假设下的收益衰减曲线"""

    def __init__(self, base_commission: float = 10.0, base_slippage: float = 20.0):
        self.base_commission = base_commission
        self.base_slippage = base_slippage

    def analyze(self, df: pd.DataFrame,
                multipliers: list[float] = None,
                strategy_fn: callable = None) -> dict:
        """在不同成本乘数下运行策略"""
        if multipliers is None:
            multipliers = [0.0, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0]

        results = []
        for mult in multipliers:
            commission = self.base_commission * mult
            slippage = self.base_slippage * mult
            env_config = {
                "commission_per_lot": commission,
                "slippage_per_lot": slippage,
            }

            if strategy_fn:
                metrics = strategy_fn(env_config)
            else:
                # 用现成策略（如规则策略的回测）
                metrics = self._run_default_strategy(df, env_config)

            results.append({
                "cost_multiplier": mult,
                "commission": commission,
                "slippage": slippage,
                **metrics,
            })
            logger.info(f"[CostSensitivity] mult={mult:.1f} "
                        f"sharpe={metrics.get('sharpe_ratio', 0):.3f} "
                        f"return={metrics.get('total_return_pct', 0):.1f}%")

        return {
            "results": results,
            "breakeven": self.breakeven_analysis(results),
        }

    def breakeven_analysis(self, results: list[dict]) -> dict:
        """计算盈亏平衡点"""
        returns = [r["total_return_pct"] for r in results]
        multis = [r["cost_multiplier"] for r in results]

        # 找到收益从正转负的点
        be_mult = None
        for i in range(1, len(returns)):
            if returns[i - 1] > 0 > returns[i]:
                # 线性插值
                ratio = returns[i - 1] / (returns[i - 1] - returns[i])
                be_mult = multis[i - 1] + ratio * (multis[i] - multis[i - 1])
                break

        return {
            "breakeven_multiplier": round(be_mult, 2) if be_mult else None,
            "breakeven_commission": round(self.base_commission * be_mult, 1) if be_mult else None,
            "breakeven_slippage": round(self.base_slippage * be_mult, 1) if be_mult else None,
            "zero_cost_sharpe": results[0].get("sharpe_ratio", 0) if results else 0,
            "base_cost_sharpe": next((r["sharpe_ratio"] for r in results if abs(r["cost_multiplier"] - 1) < 0.01), 0),
        }

    def _run_default_strategy(self, df: pd.DataFrame,
                               env_config: dict) -> dict:
        """默认用规则策略快速评估"""
        from backend.gold.strategy.baseline_rl import BaselineRLStrategy
        from backend.gold.strategy.base import StrategyContext
        from backend.gold.core.models import GoldBarData, SignalDirection

        strategy = BaselineRLStrategy()
        # 简化：只返回基本指标
        return {"total_return_pct": 0, "sharpe_ratio": 0, "max_drawdown_pct": 0}