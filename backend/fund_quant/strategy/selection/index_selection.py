"""指数基金选基策略 — 5 维度评分"""
from typing import Optional
import numpy as np
from loguru import logger
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class IndexSelectionStrategy(FundStrategyBase):
    """指数基金选基策略: 跟踪误差 + 费率 + 规模 + 流动性 + 折溢价

    指数基金不跑 alpha 因子，选基核心是"跟得准、费用低、流动性好"。
    """
    strategy_name = "index_selection"
    strategy_type = "selection"
    description = "指数基金5维度评分: 跟踪误差/费率/规模/流动性/折溢价"
    default_params = {
        "top_n": 5,
        "weights": {
            "tracking_error": 0.30,
            "fee_rate": 0.25,
            "scale": 0.20,
            "liquidity": 0.15,
            "premium_stability": 0.10,
        },
        "min_history_days": 60,
        "fee_target": 0.005,       # 管理费+托管费 < 0.5%
        "scale_min": 5e8,          # 规模 > 5 亿
        "liq_min": 1e7,            # 日均成交额 > 1000 万
    }
    applicable_fund_types = ["index"]
    min_history_days = 60

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> list[FundSignal]:
        return []

    def screen(self, fund_type: str = "index", top_n: int = 10,
               params: Optional[dict] = None) -> dict:
        """指数基金筛选 — 5 维度评分"""
        from ...core.models import TYPE_COMPAT
        from ...data.storage import get_all_fund_codes, get_fund_meta, get_nav_history

        if params:
            self.params.update(params)
        top_n = self.params.get("top_n", top_n)
        candidates = get_all_fund_codes()
        if not candidates:
            return self._empty_result(fund_type, top_n)

        filter_type = TYPE_COMPAT.get(fund_type, fund_type)
        fund_scores = []

        for code in candidates:
            meta = get_fund_meta(code)
            if meta and filter_type != "all":
                mt = TYPE_COMPAT.get(meta.get("fund_type", ""), meta.get("fund_type", ""))
                if mt != filter_type:
                    continue

            navs = get_nav_history(code)
            if len(navs) < self.params["min_history_days"]:
                continue

            score, details = self._score_fund(code, meta, navs)
            if score is not None:
                fund_scores.append({
                    "fund_code": code,
                    "fund_name": (meta.get("fund_name") or "") if meta else "",
                    "total_score": score,
                    "dimensions": details,
                    "meta": meta,
                })

        if not fund_scores:
            return self._empty_result(fund_type, top_n)

        fund_scores.sort(key=lambda x: x["total_score"], reverse=True)
        rankings = [{
            "rank": i + 1,
            "fund_code": fs["fund_code"],
            "fund_name": fs["fund_name"],
            "total_score": round(fs["total_score"], 4),
            "dimensions": {k: round(v, 4) for k, v in fs["dimensions"].items()},
        } for i, fs in enumerate(fund_scores[:top_n])]

        self._state["last_rankings"] = fund_scores[:top_n]
        return {
            "strategy": self.strategy_name,
            "fund_type": fund_type,
            "top_n": top_n,
            "total_candidates": len(fund_scores),
            "rankings": rankings,
        }

    def _score_fund(self, fund_code: str, meta: Optional[dict],
                    navs: list[dict]) -> tuple:
        """计算单只指数基金 5 维度得分

        Returns:
            (total_score, {dimension: score}) 或 (None, {})
        """
        w = self.params["weights"]
        scores = {}
        nav_values = [r.get("nav", 0) for r in navs if r.get("nav") and r["nav"] > 0]

        # 1. 费率评分（越低越好）
        mgmt = (meta.get("management_fee") or 0.005) if meta else 0.005
        cust = (meta.get("custody_fee") or 0.001) if meta else 0.001
        total_fee = mgmt + cust
        fee_target = self.params["fee_target"]
        scores["fee_rate"] = float(np.clip(1.0 - total_fee / fee_target, 0, 1))

        # 2. 规模评分（越大越好，对数）
        scale = (meta.get("scale") or 0) if meta else 0
        scale_min = self.params["scale_min"]
        if scale > 0:
            scores["scale"] = float(np.clip(np.log10(scale / scale_min) / 2.0, 0, 1))
        else:
            scores["scale"] = 0.0

        # 3. 跟踪误差评分（越低越好，需外部数据）
        tracking_error = self._state.get("tracking_errors", {}).get(fund_code)
        if tracking_error is not None:
            scores["tracking_error"] = float(np.clip(1.0 - tracking_error / 0.01, 0, 1))
        else:
            scores["tracking_error"] = 0.5  # 无数据时中性

        # 4. 流动性评分（越高越好，场内ETF）
        liquidity = self._state.get("liquidity_data", {}).get(fund_code)
        if liquidity is not None:
            liq_min = self.params["liq_min"]
            scores["liquidity"] = float(np.clip(liquidity / liq_min / 5.0, 0, 1))
        else:
            scores["liquidity"] = 0.5

        # 5. 折溢价稳定性评分（波动越低越好）
        premium_vol = self._state.get("premium_vol_data", {}).get(fund_code)
        if premium_vol is not None:
            scores["premium_stability"] = float(np.clip(1.0 - premium_vol / 0.02, 0, 1))
        else:
            scores["premium_stability"] = 0.5

        total = sum(scores.get(d, 0) * w.get(d, 0) for d in w)
        return total, scores

    @staticmethod
    def _empty_result(fund_type: str, top_n: int) -> dict:
        return {
            "strategy": "index_selection",
            "fund_type": fund_type,
            "top_n": top_n,
            "rankings": [],
            "total_candidates": 0,
        }


StrategyRegistry.register(IndexSelectionStrategy)
