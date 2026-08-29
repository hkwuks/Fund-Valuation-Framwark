"""FundQuant API 路由 — 完整实现"""

import uuid
import asyncio
from functools import partial
from datetime import date, datetime
from typing import Optional, List, Any
import numpy as np
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from loguru import logger

from ..fund_quant.core.models import (
    FundSignal, FundQuantResult, BacktestConfig, BacktestResult,
    RiskMetrics, CostModelConfig, FusionSignal, TYPE_COMPAT,
)
from ..fund_quant.core.enums import SignalType, Direction
from ..fund_quant.data.storage import (
    init_db, get_signals, save_backtest_result, get_backtest_result,
    list_backtest_results, get_nav_history, get_fund_meta, save_nav_points,
    get_index_nav_prices,
    get_bond_yield_data,
    get_etf_market_data,
    compute_tracking_errors,
)
from ..fund_quant.data.collector import fund_data_collector
from ..fund_quant.data.quality import data_quality_checker
from ..fund_quant.signal.output import signal_output_service
from ..fund_quant.risk.metrics import risk_metrics_calculator

router = APIRouter(prefix="/fund-quant", tags=["基金量化"])

# TYPE_COMPAT 定义在 backend.fund_quant.core.models 中


# ── 请求/响应模型 ──

class SelectionRequest(BaseModel):
    fund_type: str = "stock"
    top_n: int = 10
    strategy: str = "multi_factor"  # multi_factor | rating_enhanced
    params: dict = {}


class BacktestRequest(BaseModel):
    strategy_name: str
    fund_codes: List[str]
    start_date: str
    end_date: str
    initial_capital: float = 100000.0
    rebalance_freq: str = "monthly"
    params: dict = {}


class DataCollectRequest(BaseModel):
    fund_codes: List[str]
    years: int = 5


class EvaluatePoolRequest(BaseModel):
    fund_codes: List[str]
    force: bool = False


class FactorEvaluateRequest(BaseModel):
    factor_name: str
    fund_codes: List[str]
    start_date: date
    end_date: date


# ── 初始化 ──
init_db()
logger.info("FundQuant 数据库已初始化")


# ── 策略管理 ──

@router.get("/strategy/list")
async def list_strategies():
    """列出可用策略（AuroraCore 统一注册表）"""
    from core.strategy import StrategyRegistry
    import backend.fund_quant.adapter as _adapter  # noqa: F401 — 触发注册
    strategies = []
    for name, cls in StrategyRegistry.list_all().items():
        strategies.append({
            "name": name,
            "type": getattr(cls, "strategy_type", ""),
            "description": getattr(cls, "description", ""),
            "default_params": getattr(cls, "default_params", {}),
        })
    return {"success": True, "data": strategies}


@router.get("/strategy/params/{name}")
async def get_strategy_params(name: str):
    """获取策略参数（先查 AuroraCore，再回退旧 FundStrategyBase）"""
    from core.strategy import StrategyRegistry
    import backend.fund_quant.adapter as _adapter  # noqa: F401
    # 1. AuroraCore 统一注册表
    if name in StrategyRegistry.list_all():
        cls = StrategyRegistry.get(name)
        return {"success": True, "data": {
            "name": name,
            "type": getattr(cls, "strategy_type", ""),
            "description": getattr(cls, "description", ""),
            "default_params": getattr(cls, "default_params", {}),
            "param_ranges": getattr(cls, "param_ranges", {}),
            "param_choices": getattr(cls, "param_choices", {}),
        }}
    # 2. 旧 FundStrategyBase（selection 等策略）
    from ..fund_quant.strategy.base import StrategyRegistry as OldRegistry
    old_reg = OldRegistry()
    strategy = await asyncio.to_thread(old_reg.get_strategy, name)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"策略 {name} 未找到")
    return {"success": True, "data": {
        "name": strategy.strategy_name,
        "type": strategy.strategy_type,
        "description": strategy.description,
        "default_params": strategy.default_params,
        "param_ranges": strategy.param_ranges,
    }}


# ── 因子评价 ──

@router.get("/factor/list")
async def list_factors(domain: Optional[str] = Query("fund"), category: Optional[str] = Query(None)):
    """列出已注册因子及元数据。"""
    from ..core.factor.registry import FactorRegistry
    from ..fund_quant.factors import register_fund_factors

    register_fund_factors()
    return {"success": True, "data": [
        {
            "name": meta.name,
            "display_name": meta.display_name,
            "category": meta.category,
            "domain": meta.domain,
            "description": meta.description,
            "direction": meta.direction,
            "params": meta.params,
            "formula": meta.formula,
            "min_history_days": meta.min_history_days,
            "reference": meta.reference,
            "fund_types": meta.fund_types,
        }
        for meta in FactorRegistry.list(domain=domain, category=category)
    ]}


@router.post("/factor/evaluate")
async def evaluate_factor(req: FactorEvaluateRequest):
    """评价单个基金因子（IC、分组收益、FM、衰减和换手率）。"""
    if not req.fund_codes:
        raise HTTPException(status_code=400, detail="fund_codes 不能为空")
    if req.start_date > req.end_date:
        raise HTTPException(status_code=400, detail="start_date 不能晚于 end_date")

    from ..core.factor.registry import FactorRegistry
    from ..core.factor.evaluation import EvaluationEngine
    from ..fund_quant.data.feed import NavFactorFeed
    from ..fund_quant.factors import register_fund_factors

    register_fund_factors()
    try:
        factor_cls = FactorRegistry.get(req.factor_name)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"因子 {req.factor_name} 未找到") from exc
    meta = factor_cls.meta
    if meta.domain != "fund":
        raise HTTPException(status_code=400, detail="仅支持基金域因子")

    feed = NavFactorFeed()
    report = await asyncio.to_thread(
        EvaluationEngine(feed).run,
        factor_cls(), req.fund_codes, req.start_date, req.end_date,
    )
    return {"success": True, "data": {
        **report.__dict__,
        "evaluation_period": [d.isoformat() for d in report.evaluation_period],
    }}


# ── 选基筛选 ──

@router.post("/selection/screen")
async def selection_screen(req: SelectionRequest):
    """基金筛选"""
    # 策略选择
    if req.strategy == "rating_enhanced":
        from ..fund_quant.strategy.selection.rating_enhanced import RatingEnhancedSelection
        strategy = RatingEnhancedSelection()
        result = await asyncio.to_thread(
            partial(strategy.screen, fund_type=req.fund_type, top_n=req.top_n, params=req.params))
        return {"success": True, "data": result}

    from ..fund_quant.strategy.selection.multi_factor import MultiFactorSelection
    strategy = MultiFactorSelection()

    # 兼容旧值映射
    fund_type = TYPE_COMPAT.get(req.fund_type, req.fund_type)

    # 指数基金使用独立的 5 维度评分策略
    if fund_type == "index":
        from ..fund_quant.strategy.selection.index_selection import IndexSelectionStrategy
        idx_strategy = IndexSelectionStrategy()
        # 注入 ETF 市场数据
        etf_data = await asyncio.to_thread(get_etf_market_data)
        if etf_data:
            idx_strategy._state["liquidity_data"] = etf_data.get("liquidity", {})
            idx_strategy._state["premium_vol_data"] = etf_data.get("premium", {})
        # 对每个候选基金计算跟踪误差
        from ..fund_quant.data.storage import get_all_fund_codes, get_nav_history
        tracking = {}
        all_codes = await asyncio.to_thread(get_all_fund_codes) or []
        for code in all_codes:
            navs = await asyncio.to_thread(get_nav_history, code, limit=120)
            nav_vals = [r["nav"] for r in navs if r.get("nav")]
            te = await asyncio.to_thread(compute_tracking_errors, code, nav_vals)
            if te is not None:
                tracking[code] = te
        idx_strategy._state["tracking_errors"] = tracking

        result = await asyncio.to_thread(
            partial(idx_strategy.screen, fund_type="index", top_n=req.top_n, params=req.params))
        return {"success": True, "data": result}

    # 校验请求的 fund_type 是否在策略适用范围内
    # TYPE_COMPAT 会把 "stock"→"equity" 等旧名映射到新名，而 applicable_fund_types
    # 用的是策略原始类型名，两者都校验，避免误判（否则多因子策略永远返回空）
    if (req.fund_type not in strategy.applicable_fund_types
            and fund_type not in strategy.applicable_fund_types):
        # commodity/fof 等无 selection 策略的类型 → 返回空结果而非 400
        return {"success": True, "data": {
            "strategy": strategy.strategy_name,
            "fund_type": fund_type,
            "top_n": req.top_n,
            "rankings": [],
            "total_candidates": 0,
            "message": f"所选类型 '{req.fund_type}' 暂不支持 selection 策略",
        }}

    result = await asyncio.to_thread(partial(strategy.screen, fund_type=fund_type, top_n=req.top_n, params=req.params))
    return {"success": True, "data": result}


@router.post("/selection/score")
async def selection_score(req: SelectionRequest):
    """基金评分"""
    from ..fund_quant.strategy.selection.multi_factor import MultiFactorSelection
    strategy = MultiFactorSelection()
    result = await asyncio.to_thread(partial(strategy.score, fund_type=req.fund_type, params=req.params))
    return {"success": True, "data": result}


# ── 策略资产配置信号（策略为中心，非 per-fund）──

class StrategyAllocationRequest(BaseModel):
    fund_codes: List[str] = []
    capital: float = 100000  # 总资产（用于计算买入金额）
    params: dict = {}

def _load_recent_navs(fund_codes: list[str], days: int = 250) -> dict[str, list[dict]]:
    """加载每个基金最近 days 个交易日的净值（按日期升序）— 单次批量查询"""
    if not fund_codes:
        return {}
    from ..fund_quant.data.storage import get_nav_histories
    all_navs = get_nav_histories(fund_codes)
    out: dict[str, list[dict]] = {}
    for code in fund_codes:
        navs = all_navs.get(code, [])
        recent = navs[-days:]
        out[code] = [{"date": r["date"], "nav": r.get("nav", 0)} for r in recent if r.get("nav")]
    return out


def _load_nav_series(fund_codes: list[str], cutoff_date: str | None = None,
                     lookback_days: int = 0) -> dict[str, list[float]]:
    """加载基金净值序列（纯 float 列表），供 _compute_weights 使用 — 单次批量查询

    lookback_days > 0 时只保留最近 lookback_days 个交易日的净值，
    避免优化器使用过多历史数据导致权重趋同。
    """
    if not fund_codes:
        return {}
    from ..fund_quant.data.storage import get_nav_histories
    # cutoff_date 由内存过滤，避免为每个策略各做一次 SQL；全量一次取回后切片
    all_navs = get_nav_histories(fund_codes)
    nav_series: dict[str, list[float]] = {}
    for code in fund_codes:
        navs = all_navs.get(code, [])
        if cutoff_date:
            navs = [r for r in navs if r.get("date", "") <= cutoff_date]
        vals = [float(r["nav"]) for r in navs if r.get("nav") and float(r["nav"]) > 0]
        if vals:
            nav_series[code] = vals[-lookback_days:] if lookback_days > 0 else vals
    return nav_series


def _etf_rotation_current_signal(fund_codes: list[str], params: dict, capital: float = 100000) -> dict:
    """etf_rotation 当前配置：动量评分 → Top-N 权重 + 买入金额"""
    import numpy as np
    momentum_days = int(params.get("momentum_days", 25))
    top_n = int(params.get("top_n", 1))
    buy_th = float(params.get("buy_threshold", 0.0))

    nav_dict = _load_recent_navs(fund_codes, momentum_days + 10)
    scores: dict[str, float] = {}
    for code, records in nav_dict.items():
        vals = [r["nav"] for r in records if r["nav"] > 0]
        if len(vals) < momentum_days + 5:
            continue
        recent = vals[-(momentum_days + 5):]
        arr = np.array(recent, dtype=np.float64)
        log_p = np.log(arr)
        x = np.arange(len(log_p))
        slope, _ = np.polyfit(x, log_p, 1)
        annualized = np.exp(slope * 250) - 1
        y_pred = slope * x + (np.mean(log_p) - slope * np.mean(x))
        ss_res = float(np.sum((log_p - y_pred) ** 2))
        ss_tot = float(np.sum((log_p - np.mean(log_p)) ** 2))
        r2 = max(0.0, min(1.0, 1 - (ss_res / max(ss_tot, 1e-10))))
        scores[code] = annualized * r2

    if not scores:
        return {"strategy": "etf_rotation_aurora", "direction": "hold", "weights": {},
                "confidence": 0, "reason": "净值数据不足", "top_holdings": [], "buy_amounts": {},
                "momentum_rank": []}

    ranked = sorted(scores, key=lambda c: scores[c], reverse=True)
    # 动量排名（全部，供前端展示候选）
    momentum_rank = [{"fund_code": c, "score": round(scores[c], 4), "rank": i + 1}
                     for i, c in enumerate(ranked) if scores[c] != 0]
    top = ranked[:top_n]

    if scores[top[0]] < buy_th:
        return {"strategy": "etf_rotation_aurora", "direction": "hold", "weights": {},
                "confidence": 0, "buy_amounts": {},
                "reason": f"最高动量 {scores[top[0]]:.4f} 低于阈值 {buy_th}，空仓持币",
                "top_holdings": [], "momentum_rank": momentum_rank}

    weight = 1.0 / len(top)
    top_holdings = [{"fund_code": c, "weight": round(weight, 4),
                     "score": round(scores[c], 4)} for c in top]
    buy_amounts = {c: round(capital * weight, 2) for c in top}
    return {"strategy": "etf_rotation_aurora", "direction": "buy",
            "weights": {c: round(weight, 4) for c in top},
            "confidence": min(abs(scores[top[0]]) * 10, 1.0),
            "capital": round(capital, 2),
            "buy_amounts": buy_amounts,
            "reason": f"动量Top{top_n}: {'  '.join(f'{c}({scores[c]:.3f})' for c in top)}",
            "top_holdings": top_holdings, "momentum_rank": momentum_rank}


def _all_weather_current_signal(fund_codes: list[str], params: dict, capital: float = 100000) -> dict:
    """all_weather 当前配置：通过 aurora 策略 _compute_weights() 获取权重（走统一引擎逻辑）"""
    if len(fund_codes) < 2:
        return {"strategy": "all_weather_aurora", "direction": "hold",
                "weights": {}, "confidence": 0, "reason": "基金池不足2只",
                "top_holdings": [], "buy_amounts": {}}
    try:
        import backend.fund_quant.adapter as _adapter  # noqa: F401
        from core.strategy import StrategyRegistry
        cls = StrategyRegistry.get("all_weather_aurora")
        strategy = cls()
        strategy.params.update(params)
    except Exception as e:
        return {"strategy": "all_weather_aurora", "direction": "hold",
                "weights": {}, "confidence": 0, "reason": f"策略加载失败: {e}",
                "top_holdings": [], "buy_amounts": {}}

    weights = strategy._compute_weights()

    # 基金名称（从策略 asset_template 获取）
    name_map = {}
    for code, info in strategy.params.get("asset_template", {}).items():
        name_map[code] = info.get("name", code)
    top_holdings = [{"fund_code": c, "weight": w, "fund_name": name_map.get(c, c)}
                    for c, w in sorted(weights.items(), key=lambda kv: -kv[1]) if w > 0]
    buy_amounts = {c: round(capital * w, 2) for c, w in weights.items() if w > 0}
    mode = strategy.params.get("mode", "fixed")

    return {"strategy": "all_weather_aurora", "direction": "hold",
            "weights": weights, "confidence": 0.8, "capital": round(capital, 2),
            "buy_amounts": buy_amounts, "mode": mode,
            "reason": f"全天候({mode}) 配置",
            "top_holdings": top_holdings}


def _allocation_current_signal_aurora(strategy_name: str, fund_codes: list[str],
                                       params: dict, capital: float = 100000) -> dict:
    """统一配置信号：通过 aurora 策略的 _compute_weights() 获取权重（走统一引擎逻辑）"""
    if len(fund_codes) < 2:
        return {"strategy": strategy_name, "direction": "hold",
                "weights": {}, "confidence": 0, "reason": "基金池不足2只",
                "top_holdings": [], "buy_amounts": {}}
    try:
        import backend.fund_quant.adapter as _adapter  # noqa: F401 — 触发注册
        from core.strategy import StrategyRegistry
        cls = StrategyRegistry.get(strategy_name)
        strategy = cls()
        strategy.params.update(params)
    except Exception as e:
        return {"strategy": strategy_name, "direction": "hold",
                "weights": {}, "confidence": 0, "reason": f"策略加载失败: {e}",
                "top_holdings": [], "buy_amounts": {}}

    nav_series = _load_nav_series(fund_codes, cutoff_date=None,
                                   lookback_days=strategy.params.get("lookback_days", 756))
    valid_codes = [c for c in fund_codes if len(nav_series.get(c, [])) >= 20]
    if len(valid_codes) < 2:
        return {"strategy": strategy_name, "direction": "hold",
                "weights": {}, "confidence": 0, "reason": "净值数据不足",
                "top_holdings": [], "buy_amounts": {}}

    result = strategy._compute_weights(nav_series, valid_codes)
    weights = result or {}
    top_holdings = [{"fund_code": c, "weight": w}
                    for c, w in sorted(weights.items(), key=lambda kv: -kv[1]) if w > 0]
    buy_amounts = {c: round(capital * w, 2) for c, w in weights.items() if w > 0}
    return {"strategy": strategy_name,
            "direction": "buy" if weights else "hold",
            "weights": weights, "confidence": 0.7, "capital": round(capital, 2),
            "buy_amounts": buy_amounts,
            "reason": f"{strategy_name} 配置",
            "top_holdings": top_holdings}


# 内存缓存：fund_codes+params 指纹 -> (expire_ts, response)
_alloc_cache: dict[str, tuple[float, dict]] = {}
_ALLOC_TTL = 8.0  # 秒 — 点击切换策略时命中

def _alloc_cache_key(fund_codes: list[str], params: dict, capital: float) -> str:
    import hashlib, json
    raw = json.dumps({"codes": sorted(fund_codes), "params": params, "capital": capital}, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()

def _etf_rotation_from_nav(nav_dict: dict[str, list[dict]], params: dict, capital: float) -> dict:
    """基于已取回的 nav_dict 计算动量，无额外 DB"""
    import numpy as np
    momentum_days = int(params.get("momentum_days", 25))
    top_n = int(params.get("top_n", 1))
    buy_th = float(params.get("buy_threshold", 0.0))
    scores: dict[str, float] = {}
    for code, records in nav_dict.items():
        vals = [r["nav"] for r in records if r["nav"] > 0]
        if len(vals) < momentum_days + 5:
            continue
        recent = vals[-(momentum_days + 5):]
        arr = np.array(recent, dtype=np.float64)
        log_p = np.log(arr)
        x = np.arange(len(log_p))
        slope, _ = np.polyfit(x, log_p, 1)
        annualized = np.exp(slope * 250) - 1
        y_pred = slope * x + (np.mean(log_p) - slope * np.mean(x))
        ss_res = float(np.sum((log_p - y_pred) ** 2))
        ss_tot = float(np.sum((log_p - np.mean(log_p)) ** 2))
        r2 = max(0.0, min(1.0, 1 - (ss_res / max(ss_tot, 1e-10))))
        scores[code] = annualized * r2
    if not scores:
        return {"strategy": "etf_rotation_aurora", "direction": "hold", "weights": {},
                "confidence": 0, "reason": "净值数据不足", "top_holdings": [], "buy_amounts": {},
                "momentum_rank": []}
    ranked = sorted(scores, key=lambda c: scores[c], reverse=True)
    momentum_rank = [{"fund_code": c, "score": round(scores[c], 4), "rank": i + 1}
                     for i, c in enumerate(ranked) if scores[c] != 0]
    top = ranked[:top_n]
    if scores[top[0]] < buy_th:
        return {"strategy": "etf_rotation_aurora", "direction": "hold", "weights": {},
                "confidence": 0, "buy_amounts": {},
                "reason": f"最高动量 {scores[top[0]]:.4f} 低于阈值 {buy_th}，空仓持币",
                "top_holdings": [], "momentum_rank": momentum_rank}
    weight = 1.0 / len(top)
    top_holdings = [{"fund_code": c, "weight": round(weight, 4), "score": round(scores[c], 4)} for c in top]
    buy_amounts = {c: round(capital * weight, 2) for c in top}
    return {"strategy": "etf_rotation_aurora", "direction": "buy",
            "weights": {c: round(weight, 4) for c in top},
            "confidence": min(abs(scores[top[0]]) * 10, 1.0),
            "capital": round(capital, 2), "buy_amounts": buy_amounts,
            "reason": f"动量Top{top_n}: {'  '.join(f'{c}({scores[c]:.3f})' for c in top)}",
            "top_holdings": top_holdings, "momentum_rank": momentum_rank}

def _aurora_from_nav(strategy_name: str, nav_series: dict[str, list[float]], valid_codes: list[str],
                     params: dict, capital: float) -> dict:
    """基于已取回的 nav_series 计算权重，无额外 DB"""
    try:
        import backend.fund_quant.adapter as _adapter  # noqa: F401
        from core.strategy import StrategyRegistry
        cls = StrategyRegistry.get(strategy_name)
        strategy = cls()
        strategy.params.update(params)
    except Exception as e:
        return {"strategy": strategy_name, "direction": "hold", "weights": {}, "confidence": 0,
                "reason": f"策略加载失败: {e}", "top_holdings": [], "buy_amounts": {}}
    if len(valid_codes) < 2:
        return {"strategy": strategy_name, "direction": "hold", "weights": {}, "confidence": 0,
                "reason": "净值数据不足", "top_holdings": [], "buy_amounts": {}}
    result = strategy._compute_weights(nav_series, valid_codes)
    weights = result or {}
    top_holdings = [{"fund_code": c, "weight": w} for c, w in sorted(weights.items(), key=lambda kv: -kv[1]) if w > 0]
    buy_amounts = {c: round(capital * w, 2) for c, w in weights.items() if w > 0}
    return {"strategy": strategy_name, "direction": "buy" if weights else "hold",
            "weights": weights, "confidence": 0.7, "capital": round(capital, 2),
            "buy_amounts": buy_amounts, "reason": f"{strategy_name} 配置", "top_holdings": top_holdings}

@router.post("/strategy/allocation/current")
async def strategy_allocation_current(req: StrategyAllocationRequest):
    """策略资产配置信号 — 单次批量取数 + 结果缓存，命中时 <20ms"""
    import time
    fund_codes = req.fund_codes or []
    capital = req.capital or 100000
    if not fund_codes:
        return {"success": True, "data": {"strategies": [], "fund_codes": []}}

    # 缓存命中直接返回
    ckey = _alloc_cache_key(fund_codes, req.params, capital)
    now = time.monotonic()
    hit = _alloc_cache.get(ckey)
    if hit and hit[0] > now:
        return hit[1]

    # 单次批量取数，全部策略复用
    from ..fund_quant.data.storage import get_nav_histories
    # 8s TTL 内并发请求合并由上层缓存兜住；此处只做一次 IN 查询
    all_navs_raw = await asyncio.to_thread(get_nav_histories, fund_codes)

    # etf_rotation 用最近 35 天
    etf_recent = {c: [{"date": r["date"], "nav": r.get("nav", 0)} for r in (all_navs_raw.get(c, [])[-35:]) if r.get("nav")] for c in fund_codes}
    # allocation 策略用最近 756 天的 float 序列
    lookback = 756
    nav_series_all: dict[str, list[float]] = {}
    for c in fund_codes:
        vals = [float(r["nav"]) for r in all_navs_raw.get(c, []) if r.get("nav") and float(r["nav"]) > 0]
        if len(vals) >= 20:
            nav_series_all[c] = vals[-lookback:]
    valid_codes_all = [c for c in fund_codes if c in nav_series_all]

    signals: list[dict] = []
    # 每个策略独立 try，互不影响
    try:
        signals.append(_etf_rotation_from_nav(etf_recent, req.params, capital))
    except Exception as e:
        signals.append({"strategy": "etf_rotation_aurora", "direction": "hold", "weights": {}, "confidence": 0,
                        "reason": f"计算异常: {e}", "top_holdings": [], "buy_amounts": {}, "momentum_rank": []})
    # all_weather 不依赖 nav_series（固定模板），单独处理
    try:
        signals.append(_all_weather_current_signal(fund_codes, req.params, capital))
    except Exception as e:
        signals.append({"strategy": "all_weather_aurora", "direction": "hold", "weights": {}, "confidence": 0,
                        "reason": f"计算异常: {e}", "top_holdings": [], "buy_amounts": {}})
    for sn in ("bl_quadrant_aurora", "black_litterman_aurora", "risk_parity_aurora", "hrp_aurora", "max_diversification_aurora"):
        try:
            signals.append(_aurora_from_nav(sn, nav_series_all, valid_codes_all, req.params, capital))
        except Exception as e:
            signals.append({"strategy": sn, "direction": "hold", "weights": {}, "confidence": 0,
                            "reason": f"计算异常: {e}", "top_holdings": [], "buy_amounts": {}})

    resp = {"success": True, "data": {"strategies": signals, "fund_codes": fund_codes}}
    _alloc_cache[ckey] = (now + _ALLOC_TTL, resp)
    # 简单 LRU：超过 64 条淘汰最旧
    if len(_alloc_cache) > 64:
        oldest = min(_alloc_cache, key=lambda k: _alloc_cache[k][0])
        _alloc_cache.pop(oldest, None)
    return resp


# ── 回测 ──

def _run_aurora_metrics(strategy_name: str, fund_codes: list[str],
                        start_date: str, end_date: str,
                        initial_capital: float, params: dict) -> dict:
    """轻量级 aurora 回测 — 只返回指标字典，不保存结果（供 param-scan 使用）"""
    from datetime import date
    from core import BacktestEngine, BacktestConfig, T1ExecutionEngine, FundNavPoint
    from core.strategy import StrategyRegistry

    nav_dict = {}
    for code in fund_codes:
        navs = get_nav_history(code)
        if navs:
            nav_dict[code] = [{"date": r["date"], "nav": r.get("nav", 0)}
                              for r in navs if r.get("nav")]
    if not nav_dict:
        return {"sharpe": 0, "total_return": 0}

    all_points: list[FundNavPoint] = []
    for code, records in nav_dict.items():
        for r in records:
            all_points.append(FundNavPoint(
                fund_code=code,
                date=date.fromisoformat(r["date"]),
                nav=r["nav"],
            ))
    all_points.sort(key=lambda p: (p.date, p.fund_code))

    import backend.fund_quant.adapter as _adapter  # noqa: F401
    strategy = StrategyRegistry.get(strategy_name)()
    strategy.params.update(params)
    execution = T1ExecutionEngine(confirmation_delay=1)
    execution.set_capital(initial_capital)

    engine = BacktestEngine(BacktestConfig(initial_capital=initial_capital))
    engine.set_strategy(strategy)
    engine.set_executor(execution)
    from ..fund_quant.adapter import FundCostModelAdapter
    engine.set_cost_model(FundCostModelAdapter())
    engine.set_data(all_points)
    report = engine.run()

    eq = report.equity_curve
    equities = [e.get("equity", 0) for e in eq]
    total_return = (equities[-1] / equities[0] - 1) if equities and equities[0] > 0 else 0
    n_date = len(set(p.date for p in all_points))
    total_days = max(n_date, 1)
    ann_return = (1 + total_return) ** (252 / total_days) - 1
    daily_ret = [(equities[i] - equities[i-1]) / equities[i-1]
                 for i in range(1, len(equities)) if equities[i-1] > 0]
    vol = float(np.std(daily_ret, ddof=1)) * np.sqrt(252) if len(daily_ret) > 1 else 0
    sharpe = ann_return / vol if vol > 0 else 0
    neg_rets = [r for r in daily_ret if r < 0]
    downside_vol = float(np.std(neg_rets, ddof=1)) * np.sqrt(252) if len(neg_rets) > 1 else 1e-6
    sortino = ann_return / downside_vol if downside_vol > 0 else 0
    peak = equities[0] if equities else 1
    mdd = 0.0
    for e in equities:
        if e > peak: peak = e
        dd = (peak - e) / peak if peak > 0 else 0
        mdd = max(mdd, dd)
    wins = sum(1 for r in daily_ret if r > 0)
    win_rate = wins / len(daily_ret) if daily_ret else 0
    calmar = ann_return / mdd if mdd > 0 else 0

    return {
        "sharpe": round(sharpe, 4),
        "total_return": round(total_return, 4),
        "max_drawdown": round(mdd, 4),
        "volatility": round(vol, 4),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "win_rate": round(win_rate, 4),
        "total_trades": report.total_trades,
    }


def _run_backtest_sync(config_dict: dict) -> str:
    """同步回测任务 — 使用 AuroraCore 统一引擎（BacktestEngine）"""
    from datetime import date, timedelta
    from core import BacktestEngine, BacktestConfig, T1ExecutionEngine, FundNavPoint
    from core.strategy import StrategyRegistry

    backtest_id = config_dict.get("backtest_id", f"bt_{uuid.uuid4().hex[:12]}")

    strategy_name = config_dict.get("strategy_name", "")
    fund_codes = config_dict.get("fund_codes", [])
    start = config_dict.get("start_date", "2024-01-01")
    end = config_dict.get("end_date", "2025-12-31")
    initial_capital = config_dict.get("initial_capital", 100000)

    # 获取净值数据
    nav_dict = {}
    for code in fund_codes:
        navs = get_nav_history(code)
        if navs:
            nav_dict[code] = [{"date": r["date"], "nav": r.get("nav", 0)}
                              for r in navs if r.get("nav")]

    if not nav_dict:
        # 模拟数据（与旧行为一致）
        navs_list = []
        d = date.fromisoformat(start) if isinstance(start, str) else start
        ed = date.fromisoformat(end) if isinstance(end, str) else end
        cur = d
        while cur <= ed:
            days = (cur - d).days
            trend = 1.0 + days * 0.002 if days < 150 else 1.0 + (300 - days) * 0.002
            navs_list.append({"date": cur.isoformat(), "nav": round(trend, 4)})
            cur += timedelta(days=1)
        nav_dict[fund_codes[0] if fund_codes else "000001"] = navs_list

    # 构建 FundNavPoint 列表
    all_points: list[FundNavPoint] = []
    for code, records in nav_dict.items():
        for r in records:
            all_points.append(FundNavPoint(
                fund_code=code,
                date=date.fromisoformat(r["date"]),
                nav=r["nav"],
            ))
    all_points.sort(key=lambda p: (p.date, p.fund_code))

    # 构建引擎
    import backend.fund_quant.adapter as _adapter  # noqa: F401
    try:
        strategy_cls = StrategyRegistry.get(strategy_name)
    except KeyError:
        raise RuntimeError(f"策略 {strategy_name} 未注册")

    custom_params = config_dict.get("params", {})
    strategy = strategy_cls()
    strategy.params.update(custom_params)

    execution = T1ExecutionEngine(confirmation_delay=1)
    execution.set_capital(initial_capital)

    engine = BacktestEngine(BacktestConfig(initial_capital=initial_capital))
    engine.set_strategy(strategy)
    engine.set_executor(execution)
    from ..fund_quant.adapter import FundCostModelAdapter
    engine.set_cost_model(FundCostModelAdapter())
    engine.set_data(all_points)

    report = engine.run()

    # 从 equity_curve 计算指标
    eq = report.equity_curve
    equities = [e.get("equity", 0) for e in eq]
    total_return = (equities[-1] / equities[0] - 1) if equities and equities[0] > 0 else 0
    n_date = len(set(p.date for p in all_points))
    total_days = max(n_date, 1)
    ann_return = (1 + total_return) ** (252 / total_days) - 1 if total_days > 0 else 0
    daily_ret = [(equities[i] - equities[i-1]) / equities[i-1]
                 for i in range(1, len(equities)) if equities[i-1] > 0]
    vol = float(np.std(daily_ret, ddof=1)) * np.sqrt(252) if len(daily_ret) > 1 else 0
    sharpe = ann_return / vol if vol > 0 else 0

    # Sortino
    neg_rets = [r for r in daily_ret if r < 0]
    downside_vol = float(np.std(neg_rets, ddof=1)) * np.sqrt(252) if len(neg_rets) > 1 else 1e-6
    sortino = ann_return / downside_vol if downside_vol > 0 else 0

    # 最大回撤
    peak = equities[0] if equities else 1
    mdd = 0.0
    for e in equities:
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak > 0 else 0
        mdd = max(mdd, dd)

    # 胜率
    wins = sum(1 for r in daily_ret if r > 0)
    win_rate = wins / len(daily_ret) if daily_ret else 0

    # Calmar
    calmar = ann_return / mdd if mdd > 0 else 0

    # 保存结果
    from ..fund_quant.core.models import BacktestResult as BResult, BacktestConfig as BConfig
    bt_config = BConfig(
        strategy_name=strategy_name, fund_codes=fund_codes,
        start_date=start, end_date=end,
        initial_capital=initial_capital,
        params=config_dict.get("params", {}),
    )
    result = BResult(
        backtest_id=backtest_id, config=bt_config,
        status="completed",
        total_return=round(total_return, 4),
        annual_return=round(ann_return, 4),
        max_drawdown=round(mdd, 4),
        volatility=round(vol, 4),
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        calmar_ratio=round(calmar, 4),
        win_rate=round(win_rate, 4),
        total_trades=report.total_trades,
        equity_curve=eq,
    )
    result.backtest_id = backtest_id
    save_backtest_result(result)
    logger.info(f"AuroraEngine回测 [{backtest_id}] 完成: 收益 {total_return:.2%}")

    return backtest_id


async def _run_backtest_async(config_dict: dict) -> str:
    """异步回测任务 — AuroraCore 统一引擎（线程池执行）"""
    return await asyncio.to_thread(_run_backtest_sync, config_dict)


@router.post("/backtest/run")
async def run_backtest(req: BacktestRequest):
    """运行回测 (在线程池同步执行)"""
    import json

    backtest_id = f"bt_{uuid.uuid4().hex[:12]}"
    config = BacktestConfig(
        strategy_name=req.strategy_name,
        fund_codes=req.fund_codes,
        start_date=req.start_date,
        end_date=req.end_date,
        initial_capital=req.initial_capital,
        rebalance_freq=req.rebalance_freq,
        params=req.params,
    )

    # 同步执行（在线程池中避免阻塞事件循环）
    config_dict = config.model_dump()
    config_dict["backtest_id"] = backtest_id
    await asyncio.to_thread(_run_backtest_sync, config_dict)

    # 获取结果
    result = await asyncio.to_thread(get_backtest_result, backtest_id)

    return {
        "success": True,
        "data": {
            "backtest_id": backtest_id,
            "status": result["status"] if result else "completed",
            "message": "回测完成",
            "config": {
                "strategy": req.strategy_name,
                "fund_codes": req.fund_codes,
                "period": f"{req.start_date} ~ {req.end_date}",
                "initial_capital": req.initial_capital,
            },
        },
    }


@router.get("/backtest/result/{backtest_id}")
async def get_backtest(backtest_id: str):
    """获取回测结果"""
    result = await asyncio.to_thread(get_backtest_result, backtest_id)
    if not result:
        raise HTTPException(status_code=404, detail="回测结果未找到")

    import json
    payload = dict(result)
    if "result_json" in payload and payload["result_json"]:
        try:
            payload["result"] = json.loads(payload["result_json"])
        except (json.JSONDecodeError, TypeError):
            pass
    if "config_json" in payload and payload["config_json"]:
        try:
            payload["config"] = json.loads(payload["config_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    return {"success": True, "data": payload}


@router.get("/backtest/list")
async def list_backtests(strategy_name: Optional[str] = None, limit: int = 20):
    """列出回测记录"""
    results = await asyncio.to_thread(partial(list_backtest_results, strategy_name=strategy_name, limit=limit))
    return {"success": True, "data": results, "total": len(results)}


@router.post("/backtest/compare")
async def compare_backtests(req: BacktestRequest):
    """多策略对比回测 — 一次提交，并行执行"""
    import json

    # 需要策略名称列表（逗号分隔）
    strategy_names = [s.strip() for s in req.strategy_name.split(",")]
    backtest_ids = []

    for sn in strategy_names:
        bid = f"bt_{uuid.uuid4().hex[:12]}"
        config = BacktestConfig(
            strategy_name=sn,
            fund_codes=req.fund_codes,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.initial_capital,
            rebalance_freq=req.rebalance_freq,
            params=req.params,
        )
        result = BacktestResult(backtest_id=bid, config=config, status="pending")
        await asyncio.to_thread(save_backtest_result, result)
        config_dict = config.model_dump()
        asyncio.create_task(_run_backtest_async(config_dict))
        backtest_ids.append({"strategy": sn, "backtest_id": bid})

    return {"success": True, "data": {"comparison_id": f"cmp_{uuid.uuid4().hex[:8]}",
                                       "backtests": backtest_ids}}


@router.post("/backtest/export/{backtest_id}")
async def export_backtest(backtest_id: str, fmt: str = "json"):
    """导出回测结果 (CSV/JSON)"""
    from fastapi.responses import PlainTextResponse
    import json

    result = await asyncio.to_thread(get_backtest_result, backtest_id)
    if not result:
        raise HTTPException(status_code=404, detail="回测结果未找到")

    payload = dict(result)
    if "result_json" in payload and payload["result_json"]:
        try:
            payload["result"] = json.loads(payload["result_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    if fmt == "csv":
        # 简单CSV导出（权益曲线）
        equity = (payload.get("result") or payload).get("equity_curve", [])
        lines = ["date,total_value"]
        for e in equity:
            lines.append(f"{e.get('date','')},{e.get('total_value','')}")
        csv_content = "\n".join(lines)
        return PlainTextResponse(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=backtest_{backtest_id}.csv"},
        )

    # 默认JSON
    return {"success": True, "data": payload}


# ── 信号 ──

@router.get("/signal/latest")
async def get_latest_signals(fund_code: Optional[str] = None,
                              signal_type: Optional[str] = None):
    """获取最新信号（返回足够多，前端按基金去重保留最佳信号）"""
    signals = await asyncio.to_thread(partial(get_signals, fund_code=fund_code, signal_type=signal_type, limit=200))
    return {"success": True, "data": signals}


@router.get("/signal/history")
async def get_signal_history(fund_code: Optional[str] = None,
                              signal_type: Optional[str] = None,
                              page: int = 1, limit: int = 20):
    """信号历史 (分页)"""
    offset = (page - 1) * limit
    signals = await asyncio.to_thread(partial(get_signals, fund_code=fund_code, signal_type=signal_type, limit=limit, offset=offset))
    return {"success": True, "data": signals, "page": page, "limit": limit, "total": len(signals)}


@router.get("/signal/stream")
async def signal_stream():
    """SSE信号推送"""
    from fastapi.responses import StreamingResponse
    async def event_stream():
        try:
            async for signal in signal_output_service.stream_signals():
                yield f"data: {signal}\n\n"
        except Exception as e:
            logger.error(f"SSE 推送异常: {e}")
    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── 批量信号评估 ──

# 策略 applicable_fund_types 用旧值(stock/hybrid/index)，此处映射到 FundType 新值
_APPLICABLE_TO_NEW = {
    "stock": "equity", "hybrid": "equity", "index": "index",
    "bond": "bond", "qdii": "qdii", "commodity": "commodity",
    "balanced": "balanced",
}


def _fund_type_matches(applicable: list, fund_type: str) -> bool:
    """判断策略适用类型列表是否匹配某基金类型（兼容旧值映射）"""
    if not applicable:
        return True
    for a in applicable:
        if _APPLICABLE_TO_NEW.get(a, a) == fund_type:
            return True
    return False


@router.post("/signal/evaluate-pool")
async def evaluate_pool(req: EvaluatePoolRequest):
    """信号批量评估已废弃（timing 策略已删除，不再批量生成信号）"""
    raise HTTPException(status_code=410, detail="timing 择时策略已废弃，不再支持批量信号评估")


# ── 组合 ──

@router.get("/portfolio/status")
async def portfolio_status():
    """模拟组合状态（扩展版 KPI）— 批量取数，避免逐仓位往返"""
    from ..fund_quant.portfolio.tracker import portfolio_tracker
    status = await asyncio.to_thread(portfolio_tracker.get_status)

    # 净值批量取回，供 extended_status 计算年化/回撤
    try:
        codes = list(status.get("positions", {}).keys())
        if codes:
            from ..fund_quant.data.storage import get_nav_histories
            all_navs = await asyncio.to_thread(get_nav_histories, codes)
            nav_history: dict[str, list] = {
                c: [r.get("nav", 0) for r in (all_navs.get(c, []) or []) if r.get("nav")]
                for c in codes
            }
            status = await asyncio.to_thread(portfolio_tracker.get_extended_status, nav_history)
        else:
            status = await asyncio.to_thread(portfolio_tracker.get_extended_status, {})
    except Exception:
        pass

    # 信号计数保持单次查询
    try:
        signals = await asyncio.to_thread(partial(get_signals, limit=1000))
        buy = sum(1 for s in signals if s.get("direction") == "buy")
        sell = sum(1 for s in signals if s.get("direction") == "sell")
        status["signal_count"] = {"buy": buy, "sell": sell, "hold": 0}
    except Exception:
        pass

    return {"success": True, "data": status}


# ── 风险 ──

@router.get("/risk/metrics")
async def risk_metrics(fund_code: Optional[str] = None):
    """风险指标"""
    if fund_code:
        nav_data = await asyncio.to_thread(get_nav_history, fund_code)
        if nav_data and len(nav_data) > 5:
            nav_values = [p.get("nav", 0) for p in nav_data if p.get("nav") and p["nav"] > 0]
            if len(nav_values) > 5:
                returns = []
                for i in range(1, len(nav_values)):
                    returns.append((nav_values[i] - nav_values[i-1]) / nav_values[i-1])
                metrics = await asyncio.to_thread(risk_metrics_calculator.calculate, returns)
                return {
                    "success": True,
                    "data": {
                        **metrics.model_dump(),
                        "fund_code": fund_code,
                        "nav_count": len(nav_values),
                        "date_range": f"{nav_data[0]['date']} ~ {nav_data[-1]['date']}",
                    },
                }
    return {"success": True, "data": {}}


# ── 数据质量 ──

@router.get("/data/quality/{fund_code}")
async def data_quality(fund_code: str):
    """获取基金数据质量报告"""
    summary = await asyncio.to_thread(data_quality_checker.get_quality_summary, fund_code)
    return {"success": True, "data": summary}


# ── 数据 ──

@router.get("/nav/{fund_code}")
async def get_quant_nav(fund_code: str):
    """获取基金量化模块的净值历史"""
    nav_data = await asyncio.to_thread(get_nav_history, fund_code)
    if not nav_data:
        raise HTTPException(status_code=404, detail=f"基金 {fund_code} 无净值数据")
    return {"success": True, "data": {"fund_code": fund_code, "nav_history": nav_data}}


@router.post("/data/collect")
async def trigger_collection(req: DataCollectRequest):
    """触发数据采集"""
    results = []
    for fund_code in req.fund_codes:
        try:
            points = await fund_data_collector.fetch_nav_history(
                fund_code=fund_code,
                start_date=date.today().replace(year=date.today().year - req.years).strftime("%Y%m%d"),
            )
            if points:
                await asyncio.to_thread(save_nav_points, points)
            results.append({"fund_code": fund_code, "status": "ok", "count": len(points)})
        except Exception as e:
            results.append({"fund_code": fund_code, "status": "error", "message": str(e)})
    return {"success": True, "data": results}


@router.get("/data/status")
async def data_status():
    """数据采集状态"""
    from ..fund_quant.data.storage import get_pending_collections
    pending = await asyncio.to_thread(get_pending_collections)
    return {"success": True, "data": {
        "pending_count": len(pending),
        "pending": pending[:50],  # 只返回前50条
    }}


# ═════════════════════════════════════════
# 因子分析
# ═════════════════════════════════════════


@router.get("/factors/list")
async def factor_list(domain: str = "fund"):
    """列出已注册因子"""
    from backend.core.factor import FactorRegistry
    metas = FactorRegistry.list(domain=domain)
    return {"success": True, "data": [
        {"name": m.name, "display_name": m.display_name,
         "category": m.category, "domain": m.domain,
         "direction": m.direction, "description": m.description}
        for m in metas
    ]}


@router.get("/factors/audit")
async def factor_audit(domain: str = "fund", years: int = 3):
    """因子全景审计"""
    from datetime import date, timedelta
    from backend.core.factor import FactorRegistry, EvaluationEngine, EvalConfig, FactorAudit

    end = date.today()
    start = end - timedelta(days=years * 365)

    class _AuditFeed:
        def get_forward_returns(self, symbols, from_date, to_date):
            return {}
        def get_factor_input(self, symbols, as_of, lookback):
            return []

    ee = EvaluationEngine(_AuditFeed(), EvalConfig(min_stocks_per_period=1))
    audit = FactorAudit(ee)

    try:
        df = audit.audit_all(domain, [], (start, end))
        return {"success": True, "data": df.to_dict(orient="records")}
    except Exception as e:
        return {"success": True, "data": [], "message": str(e)}


@router.get("/factors/exposure/{fund_code}")
async def fund_factor_exposure(fund_code: str, lookback: int = 365):
    """获取单基金因子暴露度 — 计算各注册因子的当前暴露值"""
    import numpy as np
    from backend.core.factor import FactorRegistry
    from ..fund_quant.data.storage import get_nav_history, get_fund_meta

    nav_data = await asyncio.to_thread(get_nav_history, fund_code)
    if not nav_data:
        return {"success": False, "message": f"基金 {fund_code} 净值数据不足"}

    nav_values = [r.get("nav", 0) for r in nav_data if r.get("nav")]
    if len(nav_values) < 20:
        return {"success": False, "message": "净值数据不足20期"}

    nav_arr = np.array(nav_values, dtype=np.float64)
    returns = np.diff(nav_arr) / nav_arr[:-1] if len(nav_arr) >= 2 else np.array([])

    fund_meta = await asyncio.to_thread(get_fund_meta, fund_code)
    fund_name = (fund_meta or {}).get("fund_name", fund_code) if fund_meta else fund_code

    # 获取所有注册的基金域因子
    all_factors = FactorRegistry.list(domain="fund")
    if not all_factors:
        all_factors = FactorRegistry.list(domain="generic")

    # 计算因子暴露值
    computed: dict[str, float] = {}
    for meta in all_factors:
        name = meta.name
        if name == "sharpe_ratio" or name == "sharpe":
            if len(returns) >= 20:
                sr = float(np.mean(returns[-60:]) / (np.std(returns[-60:]) + 1e-10))
                computed[name] = float(np.clip((sr + 2) / 4, 0, 1))
        elif name == "max_drawdown":
            peak = np.maximum.accumulate(nav_arr)
            dd = (nav_arr - peak) / peak
            mdd = float(np.min(dd)) if len(dd) > 0 else 0
            computed[name] = float(np.clip(1 + mdd, 0, 1))
        elif name == "momentum" or name == "momentum_multi":
            if len(returns) >= 60:
                mom = float(np.sum(returns[-60:]))
                computed[name] = float(np.clip((mom + 0.3) / 0.6, 0, 1))
        elif name == "volatility" or name == "volatility_regime":
            if len(returns) >= 20:
                vol = float(np.std(returns[-60:]))
                computed[name] = float(np.clip(1 - vol * 5, 0, 1))
        elif name == "fund_scale":
            computed[name] = 0.5
        elif name == "fee_rate":
            computed[name] = 0.6
        elif name == "fund_flow":
            computed[name] = 0.5
        elif name == "info_ratio":
            if len(returns) >= 60:
                ir = float(np.mean(returns[-60:]) / (np.std(returns[-60:]) + 1e-10) * np.sqrt(252))
                computed[name] = float(np.clip(ir / 2, 0, 1))
        elif name == "capture_ratio":
            computed[name] = 0.5
        elif name == "manager_tenure":
            computed[name] = 0.5
        elif name == "holding_concentration":
            computed[name] = 0.5
        elif name == "calendar_return":
            computed[name] = 0.5
        else:
            computed[name] = 0.5

    # 因子权重和百分位
    n_registered = len(all_factors) if all_factors else 14
    weights = {
        "momentum": 0.25, "sharpe_ratio": 0.20, "max_drawdown": 0.15,
        "fund_flow": 0.12, "fee_rate": 0.10, "fund_scale": 0.08,
        "info_ratio": 0.05, "capture_ratio": 0.03, "manager_tenure": 0.02,
        "volatility": 0.0,
    }

    total_w = sum(weights.get(k, 0.05) for k in computed.keys())
    factors_out = {}
    total_score = 0
    for k, v in computed.items():
        w = weights.get(k, 0.05) / total_w if total_w > 0 else 0.05
        factors_out[k] = {
            "value": round(v, 4),
            "weight": round(w, 4),
            "rank_pct": int(min(max(v * 100, 5), 95)),
        }
        total_score += v * w
    total_score = round(total_score, 4)

    return {
        "success": True,
        "data": {
            "fund_code": fund_code,
            "fund_name": fund_name,
            "factors": factors_out,
            "total_score": total_score,
            "n_funds_in_category": max(50, n_registered * 5),
        },
    }


@router.get("/factors/{name}")
async def factor_detail(name: str):
    """单因子详情"""
    from backend.core.factor import FactorRegistry
    try:
        meta = FactorRegistry.get_meta(name)
        return {"success": True, "data": {
            "name": meta.name, "display_name": meta.display_name,
            "category": meta.category, "domain": meta.domain,
            "description": meta.description, "direction": meta.direction,
            "params": meta.params, "formula": meta.formula,
        }}
    except Exception as e:
        return {"success": False, "message": str(e)}


@router.post("/factors/register")
async def factor_register():
    """注册所有域因子"""
    from backend.core.factor import FactorRegistry
    from backend.fund_quant.adapter import FundDomainAdapter
    from backend.gold.adapter import GoldDomainAdapter

    count_before = FactorRegistry.count()
    FundDomainAdapter().register_factors()
    GoldDomainAdapter().register_factors()
    count_after = FactorRegistry.count()

    return {"success": True, "data": {
        "registered": count_after - count_before,
        "total": count_after,
    }}


# ═══════════════════════════════════════════
# FOF 穿透分析
# ═══════════════════════════════════════════

class FofPenetrateRequest(BaseModel):
    fund_code: str
    nav_limit: int = 200


@router.post("/fof/penetrate")
async def fof_penetrate(req: FofPenetrateRequest):
    """FOF 穿透分析 — 估算底层资产配置

    基于子类先验 + OLS 净值回归，生成穿透后权益/固收仓位。
    """
    from ..fund_quant.analysis.fof_penetration import analyze_fof_penetration_full
    from ..fund_quant.data.storage import get_nav_history, get_fund_meta

    # 获取基金元数据（含 fund_type 原始分类）
    meta = await asyncio.to_thread(get_fund_meta, req.fund_code)
    if not meta:
        raise HTTPException(404, detail=f"基金 {req.fund_code} 未找到")
    fund_type_raw = meta.get("fund_type", "")

    # 获取净值
    nav_data = await asyncio.to_thread(get_nav_history, req.fund_code,
                                        limit=req.nav_limit)
    nav_values = [r["nav"] for r in nav_data if r.get("nav")]

    result = await asyncio.to_thread(
        analyze_fof_penetration_full,
        req.fund_code, fund_type_raw, nav_values,
    )

    # 尝试用定期报告数据增强（Level 5）
    if result.confidence < 0.9:
        try:
            from ..fund_quant.analysis.report_parser import enrich_fof_penetration_with_report
            result = await asyncio.to_thread(
                enrich_fof_penetration_with_report, req.fund_code, result)
        except Exception as e:
            logger.debug(f"FOF {req.fund_code} 报告解析跳过: {e}")

    return {"success": True, "data": {
        "fund_code": result.fund_code,
        "fund_type": result.fund_type,
        "subtype": result.subtype,
        "equity_ratio": result.equity_ratio,
        "bond_ratio": result.bond_ratio,
        "method": result.method,
        "confidence": result.confidence,
        "ols_r_squared": result.ols_r_squared,
        "nav_count": len(nav_values),
        "details": result.details,
    }}


# ── 月度收益 ──

@router.get("/portfolio/monthly-returns")
async def monthly_returns(fund_code: str):
    """获取基金月度收益率矩阵"""
    nav_data = await asyncio.to_thread(get_nav_history, fund_code)
    if not nav_data or len(nav_data) < 30:
        return {"success": True, "data": {"matrix": [], "stats": {}}}

    navs = [(p.get("date", ""), p.get("nav", 0)) for p in nav_data if p.get("nav")]
    if len(navs) < 2:
        return {"success": True, "data": {"matrix": [], "stats": {}}}

    # 按年月分组计算月度收益
    monthly: dict[tuple[int, int], list[float]] = {}
    for i in range(1, len(navs)):
        prev_date, prev_nav = navs[i - 1]
        curr_date, curr_nav = navs[i]
        if prev_nav <= 0: continue
        ret = (curr_nav - prev_nav) / prev_nav
        try:
            y, m = int(curr_date[:4]), int(curr_date[5:7])
            monthly.setdefault((y, m), []).append(ret)
        except (ValueError, IndexError):
            continue

    matrix = []
    positive = negative = 0
    pos_sum = neg_sum = 0.0
    max_pos = max_neg = 0.0
    for (y, m), rets in sorted(monthly.items()):
        avg_ret = sum(rets) / len(rets)
        matrix.append({"year": y, "month": m, "return": round(avg_ret * 100, 2)})
        if avg_ret >= 0:
            positive += 1
            pos_sum += avg_ret
            max_pos = max(max_pos, avg_ret)
        else:
            negative += 1
            neg_sum += avg_ret
            max_neg = min(max_neg, avg_ret)

    stats = {
        "positive_months": positive,
        "total_months": positive + negative,
        "avg_positive": round(pos_sum / positive * 100, 2) if positive else 0,
        "avg_negative": round(neg_sum / negative * 100, 2) if negative else 0,
        "max_positive": round(max_pos * 100, 2),
        "max_negative": round(max_neg * 100, 2),
    }

    return {"success": True, "data": {"matrix": matrix, "stats": stats}}


# ── 归因分析 ──

@router.get("/attribution/brinson")
async def attribution_brinson(fund_codes: str = Query(...), start: str = "2026-01-01", end: str = "2026-06-30"):
    """Brinson 归因分析"""
    from ..fund_quant.backtest.brinson import BrinsonAttribution
    codes = [c.strip() for c in fund_codes.split(",") if c.strip()]
    if not codes:
        raise HTTPException(status_code=400, detail="需要至少一个基金代码")

    # 获取每只基金的净值数据
    periods_data: list[dict] = []
    for code in codes:
        nav_data = await asyncio.to_thread(get_nav_history, code)
        if not nav_data: continue
        navs = [(p.get("date", ""), p.get("nav", 0)) for p in nav_data if p.get("nav") and p.get("date", "").startswith(start[:4])]
        if len(navs) < 2: continue

        # 按季度分组
        quarterly: dict[str, list[float]] = {}
        for i in range(1, len(navs)):
            prev_date, prev_nav = navs[i - 1]
            curr_date, curr_nav = navs[i]
            if prev_nav <= 0: continue
            ret = (curr_nav - prev_nav) / prev_nav
            try:
                ym = curr_date[:7]  # YYYY-MM
                quarterly.setdefault(ym, []).append(ret)
            except: continue

        for ym, rets in quarterly.items():
            periods_data.append({"code": code, "period": ym, "return": sum(rets) / len(rets)})

    if not periods_data:
        return {"success": True, "data": {"periods": [], "cumulative": {}}}

    # 模拟归因：按月份聚合所有基金，等权重组合 vs 基准（简化版）
    from collections import defaultdict
    port_rets: dict[str, list[float]] = defaultdict(list)
    bench_rets: dict[str, list[float]] = defaultdict(list)
    for p in periods_data:
        port_rets[p["period"]].append(p["return"])
    # 基准使用沪深300近似（从 storage 获取）
    try:
        bench_navs = await asyncio.to_thread(get_index_nav_prices, "000300")
        if bench_navs:
            for i in range(1, len(bench_navs)):
                curr_date = bench_navs[i].get("date", "")
                if curr_date.startswith(start[:4]):
                    prev_nav = bench_navs[i-1].get("price", 0)
                    curr_nav = bench_navs[i].get("price", 0)
                    if prev_nav > 0:
                        ym = curr_date[:7]
                        bench_rets[ym].append((curr_nav - prev_nav) / prev_nav)
    except: pass

    # 构建 Brinson 输入 — 简化版：假设 sectors = {"equity": 组合, "bond": 基准}
    attribution = BrinsonAttribution()
    periods_list = []
    total_alloc = total_sel = total_inter = 0.0
    n = 0

    for period in sorted(set(list(port_rets.keys()) + list(bench_rets.keys()))):
        p_ret = (sum(port_rets.get(period, [0])) / len(port_rets.get(period, [1])) * 100) if port_rets.get(period) else 0
        b_ret = (sum(bench_rets.get(period, [0])) / len(bench_rets.get(period, [1])) * 100) if bench_rets.get(period) else 0
        excess = p_ret - b_ret

        # 简化分解：假设 allocation = excess * 0.4, selection = excess * 0.5, interaction = excess * 0.1
        alloc = excess * 0.4
        sel = excess * 0.5
        inter = excess * 0.1

        periods_list.append({
            "date": period,
            "allocation": round(alloc, 2),
            "selection": round(sel, 2),
            "interaction": round(inter, 2),
            "total": round(p_ret, 2),
        })
        total_alloc += alloc
        total_sel += sel
        total_inter += inter
        n += 1

    # 基准收益（简化：标普/沪深300年化近似）
    bench_total = sum(sum(bench_rets.get(p, [0])) / len(bench_rets.get(p, [1])) * 100 for p in bench_rets) if bench_rets else 0

    return {
        "success": True,
        "data": {
            "periods": periods_list,
            "cumulative": {
                "allocation": round(total_alloc, 2),
                "selection": round(total_sel, 2),
                "interaction": round(total_inter, 2),
                "excess": round(total_alloc + total_sel + total_inter, 2),
                "benchmark": round(bench_total, 2),
                "total": round(sum(p["total"] for p in periods_list), 2),
            },
        },
    }


# ── 回测后分析 ──

class AnalysisRequest(BaseModel):
    backtest_id: str
    n_simulations: int = 1000

@router.post("/backtest/analysis")
async def backtest_analysis(req: AnalysisRequest):
    """对已完成回测运行过拟合/显著性/Monte Carlo/市场状态检测"""
    result = await asyncio.to_thread(get_backtest_result, req.backtest_id)
    if not result:
        raise HTTPException(status_code=404, detail="回测结果未找到")

    # 解析 equity_curve → daily_returns
    import json
    payload = dict(result)
    if "result_json" in payload and payload["result_json"]:
        try:
            payload["result"] = json.loads(payload["result_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    result_data = payload.get("result") or {}
    equity = result_data.get("equity_curve", [])
    if len(equity) < 3:
        raise HTTPException(status_code=400, detail="净值曲线过短，无法分析")

    values = [e.get("equity", e.get("total_value", 0)) for e in equity]
    daily_rets = [(values[i] - values[i-1]) / values[i-1] for i in range(1, len(values)) if values[i-1] > 0]

    sharpe = result_data.get("sharpe_ratio", 0.0)
    total_return = result_data.get("total_return", 0.0)
    total_trades = result_data.get("total_trades", 0)

    # 区间年数
    dates = [e.get("date", "") for e in equity if e.get("date")]
    years = 1.0
    if len(dates) >= 2:
        try:
            d0 = datetime.strptime(dates[0][:10], "%Y-%m-%d")
            d1 = datetime.strptime(dates[-1][:10], "%Y-%m-%d")
            years = max((d1 - d0).days / 365.25, 0.1)
        except (ValueError, IndexError):
            pass

    from ..fund_quant.backtest.analysis_provider import analysis_provider
    analysis = analysis_provider.analyze(
        daily_returns=daily_rets,
        sharpe=sharpe,
        years=years,
        total_return=total_return,
        total_trades=total_trades,
        n_simulations=req.n_simulations,
    )

    return {"success": True, "data": {"backtest_id": req.backtest_id, "analysis": analysis}}


# ── 参数扫描 ──

class ParamScanRequest(BaseModel):
    strategy_name: str
    fund_codes: List[str]
    start_date: str
    end_date: str
    initial_capital: float = 100000.0
    mode: str = "single_param"           # single_param / grid_search / random_search
    param_name: str = ""
    param_values: List[Any] = []
    param_grid: dict = {}
    param_dist: dict = {}
    fixed_params: dict = {}
    n_iter: int = 50

@router.post("/backtest/param-scan")
async def backtest_param_scan(req: ParamScanRequest):
    """参数敏感性扫描 — 单参数/网格搜索/随机搜索（AuroraCore 统一引擎）"""
    from core.strategy import StrategyRegistry
    import backend.fund_quant.adapter as _adapter  # noqa: F401

    try:
        StrategyRegistry.get(req.strategy_name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"策略 {req.strategy_name} 未注册")

    def _run_with_params(params: dict) -> dict:
        return _run_aurora_metrics(
            req.strategy_name, req.fund_codes,
            req.start_date, req.end_date,
            req.initial_capital, params,
        )

    from ..fund_quant.backtest.param_scanner import ParameterScanner
    scanner = ParameterScanner(_run_with_params)

    if req.mode == "single_param":
        if not req.param_name or not req.param_values:
            raise HTTPException(status_code=400, detail="single_param 需要 param_name + param_values")
        scan_result = scanner.single_param(req.param_name, req.param_values, req.fixed_params)
    elif req.mode == "grid_search":
        if not req.param_grid:
            raise HTTPException(status_code=400, detail="grid_search 需要 param_grid")
        scan_result = scanner.grid_search(req.param_grid, req.fixed_params)
    elif req.mode == "random_search":
        if not req.param_dist:
            raise HTTPException(status_code=400, detail="random_search 需要 param_dist")
        scan_result = scanner.random_search(req.param_dist, n_iter=req.n_iter, fixed_params=req.fixed_params)
    else:
        raise HTTPException(status_code=400, detail=f"未知扫描模式: {req.mode}")

    return {
        "success": True,
        "data": {
            "mode": scan_result.mode,
            "param_names": scan_result.param_names,
            "results": scan_result.results,
            "n_iterations": scan_result.n_iterations,
            "sensitivity_score": scan_result.sensitivity_score,
            "stability_region": scan_result.stability_region,
        },
    }


# ── 向量化回测 ──

class VectorizedBacktestRequest(BaseModel):
    fund_codes: List[str]
    start_date: str
    end_date: str
    initial_capital: float = 100000.0
    strategy_name: str = "etf_rotation_aurora"  # etf_rotation_aurora | all_weather_aurora
    params: dict = {}  # 策略参数（如 all_weather 的 mode）

@router.post("/backtest/run-vectorized")
async def run_vectorized_backtest(req: VectorizedBacktestRequest):
    """向量化回测（公式策略全 numpy 计算，无事件循环）"""
    from ..fund_quant.data.storage import get_nav_history
    import numpy as np

    nav_dict: dict[str, list[float]] = {}
    for code in req.fund_codes:
        navs = await asyncio.to_thread(
            get_nav_history, code, req.start_date, req.end_date
        )
        if navs:
            nav_dict[code] = [r.get("nav", 0) for r in navs if r.get("nav")]

    if not nav_dict:
        raise HTTPException(status_code=400, detail="无净值数据")

    # 对齐日期：取所有基金的交集（简化版：按最短截断）
    min_len = min(len(v) for v in nav_dict.values())
    nav_matrix = np.array([v[-min_len:] for v in nav_dict.values()])

    @np.vectorize
    def equal_weight_signal(nav: float) -> float:
        return 1.0  # placeholder: 等权重

    # 等权重策略 (weight_func: n_funds x n_days → 每期求和=1)
    def equal_weight_strategy(nm: np.ndarray) -> np.ndarray:
        n_funds, n_days = nm.shape
        w = np.ones((n_funds, n_days)) / n_funds
        return w

    from ..fund_quant.backtest.vectorized_engine import VectorizedBacktestEngine
    vbe = VectorizedBacktestEngine()
    vr = vbe.run(nav_matrix, equal_weight_strategy, req.initial_capital)

    return {
        "success": True,
        "data": {
            "total_return": round(float(vr.total_return), 4),
            "annual_return": round(float(vr.annual_return), 4),
            "sharpe_ratio": round(float(vr.sharpe_ratio), 4),
            "max_drawdown": round(float(vr.max_drawdown), 4),
            "volatility": round(float(vr.volatility), 4),
            "n_trading_days": vr.n_trading_days,
            "funds": req.fund_codes,
            "strategy": "equal_weight",
        },
    }


@router.post("/backtest/aurora-run")
async def run_aurora_backtest(req: VectorizedBacktestRequest):
    """AuroraCore 统一引擎回测（配置策略用）"""
    from ..fund_quant.data.storage import get_nav_history
    from core import BacktestEngine, BacktestConfig, BacktestReport
    from core import T1ExecutionEngine, FundNavPoint, Direction
    from core.strategy import StrategyRegistry
    from ..fund_quant.adapter import FundCostModelAdapter
    import numpy as np

    # 1. 加载多基金净值
    nav_dict: dict[str, list[dict]] = {}
    for code in req.fund_codes:
        navs = await asyncio.to_thread(get_nav_history, code, req.start_date, req.end_date)
        if navs:
            nav_dict[code] = [{"date": r["date"], "nav": r.get("nav", 0)} for r in navs if r.get("nav")]

    if not nav_dict:
        raise HTTPException(status_code=400, detail="无净值数据")

    # 2. 按日期交错构造 FundNavPoint 列表
    from datetime import date
    all_points: list[FundNavPoint] = []
    for code, records in nav_dict.items():
        for r in records:
            all_points.append(FundNavPoint(
                fund_code=code,
                date=date.fromisoformat(r["date"]),
                nav=r["nav"],
            ))
    all_points.sort(key=lambda p: (p.date, p.fund_code))

    # 3. 构建引擎（adapter 导入触发注册）
    import backend.fund_quant.adapter as _adapter  # noqa: F401
    strategy_cls = StrategyRegistry.get(req.strategy_name)
    if not strategy_cls:
        raise HTTPException(status_code=404, detail=f"策略 {req.strategy_name} 未注册")

    strategy = strategy_cls()
    strategy.params.update(req.params)
    execution = T1ExecutionEngine(confirmation_delay=1)
    execution.set_capital(req.initial_capital)

    engine = BacktestEngine(BacktestConfig(initial_capital=req.initial_capital))
    engine.set_strategy(strategy)
    engine.set_executor(execution)
    engine.set_cost_model(FundCostModelAdapter())
    engine.set_data(all_points)

    # 4. 运行
    report = engine.run()
    eq = report.equity_curve
    prices = [e.get("close", 0) for e in eq]
    equities = [e.get("equity", 0) for e in eq]

    # 5. 计算指标
    total_return = (equities[-1] / equities[0] - 1) if equities and equities[0] > 0 else 0
    n_date = len(set(p.date for p in all_points))  # 实际交易日数（去重基金）
    total_days = max(n_date, 1)
    ann_return = (1 + total_return) ** (252 / total_days) - 1 if total_days > 0 else 0
    daily_ret = [(equities[i] - equities[i - 1]) / equities[i - 1]
                 for i in range(1, len(equities)) if equities[i - 1] > 0]
    vol = float(np.std(daily_ret, ddof=1)) * np.sqrt(252) if len(daily_ret) > 1 else 0
    sharpe = ann_return / vol if vol > 0 else 0

    # 最大回撤
    peak = equities[0]
    mdd = 0
    for e in equities:
        if e > peak: peak = e
        dd = (peak - e) / peak if peak > 0 else 0
        mdd = max(mdd, dd)

    return {
        "success": True,
        "data": {
            "total_return": round(total_return, 4),
            "annual_return": round(ann_return, 4),
            "sharpe_ratio": round(sharpe, 4),
            "max_drawdown": round(mdd, 4),
            "volatility": round(vol, 4),
            "n_trading_days": n_date,
            "n_trades": report.total_trades,
            "funds": req.fund_codes,
            "strategy": req.strategy_name,
        },
    }


# ── 模拟交易 (Paper Trader — 统一引擎) ──

class PaperTradeStartRequest(BaseModel):
    strategy_name: str
    fund_codes: List[str]
    initial_capital: float = 100000.0

class PaperTradeRunRequest(BaseModel):
    paper_trade_id: str

from ..fund_quant.paper.paper_engine import fund_paper_engine

@router.post("/paper-trade/start")
async def paper_trade_start(req: PaperTradeStartRequest):
    """启动一个新的模拟交易会话（统一引擎）"""
    session = fund_paper_engine.start(
        strategy_name=req.strategy_name,
        symbols=req.fund_codes,
        initial_capital=req.initial_capital,
    )
    return {
        "success": True,
        "data": {
            "paper_trade_id": session.session_id,
            "strategy_name": session.strategy_name,
            "fund_codes": session.symbols,
            "initial_capital": session.initial_capital,
            "status": session.status,
        },
    }

@router.post("/paper-trade/run")
async def paper_trade_run(req: PaperTradeRunRequest):
    """执行一天的模拟交易（统一引擎）"""
    from ..fund_quant.data.storage import get_nav_history

    session = fund_paper_engine.get_status(req.paper_trade_id)
    if session is None:
        raise HTTPException(status_code=404, detail="模拟交易会话未找到")
    if session.status != "running":
        return {"success": True, "data": {"status": session.status, "message": "已停止"}}

    # 获取所有基金净值数据
    nav_data = {}
    for code in session.symbols:
        navs = await asyncio.to_thread(get_nav_history, code)
        if navs:
            nav_data[code] = navs

    updated = fund_paper_engine.daily_run(req.paper_trade_id, nav_data)
    return {
        "success": True,
        "data": {
            "paper_trade_id": req.paper_trade_id,
            "status": updated.status if updated else "unknown",
            "equity_count": len(updated.equity_curve) if updated else 0,
        },
    }

@router.post("/paper-trade/stop")
async def paper_trade_stop(req: PaperTradeRunRequest):
    """停止模拟交易会话"""
    state = fund_paper_engine.stop(req.paper_trade_id)
    if state is None:
        raise HTTPException(status_code=404, detail="模拟交易会话未找到")
    return {"success": True, "data": {"paper_trade_id": req.paper_trade_id, "status": "stopped"}}

@router.get("/paper-trade/list")
async def paper_trade_list():
    """列出所有模拟交易会话"""
    summaries = fund_paper_engine.list_sessions()
    return {"success": True, "data": summaries}

@router.get("/paper-trade/status/{paper_trade_id}")
async def paper_trade_status(paper_trade_id: str):
    """获取模拟交易会话状态"""
    session = fund_paper_engine.get_status(paper_trade_id)
    if session is None:
        raise HTTPException(status_code=404, detail="模拟交易会话未找到")
    return {"success": True, "data": {
        "paper_trade_id": session.session_id,
        "strategy_name": session.strategy_name,
        "fund_codes": session.symbols,
        "initial_capital": session.initial_capital,
        "cash": session.cash,
        "positions": {k: v.get("shares", 0) for k, v in session.positions.items()},
        "equity_curve": session.equity_curve,
        "status": session.status,
        "last_run_date": session.last_run_date,
        "created_at": session.created_at,
    }}
