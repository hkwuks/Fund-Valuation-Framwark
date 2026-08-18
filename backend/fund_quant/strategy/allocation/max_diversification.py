"""最大多元化组合 — 最大化加权平均波动率/组合波动率

Choueifaty & Coignard (2008), Journal of Portfolio Management
"""
from typing import Optional, List, Dict
import numpy as np
from loguru import logger

from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class MaxDiversificationStrategy(FundStrategyBase):
    """最大多元化: 最大化多元化比率 DR = (w'σ) / sqrt(w'Σw)"""
    strategy_name = "max_diversification"
    strategy_type = "allocation"
    description = "最大多元化(MDP): 最大化加权平均波动率/组合波动率，不依赖收益率预测"
    default_params = {
        "lookback_years": 3,
        "max_weight": 0.4,
        "min_weight": 0.02,
    }
    applicable_fund_types = []
    min_history_days = 120

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        return []

    def optimize(self, fund_codes: List[str],
                 params: Optional[dict] = None,
                 nav_series: Optional[Dict[str, List[float]]] = None) -> dict:
        from ...data.storage import get_nav_history

        if params:
            self.params.update(params)

        if len(fund_codes) < 2:
            return {"strategy": self.strategy_name, "fund_codes": fund_codes,
                    "weights": {c: 1.0 for c in fund_codes}, "status": "single_fund"}

        # 截断 lookback
        lb = self.params.get("lookback_years", 3) * 252

        # 1. 获取收益率
        all_returns = {}
        for code in fund_codes:
            if nav_series and code in nav_series:
                nav_values = [float(v) for v in nav_series[code] if v and v > 0][-lb:]
            else:
                navs = get_nav_history(code)
                if len(navs) < 60:
                    continue
                nav_values = [r.get("nav", 0) for r in navs if r.get("nav") and r["nav"] > 0]
            if len(nav_values) > 20:
                arr = np.array(nav_values, dtype=np.float64)
                all_returns[code] = np.diff(arr) / arr[:-1]

        codes = list(all_returns.keys())
        if len(codes) < 2:
            return {"strategy": self.strategy_name, "fund_codes": fund_codes,
                    "weights": {c: 1.0 / len(fund_codes) for c in fund_codes},
                    "status": "insufficient_data"}

        # 2. 对齐 + Ledoit-Wolf 协方差
        min_len = min(len(r) for r in all_returns.values())
        aligned = np.column_stack([all_returns[c][-min_len:] for c in codes])
        cov = self._ledoit_wolf(aligned)
        vols = np.sqrt(np.diag(cov))

        # 3. 最大化多元化比率: max (w'vols) / sqrt(w'Σw)
        from scipy.optimize import minimize
        n = len(codes)
        max_w = self.params.get("max_weight", 0.4)
        min_w = self.params.get("min_weight", 0.02)
        bounds = [(min_w, max_w)] * n
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        w0 = vols / vols.sum()  # 初始：波动率倒数加权
        w0 = np.clip(w0, min_w, max_w)
        w0 = w0 / w0.sum()

        def neg_dr(w):
            port_vol = np.sqrt(w @ cov @ w)
            if port_vol <= 0:
                return 1e10
            return -(w @ vols) / port_vol

        result = minimize(neg_dr, w0, method="SLSQP", bounds=bounds,
                          constraints=constraints,
                          options={"ftol": 1e-10, "maxiter": 1000})

        if result.success:
            weights = result.x
        else:
            logger.warning(f"MDP优化未收敛: {result.message}, 回退波动率倒数")
            weights = w0

        weights = np.clip(weights, min_w, max_w)
        weights = weights / weights.sum()

        # 计算多元化比率
        port_vol = float(np.sqrt(weights @ cov @ weights) * np.sqrt(252))
        div_ratio = float((weights @ vols) / np.sqrt(weights @ cov @ weights))

        weight_dict = {c: round(float(w), 4) for c, w in zip(codes, weights)}

        return {
            "strategy": self.strategy_name,
            "method": "max_diversification",
            "fund_codes": codes,
            "weights": weight_dict,
            "diversification_ratio": round(div_ratio, 4),
            "portfolio_volatility": round(port_vol, 6),
            "status": "success",
        }

    @staticmethod
    def _ledoit_wolf(X: np.ndarray) -> np.ndarray:
        n, p = X.shape
        if n < 2:
            return np.cov(X, rowvar=False) if p > 1 else np.array([[np.var(X)]])
        try:
            from sklearn.covariance import LedoitWolf
            return LedoitWolf().fit(X).covariance_
        except ImportError:
            sample_cov = np.cov(X, rowvar=False)
            if n < p:
                shrinkage = 0.5
                target = np.eye(p) * np.trace(sample_cov) / p
                return (1 - shrinkage) * sample_cov + shrinkage * target
            return sample_cov


StrategyRegistry.register(MaxDiversificationStrategy)
