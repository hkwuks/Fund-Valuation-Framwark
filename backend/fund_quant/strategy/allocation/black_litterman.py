"""Black-Litterman 模型 — 均衡收益 + 观点驱动 + 均值-方差优化"""

from typing import Optional, List, Dict, Tuple
import numpy as np
from loguru import logger

from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class BlackLittermanStrategy(FundStrategyBase):
    """Black-Litterman配置策略: 均衡收益 + 观点融合 + 均值-方差优化"""
    strategy_name = "black_litterman"
    strategy_type = "allocation"
    description = "Black-Litterman模型: 均衡收益Π + 观点驱动后验 + 均值-方差优化"
    default_params = {
        "risk_aversion": 2.5,
        "tau": 0.05,
        "max_weight": 0.4,
        "min_weight": 0.05,
        "lookback_days": 756,
    }
    applicable_fund_types = []
    min_history_days = 365

    # ── 主入口 ────────────────────────────────────────────────

    def optimize(self, fund_codes: List[str],
                 params: Optional[dict] = None,
                 nav_series: Optional[Dict[str, List[float]]] = None) -> dict:
        """Black-Litterman组合优化

        1. 均衡收益Π = δ * Σ * w_mkt
        2. 观点融合 → 后验预期收益
        3. 均值-方差优化

        Args:
            fund_codes: 基金代码列表
            params: 覆盖默认参数
            nav_series: {fund_code: [净值...]} — 引擎回测传入截至当日的净值序列，
                        避免走 DB 全量历史造成前视偏差。None 时从 DB 拉取。

        Returns:
            dict: 含 weights, 状态信息的优化结果
        """
        from ...data.storage import get_nav_history, get_fund_meta

        if params:
            self.params.update(params)

        if len(fund_codes) < 2:
            return {"strategy": self.strategy_name, "fund_codes": fund_codes,
                    "weights": {c: 1.0 for c in fund_codes}, "status": "single_fund"}

        # 1. 获取收益率序列
        all_returns, codes = self._fetch_returns(fund_codes, nav_series)
        if len(codes) < 2:
            return {"strategy": self.strategy_name, "fund_codes": fund_codes,
                    "weights": {c: 1.0 / len(fund_codes) for c in fund_codes},
                    "status": "insufficient_data"}

        # 2. Ledoit-Wolf 协方差
        cov = self._ledoit_wolf_covariance(all_returns)

        # 3. 均衡收益 Π = δ * Σ * w_mkt
        w_mkt = self._market_weights(codes)
        delta = self.params["risk_aversion"]
        pi = delta * cov @ w_mkt

        # 4. 从 _state 提取信号视图
        self._state["_cov_matrix"] = cov  # 供 _build_views 复用
        views, has_views = self._build_views(codes)

        # 5. 后验预期收益
        if has_views and views["P"].shape[0] > 0:
            mu = self._posterior_return(cov, pi, views)
            method = "black_litterman"
        else:
            # 无观点 → 降级: 直接用均衡收益
            mu = pi
            method = "mean_variance"

        # 6. 均值-方差优化
        result = self._mean_variance_optimize(cov, mu, codes)
        result["method"] = method
        result["strategy"] = self.strategy_name
        result["fund_codes"] = codes

        if has_views:
            result["views_applied"] = True
            result["view_details"] = {
                "k": views["k"],
                "Q": [round(float(q), 6) for q in views["Q"]],
            }

        return result

    # ── 收益率获取 ────────────────────────────────────────────

    def _fetch_returns(self, fund_codes: List[str],
                       nav_series: Optional[Dict[str, List[float]]] = None
                       ) -> Tuple[np.ndarray, List[str]]:
        """获取对齐后的收益率矩阵

        nav_series: 提供时用其计算收益率（引擎回测传入截至当日的数据，无前视），
                    否则从 DB 拉取全量历史。
        """
        from ...data.storage import get_nav_history

        # 截断 lookback（避免过多历史导致均衡权重退化为等权）
        lb = self.params.get("lookback_days", 756)

        all_returns = {}
        for code in fund_codes:
            if nav_series and code in nav_series:
                nav_values = [float(v) for v in nav_series[code]
                              if v and v > 0][-lb:]
            else:
                navs = get_nav_history(code)
                if len(navs) < 60:
                    continue
                nav_values = [r.get("nav", 0) for r in navs
                              if r.get("nav") and r["nav"] > 0]
            if len(nav_values) > 20:
                arr = np.array(nav_values, dtype=np.float64)
                rets = np.diff(arr) / arr[:-1]
                all_returns[code] = rets

        codes = list(all_returns.keys())
        if len(codes) < 2:
            return np.array([]), []

        min_len = min(len(r) for r in all_returns.values())
        aligned = np.column_stack([all_returns[c][-min_len:] for c in codes])
        return aligned, codes

    # ── 协方差估计 ────────────────────────────────────────────

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
            sample_cov = np.cov(X, rowvar=False)
            if n < p:
                shrinkage = 0.5
                target = np.eye(p) * np.trace(sample_cov) / p
                return (1 - shrinkage) * sample_cov + shrinkage * target
            return sample_cov

    # ── 市场权重 ──────────────────────────────────────────────

    def _market_weights(self, codes: List[str]) -> np.ndarray:
        """市场权重: 基于规模(scale)的加权; 无数据时等权"""
        from ...data.storage import get_fund_meta

        n = len(codes)
        weights = np.ones(n) / n  # 默认等权

        scales = []
        for code in codes:
            meta = get_fund_meta(code)
            scale = meta.get("scale") if meta else None
            if scale is not None and scale > 0:
                scales.append(float(scale))
            else:
                scales.append(0.0)

        total = sum(scales)
        if total > 0:
            weights = np.array(scales, dtype=np.float64) / total

        return weights

    # ── 观点生成 ──────────────────────────────────────────────

    def _build_views(self, codes: List[str]) -> Tuple[dict, bool]:
        """从策略信号构建 Black-Litterman 观点

        Returns:
            (views_dict, has_views)
            views_dict = {"P": ndarray, "Q": ndarray, "k": int, "omegas": ndarray}
        """
        signals: List[FundSignal] = self._state.get("active_signals", [])
        if not signals:
            return {}, False

        # 过滤与本组合基金相关的信号
        relevant = [s for s in signals if s.fund_code in codes]
        if not relevant:
            return {}, False

        code_to_idx = {c: i for i, c in enumerate(codes)}
        n = len(codes)
        k = len(relevant)  # 观点数量

        P = np.zeros((k, n))
        Q = np.zeros(k)
        confidences = np.ones(k)

        for i, sig in enumerate(relevant):
            idx = code_to_idx[sig.fund_code]
            direction = 1.0 if sig.direction in (Direction.BUY, Direction.REBALANCE) else -1.0
            P[i, idx] = direction
            # 观点幅度: 置信度 * 年化波动率作为预期超额收益
            Q[i] = direction * sig.confidence * 0.05  # 5% as base magnitude
            confidences[i] = sig.confidence

        # Idzorek Ω: 基于置信度校准
        tau = self.params["tau"]
        omegas = self._idzorek_omega(P, Q, confidences, tau)

        return {"P": P, "Q": Q, "omegas": omegas, "k": k}, True

    @staticmethod
    def _idzorek_omega(P: np.ndarray, Q: np.ndarray,
                       confidences: np.ndarray, tau: float) -> np.ndarray:
        """Idzorek Ω校准: 将观点置信度映射到不确定性矩阵"""
        k = len(Q)
        omega = np.eye(k)
        for i in range(k):
            # 低置信度 → 高不确定性
            unc = max(1.0 - confidences[i], 0.01)
            omega[i, i] = unc / tau
        return omega

    # ── 后验收益 ──────────────────────────────────────────────

    def _posterior_return(self, cov: np.ndarray, pi: np.ndarray,
                          views: dict) -> np.ndarray:
        """BL后验预期收益: E[R] = [(τΣ)^-1 + P'Ω^-1P]^-1 * [(τΣ)^-1Π + P'Ω^-1Q]"""
        tau = self.params["tau"]
        n = cov.shape[0]

        tau_sigma = tau * cov
        try:
            tau_sigma_inv = np.linalg.inv(tau_sigma)
        except np.linalg.LinAlgError:
            tau_sigma_inv = np.linalg.pinv(tau_sigma)

        P = views["P"]
        Q = views["Q"]
        omega = views["omegas"]

        try:
            omega_inv = np.linalg.inv(omega)
        except np.linalg.LinAlgError:
            omega_inv = np.linalg.pinv(omega)

        # 后验均值
        lhs_inv = np.linalg.inv(tau_sigma_inv + P.T @ omega_inv @ P)
        rhs = tau_sigma_inv @ pi + P.T @ omega_inv @ Q
        mu = lhs_inv @ rhs

        return mu

    # ── 均值-方差优化 ─────────────────────────────────────────

    def _mean_variance_optimize(self, cov: np.ndarray,
                                 mu: np.ndarray,
                                 codes: List[str]) -> dict:
        """均值-方差优化: 最大化 预期收益 - 0.5*δ*方差"""
        n = len(codes)
        max_w = self.params["max_weight"]
        min_w = self.params["min_weight"]

        try:
            from scipy.optimize import minimize

            bounds = [(min_w, max_w) for _ in range(n)]
            constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
            w0 = np.array([1.0 / n] * n)

            def objective(w, cov, mu, delta):
                ret = w @ mu
                risk = 0.5 * delta * (w @ cov @ w)
                return -(ret - risk)

            result = minimize(
                objective, w0,
                args=(cov, mu, self.params["risk_aversion"]),
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
                options={"ftol": 1e-8, "maxiter": 1000},
            )

            if result.success:
                weights = result.x
            else:
                logger.warning(f"均值-方差优化收敛失败: {result.message}, 回退等权")
                weights = w0

        except ImportError:
            logger.warning("scipy不可用, 使用等权")
            weights = np.array([1.0 / n] * n)

        # 裁剪+归一化
        weights = np.clip(weights, min_w, max_w)
        weights = weights / np.sum(weights)

        # 风险指标
        port_var = weights @ cov @ weights
        port_ret = weights @ mu

        weight_dict = {code: round(float(w), 4) for code, w in zip(codes, weights)}

        return {
            "weights": weight_dict,
            "expected_return": round(float(port_ret * 252), 6),
            "portfolio_volatility": round(float(np.sqrt(port_var) * np.sqrt(252)), 6),
            "sharpe_ratio": round(float((port_ret * 252 - 0.02) / max(np.sqrt(port_var) * np.sqrt(252), 1e-6)), 4),
            "status": "success",
        }

    # ── 策略评估接口 ──────────────────────────────────────────

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        """策略评估: 如果有组合 -> optimize, 否则返回空"""
        if portfolio is None or not portfolio.positions:
            return []

        fund_codes = list(portfolio.positions.keys())
        result = self.optimize(fund_codes)

        if result.get("status") != "success":
            return []

        signals = []
        for code, weight in result.get("weights", {}).items():
            direction = Direction.BUY if weight > 0.05 else Direction.SELL
            signals.append(self.emit_signal(
                SignalType.ALLOCATION, code, direction,
                confidence=min(float(weight) / 0.4, 1.0),
                reason=(f"BL优化权重 {weight:.1%} (方法: {result.get('method', 'mv')}, "
                        f"预期年化 {result.get('expected_return', 0):.1%})"),
                suggested_pct=float(weight),
            ))

        return signals


StrategyRegistry.register(BlackLittermanStrategy)
