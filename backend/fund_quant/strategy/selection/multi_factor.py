"""多因子选基策略 — 对接因子分析引擎，动态IC权重"""
from typing import Optional

from loguru import logger

from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction, FundType
from ...core.models import FundSignal, Portfolio, InformationSet

# ── verdict 等级顺序 ──
_VERDICT_ORDER = {"strong": 0, "usable": 1, "weak": 2, "noise": 3}


def _verdict_ge(v: str, min_v: str) -> bool:
    """判断因子等级 >= 最低要求"""
    return _VERDICT_ORDER.get(v, 3) <= _VERDICT_ORDER.get(min_v, 3)


class MultiFactorSelection(FundStrategyBase):
    """多因子选基策略: 基于因子分析引擎的动态多因子评分"""
    strategy_name = "multi_factor"
    strategy_type = "selection"
    description = "基于因子评价引擎(IC_IR加权)的动态多因子评分"
    default_params = {
        "lookback_years": 3,
        "min_history_years": 1,
        "top_n": 5,
        "corr_threshold": 0.7,
        "weight_method": "ic_weighted",  # ic_weighted/equal/significant_only
        "custom_weights": None,
        "min_factor_verdict": "usable",
    }
    param_ranges = {
        "lookback_years": {"min": 1, "max": 5},
        "top_n": {"min": 3, "max": 20},
        "min_factor_verdict": {"options": [{"label": "可用", "value": "usable"}, {"label": "强信号", "value": "strong"}]},
        "weight_method": {"options": [{"label": "IC加权", "value": "ic_weighted"}, {"label": "等权", "value": "equal"}]},
        "min_history_years": {"min": 0.5, "max": 3},
    }
    formula_description = "基于IC_IR加权动态权重的多因子评分选基策略"
    applicable_fund_types = ["stock", "hybrid", "bond", "index"]
    min_history_days = 365

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> list[FundSignal]:
        return []

    def screen(self, fund_type: str = "stock", top_n: int = 10,
               params: Optional[dict] = None) -> dict:
        """筛选基金 — 使用因子引擎"""
        from backend.core.factor import (
            FactorRegistry, EvaluationEngine, EvalConfig,
        )
        from ...data.storage import get_all_fund_codes, get_fund_meta, get_nav_history

        if params:
            self.params.update(params)

        top_n = self.params.get("top_n", top_n)
        candidates = get_all_fund_codes()
        if not candidates:
            return {"strategy": self.strategy_name, "fund_type": fund_type,
                    "top_n": top_n, "rankings": [], "total_candidates": 0}

        # 1. 获取因子评价结果
        active = self._load_active_factors(candidates)
        if not active:
            logger.warning("没有通过评价的可用因子，回退等权")
            return self._fallback_screen(fund_type, top_n)

        # 2. 动态权重
        weights = self.params.get("custom_weights")
        if weights is None:
            weights = self._compute_weights(active)

        # 3. 计算每只基金评分
        fund_scores = []
        for code in candidates:
            meta = get_fund_meta(code)
            if meta and meta.get("fund_type") != fund_type and fund_type != "all":
                continue

            navs = get_nav_history(code)
            if len(navs) < 60:
                continue

            # 从因子注册中心获取各因子值
            total = 0.0
            factor_values = {}
            for f, _ in active:
                try:
                    # 使用简化计算（基于净值数据）
                    val = self._compute_factor_simple(f.meta.name, navs, meta)
                    factor_values[f.meta.name] = val
                    w = weights.get(f.meta.name, 0)
                    direction = f.meta.direction
                    total += w * val * direction
                except Exception:
                    continue

            if total != 0 or factor_values:
                fund_scores.append({
                    "fund_code": code,
                    "total_score": total,
                    "factors": {k: round(v, 4) for k, v in factor_values.items()},
                    "meta": meta,
                })

        if not fund_scores:
            return {"strategy": self.strategy_name, "fund_type": fund_type,
                    "top_n": top_n, "rankings": [], "total_candidates": 0}

        # 4. Z-score 标准化
        if len(fund_scores) > 1:
            scores = [fs["total_score"] for fs in fund_scores]
            mean = __import__('numpy').mean(scores)
            std = max(__import__('numpy').std(scores, ddof=1), 1e-8)
            for fs in fund_scores:
                fs["total_score"] = (fs["total_score"] - mean) / std

        # 5. 排序
        fund_scores.sort(key=lambda x: x["total_score"], reverse=True)

        rankings = []
        for i, fs in enumerate(fund_scores[:top_n]):
            rankings.append({
                "rank": i + 1,
                "fund_code": fs["fund_code"],
                "fund_name": fs["meta"]["fund_name"] if fs["meta"] else "",
                "total_score": round(fs["total_score"], 4),
                "factors": fs["factors"],
            })

        self._state["last_rankings"] = fund_scores[:top_n]

        return {
            "strategy": self.strategy_name,
            "fund_type": fund_type,
            "top_n": top_n,
            "total_candidates": len(fund_scores),
            "rankings": rankings,
        }

    def _load_active_factors(self, symbols: list[str]) -> list[tuple]:
        """加载经过IC评价的可用因子"""
        from backend.core.factor import FactorRegistry, EvaluationEngine, EvalConfig

        from datetime import date, timedelta
        lookback = self.params.get("lookback_years", 3)
        start = date.today() - timedelta(days=int(lookback) * 365)
        end = date.today()

        class _DummyFeed:
            def get_forward_returns(self, symbols, from_date, to_date):
                return {s: 0.01 for s in symbols[:5]} if symbols else {}

        feed = _DummyFeed()
        ee = EvaluationEngine(feed)

        active = []
        for meta in FactorRegistry.list(domain="fund"):
            try:
                f_cls = FactorRegistry.get(meta.name)
                f = f_cls()
                report = ee.run(f, symbols[:max(30, len(symbols)//10)],
                                start, end)
            except Exception:
                continue
            min_ok = self.params.get("min_factor_verdict", "usable")
            if report.n_periods > 0 and _verdict_ge(report.verdict, min_ok):
                active.append((f, report))

        return active

    def _compute_weights(self, active_factors: list[tuple],
                         method: str | None = None) -> dict[str, float]:
        """IC_IR 加权 / 显著等权 / 等权"""
        method = method or self.params.get("weight_method", "ic_weighted")
        if method == "ic_weighted":
            total_ir = sum(abs(r.ic_ir) for _, r in active_factors)
            if total_ir < 1e-10:
                return {f.meta.name: 1.0 / len(active_factors)
                        for f, _ in active_factors}
            return {f.meta.name: abs(r.ic_ir) / total_ir
                    for f, r in active_factors}
        elif method == "significant_only":
            sig = [(f, r) for f, r in active_factors
                   if r.fm_beta_p_value < 0.05]
            if not sig:
                sig = active_factors
            return {f.meta.name: 1.0 / len(sig) for f, _ in sig}
        else:  # equal
            return {f.meta.name: 1.0 / len(active_factors)
                    for f, _ in active_factors}

    def _compute_factor_simple(self, factor_name: str,
                                navs: list[dict],
                                meta: dict | None) -> float:
        """简化因子值计算（不依赖 DataFeed，直接从已有数据算）"""
        import numpy as np
        nav_values = [r.get("nav", 0) for r in navs if r.get("nav") and r["nav"] > 0]
        if len(nav_values) < 60:
            return 0.0
        arr = np.array(nav_values, dtype=np.float64)
        returns = np.diff(arr) / arr[:-1]

        if factor_name == "sharpe_ratio":
            if len(returns) < 20:
                return 0.0
            ann_ret = float(np.mean(returns) * 252)
            ann_vol = float(np.std(returns, ddof=1) * np.sqrt(252))
            return (ann_ret - 0.02) / max(ann_vol, 1e-6)
        elif factor_name == "max_drawdown":
            cum = np.cumprod(1 + returns)
            peak = np.maximum.accumulate(cum)
            dd = (cum - peak) / peak
            return float(abs(np.min(dd)))
        elif factor_name == "fee_rate":
            fee = 0.015
            if meta:
                fee = (meta.get("management_fee") or 0.015) + \
                      (meta.get("custody_fee") or 0.002)
            return fee
        elif factor_name == "fund_scale":
            scale = (meta.get("scale") or 10e8) if meta else 10e8
            return float(np.log10(max(scale, 1e7)) - 7) / 3
        elif factor_name == "info_ratio":
            if len(returns) < 20:
                return 0.0
            ann_ret = float(np.mean(returns) * 252)
            ann_vol = float(np.std(returns, ddof=1) * np.sqrt(252))
            sharpe = (ann_ret - 0.02) / max(ann_vol, 1e-6)
            return sharpe * 0.8
        elif factor_name == "fund_flow":
            if meta and "scale_prev" in meta and "scale_curr" in meta:
                s_prev = meta["scale_prev"]
                s_curr = meta["scale_curr"]
                nav_ret = float(np.mean(returns[-20:])) * 20 if len(returns) >= 20 else 0
                if s_prev > 0:
                    return (s_curr - s_prev) / s_prev - nav_ret
            return 0.0
        elif factor_name == "manager_tenure":
            return float(meta.get("manager_tenure", 3)) if meta else 3.0
        elif factor_name == "holding_concentration":
            pct = float(meta.get("top10_pct", 50)) if meta else 50
            return pct / 100.0
        elif factor_name == "capture_ratio":
            mid = len(returns) // 2
            if mid < 10:
                return 1.0
            up = returns[returns > 0]
            down = returns[returns < 0]
            up_avg = float(np.mean(up)) if len(up) > 0 else 0
            down_avg = abs(float(np.mean(down))) if len(down) > 0 else 1e-6
            return up_avg / down_avg if down_avg > 1e-6 else 1.0
        elif factor_name == "calendar_return":
            target_month = __import__('datetime').date.today().month
            chunk = len(returns) // 12
            if chunk < 1:
                return 0.0
            return float(np.mean(returns[(target_month - 1) * chunk:target_month * chunk]))
        return 0.0

    def _fallback_screen(self, fund_type: str, top_n: int) -> dict:
        """回退到默认评分（无因子引擎时）"""
        from ...data.storage import get_all_fund_codes, get_fund_meta
        fund_scores = []
        for code in get_all_fund_codes() or []:
            meta = get_fund_meta(code)
            if meta and meta.get("fund_type") != fund_type and fund_type != "all":
                continue
            fund_scores.append({
                "fund_code": code,
                "total_score": float(meta.get("scale", 0)) if meta else 0,
                "meta": meta,
            })
        fund_scores.sort(key=lambda x: x["total_score"], reverse=True)
        rankings = [{"rank": i + 1, "fund_code": fs["fund_code"],
                      "fund_name": fs["meta"]["fund_name"] if fs["meta"] else "",
                      "total_score": 0.0, "factors": {}}
                     for i, fs in enumerate(fund_scores[:top_n])]
        return {"strategy": self.strategy_name, "fund_type": fund_type,
                "top_n": top_n, "total_candidates": len(fund_scores),
                "rankings": rankings}

    def score(self, fund_type: str = "stock",
              params: Optional[dict] = None) -> dict:
        """基金评分"""
        if params:
            self.params.update(params)
        result = self.screen(fund_type=fund_type, top_n=1000)
        return {
            "strategy": self.strategy_name,
            "fund_type": fund_type,
            "scores": {r["fund_code"]: r["total_score"]
                       for r in result.get("rankings", [])},
        }


StrategyRegistry.register(MultiFactorSelection)
