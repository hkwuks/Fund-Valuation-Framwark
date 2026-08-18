"""层次风险平价(HRP) — 层次聚类 + 递归二分，不需要协方差矩阵求逆

López de Prado (2016), Advances in Financial Machine Learning, Chapter 14
"""
from typing import Optional, List, Dict
import numpy as np
from loguru import logger

from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class HRPStrategy(FundStrategyBase):
    """层次风险平价: 用聚类替代矩阵求逆，对估计误差更稳健"""
    strategy_name = "hrp"
    strategy_type = "allocation"
    description = "层次风险平价(HRP): 层次聚类 + 递归二分，不依赖协方差矩阵求逆"
    default_params = {
        "lookback_years": 3,
        "max_weight": 0.4,
        "min_weight": 0.02,
        "linkage_method": "ward",
    }
    applicable_fund_types = []
    min_history_days = 120

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        return []

    def optimize(self, fund_codes: List[str],
                 params: Optional[dict] = None,
                 nav_series: Optional[Dict[str, List[float]]] = None) -> dict:
        """HRP组合优化

        nav_series: {fund_code: [净值...]} — 引擎回测传入截至当日的净值序列，无前视。
        """
        from ...data.storage import get_nav_history

        if params:
            self.params.update(params)

        if len(fund_codes) < 2:
            return {"strategy": self.strategy_name, "fund_codes": fund_codes,
                    "weights": {c: 1.0 for c in fund_codes}, "status": "single_fund"}

        # 1. 获取收益率矩阵
        all_returns = {}
        for code in fund_codes:
            if nav_series and code in nav_series:
                nav_values = [float(v) for v in nav_series[code] if v and v > 0]
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

        # 2. 对齐长度
        min_len = min(len(r) for r in all_returns.values())
        aligned = np.column_stack([all_returns[c][-min_len:] for c in codes])

        # 3. 计算相关性矩阵 + 距离矩阵
        corr = np.corrcoef(aligned, rowvar=False)
        # 处理 NaN（常数序列导致）
        corr = np.nan_to_num(corr, nan=0.0)
        np.fill_diagonal(corr, 1.0)
        dist = np.sqrt(0.5 * (1 - np.clip(corr, -1, 1)))

        # 4. 层次聚类
        from scipy.cluster.hierarchy import linkage
        from scipy.spatial.distance import squareform
        dist_condensed = squareform(dist, checks=False)
        method = self.params.get("linkage_method", "ward")
        link = linkage(dist_condensed, method=method)

        # 5. 准对角化（按聚类结果重排资产顺序）
        order = self._quasi_diag(link)
        ordered_codes = [codes[i] for i in order]
        ordered_corr = corr[np.ix_(order, order)]

        # 6. 递归二分分配权重
        weights = self._recursive_bisection(aligned, ordered_codes, codes)

        # 裁剪到 [min_weight, max_weight] 并归一化
        max_w = self.params.get("max_weight", 0.4)
        min_w = self.params.get("min_weight", 0.02)
        w_arr = np.array([weights.get(c, 0) for c in codes])
        w_arr = np.clip(w_arr, min_w, max_w)
        w_arr = w_arr / w_arr.sum()

        weight_dict = {c: round(float(w), 4) for c, w in zip(codes, w_arr)}

        return {
            "strategy": self.strategy_name,
            "method": "hrp",
            "fund_codes": codes,
            "weights": weight_dict,
            "status": "success",
        }

    @staticmethod
    def _quasi_diag(link) -> list:
        """从 linkage 矩阵恢复聚类排序（准对角化）"""
        n = int(link[-1, 3])
        order = [n + link.shape[0] - 1]
        while True:
            order_new = []
            for i in order:
                if i >= n:
                    idx = int(i - n)
                    order_new.append(int(link[idx, 0]))
                    order_new.append(int(link[idx, 1]))
                else:
                    order_new.append(i)
            if order_new == order:
                break
            order = order_new
        return [int(i) for i in order]

    @staticmethod
    def _recursive_bisection(aligned: np.ndarray, ordered_codes: list, all_codes: list) -> dict:
        """递归二分：低方差子集分配更多权重"""
        weights = {c: 1.0 for c in ordered_codes}
        cluster_items = [ordered_codes]

        while cluster_items:
            new_clusters = []
            for cluster in cluster_items:
                if len(cluster) <= 1:
                    continue
                mid = len(cluster) // 2
                left = cluster[:mid]
                right = cluster[mid:]

                left_var = np.mean([np.var(aligned[:, all_codes.index(c)]) for c in left])
                right_var = np.mean([np.var(aligned[:, all_codes.index(c)]) for c in right])

                total_var = left_var + right_var
                if total_var <= 0:
                    continue
                # 方差小的子集分配更多权重
                left_alloc = 1.0 - left_var / total_var
                right_alloc = 1.0 - left_alloc

                for c in left:
                    weights[c] *= left_alloc
                for c in right:
                    weights[c] *= right_alloc

                if len(left) > 1:
                    new_clusters.append(left)
                if len(right) > 1:
                    new_clusters.append(right)
            cluster_items = new_clusters

        total_w = sum(weights.values())
        if total_w > 0:
            weights = {c: w / total_w for c, w in weights.items()}
        return weights


StrategyRegistry.register(HRPStrategy)
