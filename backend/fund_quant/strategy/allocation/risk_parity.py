"""约束风险平价策略 — Ledoit-Wolf协方差 + SLSQP求解 + 债券波动率放大"""

from typing import Optional, List, Dict, Tuple
import numpy as np
from loguru import logger

from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class RiskParityStrategy(FundStrategyBase):
    """约束风险平价策略: 使各基金风险贡献相等"""
    strategy_name = "risk_parity"
    strategy_type = "allocation"
    description = "基于约束风险平价(Constrained Risk Parity)的组合配置策略, Ledoit-Wolf协方差+SLSQP求解"
    default_params = {
        "lookback_years": 3,
        "shrinkage": "auto",
        "rebalance_freq": "monthly",
        "max_weight": 0.4,
        "min_weight": 0.05,
        "min_weight_bond": 0.10,
        "bond_vol_multiplier": "auto",
        "fee_penalty_threshold": 0.02,
    }
    applicable_fund_types = []
    min_history_days = 365

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        return []

    def optimize(self, fund_codes: List[str],
                 params: Optional[dict] = None,
                 nav_series: Optional[Dict[str, List[float]]] = None) -> dict:
        """组合优化 — 约束风险平价

        nav_series: {fund_code: [净值...]} — 引擎回测传入截至当日的净值序列，
                    避免走 DB 全量历史造成前视偏差。None 时从 DB 拉取。
        """
        from ...data.storage import get_nav_history

        if params:
            self.params.update(params)

        if len(fund_codes) < 2:
            return {"strategy": self.strategy_name, "fund_codes": fund_codes,
                    "weights": {code: 1.0 for code in fund_codes}, "status": "single_fund"}

        # 截断 lookback（避免传入过多历史导致权重趋同）
        lb = self.params.get("lookback_years", 3) * 252

        # 1. 获取各基金收益率序列
        all_returns = {}
        for code in fund_codes:
            if nav_series and code in nav_series:
                nav_values = [float(v) for v in nav_series[code]
                              if v and v > 0][-lb:]
            else:
                navs = get_nav_history(code)
                if len(navs) < 60:
                    continue
                nav_values = [r.get("nav", 0) for r in navs if r.get("nav") and r["nav"] > 0]
            if len(nav_values) > 20:
                returns = np.diff(np.array(nav_values, dtype=np.float64)) / np.array(nav_values[:-1], dtype=np.float64)
                all_returns[code] = returns

        if len(all_returns) < 2:
            return {"strategy": self.strategy_name, "fund_codes": fund_codes,
                    "weights": {code: 1.0 / len(fund_codes) for code in fund_codes},
                    "status": "insufficient_data"}

        # 2. 对齐长度
        codes = list(all_returns.keys())
        min_len = min(len(r) for r in all_returns.values())
        aligned = np.column_stack([all_returns[c][-min_len:] for c in codes])

        # 3. Ledoit-Wolf协方差估计
        cov = self._ledoit_wolf_covariance(aligned)

        # 4. 债券波动率放大
        cov = self._apply_bond_vol_multiplier(cov, codes)

        # 5. SLSQP求解风险平价
        n = len(codes)
        max_w = self.params["max_weight"]
        min_w = self.params["min_weight"]

        # 自适应权重范围：基金数多时放宽约束，否则风险平价无法拉开差距
        # min_weight*n 必须 <= 1 才存在可行解
        if min_w * n > 1.0:
            min_w = 0.9 / n
        # 基金数多时降低 max_weight 避免不可行（max_weight*n 必须 >= 1）
        if max_w * n < 1.0:
            max_w = 1.0 / n
        # 基金数 >= 10 时放宽上限，否则风险平价退化为近似等权
        if n >= 10:
            max_w = max(max_w, 0.6)

        bounds = [(min_w, max_w) for _ in range(n)]
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]

        # 初始解: 逆方差加权（比等权更接近风险平价解，避免 SLSQP 收敛到等权平坦区）
        diag_var = np.diag(cov)
        if np.all(diag_var > 0):
            w0 = (1.0 / diag_var)
            w0 = w0 / np.sum(w0)
            w0 = np.clip(w0, min_w, max_w)
            w0 = w0 / np.sum(w0)
        else:
            w0 = np.array([1.0 / n] * n)

        try:
            from scipy.optimize import minimize

            def risk_parity_objective(w, cov):
                """风险平价目标函数: 最小化各资产风险贡献的方差"""
                portfolio_var = w @ cov @ w
                if portfolio_var <= 0:
                    return 1e10
                # 边际风险贡献: RC_i = w_i * (Σw)_i
                mrc = cov @ w
                rc = w * mrc
                # 使所有RC相等
                target = np.mean(rc)
                return np.sum((rc - target) ** 2)

            result = minimize(
                risk_parity_objective,
                w0,
                args=(cov,),
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"ftol": 1e-8, "maxiter": 1000},
            )

            if result.success:
                weights = result.x
            else:
                logger.warning(f"风险平价求解收敛失败: {result.message}, 回退等权")
                weights = w0

        except ImportError:
            logger.warning("scipy不可用, 使用简化等权")
            weights = w0

        # 6. 归一化
        weights = np.clip(weights, min_w, max_w)
        weights = weights / np.sum(weights)

        # 计算各基金风险贡献
        port_var = weights @ cov @ weights
        mrc = cov @ weights
        rc = weights * mrc
        rc_pct = rc / max(port_var, 1e-10) * 100  # 百分比

        weight_dict = {code: round(float(w), 4) for code, w in zip(codes, weights)}
        risk_dict = {code: round(float(r), 4) for code, r in zip(codes, rc_pct)}

        return {
            "strategy": self.strategy_name,
            "method": "risk_parity_slsqp",
            "fund_codes": codes,
            "weights": weight_dict,
            "risk_contributions": risk_dict,
            "portfolio_volatility": round(float(np.sqrt(port_var) * np.sqrt(252)), 6),
            "status": "success",
        }

    @staticmethod
    def _ledoit_wolf_covariance(X: np.ndarray) -> np.ndarray:
        """Ledoit-Wolf压缩估计协方差矩阵"""
        n, p = X.shape
        if n < 2:
            return np.cov(X, rowvar=False) if p > 1 else np.array([[np.var(X)]])

        try:
            from sklearn.covariance import LedoitWolf
            lw = LedoitWolf().fit(X)
            return lw.covariance_
        except ImportError:
            # 无scikit-learn时使用样本协方差 + 压缩
            sample_cov = np.cov(X, rowvar=False)
            if n < p:
                # 高维: 压缩到单位矩阵
                shrinkage = 0.5
                target = np.eye(p) * np.trace(sample_cov) / p
                return (1 - shrinkage) * sample_cov + shrinkage * target
            return sample_cov

    def _apply_bond_vol_multiplier(self, cov: np.ndarray,
                                    codes: List[str]) -> np.ndarray:
        """债券波动率放大"""
        mult = self.params.get("bond_vol_multiplier", "auto")
        if mult == "none" or mult == 1.0:
            return cov

        cov_copy = cov.copy()
        for i, code in enumerate(codes):
            # 检查是否为债券基金
            from ...data.storage import get_fund_meta
            meta = get_fund_meta(code)
            if meta and meta.get("fund_type") in ("bond", "money"):
                factor = 2.0 if mult == "auto" else float(mult)
                factor = min(factor, 5.0)  # max_multiplier=5.0
                cov_copy[i, i] *= (factor ** 2)
                # 协方差也放大
                for j in range(len(codes)):
                    if i != j:
                        cov_copy[i, j] *= factor

        return cov_copy


StrategyRegistry.register(RiskParityStrategy)
