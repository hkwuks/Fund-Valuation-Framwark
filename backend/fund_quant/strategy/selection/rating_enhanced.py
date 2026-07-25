"""评级增强选基策略 — 晨星评级 + 量化因子 + 估值偏差"""

from typing import Optional, List, Dict
import numpy as np
from loguru import logger

from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class RatingEnhancedSelection(FundStrategyBase):
    """评级增强选基策略: 晨星评级(35%) + 量化因子(45%) + 估值偏差(20%) 综合评分"""
    strategy_name = "rating_enhanced"
    strategy_type = "selection"
    description = "晨星评级 + 量化因子(夏普/回撤/超额收益) + 估值偏差的基金评分策略"
    default_params = {
        "rating_weight": 0.35,
        "quant_weight": 0.45,
        "deviation_weight": 0.20,
        "top_n": 5,
        "z_deviation_threshold": 1.5,
        "min_history_days": 60,
    }
    param_ranges = {
        "rating_weight": {"min": 0, "max": 1},
        "quant_weight": {"min": 0, "max": 1},
        "deviation_weight": {"min": 0, "max": 1},
        "top_n": {"min": 3, "max": 20},
        "z_deviation_threshold": {"min": 0.5, "max": 3.0},
    }
    formula_description = "晨星评级+量化因子+估值偏差综合基金评分策略"
    applicable_fund_types = ["stock", "hybrid", "bond", "index"]
    min_history_days = 365

    # ── 评级归一化 ──────────────────────────────────────────────

    def _normalize_rating(self, rating: Optional[int]) -> float:
        """晨星评级 1-5 → 0.0-1.0"""
        if rating is None or rating < 1 or rating > 5:
            return 0.5
        return (rating - 1) / 4.0

    # ── 量化因子 ────────────────────────────────────────────────

    def _calc_quant_factors(self, nav_values: list) -> Dict[str, float]:
        """计算量化因子: 夏普比率 / 最大回撤 / 超额收益"""
        arr = np.array(nav_values, dtype=np.float64)
        if len(arr) < 20:
            return {}
        returns = np.diff(arr) / arr[:-1]
        if len(returns) < 20:
            return {}

        ann_return = float(np.mean(returns) * 252)
        ann_vol = float(np.std(returns, ddof=1) * np.sqrt(252))

        sharpe = (ann_return - 0.02) / max(ann_vol, 1e-6)

        cum = np.cumprod(1 + returns)
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak) / peak
        max_dd = float(abs(np.min(dd)))

        excess_return = ann_return - 0.02  # 相对无风险利率的超额收益

        return {"sharpe_ratio": sharpe, "max_drawdown": max_dd, "excess_return": excess_return}

    @staticmethod
    def _zscore_map_to_01(values: list, reverse: bool = False) -> Dict[str, float]:
        """Z-Score → 归一化到 0-1 (reverse=True 表示值越小得分越高)"""
        arr = np.array(values, dtype=np.float64)
        if len(arr) < 2:
            return {i: 0.5 for i in range(len(values))}
        mu, sigma = float(np.mean(arr)), float(np.std(arr, ddof=1))
        if sigma < 1e-8:
            return {i: 0.5 for i in range(len(values))}
        scores = {}
        for i, v in enumerate(values):
            z = (v - mu) / sigma
            # sigmoid into 0-1
            norm = 1.0 / (1.0 + np.exp(-z))
            if reverse:
                norm = 1.0 - norm
            scores[i] = float(norm)
        return scores

    # ── 估值偏差逆向映射 ────────────────────────────────────────

    def _deviation_to_score(self, z: float) -> float:
        """估值偏差z-score → 得分: z<-1.5→1.0, |z|≤1.5→0.5, z>1.5→0.0"""
        threshold = self.params["z_deviation_threshold"]
        if z < -threshold:
            return 1.0
        if z > threshold:
            return 0.0
        return 0.5

    def _calc_deviation_score(self, nav_values: list) -> float:
        """基于净值计算估值偏差z-score并映射到得分"""
        arr = np.array(nav_values, dtype=np.float64)
        if len(arr) < 30:
            return 0.5
        returns = np.diff(arr) / arr[:-1]
        if len(returns) < 20:
            return 0.5
        window = returns[-int(min(self.params["z_deviation_threshold"] * 40, len(returns))):]
        mu = float(np.mean(window))
        sigma = float(np.std(window, ddof=1))
        if sigma < 1e-10:
            return 0.5
        z = (returns[-1] - mu) / sigma
        return self._deviation_to_score(z)

    # ── 综合评分 ────────────────────────────────────────────────

    def screen(self, fund_type: str = "stock", top_n: int = 10,
               params: Optional[dict] = None) -> dict:
        """评级 + 量化因子 + 估值偏差 综合筛选

        Args:
            fund_type: 基金类型筛选
            top_n: 返回前N只基金
            params: 覆盖默认参数

        Returns:
            dict: 含 rankings 列表的筛选结果
        """
        from ...data.storage import get_all_fund_codes, get_fund_meta, get_nav_history

        if params:
            self.params.update(params)
        top_n = self.params.get("top_n", top_n)

        candidates = get_all_fund_codes()
        if not candidates:
            return {"strategy": self.strategy_name, "fund_type": fund_type,
                    "top_n": top_n, "rankings": [], "total_candidates": 0}

        # ── 逐基金计算原始因子 ──────────────────────────────
        fund_data = []
        for code in candidates:
            meta = get_fund_meta(code)
            if meta and meta.get("fund_type") != fund_type and fund_type != "all":
                continue

            navs = get_nav_history(code)
            if len(navs) < self.params["min_history_days"]:
                continue

            nav_values = [r["nav"] for r in navs if r.get("nav") and r["nav"] > 0]
            if len(nav_values) < self.params["min_history_days"]:
                continue

            rating_score = self._normalize_rating(meta.get("rating") if meta else None)
            quant = self._calc_quant_factors(nav_values)
            deviation_score = self._calc_deviation_score(nav_values)

            fund_data.append({
                "fund_code": code,
                "fund_name": (meta.get("fund_name") or "") if meta else "",
                "meta": meta,
                "rating_score": rating_score,
                "quant": quant,
                "deviation_score": deviation_score,
            })

        if not fund_data:
            return {"strategy": self.strategy_name, "fund_type": fund_type,
                    "top_n": top_n, "rankings": [], "total_candidates": 0}

        # ── 量化因子 Z-score 归一化 ────────────────────────
        # 收集各因子原始值
        sharpe_vals = [d["quant"].get("sharpe_ratio", 0) for d in fund_data]
        dd_vals = [d["quant"].get("max_drawdown", 0) for d in fund_data]
        excess_vals = [d["quant"].get("excess_return", 0) for d in fund_data]

        sharpe_scores = self._zscore_map_to_01(sharpe_vals)
        dd_scores = self._zscore_map_to_01(dd_vals, reverse=True)  # 回撤越小越好
        excess_scores = self._zscore_map_to_01(excess_vals)

        # ── 加权综合 ────────────────────────────────────────
        w_rating = self.params["rating_weight"]
        w_quant = self.params["quant_weight"]
        w_deviation = self.params["deviation_weight"]

        for i, d in enumerate(fund_data):
            quant_score = (sharpe_scores.get(i, 0) * 0.4 +
                           dd_scores.get(i, 0) * 0.3 +
                           excess_scores.get(i, 0) * 0.3)
            d["total_score"] = (w_rating * d["rating_score"] +
                                w_quant * quant_score +
                                w_deviation * d["deviation_score"])
            d["quant_score"] = quant_score

        # ── 排序 ────────────────────────────────────────────
        fund_data.sort(key=lambda x: x["total_score"], reverse=True)

        rankings = []
        for i, d in enumerate(fund_data[:top_n]):
            rankings.append({
                "rank": i + 1,
                "fund_code": d["fund_code"],
                "fund_name": d["fund_name"],
                "total_score": round(d["total_score"], 4),
                "rating_score": round(d["rating_score"], 4),
                "quant_score": round(d["quant_score"], 4),
                "deviation_score": round(d["deviation_score"], 4),
                "factors": {k: round(v, 4) for k, v in d["quant"].items()},
            })

        self._state["last_rankings"] = fund_data[:top_n]

        return {
            "strategy": self.strategy_name,
            "fund_type": fund_type,
            "top_n": top_n,
            "total_candidates": len(fund_data),
            "rankings": rankings,
        }

    def score(self, fund_type: str = "stock",
              params: Optional[dict] = None) -> dict:
        """批量评分"""
        if params:
            self.params.update(params)
        result = self.screen(fund_type=fund_type, top_n=1000)
        return {
            "strategy": self.strategy_name,
            "fund_type": fund_type,
            "scores": {r["fund_code"]: r["total_score"] for r in result.get("rankings", [])},
        }

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        """单基金评估（回测场景），通过 screen() 做截面排名后查找"""
        fund_code = self._state.get("fund_code", "")
        if not fund_code:
            return []

        result = self.screen(fund_type="all", top_n=1000)
        for r in result.get("rankings", []):
            if r["fund_code"] == fund_code:
                return [self.emit_signal(
                    SignalType.SELECTION, fund_code,
                    Direction.BUY if r["total_score"] >= 0.5 else Direction.HOLD,
                    confidence=r["total_score"],
                    reason=(f"评级增强评分 {r['total_score']:.2f} = "
                            f"评级{r['rating_score']:.2f}×{self.params['rating_weight']} "
                            f"+ 量化{r['quant_score']:.2f}×{self.params['quant_weight']} "
                            f"+ 偏差{r['deviation_score']:.2f}×{self.params['deviation_weight']}"),
                )]
        return []


StrategyRegistry.register(RatingEnhancedSelection)
