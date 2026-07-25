"""桥水全天候策略 — 风险平价 + 固定权重双模式

策略逻辑（Ray Dalio All Weather Portfolio）:
  1. 将经济划分为4个象限（增长↑↓ × 通胀↑↓）
  2. 每个象限分配25%风险敞口
  3. 在各象限内分散配置适配资产（股票/债券/商品/黄金）
  4. 每月再平衡

两种模式:
  - fixed: Dalio公开的固定比例（30%股票+15%中债+40%长债+7.5%黄金+7.5%商品）
  - risk_parity: 用风险平价动态计算权重（波动率更低，夏普更高）

参考：
  - 桥水全天候基金（1996年成立）年化7-12%，回撤6-12%
  - CSDN完整复现：固定权重版年化7.0%/回撤11.86%，风险平价版年化5.83%/回撤6.59%
"""
from typing import Optional, List, Dict
import numpy as np
from loguru import logger

from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class AllWeatherStrategy(FundStrategyBase):
    """全天候策略: 固定权重/风险平价双模式 + 月度再平衡"""

    strategy_name = "all_weather"
    strategy_type = "allocation"
    description = "桥水全天候策略(All Weather): 四象限风险平价 + 月度再平衡, 支持fixed/risk_parity双模式"
    default_params = {
        # 模式
        "mode": "fixed",               # "fixed" | "risk_parity"
        # 资产配置模板: code -> {name, asset_class, weight(fixed模式)}
        "asset_template": {
            "510300": {"name": "沪深300ETF", "asset_class": "equity", "fixed_weight": 0.10},
            "513500": {"name": "标普500ETF", "asset_class": "equity", "fixed_weight": 0.10},
            "513100": {"name": "纳指ETF",   "asset_class": "equity", "fixed_weight": 0.10},
            "511520": {"name": "5年国债ETF", "asset_class": "bond_medium", "fixed_weight": 0.15},
            "511260": {"name": "10年国债ETF","asset_class": "bond_long",   "fixed_weight": 0.40},
            "518880": {"name": "黄金ETF",    "asset_class": "gold",     "fixed_weight": 0.075},
            "159985": {"name": "豆粕ETF",    "asset_class": "commodity","fixed_weight": 0.075},
        },
        # 再平衡
        "rebalance_freq": "monthly",    # monthly / weekly / daily
        "rebalance_threshold": 0.05,    # 偏离超过此比例触发再平衡
        # 风险平价参数
        "lookback_days": 756,           # 3年交易日
        "max_weight": 0.50,
        "min_weight": 0.02,
        "bond_vol_multiplier": "auto",  # 债券波动率放大（auto=2x）
        # 杠杆
        "leverage": 1.0,               # 1.0=无杠杆, 3.0=3倍
        # 费用
        "fee_rate": 0.0003,
    }
    applicable_fund_types = []
    min_history_days = 365

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        return []

    def optimize(self, fund_codes: Optional[List[str]] = None,
                 params: Optional[dict] = None) -> dict:
        """执行全天候组合优化

        Args:
            fund_codes: 若指定，覆盖默认资产模板中的基金列表
            params: 覆盖默认参数

        Returns:
            dict: 含 weights, 各类资产配置比例, 风险贡献
        """
        if params:
            self.params.update(params)

        # 1. 构建资产列表
        asset_codes, asset_info = self._build_asset_list(fund_codes)
        if not asset_codes:
            return {"strategy": self.strategy_name, "status": "no_assets",
                    "weights": {}, "reason": "未配置任何资产"}

        mode = self.params["mode"]

        if mode == "risk_parity":
            return self._optimize_risk_parity(asset_codes, asset_info)
        else:
            return self._optimize_fixed(asset_codes, asset_info)

    def _build_asset_list(self, fund_codes: Optional[List[str]] = None
                          ) -> tuple[List[str], Dict]:
        """构建资产列表"""
        template = self.params["asset_template"]

        if fund_codes:
            # 使用指定基金列表，尝试匹配模板中的配置
            asset_info = {}
            for c in fund_codes:
                if c in template:
                    asset_info[c] = dict(template[c])
                else:
                    # 未匹配模板 → 等权 equity
                    asset_info[c] = {"name": c, "asset_class": "equity",
                                      "fixed_weight": 1.0 / max(len(fund_codes), 1)}

            codes = fund_codes
        else:
            codes = list(template.keys())
            asset_info = {c: dict(v) for c, v in template.items()}

        return codes, asset_info

    def _optimize_fixed(self, codes: List[str],
                        asset_info: Dict) -> dict:
        """固定权重模式: Dalio公开比例

        权重归一化后按比例分配，同资产类别的ETF共享该类权重。
        """
        # 按资产类别聚合固定权重
        class_weights = {}
        for c in codes:
            cls = asset_info[c].get("asset_class", "equity")
            fw = asset_info[c].get("fixed_weight", 0.0)
            class_weights[cls] = class_weights.get(cls, 0.0) + fw

        # 按类别归一化
        total = sum(class_weights.values())
        if total <= 0:
            return self._empty_result(codes, "固定权重之和为0")
        for cls in class_weights:
            class_weights[cls] /= total

        # 分配到具体ETF
        weights = {}
        class_counts: Dict[str, int] = {}
        for c in codes:
            cls = asset_info[c]["asset_class"]
            class_counts[cls] = class_counts.get(cls, 0) + 1

        for c in codes:
            cls = asset_info[c]["asset_class"]
            cnt = class_counts.get(cls, 1)
            weights[c] = class_weights[cls] / cnt

        # 应用杠杆
        lev = self.params.get("leverage", 1.0)
        if lev != 1.0:
            weights = {c: w * lev for c, w in weights.items()}

        # 计算各类别占比
        class_allocation = {}
        for c in codes:
            cls = asset_info[c]["asset_class"]
            class_allocation[cls] = class_allocation.get(cls, 0.0) + weights[c]

        return {
            "strategy": self.strategy_name,
            "status": "success",
            "mode": "fixed",
            "method": "dalio_fixed_weight",
            "weights": {c: round(float(w), 4) for c, w in weights.items()},
            "asset_allocation": {cls: round(float(w), 4)
                                  for cls, w in sorted(class_allocation.items())},
            "leverage": lev,
            "rebalance_freq": self.params["rebalance_freq"],
            "rebalance_threshold": self.params["rebalance_threshold"],
        }

    def _optimize_risk_parity(self, codes: List[str],
                              asset_info: Dict) -> dict:
        """风险平价模式: Ledoit-Wolf协方差 + SLSQP求解

        追求各资产风险贡献相等，波动率更低，夏普更高。
        """
        from ...data.storage import get_nav_history

        # 1. 获取收益率序列
        lookback = self.params["lookback_days"]
        all_returns = {}
        for code in codes:
            navs = get_nav_history(code)
            if not navs or len(navs) < 60:
                continue
            nav_values = [r.get("nav", 0) for r in navs if r.get("nav") and r["nav"] > 0]
            if len(nav_values) < 20:
                continue
            arr = np.array(nav_values[-lookback:], dtype=np.float64)
            rets = np.diff(arr) / arr[:-1]
            all_returns[code] = rets

        valid = list(all_returns.keys())
        if len(valid) < 2:
            # 数据不足时回退固定权重
            logger.warning(f"全天候风险平价: 有效资产 {len(valid)} < 2，回退固定权重")
            return self._optimize_fixed(codes, asset_info)

        # 2. 对齐
        min_len = min(len(r) for r in all_returns.values())
        aligned = np.column_stack([all_returns[c][-min_len:] for c in valid])

        # 3. Ledoit-Wolf协方差
        cov = self._ledoit_wolf(aligned)

        # 4. 债券波动率放大
        cov = self._adjust_bond_vol(cov, valid, asset_info)

        # 5. SLSQP求解风险平价
        n = len(valid)
        max_w = self.params["max_weight"]
        min_w = self.params["min_weight"]
        bounds = [(min_w, max_w) for _ in range(n)]
        constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1.0}]
        w0 = np.array([1.0 / n] * n)

        try:
            from scipy.optimize import minimize

            def risk_parity_obj(w, cov):
                port_var = w @ cov @ w
                if port_var <= 0:
                    return 1e10
                mrc = cov @ w
                rc = w * mrc
                target = np.mean(rc)
                return float(np.sum((rc - target) ** 2))

            result = minimize(
                risk_parity_obj, w0, args=(cov,),
                method="SLSQP", bounds=bounds,
                constraints=constraints,
                options={"ftol": 1e-8, "maxiter": 1000},
            )
            if result.success:
                weights_arr = result.x
            else:
                logger.warning(f"风险平价求解失败: {result.message}, 回退等权")
                weights_arr = w0
        except ImportError:
            weights_arr = w0

        weights_arr = np.clip(weights_arr, min_w, max_w)
        weights_arr = weights_arr / np.sum(weights_arr)

        # 6. 构建结果
        weights = {}
        for i, c in enumerate(valid):
            weights[c] = float(weights_arr[i])

        # 对无效代码置0
        for c in codes:
            if c not in weights:
                weights[c] = 0.0

        # 风险贡献
        port_var = weights_arr @ cov @ weights_arr
        mrc = cov @ weights_arr
        rc = weights_arr * mrc
        rc_pct = rc / max(port_var, 1e-10) * 100

        risk_contrib = {c: round(float(rc_pct[i]), 2)
                        for i, c in enumerate(valid)}

        # 资产类别聚合
        class_allocation = {}
        for c in codes:
            cls = asset_info.get(c, {}).get("asset_class", "other")
            class_allocation[cls] = class_allocation.get(cls, 0.0) + weights.get(c, 0.0)

        # 应用杠杆
        lev = self.params.get("leverage", 1.0)
        if lev != 1.0:
            weights = {c: w * lev for c, w in weights.items()}
            class_allocation = {cls: w * lev for cls, w in class_allocation.items()}

        return {
            "strategy": self.strategy_name,
            "status": "success",
            "mode": "risk_parity",
            "method": "risk_parity_slsqp",
            "weights": {c: round(float(w), 4) for c, w in weights.items()},
            "risk_contributions": risk_contrib,
            "asset_allocation": {cls: round(float(w), 4)
                                  for cls, w in sorted(class_allocation.items())},
            "portfolio_volatility": round(float(np.sqrt(port_var) * np.sqrt(252)), 6),
            "leverage": lev,
            "rebalance_freq": self.params["rebalance_freq"],
            "n_assets": len(valid),
            "status_detail": "risk_parity",
        }

    @staticmethod
    def _ledoit_wolf(X: np.ndarray) -> np.ndarray:
        """Ledoit-Wolf压缩估计协方差矩阵"""
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

    def _adjust_bond_vol(self, cov: np.ndarray, codes: List[str],
                         asset_info: Dict) -> np.ndarray:
        """债券波动率放大: 解决债券波动率过低导致风险平价过度配置债券的问题"""
        mult = self.params.get("bond_vol_multiplier", "auto")
        if mult == "none" or mult == 1.0:
            return cov

        cov_copy = cov.copy()
        for i, code in enumerate(codes):
            cls = asset_info.get(code, {}).get("asset_class", "")
            if cls in ("bond_medium", "bond_long", "bond"):
                factor = 2.0 if mult == "auto" else float(mult)
                factor = min(factor, 5.0)
                cov_copy[i, i] *= (factor ** 2)
                for j in range(len(codes)):
                    if i != j:
                        cov_copy[i, j] *= factor

        return cov_copy

    def _empty_result(self, codes: List[str], reason: str) -> dict:
        return {
            "strategy": self.strategy_name,
            "status": "error",
            "reason": reason,
            "weights": {c: 0.0 for c in codes},
        }


StrategyRegistry.register(AllWeatherStrategy)
