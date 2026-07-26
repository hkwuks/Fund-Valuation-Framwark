"""回测后分析服务 — 整合 5 个分析模块为统一接口

提供单次调用返回过拟合/显著性/Monte Carlo/状态检测/因子归因的能力。
"""
from __future__ import annotations

from typing import Dict, List, Optional
import numpy as np
from loguru import logger

from .overfitting import OverfittingDetector
from .significance import SignificanceTester
from .monte_carlo import MonteCarloEngine, MonteCarloReport
from .regime_detector import RegimeDetector, RegimeReport
from .factor_attribution import FactorAttribution, FactorAttributionReport


class BacktestAnalysisProvider:
    """回测后分析提供者 — 将分析模块的输出整合为统一 dict"""

    def __init__(self):
        self._overfitting = OverfittingDetector()
        self._significance = SignificanceTester()
        self._monte_carlo = MonteCarloEngine()
        self._regime = RegimeDetector()
        self._attribution = FactorAttribution()

    def analyze(
        self,
        daily_returns: List[float],
        sharpe: float,
        years: float,
        total_return: float,
        total_trades: int,
        n_simulations: int = 1000,
        factor_returns: Optional[Dict[str, List[float]]] = None,
    ) -> dict:
        """对回测结果运行全部分析，返回统一 dict。"""
        ret = np.array(daily_returns, dtype=float)
        result: dict = {"has_analysis": False}

        # 1. 过拟合检测
        try:
            of = self._overfitting.report(ret, sharpe, years)
            result["overfitting"] = {
                "deflated_sharpe": round(of.deflated_sharpe, 4),
                "min_btl_years": round(of.min_btl_years, 2),
                "actual_years": round(of.actual_years, 2),
                "min_btl_warning": of.min_btl_warning,
                "shuffle_p_value": round(of.shuffle_p_value, 4),
                "is_significant": of.is_significant,
                "total_trials": of.total_attempts,
            }
        except Exception as e:
            result["overfitting"] = {"error": str(e)}

        # 2. 显著性检验
        try:
            sr = self._significance.test(ret)
            result["significance"] = {
                "sharpe": round(sr.sharpe, 4),
                "p_value": round(sr.p_value, 4),
                "ci_lower": round(sr.ci_lower, 4),
                "ci_upper": round(sr.ci_upper, 4),
                "is_significant": sr.is_significant,
            }
        except Exception as e:
            result["significance"] = {"error": str(e)}

        # 3. Monte Carlo 模拟
        try:
            mc = self._monte_carlo.run(ret.tolist(), n_simulations=n_simulations)
            result["monte_carlo"] = {
                "n_simulations": mc.n_simulations,
                "return_pct": mc.return_pct,
                "sharpe_ratio": mc.sharpe_ratio,
                "max_drawdown_pct": mc.max_drawdown_pct,
                "probability_of_loss": mc.probability_of_loss,
            }
        except Exception as e:
            result["monte_carlo"] = {"error": str(e)}

        # 4. 市场状态检测
        try:
            rr = self._regime.detect(ret)
            result["regime"] = {
                "n_regimes": rr.n_regimes,
                "warning": rr.warning,
                "regimes": [
                    {
                        "label": r.label,
                        "duration_days": r.duration_days,
                        "ann_return": round(r.ann_return, 4),
                        "ann_vol": round(r.ann_vol, 4),
                        "sharpe": round(r.sharpe, 4),
                    }
                    for r in rr.regimes
                ],
            }
        except Exception as e:
            result["regime"] = {"error": str(e)}

        # 5. 因子归因 (需额外因子收益率数据)
        if factor_returns and len(factor_returns) > 0:
            try:
                fa = self._attribution.run(ret, factor_returns)
                result["factor_attribution"] = {
                    "alpha": round(fa.alpha, 6),
                    "alpha_tstat": round(fa.alpha_tstat, 4),
                    "alpha_pvalue": round(fa.alpha_pvalue, 4),
                    "alpha_significant": fa.alpha_significant,
                    "betas": fa.betas,
                    "beta_tstats": fa.beta_tstats,
                    "r_squared": round(fa.r_squared, 4),
                    "adj_r_squared": round(fa.adj_r_squared, 4),
                }
            except Exception as e:
                result["factor_attribution"] = {"error": str(e)}

        result["has_analysis"] = True
        return result


analysis_provider = BacktestAnalysisProvider()
