"""Black-Litterman + 四象限观点策略

继承自 BlackLittermanStrategy，覆盖 _build_views 注入桥水四象限目标比例观点。
"""
from typing import Optional, List, Tuple
import numpy as np
from loguru import logger

from .black_litterman import BlackLittermanStrategy
from ..base import StrategyRegistry


# 四象限目标比例（桥水 All Weather）
QUADRANT_TARGETS = {
    "growth": 0.30,
    "deflation": 0.40,
    "neutral": 0.15,
    "inflation": 0.15,
}

# 基金代码→象限映射（从 portfolio quadrant 字段整理）
QUADRANT_MAP = {
    # neutral
    "001961": "neutral", "005159": "neutral", "001235": "neutral",
    "013745": "neutral", "014846": "neutral", "021282": "neutral",
    "163806": "neutral", "013428": "neutral", "012951": "neutral",
    "009308": "neutral", "968075": "neutral",
    # deflation (长期国债)
    "006961": "deflation",
    # growth
    "015740": "growth", "019261": "growth", "019260": "growth",
    "012323": "growth", "008163": "growth", "017436": "growth",
    "007751": "growth", "005051": "growth", "019305": "growth",
    "021735": "growth", "019018": "growth", "023920": "growth",
    "016665": "growth", "160125": "growth", "021674": "growth",
    "012922": "growth", "968040": "growth", "968041": "growth",
    "968067": "growth", "968048": "growth", "000614": "growth",
    # inflation (黄金/商品)
    "000218": "inflation", "018167": "inflation", "161815": "inflation",
}


class BlackLittermanQuadrant(BlackLittermanStrategy):
    """BL + 四象限观点: 桥水 All Weather 目标比例作为观点注入"""
    strategy_name = "bl_quadrant"
    strategy_type = "allocation"
    description = "Black-Litterman + 四象限观点: 桥水全天候30/40/15/15比例作为相对观点"
    default_params = {
        "risk_aversion": 2.5,
        "tau": 0.05,
        "max_weight": 0.4,
        "min_weight": 0.05,
        "lookback_days": 756,
        "view_confidence": 0.6,       # 观点置信度
        "growth_underperform": -0.03,  # growth 年化跑输 deflation
        "inflation_outperform": 0.04,  # inflation 年化跑赢 neutral
    }
    applicable_fund_types = []
    min_history_days = 365

    def _build_views(self, codes: List[str]) -> Tuple[dict, bool]:
        """用四象限目标比例构建相对观点

        观点 1: Growth 年化跑输 Deflation 3%（对应 growth 48%→30% 目标）
        观点 2: Inflation 年化跑赢 Neutral 4%（对应 inflation 7.4%→15% 目标）

        协方差矩阵由 optimize() 计算后缓存在 _state["_cov_matrix"]，此处复用。
        """
        n = len(codes)
        quads = [QUADRANT_MAP.get(c, "growth") for c in codes]

        cov = self._state.get("_cov_matrix")
        if cov is None:
            return {}, False

        tau = self.params["tau"]
        confidence = self.params["view_confidence"]

        # 构造观点
        g_idx = [i for i, q in enumerate(quads) if q == "growth"]
        d_idx = [i for i, q in enumerate(quads) if q == "deflation"]
        i_idx = [i for i, q in enumerate(quads) if q == "inflation"]
        n_idx = [i for i, q in enumerate(quads) if q == "neutral"]

        P_list, Q_list = [], []

        if g_idx and d_idx:
            p = np.zeros(n)
            for i in g_idx: p[i] = 1.0 / len(g_idx)
            for i in d_idx: p[i] = -1.0 / len(d_idx)
            P_list.append(p)
            Q_list.append(self.params["growth_underperform"] / 252)  # 年化→日度

        if i_idx and n_idx:
            p = np.zeros(n)
            for i in i_idx: p[i] = 1.0 / len(i_idx)
            for i in n_idx: p[i] = -1.0 / len(n_idx)
            P_list.append(p)
            Q_list.append(self.params["inflation_outperform"] / 252)

        if not P_list:
            return {}, False

        k = len(P_list)
        P = np.vstack(P_list)
        Q = np.array(Q_list)

        # 标准 Idzorek Ω: (1/conf - 1) * τ * diag(P @ Σ @ P.T)
        diag_PCPT = np.array([p @ cov @ p for p in P_list])
        omega = np.diag((1.0 / confidence - 1.0) * tau * diag_PCPT)

        return {"P": P, "Q": Q, "omegas": omega, "k": k}, True


StrategyRegistry.register(BlackLittermanQuadrant)