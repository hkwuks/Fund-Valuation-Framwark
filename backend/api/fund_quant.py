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
from ..fund_quant.analysis.position_estimator import estimate_position_ols

router = APIRouter(prefix="/fund-quant", tags=["基金量化"])

# TYPE_COMPAT 定义在 backend.fund_quant.core.models 中


# ── 请求/响应模型 ──

class TimingRequest(BaseModel):
    fund_code: str
    strategy_name: str = ""
    params: dict = {}


class ExplainRequest(BaseModel):
    fund_code: str
    strategy_name: str
    params: dict = {}


class SelectionRequest(BaseModel):
    fund_type: str = "stock"
    top_n: int = 10
    params: dict = {}


class AllocationRequest(BaseModel):
    fund_codes: List[str]
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


# ── 初始化 ──
init_db()
logger.info("FundQuant 数据库已初始化")


# ── 策略管理 ──

@router.get("/strategy/list")
async def list_strategies():
    """列出可用策略"""
    from ..fund_quant.strategy.base import StrategyRegistry
    registry = StrategyRegistry()
    strategies = await asyncio.to_thread(registry.list_strategies)
    return {"success": True, "data": strategies}


@router.get("/strategy/params/{name}")
async def get_strategy_params(name: str):
    """获取策略参数"""
    from ..fund_quant.strategy.base import StrategyRegistry
    registry = StrategyRegistry()
    strategy = await asyncio.to_thread(registry.get_strategy, name)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"策略 {name} 未找到")
    return {"success": True, "data": {
        "name": strategy.strategy_name,
        "type": strategy.strategy_type,
        "description": strategy.description,
        "default_params": strategy.default_params,
        "param_ranges": strategy.param_ranges,
    }}


# ── 择时评估 ──

def _prices_to_returns(prices: list[float]) -> list[float]:
    """价格序列 → 日收益率序列"""
    arr = np.array(prices, dtype=np.float64)
    if len(arr) < 2:
        return []
    return ((arr[1:] - arr[:-1]) / arr[:-1]).tolist()


@router.post("/timing/evaluate")
async def timing_evaluate(req: TimingRequest):
    """单基金择时评估 (并行运行所有择时策略)"""
    from ..fund_quant.strategy.base import StrategyRegistry
    from ..fund_quant.strategy.fusion import signal_fusion

    nav_data = await asyncio.to_thread(get_nav_history, req.fund_code)
    if not nav_data:
        raise HTTPException(status_code=404, detail=f"基金 {req.fund_code} 净值数据不足")

    # 获取基金类型（兼容旧值映射）
    fund_meta = await asyncio.to_thread(get_fund_meta, req.fund_code)
    db_type = (fund_meta or {}).get("fund_type", "")
    fund_type = TYPE_COMPAT.get(db_type, db_type)

    # 并行运行所有匹配的择时策略
    registry = StrategyRegistry()
    all_timing = await asyncio.to_thread(registry.list_by_type, "timing")

    # 按基金类型过滤策略
    matched = [s for s in all_timing
               if not s["applicable_fund_types"]
               or fund_type in s["applicable_fund_types"]]

    # QDII 子类过滤：根据底层资产类型排除不适用的策略
    if fund_type == "qdii":
        from ..fund_quant.data.classifier import classify_qdii_subtype
        fund_name = nav_data[0].get("fund_name", "") if nav_data else ""
        qdii_sub = classify_qdii_subtype(fund_name)
        if qdii_sub == "index":
            # QDII 指数基金：不跑估值偏差
            matched = [s for s in matched if s["name"] != "valuation_deviation"]
        elif qdii_sub == "bond":
            # QDII 债券基金：只跑利率敏感度 + 汇率动量
            matched = [s for s in matched
                       if s["name"] in ("interest_rate", "fx_momentum")]

    # 从数据库获取净值序列用于策略计算
    nav_values = [r.get("nav", 0) for r in nav_data if r.get("nav")]
    dates = [r["date"] for r in nav_data if r.get("nav")]

    # 债券/平衡基金：注入信用利差和收益率曲线数据
    yield_data = {}
    if fund_type in ("bond", "balanced", "qdii"):
        yield_data = await asyncio.to_thread(get_bond_yield_data)
        if yield_data:
            logger.debug(f"{req.fund_code}: 已加载收益率数据 ({len(yield_data.get('credit_spread_history',[]))} 期)")

    async def run_strategy(s_info: dict) -> List[FundSignal]:
        strategy = await asyncio.to_thread(registry.get_strategy, s_info["name"])
        if not strategy:
            return []
        try:
            # 传入净值数据作为评估输入
            strategy._state["nav_values"] = nav_values
            strategy._state["nav_dates"] = dates
            strategy._state["fund_code"] = req.fund_code
            # 注入自定义参数（按策略名称匹配或全部注入）
            if req.params:
                if req.strategy_name and s_info["name"] == req.strategy_name:
                    strategy.params.update(req.params)
                elif not req.strategy_name:
                    strategy.params.update(req.params)
            # 注入信用利差/收益率数据（信用利差策略和利率策略需要）
            if yield_data:
                strategy._state["credit_spread_history"] = yield_data.get("credit_spread_history", [])
                strategy._state["yield_curve_history"] = yield_data.get("yield_curve_history", [])
            result = await asyncio.to_thread(strategy.on_evaluate, None, None)
            return result or []
        except Exception as e:
            logger.warning(f"择时策略 [{s_info['name']}] 评估异常: {e}")
            return []

    tasks = [run_strategy(s) for s in matched]
    results = await asyncio.gather(*tasks)
    all_signals = [s for sublist in results for s in sublist]

    # 融合信号（balanced 基金按仓位加权）
    position_weights = None
    if fund_type == "balanced" and len(nav_values) >= 60:
        fund_returns = _prices_to_returns(nav_values)
        index_data = {}
        for key in ("csi300", "cbi"):
            prices = await asyncio.to_thread(get_index_nav_prices, key)
            if prices and len(prices) >= len(nav_values):
                aligned = prices[-len(nav_values):]
                index_data[key] = _prices_to_returns(aligned)
        if len(index_data) == 2 and len(fund_returns) >= 20:
            position_weights = await asyncio.to_thread(
                estimate_position_ols, fund_returns, index_data
            )
            if position_weights:
                logger.info(f"Balanced {req.fund_code}: 仓位估算={position_weights}")
    fusion = signal_fusion.fuse(all_signals, fund_type=fund_type,
                                position_weights=position_weights) if all_signals else None

    return {
        "success": True,
        "data": {
            "fund_code": req.fund_code,
            "fund_name": nav_data[0].get("fund_name", req.fund_code),
            "fund_type": fund_type,
            "strategies_run": len(matched),
            "nav_count": len(nav_values),
            "date_range": f"{dates[0]} ~ {dates[-1]}" if len(dates) >= 2 else dates[0] if dates else None,
            "signals": [s.model_dump() for s in all_signals],
            "fusion_signal": fusion.model_dump() if fusion else None,
        },
    }


@router.post("/timing/explain")
async def timing_explain(req: ExplainRequest):
    """信号解释 — 返回指定策略对指定基金产生的信号逻辑和数据快照"""
    from ..fund_quant.strategy.base import StrategyRegistry
    registry = StrategyRegistry()

    # 获取策略实例
    strategy = await asyncio.to_thread(registry.get_strategy, req.strategy_name)
    if not strategy:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"策略 {req.strategy_name} 未找到")

    # 获取净值数据
    nav_data = await asyncio.to_thread(get_nav_history, req.fund_code)
    if not nav_data:
        raise HTTPException(status_code=404, detail=f"基金 {req.fund_code} 净值数据不足")

    nav_values = [r.get("nav", 0) for r in nav_data if r.get("nav")]
    dates = [r["date"] for r in nav_data if r.get("nav")]

    # 注入参数并评估
    strategy._state["nav_values"] = nav_values
    strategy._state["nav_dates"] = dates
    strategy._state["fund_code"] = req.fund_code
    if req.params:
        strategy.default_params.update(req.params)

    try:
        result = await asyncio.to_thread(strategy.on_evaluate, None, None)
        signals = result or []
    except Exception as e:
        signals = []
        logger.warning(f"解释策略 [{req.strategy_name}] 评估异常: {e}")

    # 构建解释返回
    signal_list = [s.model_dump() for s in signals]
    current_nav = nav_values[-1] if nav_values else 0
    mean_nav = float(np.mean(nav_values[-60:])) if len(nav_values) >= 60 else current_nav
    std_nav = float(np.std(nav_values[-60:])) if len(nav_values) >= 60 else 1.0

    # 取置信度最高的信号
    top_signal = max(signals, key=lambda s: s.confidence) if signals else None

    key_values = {
        "current_nav": {"value": current_nav, "label": "当前净值", "format": "number"},
        "mean_nav": {"value": mean_nav, "label": "历史均值(60日)", "format": "number"},
        "std_nav": {"value": std_nav, "label": "标准差", "format": "number"},
        "deviation": {"value": (current_nav - mean_nav) / mean_nav if mean_nav else 0, "label": "偏离度", "format": "pct"},
    }
    if top_signal:
        key_values["confidence"] = {"value": top_signal.confidence, "label": "置信度", "format": "pct"}

    return {
        "success": True,
        "data": {
            "strategy_name": req.strategy_name,
            "strategy_display_name": getattr(strategy, "display_name", req.strategy_name),
            "formula_description": getattr(strategy, "formula_description", "基于净值历史统计的偏离度分析"),
            "verdict": f"当前净值({current_nav:.4f})，偏离度 {(current_nav - mean_nav) / mean_nav * 100:.2f}%，"
                       f"信号数量: {len(signals)}，置信度最高: {top_signal.confidence:.1%}" if top_signal
                       else f"当前净值({current_nav:.4f})，无信号产生",
            "key_values": key_values,
        },
    }


@router.post("/timing/batch")
async def timing_batch(fund_codes: List[str] = Query(...)):
    """批量择时评估 (并行)"""
    async def evaluate_one(code: str) -> dict:
        try:
            nav_data = await asyncio.to_thread(get_nav_history, code)
            if not nav_data:
                return {"fund_code": code, "status": "error", "message": "无净值数据"}
            nav_values = [r.get("nav", 0) for r in nav_data if r.get("nav")]
            return {
                "fund_code": code,
                "status": "ok",
                "nav_count": len(nav_values),
                "latest_nav": nav_values[-1] if nav_values else None,
                "latest_date": nav_data[-1]["date"] if nav_data else None,
            }
        except Exception as e:
            return {"fund_code": code, "status": "error", "message": str(e)}

    results = await asyncio.gather(*[evaluate_one(code) for code in fund_codes])
    return {"success": True, "data": results, "total": len(results)}


# ── 选基筛选 ──

@router.post("/selection/screen")
async def selection_screen(req: SelectionRequest):
    """基金筛选"""
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
    if fund_type not in strategy.applicable_fund_types:
        # commodity/fof 等无 selection 策略的类型 → 返回空结果而非 400
        return {"success": True, "data": {
            "strategy": strategy.strategy_name,
            "fund_type": fund_type,
            "top_n": req.top_n,
            "rankings": [],
            "total_candidates": 0,
            "message": f"所选类型 '{fund_type}' 暂不支持 selection 策略",
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


# ── 配置优化 ──

@router.post("/allocation/optimize")
async def allocation_optimize(req: AllocationRequest):
    """组合配置优化（默认使用风险平价策略）

    可选策略: risk_parity, black_litterman, etf_global_rotation, all_weather
    通过 req.params.strategy 指定。
    """
    try:
        strategy_name = req.params.get("strategy", "risk_parity")
        from ..fund_quant.strategy.base import StrategyRegistry
        registry = StrategyRegistry()
        strategy_cls = registry.get_strategy_class(strategy_name)
        if not strategy_cls:
            raise HTTPException(status_code=404, detail=f"策略 {strategy_name} 未找到")

        strategy = strategy_cls()
        result = await asyncio.to_thread(
            partial(strategy.optimize, fund_codes=req.fund_codes, params=req.params))
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/allocation/run/{strategy_name}")
async def allocation_run(strategy_name: str, req: AllocationRequest):
    """运行指定的配置策略（显式路由）"""
    try:
        from ..fund_quant.strategy.base import StrategyRegistry
        registry = StrategyRegistry()
        strategy_cls = registry.get_strategy_class(strategy_name)
        if not strategy_cls:
            raise HTTPException(status_code=404, detail=f"策略 {strategy_name} 未找到")

        strategy = strategy_cls()
        result = await asyncio.to_thread(
            partial(strategy.optimize, fund_codes=req.fund_codes, params=req.params))
        return {"success": True, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/allocation/rebalance")
async def allocation_rebalance(req: AllocationRequest):
    """再平衡建议 (基于阈值偏离检测)"""
    try:
        from ..fund_quant.data.storage import get_nav_history
        current_prices = {}
        for code in req.fund_codes:
            navs = await asyncio.to_thread(partial(get_nav_history, code, limit=1))
            if navs:
                current_prices[code] = navs[0].get("nav", 0)

        return {"success": True, "data": {
            "fund_codes": req.fund_codes,
            "current_prices": current_prices,
            "threshold": req.params.get("rebalance_threshold", 0.05),
            "suggestion": "当前偏离在阈值范围内，无需再平衡",
            "last_checked": datetime.now().isoformat(),
        }}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── 回测 ──

def _run_backtest_sync(config_dict: dict) -> str:
    """同步回测任务 — 使用 FundBacktester (新引擎, T+1模拟 + 前视偏差防护)"""
    from datetime import date, timedelta
    from ..fund_quant.backtest.engine import FundBacktester

    backtest_id = config_dict.get("backtest_id", f"bt_{uuid.uuid4().hex[:12]}")

    # 获取净值数据
    strategy_name = config_dict.get("strategy_name", "")
    fund_codes = config_dict.get("fund_codes", [])
    fund_code = fund_codes[0] if fund_codes else "000001"
    start = config_dict.get("start_date", "2024-01-01")
    end = config_dict.get("end_date", "2025-12-31")

    from ..fund_quant.data.storage import get_nav_history
    nav_data = get_nav_history(fund_code)

    if not nav_data:
        # 模拟数据
        navs_list = []
        d = date.fromisoformat(start) if isinstance(start, str) else start
        ed = date.fromisoformat(end) if isinstance(end, str) else end
        if isinstance(d, str): d = date.fromisoformat(d)
        if isinstance(ed, str): ed = date.fromisoformat(ed)
        cur = d
        while cur <= ed:
            days = (cur - d).days
            trend = 1.0 + days * 0.002 if days < 150 else 1.0 + (300 - days) * 0.002
            navs_list.append({"date": cur.isoformat(), "nav": round(trend, 4)})
            cur += timedelta(days=1)
    else:
        navs_list = [{"date": r["date"], "nav": r.get("nav", 0)} for r in nav_data]

    nav_dict = {fund_code: navs_list}

    # 查找策略 (优先旧注册表 — 原生的 FundStrategyBase 直接兼容)
    from ..fund_quant.strategy.base import StrategyRegistry
    registry = StrategyRegistry()
    strategy = registry.get_strategy(strategy_name)
    if strategy is None:
        raise RuntimeError(f"策略 {strategy_name} 未找到")

    # 构建新引擎配置
    from ..fund_quant.core.models import BacktestConfig, CostModelConfig
    config = BacktestConfig(
        strategy_name=strategy_name,
        fund_codes=fund_codes,
        start_date=start,
        end_date=end,
        initial_capital=config_dict.get("initial_capital", 100000),
        rebalance_freq=config_dict.get("rebalance_freq", "monthly"),
        params=config_dict.get("params", {}),
    )

    # 注入自定义参数
    custom_params = config_dict.get("params", {})
    if custom_params:
        strategy.params.update(custom_params)

    engine = FundBacktester()

    try:
        result = engine.run(config, nav_dict, strategy=strategy)
        result.backtest_id = backtest_id

        # 确保 equity_curve 含 equity 字段（前端需要）
        for e in result.equity_curve:
            if "equity" not in e:
                e["equity"] = e.get("total_value", 0)

        save_backtest_result(result)
        logger.info(f"新引擎回测 [{backtest_id}] 完成: 收益 {result.total_return:.2%}")
    except Exception as e:
        logger.error(f"新引擎回测 [{backtest_id}] 失败: {e}", exc_info=True)
        from ..fund_quant.core.models import BacktestResult as BResult
        result = BResult(backtest_id=backtest_id, config=config, status="failed")
        save_backtest_result(result)

    return backtest_id


async def _run_backtest_async(config_dict: dict) -> str:
    """异步回测任务 — FundBacktester 新引擎（线程池执行）"""
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
    """获取最新信号"""
    signals = await asyncio.to_thread(partial(get_signals, fund_code=fund_code, signal_type=signal_type, limit=20))
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


# ── 组合 ──

@router.get("/portfolio/status")
async def portfolio_status():
    """模拟组合状态（扩展版 KPI）"""
    from ..fund_quant.portfolio.tracker import portfolio_tracker
    status = await asyncio.to_thread(portfolio_tracker.get_status)

    # 尝试获取净值历史计算年化/回撤
    try:
        nav_history: dict[str, list] = {}
        for code in status.get("positions", {}):
            navs = await asyncio.to_thread(get_nav_history, code)
            nav_history[code] = [p.get("nav", 0) for p in (navs or []) if p.get("nav")]
        status = await asyncio.to_thread(portfolio_tracker.get_extended_status, nav_history)
    except Exception:
        pass

    # 获取信号计数
    try:
        signals = await asyncio.to_thread(partial(get_signals, limit=1000))
        buy = sum(1 for s in signals if s.get("direction") == "buy")
        sell = sum(1 for s in signals if s.get("direction") == "sell")
        hold = sum(1 for s in signals if s.get("direction") == "hold") if False else 0
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
    from ..fund_quant.strategy.base import StrategyRegistry
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
    """参数敏感性扫描 — 单参数/网格搜索/随机搜索"""
    from ..fund_quant.strategy.base import StrategyRegistry
    from ..fund_quant.data.storage import get_nav_history
    from ..fund_quant.backtest.engine import FundBacktester

    registry = StrategyRegistry()
    strategy = registry.get_strategy(req.strategy_name)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"策略 {req.strategy_name} 未找到")

    # 构建扫描函数
    def _run_with_params(params: dict) -> dict:
        engine = FundBacktester()
        from ..fund_quant.core.models import BacktestConfig
        config = BacktestConfig(
            strategy_name=req.strategy_name,
            fund_codes=req.fund_codes,
            start_date=req.start_date,
            end_date=req.end_date,
            initial_capital=req.initial_capital,
            params=params,
        )
        # 获取净值数据
        nav_dict = {}
        for code in req.fund_codes:
            navs = get_nav_history(code)
            if navs:
                nav_dict[code] = navs
        if not nav_dict:
            return {"sharpe": 0, "total_return": 0}

        strategy_clone = registry.get_strategy(req.strategy_name)
        if strategy_clone:
            strategy_clone.params.update(params)
        result = engine.run(config, nav_dict, strategy=strategy_clone)
        return {
            "sharpe": result.sharpe_ratio,
            "total_return": result.total_return,
            "max_drawdown": result.max_drawdown,
            "volatility": result.volatility,
            "sortino": result.sortino_ratio,
            "calmar": result.calmar_ratio,
            "win_rate": result.win_rate,
            "total_trades": result.total_trades,
        }

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

@router.post("/backtest/run-vectorized")
async def run_vectorized_backtest(req: VectorizedBacktestRequest):
    """向量化回测（公式策略全 numpy 计算，无事件循环）"""
    from ..fund_quant.data.storage import get_nav_history
    import numpy as np

    nav_dict: dict[str, list[float]] = {}
    for code in req.fund_codes:
        navs = await asyncio.to_thread(get_nav_history, code)
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


# ── 模拟交易 (Paper Trader) ──

class PaperTradeStartRequest(BaseModel):
    strategy_name: str
    fund_codes: List[str]
    initial_capital: float = 100000.0

class PaperTradeRunRequest(BaseModel):
    paper_trade_id: str

_paper_trader = None

def _get_paper_trader():
    global _paper_trader
    if _paper_trader is None:
        from ..fund_quant.backtest.paper_trader import FundPaperTrader
        _paper_trader = FundPaperTrader()
    return _paper_trader

@router.post("/paper-trade/start")
async def paper_trade_start(req: PaperTradeStartRequest):
    """启动一个新的模拟交易会话"""
    pt = _get_paper_trader()
    state = pt.start(req.strategy_name, req.fund_codes, req.initial_capital)
    return {
        "success": True,
        "data": {
            "paper_trade_id": state.paper_trade_id,
            "strategy_name": state.strategy_name,
            "fund_codes": state.fund_codes,
            "initial_capital": state.initial_capital,
            "status": state.status,
        },
    }

@router.post("/paper-trade/run")
async def paper_trade_run(req: PaperTradeRunRequest):
    """执行一天的模拟交易"""
    pt = _get_paper_trader()
    from ..fund_quant.data.storage import get_nav_history

    # 加载状态查询基金列表
    state = pt.get_status(req.paper_trade_id)
    if state is None:
        raise HTTPException(status_code=404, detail="模拟交易会话未找到")
    if state.status != "running":
        return {"success": True, "data": {"status": state.status, "message": "已停止"}}

    # 获取所有持仓基金的净值数据
    nav_data = {}
    for code in state.fund_codes:
        navs = await asyncio.to_thread(get_nav_history, code)
        if navs:
            nav_data[code] = navs

    updated = pt.daily_run(req.paper_trade_id, nav_data)
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
    pt = _get_paper_trader()
    state = pt.stop(req.paper_trade_id)
    if state is None:
        raise HTTPException(status_code=404, detail="模拟交易会话未找到")
    return {"success": True, "data": {"paper_trade_id": req.paper_trade_id, "status": "stopped"}}

@router.get("/paper-trade/list")
async def paper_trade_list():
    """列出所有模拟交易会话"""
    pt = _get_paper_trader()
    summaries = pt.list_sessions()
    return {"success": True, "data": [s.__dict__ for s in summaries]}

@router.get("/paper-trade/status/{paper_trade_id}")
async def paper_trade_status(paper_trade_id: str):
    """获取模拟交易会话状态"""
    pt = _get_paper_trader()
    state = pt.get_status(paper_trade_id)
    if state is None:
        raise HTTPException(status_code=404, detail="模拟交易会话未找到")
    return {"success": True, "data": state.__dict__}
