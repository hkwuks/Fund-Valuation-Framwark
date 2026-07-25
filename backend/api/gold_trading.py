"""
黄金量化交易API路由

完整端点: 状态/策略/回测/对比/敏感性/验证/信号生成/风控/数据同步/配置
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from pydantic import BaseModel
from datetime import datetime, timedelta
import asyncio
import time
import pandas as pd

from backend.gold.data.labeling import TripleBarrierLabeler
from backend.gold.risk.checks import RiskChecker
from backend.gold.signal.output import SignalOutput
from backend.gold.data.gateway import GoldDataGateway
from backend.gold.data.storage import GoldDataStore
from backend.gold.core.models import (
    BacktestRequest, StrategyComparisonRequest, RiskCheckResult, RiskLevel,
    GoldSignal, SignalDirection,
)
from backend.gold.core.config import GoldSettings
from backend.data_sync import get_gold_training_data
from loguru import logger

# AuroraCore 新引擎
from core import (
    BacktestEngine, BacktestConfig, BacktestReport, EventBus, Bar,
    MetricsCalculator, RiskPipeline,
)
from gold.adapter import GoldDomainAdapter

router = APIRouter(prefix="/gold/trading", tags=["黄金量化交易"])

gateway = GoldDataGateway()
_adapter: GoldDomainAdapter | None = None

def _get_domain_adapter() -> GoldDomainAdapter:
    """懒加载 GoldDomainAdapter（避免导入时序问题）"""
    global _adapter
    if _adapter is None:
        _adapter = GoldDomainAdapter()
    return _adapter


# ── AuroraCore 新引擎辅助函数 ──

def _bars_to_core(bars: list) -> list:
    """GoldBarData → core.Bar 转换"""
    return [Bar(
        symbol=b.symbol, exchange=getattr(b, "exchange", "SHFE"),
        timeframe=getattr(b, "period", "1d"),
        datetime=b.datetime, open=float(b.open), high=float(b.high),
        low=float(b.low), close=float(b.close), volume=float(b.volume),
    ) for b in bars]


def _create_engine(strategy_name: str, bars: list, capital: float,
                   params: dict | None = None) -> BacktestEngine:
    """创建并配置 AuroraCore BacktestEngine（不运行）"""
    adapter = _get_domain_adapter()
    strategies = adapter.get_available_strategies()
    if strategy_name not in strategies:
        raise ValueError(f"策略 {strategy_name} 在 AuroraCore 中未找到")
    strategy = strategies[strategy_name]()
    if params:
        strategy.params = params

    cfg = BacktestConfig(initial_capital=capital, timeframe="1d")
    engine = BacktestEngine(cfg)
    engine.set_event_bus(EventBus())
    engine.set_strategy(strategy)
    engine.set_executor(adapter.create_executor({}))
    engine.set_cost_model(adapter.create_cost_model({}))
    pipeline = RiskPipeline()
    for c in adapter.default_risk_checks():
        pipeline.add(c)
    engine.set_risk(pipeline)
    engine.set_data(bars)
    return engine


def _convert_report(engine: BacktestEngine, report: BacktestReport,
                    capital: float, start_date: str, end_date: str) -> dict:
    """BacktestEngine.run() 输出转旧格式 report dict"""
    equity = [e["equity"] for e in report.equity_curve]
    metrics = MetricsCalculator.calculate(equity)

    exec_ = engine._execution
    trades = list(getattr(exec_, "_trades", []) or [])
    fills = getattr(exec_, "_fills", []) or []
    close_trades = [t for t in trades if t.get("type") == "close"]
    trade_pnls = [t.get("pnl", 0) for t in close_trades]
    total_commission = sum(getattr(f, "commission", 0) for f in fills)
    total_slippage = sum(t.get("slippage", 0) for t in trades)
    net_pnl = sum(trade_pnls) - total_commission
    gross_pnl = net_pnl + total_commission + total_slippage

    n = len(close_trades)
    wins = sum(1 for p in trade_pnls if p > 0)
    win_rate = wins / n if n else 0
    trade_returns = [p / capital for p in trade_pnls] if capital else []
    gross_profit = sum(r for r in trade_returns if r > 0)
    gross_loss = abs(sum(r for r in trade_returns if r < 0))
    profit_factor = gross_profit / gross_loss if gross_loss else None
    avg_holding = sum(t.get("holding_bars", 0) for t in close_trades) / n if n else 0
    avg_profit_val = (gross_profit / wins * 100) if wins else 0
    avg_loss_val = (gross_loss / (n - wins) * 100) if (n - wins) else 0
    max_loss = min(trade_returns) * 100 if trade_returns else 0

    return {
        "performance": {
            "total_return": round(metrics.total_return * 100, 2),
            "annualized_return": round(metrics.annual_return * 100, 2),
            "sharpe_ratio": round(metrics.sharpe_ratio, 2),
            "sortino_ratio": round(metrics.sortino_ratio, 2),
            "calmar_ratio": round(metrics.calmar_ratio, 2),
            "win_rate": round(win_rate * 100, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor else None,
        },
        "risk": {
            "max_drawdown": round(metrics.max_drawdown * 100, 2),
            "var_95": round(metrics.var_95, 2),
            "cvar_95": round(metrics.cvar_95, 2),
            "volatility": round(metrics.volatility * 100, 2),
        },
        "trades": {
            "total_count": n,
            "avg_holding_bars": round(avg_holding, 1),
            "avg_profit": round(avg_profit_val, 2),
            "avg_loss": round(avg_loss_val, 2),
            "max_single_loss": round(max_loss, 2),
        },
        "cost": {
            "total_commission": round(total_commission, 2),
            "total_slippage": round(total_slippage, 2),
            "gross_pnl": round(gross_pnl, 2),
            "net_pnl": round(net_pnl, 2),
        },
        "meta": {
            "capital": capital,
            "start_date": start_date,
            "end_date": end_date,
            "total_days": len(equity) - 1,
            "risk_free_rate": 0.025,
        },
    }


def _aurora_backtest(strategy_name: str, bars: list, capital: float,
                     params: dict | None = None) -> dict:
    """使用 AuroraCore 新引擎运行回测，返回与旧格式兼容的结果"""
    core_bars = _bars_to_core(bars)
    engine = _create_engine(strategy_name, core_bars, capital, params)
    report = engine.run()

    trades = list(getattr(engine._execution, "_trades", []) or [])
    signals = list(getattr(engine, "_captured_signals", []) or [])
    start_date = core_bars[0].datetime.strftime("%Y-%m-%d") if core_bars else ""
    end_date = core_bars[-1].datetime.strftime("%Y-%m-%d") if core_bars else ""

    return {
        "report": _convert_report(engine, report, capital, start_date, end_date),
        "trades": trades[-100:],
        "signals": [{"strategy": strategy_name, "direction": str(s.direction), "price": s.price,
                      "volume": s.volume, "stop_loss": s.stop_loss, "reason": s.reason}
                     for s in signals],
    }


# ===== 系统状态 =====

@router.get("/status")
async def get_status():
    """系统状态"""
    strategies = _get_domain_adapter().get_available_strategies()
    return {
        "status": "ok",
        "mode": "signal_only",
        "strategies": list(strategies.keys()),
    }


# ===== 策略管理 =====

@router.get("/strategies")
async def list_strategies():
    """列出所有可用策略"""
    strategies = _get_domain_adapter().get_available_strategies()
    result = []
    for name, cls in strategies.items():
        result.append({
            "strategy_id": name,
            "strategy_name": getattr(cls, "name", name),
            "strategy_type": getattr(cls, "strategy_type", ""),
            "description": getattr(cls, "description", ""),
            "default_params": getattr(cls, "default_params", {}),
            "param_ranges": getattr(cls, "param_ranges", {}),
        })
    return {"success": True, "data": result}


@router.get("/strategies/{strategy_name}")
async def get_strategy_detail(strategy_name: str):
    """获取策略详情"""
    strategies = _get_domain_adapter().get_available_strategies()
    cls = strategies.get(strategy_name)
    if cls is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")
    return {
        "success": True,
        "data": {
            "strategy_id": strategy_name,
            "strategy_name": cls.strategy_name,
            "strategy_type": cls.strategy_type,
            "description": cls.description,
            "default_params": cls.default_params,
            "param_ranges": cls.param_ranges,
        },
    }


# ===== 回测 =====

@router.post("/backtest")
async def run_backtest(req: BacktestRequest):
    """运行单策略回测 — AuroraCore 引擎"""
    # 决定回测模式
    is_ml = req.strategy_name in ("ml_predictor",)
    effective_method = req.method if req.method != "auto" else ("walk_forward" if is_ml else "simple")

    bars = await gateway.get_bars(
        symbol=req.symbol, period=req.period,
        start=req.start_date, end=req.end_date, refresh=True,
    )
    if not bars:
        raise HTTPException(status_code=400, detail="No bar data available for the given parameters")

    # Walk-Forward
    if effective_method == "walk_forward":
        core_bars = _bars_to_core(bars)
        n_bars = len(core_bars)

        def _wf_engine_factory(bar_slice):
            return _create_engine(req.strategy_name, bar_slice, req.capital, req.params)

        from core.validation import WalkForwardValidator
        validator = WalkForwardValidator(
            train_window=max(60, n_bars // 3), test_window=max(10, n_bars // 10),
            purge_days=1, embargo_days=5,
        )
        try:
            wf_result = await asyncio.to_thread(validator.run, core_bars, _wf_engine_factory, 60)
        except Exception as e:
            logger.error(f"WF backtest failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

        # 合并各窗口权益曲线用于估算总收益
        all_equity = [req.capital]
        for w in wf_result["windows"]:
            test_ret = w["test_return"]
            if test_ret != 0:
                all_equity.append(all_equity[-1] * (1 + test_ret))
        full_equity = all_equity[1:] if len(all_equity) > 1 else [0]
        total_return = (full_equity[-1] / req.capital - 1) if full_equity else 0

        wf_report = {
            "performance": {
                "total_return": round(total_return * 100, 2),
                "sharpe_ratio": round(wf_result.get("avg_test_return", 0) / max(wf_result.get("std_test_return", 0.01), 0.01), 2),
                "win_rate": round(wf_result.get("positive_window_ratio", 0), 2),
            },
            "risk": {"max_drawdown": 0},
            "trades": {"total_count": wf_result.get("total_test_trades", 0)},
            "cost": {"total_commission": 0, "total_slippage": 0, "net_pnl": 0, "gross_pnl": 0},
            "meta": {"capital": req.capital, "start_date": str(bars[0].datetime)[:10] if bars else "",
                     "end_date": str(bars[-1].datetime)[:10] if bars else "", "total_days": len(bars), "risk_free_rate": 0.025},
        }

        return {
            "success": True,
            "data": {
                "strategy": req.strategy_name,
                "engine": "aurora_core",
                "method": "walk_forward",
                "report": wf_report,
                "signals": [],
                "trades": [],
                "walk_forward": wf_result,
            },
        }

    # Simple → AuroraCore 新引擎
    try:
        result = await asyncio.to_thread(_aurora_backtest, req.strategy_name, bars, req.capital, req.params)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"AuroraCore backtest failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "success": True,
        "data": {
            "strategy": req.strategy_name,
            "engine": "aurora_core",
            "method": "simple",
            "report": result["report"],
            "signals": result.get("signals", [])[-20:],
            "trades": result.get("trades", [])[-50:],
            "walk_forward": result.get("walk_forward"),
        },
    }


# ===== 多策略对比 =====

@router.post("/compare")
async def compare_strategies(req: StrategyComparisonRequest):
    """
    多策略对比回测 — AuroraCore 引擎
    """
    results = {}
    errors = []
    valid_names = []

    strategies = _get_domain_adapter().get_available_strategies()

    for name in req.strategy_names:
        if name not in strategies:
            errors.append(f"Strategy '{name}' not found in AuroraCore")
        else:
            valid_names.append(name)

    if not valid_names:
        return {"success": True, "data": {"strategies": {}, "comparison": {}, "errors": errors}}

    bars = await gateway.get_bars(
        symbol=req.symbol, period=req.period,
        start=req.start_date, end=req.end_date,
    )
    if not bars:
        raise HTTPException(status_code=400, detail="No bar data available")

    async with asyncio.TaskGroup() as tg:
        tasks = {}
        for name in valid_names:
            task = tg.create_task(
                asyncio.to_thread(_aurora_backtest, name, bars, req.capital)
            )
            tasks[name] = task

    for name, task in tasks.items():
        try:
            result = task.result()
            results[name] = result["report"]
        except Exception as e:
            errors.append(f"Strategy '{name}' failed: {str(e)}")

    # 排名
    comparison = {
        "sharpe_ranking": sorted(
            [(n, r["performance"]["sharpe_ratio"]) for n, r in results.items()],
            key=lambda x: x[1] if x[1] is not None else -999, reverse=True
        ),
        "return_ranking": sorted(
            [(n, r["performance"]["total_return"]) for n, r in results.items()],
            key=lambda x: x[1], reverse=True
        ),
        "max_dd_ranking": sorted(
            [(n, r["risk"]["max_drawdown"]) for n, r in results.items()],
            key=lambda x: abs(x[1])
        ),
        "win_rate_ranking": sorted(
            [(n, r["performance"]["win_rate"]) for n, r in results.items()],
            key=lambda x: x[1], reverse=True
        ),
    }

    return {
        "success": True,
        "data": {
            "strategies": results,
            "comparison": comparison,
            "errors": errors,
        },
    }


# ===== 参数敏感性分析 =====

class SensitivityRequest(BaseModel):
    strategy_name: str
    symbol: str = "AU0"
    period: str = "d"
    start_date: str = "2024-01-01"
    end_date: str = "2025-12-31"
    capital: float = 1_000_000
    param_ranges: Optional[dict] = None  # None → use strategy's param_ranges


@router.post("/backtest/sensitivity")
async def run_sensitivity(req: SensitivityRequest):
    """参数敏感性分析 — AuroraCore 引擎"""
    bars = await gateway.get_bars(
        symbol=req.symbol, period=req.period,
        start=req.start_date, end=req.end_date, refresh=True,
    )
    if not bars:
        raise HTTPException(status_code=400, detail="No bar data available")

    # 从策略获取 param_ranges
    strategies = _get_domain_adapter().get_available_strategies()
    if req.strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy '{req.strategy_name}' not found")
    param_ranges = req.param_ranges or getattr(strategies[req.strategy_name], 'param_ranges', None)
    if not param_ranges:
        raise HTTPException(status_code=400, detail="No param_ranges available for this strategy")

    # 邻域扫描
    import itertools
    results: list[dict] = []
    for param_name, values in param_ranges.items():
        for value in values:
            params = {param_name: value}
            try:
                r = await asyncio.to_thread(_aurora_backtest, req.strategy_name, bars, req.capital, params)
                report = r["report"]
                results.append({
                    "param_name": param_name,
                    "param_value": value,
                    "sharpe": report["performance"]["sharpe_ratio"],
                    "max_dd": report["risk"]["max_drawdown"],
                    "total_return": report["performance"]["total_return"],
                    "win_rate": report["performance"]["win_rate"],
                })
            except Exception as e:
                logger.debug(f"Sensitivity failed for {param_name}={value}: {e}")
                results.append({"param_name": param_name, "param_value": value,
                                "sharpe": None, "max_dd": None, "total_return": None, "win_rate": None})

    # 稳健性评估
    conclusion = {}
    for param_name in param_ranges:
        vals = [r for r in results if r["param_name"] == param_name and r["sharpe"] is not None]
        if vals:
            sharpes = [v["sharpe"] for v in vals]
            mean_s = sum(sharpes) / len(sharpes)
            std_s = (sum((s - mean_s) ** 2 for s in sharpes) / len(sharpes)) ** 0.5
            cv = std_s / abs(mean_s) if mean_s else 999
            conclusion[param_name] = {
                "mean_sharpe": round(mean_s, 2),
                "std_sharpe": round(std_s, 2),
                "cv_sharpe": round(cv, 2),
                "status": "稳健" if cv < 0.5 else ("中等" if cv < 1.0 else "脆弱"),
            }

    return {
        "success": True,
        "data": {
            "strategy": req.strategy_name,
            "sensitivity_data": results,
            "conclusion": conclusion,
        },
    }


# ===== In/Out样本验证 + 场景验证 =====

class ValidationRequest(BaseModel):
    strategy_name: str
    symbol: str = "AU0"
    period: str = "d"
    start_date: str = "2020-01-01"
    end_date: str = "2025-12-31"
    capital: float = 1_000_000
    in_sample_ratio: float = 0.7
    scenario_name: Optional[str] = None  # None → run all scenarios


@router.post("/backtest/validation")
async def run_validation(req: ValidationRequest):
    """In/Out样本验证 + 场景验证 (AuroraCore 引擎)"""
    bars = await gateway.get_bars(
        symbol=req.symbol, period=req.period,
        start=req.start_date, end=req.end_date, refresh=True,
    )
    if not bars:
        raise HTTPException(status_code=400, detail="No bar data available")

    if req.strategy_name not in _get_domain_adapter().get_available_strategies():
        raise HTTPException(status_code=404, detail=f"Strategy '{req.strategy_name}' not found")

    split_idx = int(len(bars) * req.in_sample_ratio)
    in_bars, out_bars = bars[:split_idx], bars[split_idx:]

    # In/Out 并行跑
    async def _run_in():
        return await asyncio.to_thread(_aurora_backtest, req.strategy_name, in_bars, req.capital)

    async def _run_out():
        return await asyncio.to_thread(_aurora_backtest, req.strategy_name, out_bars, req.capital)

    in_result, out_result = await asyncio.gather(_run_in(), _run_out())
    in_report, out_report = in_result["report"], out_result["report"]

    in_sharpe = in_report["performance"]["sharpe_ratio"] or 0
    out_sharpe = out_report["performance"]["sharpe_ratio"] or 0
    degrade = ((in_sharpe - out_sharpe) / max(abs(in_sharpe), 0.01)) * 100 if in_sharpe else 0
    overfit_risk = "高" if degrade > 50 else ("中" if degrade > 25 else "低")

    # 场景验证 — 简单波动场景
    all_returns = []
    for i in range(1, len(bars)):
        prev = float(bars[i-1].close)
        if prev:
            all_returns.append((float(bars[i].close) - prev) / prev)
    volatility = (sum(r**2 for r in all_returns) / len(all_returns))**0.5 * (252**0.5) if all_returns else 0
    scenario_results = [
        {"scenario": "正常", "status": "通过", "sharpe": in_sharpe},
        {"scenario": f"波动率={volatility:.0%}", "status": "通过" if volatility < 0.3 else "警告", "sharpe": in_sharpe},
    ]

    return {
        "success": True,
        "data": {
            "sample_validation": {
                "in_sample": {"performance": in_report["performance"], "risk": in_report["risk"]},
                "out_sample": {"performance": out_report["performance"], "risk": out_report["risk"]},
                "sharpe_degradation_pct": round(degrade, 1),
                "overfitting_risk": overfit_risk,
            },
            "scenario_validation": {"results": scenario_results},
        },
    }


# ===== Walk-Forward 回测 =====

@router.post("/backtest/walk-forward")
async def walk_forward_backtest(
    strategy_name: str = "trend_following",
    train_window: int = Query(252, ge=60, le=500),
    test_window: int = Query(20, ge=5, le=60),
    embargo_days: int = Query(20, ge=0, le=100),
    capital: float = Query(1_000_000, ge=10_000),
    warmup_bars: int = Query(60, ge=0, le=200),
):
    """Walk-Forward 滚动窗口回测 (AuroraCore 引擎)"""
    bars = await gateway.get_bars("AU0", period="d")
    if not bars:
        raise HTTPException(400, detail="无K线数据")
    core_bars = _bars_to_core(bars)

    def _wf_engine_factory(bar_slice):
        return _create_engine(strategy_name, bar_slice, capital)

    from core.validation import WalkForwardValidator
    validator = WalkForwardValidator(
        train_window=train_window, test_window=test_window,
        embargo_days=embargo_days,
    )
    result = await asyncio.to_thread(
        validator.run, core_bars, _wf_engine_factory, warmup_bars,
    )
    return {"success": True, "data": result}


# ===== CPCV 回测 =====

@router.post("/backtest/cpcv")
async def cpcv_backtest(
    strategy_name: str = "trend_following",
    n_splits: int = Query(6, ge=3, le=10),
    test_size: float = Query(0.25, ge=0.1, le=0.5),
    capital: float = Query(1_000_000, ge=10_000),
    warmup_bars: int = Query(60, ge=0, le=200),
):
    """CPCV (Combinatorial Purged Cross-Validation) 回测 + PBO (AuroraCore 引擎)"""
    bars = await gateway.get_bars("AU0", period="d")
    if not bars or len(bars) < 200:
        raise HTTPException(status_code=400, detail=f"K线数据不足 {len(bars or [])} 根")
    core_bars = _bars_to_core(bars)

    def _cpcv_engine_factory(bar_slice):
        return _create_engine(strategy_name, bar_slice, capital)

    from core.validation import CPCVValidator
    validator = CPCVValidator(n_splits=n_splits, test_size=test_size)
    result = await asyncio.to_thread(
        validator.run, core_bars, _cpcv_engine_factory, warmup_bars,
    )
    return {"success": True, "data": result}

    return {"success": True, "data": result}


# ===== Triple-Barrier 标注 =====

@router.post("/label/triple-barrier")
async def triple_barrier_label(
    atr_window: int = Query(20, ge=5, le=60),
    tp_multiplier: float = Query(1.5, ge=0.5, le=5.0),
    sl_multiplier: float = Query(1.0, ge=0.5, le=5.0),
    max_holding_days: int = Query(5, ge=1, le=30),
):
    """对当前K线进行 Triple-Barrier 标注，返回标签分布和明细"""
    bars = await gateway.get_bars("AU0", period="d")
    if not bars or len(bars) < 50:
        raise HTTPException(status_code=400, detail=f"K线数据不足 {len(bars or [])} 根")

    labeler = TripleBarrierLabeler(
        atr_window=atr_window, tp_multiplier=tp_multiplier,
        sl_multiplier=sl_multiplier, max_holding_days=max_holding_days,
    )
    labels = await asyncio.to_thread(labeler.label_bars, bars)
    distribution = labeler.label_distribution(labels)

    return {
        "success": True,
        "data": {
            "total_bars": len(bars),
            "labeled": len([l for l in labels if l["label"] != 0]),
            "config": {
                "atr_window": atr_window,
                "tp_multiplier": tp_multiplier,
                "sl_multiplier": sl_multiplier,
                "max_holding_days": max_holding_days,
            },
            "distribution": distribution,
            "labels": labels[-50:],
        },
    }


# ===== 特征重要性（ML策略） =====

@router.get("/feature-importance")
async def get_ml_feature_importance():
    """返回 ML 预测器最近一次训练的特征重要性 (AuroraCore 引擎)"""
    bars = await gateway.get_bars("AU0", period="d")
    if not bars or len(bars) < 150:
        raise HTTPException(status_code=400, detail="K线数据不足")
    core_bars = _bars_to_core(bars)

    strategies = _get_domain_adapter().get_available_strategies()
    if "ml_predictor" not in strategies:
        raise HTTPException(status_code=404, detail="ML 策略未注册")

    def _run_ml():
        wrapper_cls = strategies["ml_predictor"]
        strategy = wrapper_cls()
        strategy._old._macro_df = _get_macro_df()

        engine = BacktestEngine(BacktestConfig(initial_capital=1_000_000, timeframe="1d"))
        engine.set_event_bus(EventBus())
        engine.set_strategy(strategy)
        engine.set_executor(_get_domain_adapter().create_executor({}))
        engine.set_cost_model(_get_domain_adapter().create_cost_model({}))
        pipeline = RiskPipeline()
        for c in _get_domain_adapter().default_risk_checks():
            pipeline.add(c)
        engine.set_risk(pipeline)
        engine.set_data(core_bars[-200:] if len(core_bars) > 200 else core_bars)
        engine.run()

        return strategy._old.get_feature_importance(), strategy._old.get_tb_label_distribution()

    importance, tb_dist = await asyncio.to_thread(_run_ml)

    return {
        "success": True,
        "data": {
            "feature_importance": importance,
            "tb_label_distribution": tb_dist if tb_dist else None,
        },
    }


# ===== Monte Carlo 模拟 =====

@router.post("/backtest/monte-carlo")
async def monte_carlo_simulation(
    strategy_name: str = "trend_following",
    n_simulations: int = Query(200, ge=10, le=5000, description="模拟路径数"),
    capital: float = Query(1_000_000, ge=10_000),
):
    """Monte Carlo 模拟 — 对回测交易序列 bootstrap 重采样 (AuroraCore 引擎)"""
    bars = await gateway.get_bars("AU0", period="d")
    if not bars or len(bars) < 50:
        raise HTTPException(status_code=400, detail="K线数据不足")

    result = await asyncio.to_thread(_aurora_backtest, strategy_name, bars, capital)
    trades = result.get("trades", [])
    close_trades = [t for t in trades if t.get("type") == "close"]
    if not close_trades:
        return {"success": True, "data": {"error": "无平仓交易，无法模拟"}}

    from backend.gold.backtest.monte_carlo import MonteCarloSimulator
    simulator = MonteCarloSimulator(n_simulations=n_simulations)
    mc_result = simulator.simulate(trades, capital, len(bars))

    return {
        "success": True,
        "data": {
            "strategy": strategy_name,
            **mc_result,
        },
    }


def _get_macro_df():
    """获取宏观因子DataFrame（供ML策略注入）"""
    try:
        df = get_gold_training_data("GC", lookback_days=500)
        return df if not df.empty else None
    except Exception:
        return None


# ===== 信号生成 =====

@router.post("/signal/generate")
async def generate_signal(
    strategy_name: str = Query(..., description="策略名称"),
    symbol: str = Query("AU0", description="合约代码"),
    auto_execute: bool = Query(False, description="风控通过后自动发单到SimNow"),
):
    """
    手动触发信号生成

    用最新行情数据驱动策略，输出交易建议。
    auto_execute=true 时，风控通过后自动发单到 CTP/SimNow。
    """
    strategies = _get_domain_adapter().get_available_strategies()
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_name}' not found")

    # 获取行情数据（需要足够的历史让策略积累状态）
    bars = await gateway.get_bars(symbol=symbol, period="d", start="2024-01-01", refresh=True)
    if not bars or len(bars) < 60:
        raise HTTPException(status_code=400, detail="Insufficient data for signal generation")

    from backend.gold.core.models import SignalDirection, GoldSignal

    # ML策略走直接预测路径（不用全量回测，避免宏观特征NaN问题）
    if strategy_name == "ml_predictor":
        return await _generate_ml_signal(strategy_name, bars, gateway)

    # AuroraCore 引擎驱动策略
    capital = GoldSettings().backtest_capital
    result = await asyncio.to_thread(_aurora_backtest, strategy_name, bars, capital)
    signal_dicts = result.get("signals", [])
    current_price = bars[-1].close

    if not signal_dicts:
        return {
            "success": True,
            "data": {
                "signal": None,
                "message": "当前无交易信号",
                "strategy": strategy_name,
                "price": round(current_price, 2),
            },
        }

    s = signal_dicts[-1]
    signal_data = GoldSignal(
        signal_id=f"{strategy_name}_{datetime.now().strftime('%Y%m%d%H%M%S')}_gen",
        strategy_id=strategy_name, strategy_name=strategy_name, symbol=symbol,
        direction=SignalDirection(s["direction"]),
        price=round(current_price, 2),
        volume=int(s.get("volume", 1)),
        stop_loss=round(s["stop_loss"], 2) if s.get("stop_loss") else None,
        reason=s.get("reason", ""),
        created_at=datetime.now(),
    )

    # 用当前时间覆盖
    now = datetime.now()
    signal_data.created_at = now
    signal_data.signal_id = f"{strategy_name}_{now.strftime('%Y%m%d%H%M%S')}_gen"

    # 存入数据库
    try:
        store = GoldDataStore()
        store.save_signal(signal_data)
    except Exception as e:
        logger.warning(f"保存信号失败: {e}")

    # 风控检查（含 CTP 方向/资金检查）
    risk_result = await _check_risk_with_ctp(signal_data)

    # 记录信号到风控状态
    risk_checker = RiskChecker()
    risk_checker.record_signal(signal_data)

    # 自动执行
    execution = None
    if auto_execute and risk_result.passed:
        execution = await _execute_signal_to_ctp(store, signal_data)
        risk_checker.set_equity(GoldSettings().backtest_capital)  # 更新权益估算

    # 输出交易建议
    signal_output = SignalOutput()
    advice = signal_output.output(signal_data, risk_result)
    advice["execution"] = execution

    return {"success": True, "data": advice}


async def _check_risk_with_ctp(signal: GoldSignal) -> RiskCheckResult:
    """风控检查（含交易后端持仓/资金检查）"""
    positions = await query_ctp_positions_raw()
    account = await query_ctp_account_raw()

    risk_checker = RiskChecker()
    risk_result = risk_checker.check(
        signal,
        positions=positions,
        account=account,
    )

    return risk_result


async def _execute_signal_to_ctp(store, signal: GoldSignal) -> dict:
    """执行信号到当前交易后端"""
    try:
        from backend.gold.trading.execution.executor import LiveExecutor
        from backend.gold.trading.execution.sim_account import InternalSimAccount
        from backend.gold.risk.order_manager import OrderManager

        adapter = await _get_adapter()
        if adapter is None:
            return {"executed": False, "reason": "交易后端未连接"}

        om = OrderManager(store)
        sim = InternalSimAccount()
        executor = LiveExecutor(adapter, om, sim)
        result = executor.execute(signal, market_price=signal.price)
        return {
            "executed": result["executed"],
            "ctp_ref": result.get("ctp_ref"),
            "ctp_status": result.get("ctp_status"),
            "order_id": result.get("order", {}).get("order_id"),
            "sim_trade": result.get("sim_trade"),
        }
    except Exception as e:
        logger.error(f"执行失败: {e}")
        return {"executed": False, "reason": str(e)}


@router.post("/signal/execute")
async def execute_signal(
    signal_id: str = Query(..., description="信号ID"),
):
    """手动执行信号到 SimNow"""
    store = GoldDataStore()
    signals = store.get_signals(limit=100)
    signal = next((s for s in signals if s.signal_id == signal_id), None)
    if signal is None:
        raise HTTPException(status_code=404, detail=f"信号 {signal_id} 不存在")

    # 风控
    risk_result = await _check_risk_with_ctp(signal)
    if not risk_result.passed:
        return {"success": True, "data": {"executed": False, "reason": risk_result.reason}}

    # 执行
    execution = await _execute_signal_to_ctp(store, signal)
    return {"success": True, "data": execution}


# 模块级缓存（避免重复查询交易后端）
_last_ctp_positions: list = []
_last_ctp_positions_time: float = 0

async def query_ctp_positions_raw() -> list:
    """获取持仓（带缓存，避免高频查询）"""
    global _last_ctp_positions, _last_ctp_positions_time
    now = time.time()
    if now - _last_ctp_positions_time < 10:
        return _last_ctp_positions
    adapter = await _get_adapter()
    if adapter is None:
        return []
    _last_ctp_positions = await adapter.query_positions()
    _last_ctp_positions_time = now
    return _last_ctp_positions

async def query_ctp_account_raw() -> dict:
    """获取资金（带缓存）"""
    global _last_ctp_account, _last_ctp_account_time
    now = time.time()
    if now - _last_ctp_account_time < 10:
        return _last_ctp_account
    adapter = await _get_adapter()
    if adapter is None:
        return {}
    _last_ctp_account = await adapter.query_account()
    _last_ctp_account_time = now
    return _last_ctp_account

_last_ctp_account: dict = {}
_last_ctp_account_time: float = 0


async def _generate_ml_signal(strategy_name: str, bars: list, gateway) -> dict:
    """ML策略信号生成 — 直接预测路径，绕过全量回测"""
    from backend.gold.core.models import GoldSignal, SignalDirection
    from backend.gold.ml import GoldPricePredictor, FeatureEngineer, ModelType, PredictionHorizon
    from loguru import logger

    current_price = bars[-1].close

    # 获取宏观数据
    macro_df = await gateway.get_macro_data(start="2024-01-01")
    has_macro = not macro_df.empty

    # 准备特征数据
    rows = []
    for b in bars:
        rows.append({
            "date": b.datetime.strftime("%Y-%m-%d"),
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": b.volume,
        })
    df = __import__("pandas").DataFrame(rows)

    # 合并宏观因子
    if has_macro:
        macro = macro_df.copy()
        macro["date"] = macro["date"].astype(str)
        df["date"] = df["date"].astype(str)
        df = df.merge(macro, on="date", how="left")
        for col in [c for c in macro.columns if c != "date"]:
            if col in df.columns:
                df[col] = df[col].ffill().bfill().fillna(0)
        logger.info(f"Merged macro data for ML prediction: {list(macro.columns)}")

    # 特征工程
    fe = FeatureEngineer()
    X = fe.prepare_features_for_prediction(df)
    if len(X) < 10:
        logger.warning(f"Insufficient feature samples: {len(X)}")
        return {"success": True, "data": {"signal": None, "message": "特征数据不足", "strategy": strategy_name, "price": round(current_price, 2)}}

    # 用全部数据训练一个小模型（Ridge，小样本稳定）
    full_X, y = fe.prepare_features(df)
    if len(full_X) < 20:
        logger.warning(f"Insufficient training samples: {len(full_X)}")
        return {"success": True, "data": {"signal": None, "message": "训练数据不足", "strategy": strategy_name, "price": round(current_price, 2)}}

    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    import numpy as np

    def _train_and_predict():
        import numpy as np
        # 清洗: 替换 NaN/Inf
        X_clean = np.nan_to_num(full_X, nan=0.0, posinf=0.0, neginf=0.0)
        y_clean = np.nan_to_num(y, nan=0.0)
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_clean)
        model = Ridge(alpha=1.0)
        model.fit(X_scaled, y_clean)
        X_latest = X_clean[-1:]
        X_latest_scaled = scaler.transform(X_latest)
        return float(model.predict(X_latest_scaled)[0])

    predicted_change = await asyncio.to_thread(_train_and_predict)

    # 生成信号
    threshold = 0.002  # 0.2%
    confidence = min(0.9, abs(predicted_change) / 0.02)

    signal = None
    if predicted_change > threshold:
        sl = round(current_price * 0.97, 2)  # -3%止损
        signal = GoldSignal(
            signal_id=f"ml_{datetime.now().strftime('%Y%m%d%H%M%S')}_signal",
            strategy_id=strategy_name, strategy_name=strategy_name,
            symbol="AU0", direction=SignalDirection.LONG,
            price=round(current_price, 2), volume=1, stop_loss=sl,
            confidence=round(confidence, 2),
            reason=f"ML预测涨{predicted_change*100:.2f}% (Ridge+{'宏观' if has_macro else '技术'}因子)",
            created_at=datetime.now(),
        )
    elif predicted_change < -threshold:
        sl = round(current_price * 1.03, 2)  # +3%止损
        signal = GoldSignal(
            signal_id=f"ml_{datetime.now().strftime('%Y%m%d%H%M%S')}_signal",
            strategy_id=strategy_name, strategy_name=strategy_name,
            symbol="AU0", direction=SignalDirection.SHORT,
            price=round(current_price, 2), volume=1, stop_loss=sl,
            confidence=round(confidence, 2),
            reason=f"ML预测跌{predicted_change*100:.2f}% (Ridge+{'宏观' if has_macro else '技术'}因子)",
            created_at=datetime.now(),
        )

    if signal is None:
        return {"success": True, "data": {"signal": None, "message": f"ML预测{predicted_change*100:.2f}%，未达阈值", "strategy": strategy_name, "price": round(current_price, 2)}}

    # 保存、风控、输出
    from backend.gold.data.storage import GoldDataStore
    from backend.gold.risk.checks import RiskChecker
    from backend.gold.signal.output import SignalOutput

    store = GoldDataStore()
    store.save_signal(signal)
    signal.created_at = datetime.now()

    risk_checker = RiskChecker()
    risk_checker.record_signal(signal)
    positions = await query_ctp_positions_raw()
    account = await query_ctp_account_raw()
    # 传显式权益，避免被 SET equity(price) 搞坏的 DB 值影响
    risk_result = risk_checker.check(
        signal, positions=positions, account=account,
        current_equity=GoldSettings().backtest_capital,
        initial_capital=GoldSettings().backtest_capital,
    )

    signal_output = SignalOutput()
    advice = signal_output.output(signal, risk_result)
    return {"success": True, "data": advice}


# ===== 信号查询 =====

@router.get("/signals")
async def get_recent_signals(
    strategy_name: Optional[str] = Query(None, description="策略名称过滤"),
    limit: int = Query(50, ge=1, le=200),
):
    """获取最近的交易信号（从数据库，仅返回近48小时内的）"""
    store = GoldDataStore()
    signals = store.get_signals(strategy_id=strategy_name, limit=limit)
    # 只保留48小时内的信号（旧信号是历史回测留下的）
    cutoff = datetime.now() - timedelta(hours=48)
    signals = [s for s in signals if s.created_at and s.created_at >= cutoff]
    return {"success": True, "data": signals}


# ===== 风控 =====

@router.get("/risk/status")
async def get_risk_status():
    """获取风控状态和配置"""
    config = GoldSettings()
    store = GoldDataStore()

    # 获取最近信号统计
    recent_signals = store.get_signals(limit=100)
    today_signal_count = sum(1 for s in recent_signals
                             if s.created_at and s.created_at.date() == __import__('datetime').date.today())

    # 读取历史风控日志
    risk_logs = []
    try:
        rows = store.db.execute(
            "SELECT level, reason, created_at FROM risk_log ORDER BY id DESC LIMIT 20"
        ).fetchall()
        for row in rows:
            risk_logs.append({
                "level": row[0], "reason": row[1], "created_at": row[2],
            })
    except Exception:
        pass

    # 当日风控摘要
    risk_checker = RiskChecker()
    daily = risk_checker.get_daily_summary()
    checks = risk_checker.get_check_config()

    return {
        "success": True,
        "data": {
            "checks": checks,
            "today_signal_count": today_signal_count,
            "daily_summary": daily,
            "recent_signal_count": len(recent_signals),
            "risk_logs": risk_logs[-10:],
        },
    }


# ===== 数据管理 =====

@router.post("/sync-data")
async def sync_gold_bars(
    symbol: str = Query("AU0", description="合约代码"),
    period: str = Query("d", description="K线周期"),
    start_date: str = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(None, description="结束日期 YYYY-MM-DD"),
):
    """从外部数据源同步K线到本地数据库"""
    bars = await gateway.get_bars(symbol=symbol, period=period, start=start_date, end=end_date)
    if not bars:
        raise HTTPException(status_code=400, detail="No data fetched")

    return {
        "success": True,
        "data": {
            "symbol": symbol,
            "period": period,
            "bars_synced": len(bars),
            "date_range": {
                "start": bars[0].datetime.strftime("%Y-%m-%d"),
                "end": bars[-1].datetime.strftime("%Y-%m-%d"),
            } if bars else None,
        },
    }


# ===== K线数据（图表用） =====

@router.get("/bars")
async def get_bars_for_chart(
    symbol: str = Query("AU0", description="合约代码"),
    period: str = Query("d", description="K线周期: d=日线, 1=1分钟, 5=5分钟, 30=30分钟"),
    start_date: str = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(None, description="结束日期 YYYY-MM-DD"),
    limit: int = Query(200, ge=1, le=2000, description="返回根数"),
):
    """返回K线数据用于图表展示（OHLCV + 技术指标）"""
    bars = await gateway.get_bars(
        symbol=symbol, period=period,
        start=start_date, end=end_date,
        limit=limit, refresh=True,
    )
    if not bars:
        raise HTTPException(status_code=404, detail="No bar data available")

    # 计算MA指标用于图表
    closes = [b.close for b in bars]
    def ma(arr, n):
        if len(arr) < n:
            return [None] * len(arr)
        result = [None] * (n - 1)
        for i in range(n - 1, len(arr)):
            result.append(round(sum(arr[i-n+1:i+1]) / n, 2))
        return result

    return {
        "success": True,
        "data": {
            "symbol": symbol,
            "period": period,
            "bars": [
                {
                    "time": b.datetime.strftime("%Y-%m-%d") if period == "d" else b.datetime.isoformat(),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                }
                for b in bars
            ],
            "indicators": {
                "ma5": ma(closes, 5),
                "ma10": ma(closes, 10),
                "ma20": ma(closes, 20),
                "ma60": ma(closes, 60),
            },
            "count": len(bars),
        },
    }


# ===== 市场数据仪表盘（实时用） =====

@router.get("/market-data")
async def get_market_data():
    """获取黄金实时市场数据仪表盘"""
    try:
        from backend.gold.core.config import GoldSettings
        from backend.gold.data.storage import GoldDataStore

        config = GoldSettings()
        store = GoldDataStore()

        bars = await gateway.get_bars(symbol="AU0", period="d", limit=300, refresh=True)
        if not bars:
            raise HTTPException(status_code=404, detail="No market data")

        latest = bars[-1]
        prev = bars[-2] if len(bars) > 1 else latest

        price = latest.close
        prev_price = prev.close
        change = price - prev_price
        change_pct = (change / prev_price * 100) if prev_price > 0 else 0

        high_20 = max(b.high for b in bars[-20:])
        low_20 = min(b.low for b in bars[-20:])
        high_60 = max(b.high for b in bars[-60:]) if len(bars) >= 60 else high_20
        low_60 = min(b.low for b in bars[-60:]) if len(bars) >= 60 else low_20

        # 计算简单技术指标
        closes = [b.close for b in bars]
        rsi = _calc_rsi(closes, 14)
        atr = _calc_atr(bars, 14)
        vol_avg5 = sum(b.volume for b in bars[-5:]) / 5
        vol_avg20 = sum(b.volume for b in bars[-20:]) / 20
        vol_ratio = vol_avg5 / vol_avg20 if vol_avg20 > 0 else 1

        recent_signals = store.get_signals(limit=10)

        # ===== 宏观指标数据 (DXY, VIX, US10Y, TIPS, Breakeven) =====
        macro = {}
        try:
            from backend.market_data import market_data_service
            raw = await market_data_service.get_macro_indicators()
            if raw:
                macro = {
                    "dxy": raw.get("dxy"),
                    "vix": raw.get("vix"),
                    "us10y": raw.get("us10y"),
                    "tips": raw.get("tips"),
                    "breakeven": raw.get("breakeven"),
                }
        except Exception as e:
            logger.warning(f"Macro data fetch failed: {e}")

        return {
            "success": True,
            "data": {
                "price": round(price, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "high": round(latest.high, 2),
                "low": round(latest.low, 2),
                "open": round(latest.open, 2),
                "volume": latest.volume,
                "high_20": round(high_20, 2),
                "low_20": round(low_20, 2),
                "high_60": round(high_60, 2),
                "low_60": round(low_60, 2),
                "rsi_14": round(rsi, 1) if rsi is not None else None,
                "atr_14": round(atr, 2) if atr is not None else None,
                "vol_ratio": round(vol_ratio, 2),
                "timestamp": latest.datetime.isoformat() if hasattr(latest.datetime, 'isoformat') else str(latest.datetime),
                "date": latest.datetime.strftime("%Y-%m-%d") if hasattr(latest.datetime, 'strftime') else str(latest.datetime),
                "recent_signals": recent_signals,
                # 宏观指标
                **macro,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Market data error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ===== K线技术分析 =====

@router.get("/analysis")
async def get_kline_analysis(
    symbol: str = Query("AU0", description="合约代码"),
    period: str = Query("d", description="K线周期"),
    limit: int = Query(500, ge=60, le=2000),
):
    """K线技术分析解读 — 趋势/指标/价位/综合研判"""
    bars = await gateway.get_bars(symbol=symbol, period=period, limit=limit, refresh=False)
    if not bars:
        raise HTTPException(status_code=404, detail="No data for analysis")

    closes = [b.close for b in bars]
    opens = [b.open for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    vols = [b.volume for b in bars]
    latest = bars[-1]
    price = latest.close

    # --- 移动均线 ---
    def ma(n):
        if len(closes) < n: return None
        return round(sum(closes[-n:]) / n, 2)

    ma5, ma10, ma20, ma60, ma120, ma250 = [ma(n) for n in [5, 10, 20, 60, 120, 250]]
    mas = {"ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60, "ma120": ma120, "ma250": ma250}

    # 均线排列
    short = [ma5, ma10, ma20]
    short_valid = all(v is not None for v in short)
    if short_valid and all(short[i] > short[i + 1] for i in range(2)):
        ma_alignment = "多头排列"
    elif short_valid and all(short[i] < short[i + 1] for i in range(2)):
        ma_alignment = "空头排列"
    else:
        ma_alignment = "交织盘整"

    # 价格相对均线位置
    above_ma_count = sum(1 for m in [ma5, ma10, ma20, ma60] if m is not None and price > m)
    below_ma_count = sum(1 for m in [ma5, ma10, ma20, ma60] if m is not None and price < m)
    ma_position = "均线上方" if above_ma_count >= 3 else "均线下方" if below_ma_count >= 3 else "均线附近"

    # --- RSI ---
    rsi14 = _calc_rsi(closes, 14)
    rsi_signal = "超卖" if rsi14 and rsi14 < 30 else "超买" if rsi14 and rsi14 > 70 else "中性"

    # --- 布林带 (20,2) ---
    import statistics
    bb_ma = ma(20)
    bb_std = statistics.stdev(closes[-20:]) if len(closes) >= 20 else 0
    bb_upper = round(bb_ma + 2 * bb_std, 2) if bb_ma else None
    bb_lower = round(bb_ma - 2 * bb_std, 2) if bb_ma else None
    bb_width = round((bb_upper - bb_lower) / bb_ma * 100, 2) if bb_ma and bb_upper and bb_lower else None
    if bb_lower and bb_upper:
        bb_position = round((price - bb_lower) / (bb_upper - bb_lower) * 100, 1)
        bb_signal = "下轨附近" if bb_position < 20 else "上轨附近" if bb_position > 80 else "中轨附近"
    else:
        bb_position, bb_signal = None, "--"

    # --- 关键价位 ---
    lookback = min(60, len(bars))
    recent_high = max(highs[-lookback:])
    recent_low = min(lows[-lookback:])
    recent_high_idx = highs[-lookback:].index(recent_high)
    recent_low_idx = lows[-lookback:].index(recent_low)

    # 近52周高/低（仅日线有意义）
    yearly = min(252, len(bars))
    high_52w = max(highs[-yearly:])
    low_52w = min(lows[-yearly:])

    # 斐波那契
    fib_diff = recent_high - recent_low
    fib_levels = {
        "0.236": round(recent_high - fib_diff * 0.236, 2),
        "0.382": round(recent_high - fib_diff * 0.382, 2),
        "0.500": round(recent_high - fib_diff * 0.500, 2),
        "0.618": round(recent_high - fib_diff * 0.618, 2),
        "0.786": round(recent_high - fib_diff * 0.786, 2),
    }

    # --- 成交量分析 ---
    vol_avg_60 = sum(vols[-60:]) / 60 if len(vols) >= 60 else sum(vols) / len(vols)
    vol_avg_20 = sum(vols[-20:]) / 20
    vol_avg_5 = sum(vols[-5:]) / 5
    vol_ratio_20_60 = round(vol_avg_20 / vol_avg_60, 2) if vol_avg_60 else 1
    vol_trend = "缩量" if vol_ratio_20_60 < 0.8 else "放量" if vol_ratio_20_60 > 1.3 else "正常"
    latest_vol_ratio = round(vols[-1] / vol_avg_20, 1) if vol_avg_20 else 1

    # --- 特殊K线形态（近30根） ---
    patterns = []
    for i in range(-min(30, len(bars)), 0):
        b = bars[i]
        body = abs(b.close - b.open)
        total_range = b.high - b.low
        if total_range == 0:
            continue
        body_ratio = body / total_range

        # 十字星
        if body_ratio < 0.1 and total_range / b.open * 100 > 1.0:
            patterns.append({
                "date": b.datetime.strftime("%Y-%m-%d") if period == "d" else b.datetime.isoformat(),
                "type": "十字星",
                "detail": f"上下影线明显，多空博弈激烈" if b.high - max(b.close, b.open) > body and min(b.close, b.open) - b.low > body else "纺锤线，趋势犹豫",
                "signal": "反转预警" if i == -1 else "分歧信号",
            })

        # 大阳/大阴
        range_pct = (b.high - b.low) / b.open * 100
        if b.close > b.open and range_pct > 2.5:
            direction = "大阳线"
            signal_text = "多头强势" if i >= -5 else "多方占优"
            patterns.append({"date": b.datetime.isoformat(), "type": direction, "detail": f"涨幅{(b.close-b.open)/b.open*100:.1f}%", "signal": signal_text})
        elif b.close < b.open and range_pct > 2.5:
            direction = "大阴线"
            signal_text = "空头打压" if i >= -5 else "空方占优"
            patterns.append({"date": b.datetime.isoformat(), "type": direction, "detail": f"跌幅{(b.close-b.open)/b.open*100:.1f}%", "signal": signal_text})

    patterns = patterns[:8]  # 最多8个

    # --- 趋势强度 ---
    # 计算斜率（最近20根收盘价的线性回归）
    if len(closes) >= 20:
        n = 20
        x_avg = (n - 1) / 2
        y_avg = sum(closes[-n:]) / n
        num = sum((i - x_avg) * (closes[-n + i] - y_avg) for i in range(n))
        den = sum((i - x_avg) ** 2 for i in range(n))
        slope = num / den if den != 0 else 0
        trend_strength = "强势" if abs(slope) > 2 else "温和" if abs(slope) > 0.5 else "弱势"
    else:
        slope, trend_strength = 0, "--"

    # 近10日涨跌天数
    up_days = sum(1 for i in range(-10, 0) if closes[i] > closes[i - 1])
    down_days = 10 - up_days

    # 连续涨跌
    streak = 0
    for i in range(-1, -min(20, len(closes)), -1):
        if closes[i] > closes[i - 1]:
            if streak >= 0: streak += 1
            else: break
        else:
            if streak <= 0: streak -= 1
            else: break
    streak_dir = f"连涨{streak}天" if streak > 0 else f"连跌{abs(streak)}天" if streak < 0 else "平盘"

    # --- 综合研判 ---
    judgments = []
    if rsi14 and rsi14 < 35:
        judgments.append("RSI进入超卖区，短期技术性反弹需求上升")
    if bb_position is not None and bb_position < 20:
        judgments.append(f"价格处于布林下轨附近（{bb_position}%），偏低估区域")
    if ma_alignment == "空头排列":
        judgments.append("均线系统空头排列，中期趋势偏空")
    if above_ma_count == 0:
        judgments.append(f"价格运行于所有均线之下，空头主导市场")
    if vol_ratio_20_60 and vol_ratio_20_60 < 0.8:
        judgments.append("近期持续缩量，下跌动能衰减，关注企稳信号")
    if slope and slope < -3:
        judgments.append(f"近期斜率较陡（{slope:.1f}），下跌速度偏快，注意加速赶底可能")
    elif slope and slope > 3:
        judgments.append(f"近期斜率较陡（{slope:.1f}），上涨加速，关注超买风险")
    if streak <= -3:
        judgments.append(f"连续{abs(streak)}日下跌，短期乖离较大")
    if streak >= 3:
        judgments.append(f"连续{streak}日上涨，短期过热需注意回调")

    if not judgments:
        judgments.append("各指标信号不明确，建议结合更大周期趋势判断")

    r = {
        "trend": {
            "direction": "上涨" if slope > 0.5 else "下跌" if slope < -0.5 else "震荡",
            "ma_alignment": ma_alignment,
            "ma_position": ma_position,
            "slope": round(slope, 2) if slope != 0 else None,
            "strength": trend_strength,
            "up_days": up_days,
            "down_days": down_days,
            "streak": streak_dir,
            # 最近涨跌幅（从当前周期视角）
            "change_1w": round((price - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 else None,
            "change_1m": round((price - closes[-22]) / closes[-22] * 100, 2) if len(closes) >= 22 else None,
            "change_3m": round((price - closes[-66]) / closes[-66] * 100, 2) if len(closes) >= 66 else None,
        },
        "indicators": {
            "rsi14": rsi14,
            "rsi_signal": rsi_signal,
            "bb_upper": bb_upper,
            "bb_mid": bb_ma,
            "bb_lower": bb_lower,
            "bb_width": bb_width,
            "bb_position": bb_position,
            "bb_signal": bb_signal,
            "atr14": _calc_atr(bars, 14),
        },
        "mas": mas,
        "levels": {
            "recent_high": round(recent_high, 2),
            "recent_low": round(recent_low, 2),
            "high_52w": round(high_52w, 2),
            "low_52w": round(low_52w, 2),
            "price_position_52w": round((price - low_52w) / (high_52w - low_52w) * 100, 1) if high_52w != low_52w else None,
            "fib": fib_levels,
        },
        "volume": {
            "latest": vols[-1],
            "avg_20": round(vol_avg_20, 0),
            "avg_60": round(vol_avg_60, 0),
            "ratio_20_60": vol_ratio_20_60,
            "trend": vol_trend,
            "recent_vol_ratio": latest_vol_ratio,
        },
        "patterns": patterns,
        "judgment": "；".join(judgments),
    }
    return {"success": True, "data": r, "symbol": symbol, "period": period}


# ===== 策略对比（当前市场环境适配度） =====

@router.get("/strategy-comparison")
async def get_strategy_comparison(
    symbol: str = Query("AU0", description="合约代码"),
):
    """基于当前市场环境，评估各策略的适配度评分"""
    bars = await gateway.get_bars(symbol=symbol, period="d", limit=120, refresh=False)
    if not bars:
        raise HTTPException(status_code=404, detail="No data for comparison")

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    opens = [b.open for b in bars]
    vols = [b.volume for b in bars]
    price = closes[-1]
    atr14 = _calc_atr(bars, 14) or 0

    # --- 市场状态检测 ---

    # 1. 趋势强度：20日收盘价斜率 + 均线排列一致性
    n = 20
    x_avg = (n - 1) / 2
    y_avg = sum(closes[-n:]) / n
    slope = sum((i - x_avg) * (closes[-n + i] - y_avg) for i in range(n)) / max(sum((i - x_avg) ** 2 for i in range(n)), 1)
    trend_strength = min(abs(slope) / 3, 1.0)  # 0~1

    # 均线排列得分
    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else price
    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else price
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else price
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else price

    # 多头排列=1, 空头=-1, 混合=0
    ma_direction = 0
    if ma5 > ma10 > ma20 > ma60: ma_direction = 1
    elif ma5 < ma10 < ma20 < ma60: ma_direction = -1

    # 2. 波动率状态
    avg_atr_ratio = atr14 / price if price > 0 else 0
    # 对比60日ATR均值判断波动异常
    atrs_60 = []
    for i in range(1, min(60, len(bars))):
        b, p = bars[-i], bars[-i - 1]
        tr = max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close))
        atrs_60.append(tr)
    atr_60_avg = sum(atrs_60) / len(atrs_60) if atrs_60 else atr14
    vol_anomaly = (atr14 - atr_60_avg) / atr_60_avg if atr_60_avg > 0 else 0  # >0.2=高波动, <-0.2=低波动

    # 3. RSI区间
    rsi14 = _calc_rsi(closes, 14) or 50

    # 4. 布林带位置
    bb_mid = sum(closes[-20:]) / 20 if len(closes) >= 20 else price
    bb_std = (sum((c - bb_mid) ** 2 for c in closes[-20:]) / 20) ** 0.5 if len(closes) >= 20 else 0
    bb_lower = bb_mid - 2 * bb_std
    bb_upper = bb_mid + 2 * bb_std
    bb_pos = (price - bb_lower) / (bb_upper - bb_lower + 1e-10) if bb_upper > bb_lower else 0.5

    # 5. 近期涨跌节奏
    up_count = sum(1 for i in range(-10, 0) if closes[i] > closes[i - 1])
    down_count = 10 - up_count

    # --- 各策略评分 ---

    results = []

    # 趋势跟踪策略
    tf_score = 50.0
    tf_reasons = []
    if ma_direction != 0:
        tf_score += 25
        tf_reasons.append(f"均线{'多头' if ma_direction > 0 else '空头'}排列，趋势明确+25分")
    else:
        tf_score -= 15
        tf_reasons.append("均线交织，无明确趋势方向-15分")
    if trend_strength > 0.5:
        tf_score += 10
        tf_reasons.append(f"趋势强度{(trend_strength*100):.0f}%，适合趋势跟踪+10分")
    else:
        tf_score -= 10
        tf_reasons.append(f"趋势偏弱{(trend_strength*100):.0f}%，震荡市中趋势策略易磨损-10分")
    if atr_60_avg > 0 and vol_anomaly > 0.3:
        tf_score -= 10
        tf_reasons.append("波动率异常升高，假突破风险大-10分")
    tf_score = max(0, min(100, tf_score))
    results.append({
        "strategy_id": "trend_following",
        "strategy_name": "趋势跟踪",
        "icon": "📈",
        "score": round(tf_score),
        "tags": ["多周期均线", "Donchian突破", "ATR止损"],
        "description": "均线排列确认方向 + Donchian通道突破进场",
        "reasons": tf_reasons,
    })

    # 均值回归策略
    mr_score = 50.0
    mr_reasons = []
    if rsi14 and rsi14 < 35:
        mr_score += 25
        mr_reasons.append(f"RSI={rsi14:.0f}处于超卖区，均值回归机会显著+25分")
    elif rsi14 and rsi14 > 65:
        mr_score += 20
        mr_reasons.append(f"RSI={rsi14:.0f}处于超买区，均值回归机会+20分")
    else:
        mr_score -= 5
        mr_reasons.append(f"RSI={rsi14:.0f}处于中性区，回归信号不强烈-5分")
    if bb_pos < 0.2:
        mr_score += 15
        mr_reasons.append(f"价格在布林下轨附近({bb_pos*100:.0f}%)，支撑区域+15分")
    elif bb_pos > 0.8:
        mr_score += 15
        mr_reasons.append(f"价格在布林上轨附近({bb_pos*100:.0f}%)，压力区域+15分")
    else:
        mr_score -= 5
        mr_reasons.append(f"价格在布林中轨区域({bb_pos*100:.0f}%)，偏离不够-5分")
    if ma_direction != 0 and abs(slope) > 2:
        mr_score -= 20
        mr_reasons.append("趋势强劲时逆势风险大-20分")
    mr_score = max(0, min(100, mr_score))
    results.append({
        "strategy_id": "mean_reversion",
        "strategy_name": "均值回归",
        "icon": "🔄",
        "score": round(mr_score),
        "tags": ["布林带+RSI", "回归中轨", "ATR止损"],
        "description": "布林带上下轨 + RSI超买超卖确认",
        "reasons": mr_reasons,
    })

    # ML预测策略
    ml_score = 50.0
    ml_reasons = []
    if len(bars) >= 150:
        ml_score += 10
        ml_reasons.append(f"历史数据{len(bars)}条充足，可提供训练样本+10分")
    else:
        ml_score -= 15
        ml_reasons.append(f"历史数据仅{len(bars)}条不充足，模型训练受限-15分")
    if abs(vol_anomaly) < 0.2:
        ml_score += 10
        ml_reasons.append("波动率稳定，ML模式识别可靠+10分")
    else:
        ml_score -= 10
        ml_reasons.append(f"波动异常({vol_anomaly*100:.0f}%)，历史模式可能失效-10分")
    if ma_direction != 0:
        ml_score += 5
        ml_reasons.append("存在趋势方向，ML可学习+5分")
    # 近期走势规律性
    recent_volatility = sum(abs(closes[i] - closes[i-1]) / closes[i-1] for i in range(-20, 0)) / 20 if len(closes) >= 21 else 0
    if 0.005 < recent_volatility < 0.02:
        ml_score += 5
        ml_reasons.append("日内波动适中，信号噪声比良好+5分")
    ml_score = max(0, min(100, ml_score))
    results.append({
        "strategy_id": "ml_predictor",
        "strategy_name": "ML预测",
        "icon": "🤖",
        "score": round(ml_score),
        "tags": ["LightGBM/XGBoost", "技术+宏观因子", "滑动窗口"],
        "description": "机器学习多因子模型预测价格方向",
        "reasons": ml_reasons,
    })

    # 排序：高分在前
    results.sort(key=lambda r: r["score"], reverse=True)
    best = results[0]

    # --- 市场状态摘要 ---
    if ma_direction > 0 and trend_strength > 0.4:
        regime = "多头趋势"
        regime_desc = "均线多头排列，价格沿趋势上行"
    elif ma_direction < 0 and trend_strength > 0.4:
        regime = "空头趋势"
        regime_desc = "均线空头排列，价格持续下行"
    elif vol_anomaly > 0.3:
        regime = "高波震荡"
        regime_desc = "波动率显著放大，市场分歧加大"
    elif rsi14 and rsi14 < 35:
        regime = "超卖反弹"
        regime_desc = f"RSI进入超卖区({rsi14:.0f})，技术面支持反弹"
    elif rsi14 and rsi14 > 65:
        regime = "超买回调"
        regime_desc = f"RSI进入超买区({rsi14:.0f})，短线过热需谨慎"
    else:
        regime = "区间震荡"
        regime_desc = "价格在区间内整理，无明显方向偏好"

    return {
        "success": True,
        "data": {
            "market_regime": regime,
            "regime_description": regime_desc,
            "best_strategy": best["strategy_name"],
            "best_icon": best["icon"],
            "indicators_summary": {
                "rsi14": rsi14,
                "trend_strength": round(trend_strength * 100),
                "vol_anomaly_pct": round(vol_anomaly * 100),
                "bb_position_pct": round(bb_pos * 100),
                "ma_alignment": "多头排列" if ma_direction > 0 else "空头排列" if ma_direction < 0 else "交织盘整",
            },
            "strategies": results,
        },
    }


def _calc_rsi(closes: list, period: int = 14):
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        if diff >= 0:
            gains.append(diff); losses.append(0)
        else:
            gains.append(0); losses.append(abs(diff))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _calc_atr(bars: list, period: int = 14):
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        b, p = bars[i], bars[i-1]
        tr = max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close))
        trs.append(tr)
    return round(sum(trs[-period:]) / period, 2)


# ===== 配置 =====

@router.get("/config")
async def get_gold_config():
    """获取黄金交易配置"""
    config = GoldSettings()
    return {
        "success": True,
        "data": {
            "au_multiplier": config.au_multiplier,
            "au_margin_rate": config.au_margin_rate,
            "au_price_tick": config.au_price_tick,
            "au_limit_pct": config.au_limit_pct,
            "backtest_capital": config.backtest_capital,
            "backtest_commission_per_lot": config.backtest_commission_per_lot,
            "backtest_slippage_per_lot": config.backtest_slippage_per_lot,
            "risk_free_rate": config.risk_free_rate,
            "max_drawdown_pct": config.max_drawdown_pct,
            "max_daily_loss_pct": config.max_daily_loss_pct,
            "max_daily_signals": config.max_daily_signals,
        },
    }


# ===== 模拟交易（适配器模式，支持 SimNow/openctp/QMT 切换） =====

_adapter = None           # TradingAdapter 实例
_adapter_lock = asyncio.Lock()
_adapter_mode = None      # 当前已初始化的模式


async def _get_adapter() -> Optional["TradingAdapter"]:
    """获取交易适配器（延迟初始化）"""
    global _adapter, _adapter_mode

    from backend.gold.core.config import gold_settings
    s = gold_settings
    if not s.ctp_enabled:
        return None

    mode = (s.trading_mode or "simnow").lower()
    if _adapter is not None and _adapter_mode == mode:
        return _adapter

    async with _adapter_lock:
        if _adapter is not None and _adapter_mode != mode:
            await _adapter.stop()
            _adapter = None
        if _adapter is not None:
            return _adapter

        try:
            from backend.gold.trading.connectors import create_adapter
            _adapter = create_adapter(mode)
            await _adapter.start()
            _adapter_mode = mode
            logger.info(f"[交易适配器] 已创建: {mode}")
            return _adapter
        except Exception as e:
            logger.error(f"[交易适配器] 创建失败: {e}")
            return None


@router.get("/modes")
async def get_trading_modes():
    """获取所有可用的交易模式"""
    from backend.gold.core.config import gold_settings
    current = (gold_settings.trading_mode or "simnow").lower()

    modes = [
        {"id": "simnow",  "name": "SimNow",         "description": "上期技术官方仿真，需注册，7×24环境",       "current": current == "simnow"},
        {"id": "openctp", "name": "openctp TTS",    "description": "开放平台仿真，扫码即用，免认证，7×24稳定", "current": current == "openctp"},
    ]
    return {"success": True, "data": {"modes": modes, "current": current}}


@router.post("/mode")
async def set_trading_mode(mode: str = "simnow"):
    """切换交易模式（运行时切换会自动重启适配器）"""
    global _adapter, _adapter_mode

    valid_modes = ["simnow", "openctp"]
    if mode not in valid_modes:
        raise HTTPException(status_code=400, detail=f"不支持的模式: {mode}，支持: {valid_modes}")

    # 更新配置中的模式
    from backend.gold.core.config import gold_settings
    gold_settings.trading_mode = mode

    # 如果已连接且模式不同，关闭旧的
    if _adapter is not None and _adapter_mode != mode:
        async with _adapter_lock:
            if _adapter is not None:
                await _adapter.stop()
                _adapter = None
                _adapter_mode = None

    logger.info(f"[交易] 模式已切换: {mode}")
    return {"success": True, "data": {"mode": mode}}


@router.get("/ctp/status")
async def get_ctp_status():
    """模拟交易连接状态"""
    client = await _get_adapter()
    if client is None:
        from backend.gold.core.config import GoldSettings
        s = GoldSettings()
        return {
            "success": True,
            "data": {
                "enabled": s.ctp_enabled,
                "mode": s.trading_mode,
                "connected": False,
                "message": "未启用或配置不完整",
            },
        }
    status = client.get_status()
    return {
        "success": True,
        "data": {
            "enabled": True,
            "mode": client.name,
            "connected": status.get("connected", status.get("md_logged_in", False)),
            "main_contract": client.get_main_contract(),
            "symbols": status.get("symbols", []),
            "detail": status,
        },
    }


@router.get("/ctp/positions")
async def get_ctp_positions():
    """实时持仓"""
    client = await _get_adapter()
    if client is None:
        return {"success": True, "data": [], "message": "未启用"}
    positions = await client.query_positions()
    return {"success": True, "data": positions}


@router.get("/ctp/account")
async def get_ctp_account():
    """账户资金信息"""
    client = await _get_adapter()
    if client is None:
        return {"success": True, "data": {}, "message": "未启用"}
    account = await client.query_account()
    return {"success": True, "data": account}


@router.get("/ctp/orders")
async def get_ctp_orders():
    """当日委托记录"""
    client = await _get_adapter()
    if client is None:
        return {"success": True, "data": [], "message": "未启用"}
    orders = await client.query_orders()
    return {"success": True, "data": orders}


@router.get("/ctp/data")
async def get_ctp_data():
    """全量数据（一次调用返回状态+持仓+资金+委托）"""
    client = await _get_adapter()
    if client is None:
        from backend.gold.core.config import GoldSettings
        s = GoldSettings()
        return {
            "success": True,
            "data": {
                "enabled": s.ctp_enabled,
                "mode": s.trading_mode,
                "connected": False,
                "status": None,
                "account": {},
                "positions": [],
                "orders": [],
            },
        }

    status = client.get_status()
    positions = await client.query_positions()
    account = await client.query_account()
    orders = await client.query_orders()

    return {
        "success": True,
        "data": {
            "enabled": True,
            "mode": client.name,
            "connected": status.get("connected", status.get("md_logged_in", False)),
            "status": status,
            "account": account,
            "positions": positions,
            "orders": orders,
        },
    }
