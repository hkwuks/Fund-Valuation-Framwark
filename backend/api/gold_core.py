"""AuroraCore 黄金量化 API — 新内核驱动，与旧 API 共存"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from loguru import logger

from core import (
    BacktestEngine, BacktestConfig, EventBus, Bar,
    MetricsCalculator, ComparisonReport, StrategyRegistry,
    WalkForwardValidator,
)
from gold.adapter import GoldDomainAdapter

router = APIRouter(prefix="/gold-core", tags=["黄金量化 (AuroraCore)"])

adapter = GoldDomainAdapter()


class BacktestRequest(BaseModel):
    strategy_name: str
    symbol: str = "AU0"
    period: str = "d"
    start_date: str = "2024-01-01"
    end_date: str = "2025-12-31"
    capital: float = 1_000_000.0

    # 回测模式
    method: str = "simple"  # simple / walk_forward / auto（ML 策略自动走 WF）

    # Walk-Forward 参数
    wf_train_window: int = 252
    wf_test_window: int = 60
    wf_purge_days: int = 1
    wf_embargo_days: int = 20
    wf_warmup_bars: int = 60

    # 期货撮合参数
    multiplier: int = 1000
    margin_rate: float = 0.08
    fill_ratio: float = 1.0
    execution_delay: int = 0
    slippage_per_lot: float = 20.0
    dynamic_slippage: bool = True
    slippage_atr_ratio: float = 0.5
    open_commission: float = 10.0
    close_commission: float = 10.0
    close_today_commission: float = 0.0
    params: dict = {}


@router.get("/strategies")
async def list_core_strategies():
    """列出 AuroraCore 注册的黄金策略"""
    gold_strategies = adapter.get_available_strategies()
    result = []
    for name, cls in gold_strategies.items():
        result.append({
            "name": name,
            "type": getattr(cls, "strategy_type", ""),
            "description": getattr(cls, "description", ""),
            "default_params": getattr(cls, "default_params", {}),
        })
    return {"success": True, "data": result}


@router.post("/backtest")
async def run_core_backtest(req: BacktestRequest):
    """使用 AuroraCore 引擎运行黄金回测

    method:
      - simple: 一次性回测（默认）
      - walk_forward: 滚动窗口回测（Purging + Embargo）
      - auto: ML 策略走 walk_forward，其它走 simple
    """
    from backend.gold.data.gateway import GoldDataGateway

    gateway = GoldDataGateway()
    klines = await gateway.get_bars(
        symbol=req.symbol, period=req.period,
        start=req.start_date, end=req.end_date, refresh=True,
    )
    if not klines:
        raise HTTPException(400, detail="无 K 线数据")

    bars = []
    for k in klines:
        dt = k.datetime if hasattr(k, "datetime") and k.datetime else datetime.now()
        bars.append(Bar(
            symbol=req.symbol, exchange="SHFE", timeframe=req.period,
            datetime=dt,
            open=float(k.open),
            high=float(k.high),
            low=float(k.low),
            close=float(k.close),
            volume=float(k.volume),
        ))
    if not bars:
        raise HTTPException(400, detail="K 线数据解析失败")

    # 查找策略
    strategies = adapter.get_available_strategies()
    if req.strategy_name not in strategies:
        try:
            cls = StrategyRegistry.get(req.strategy_name)
        except KeyError:
            raise HTTPException(404, detail=f"策略 {req.strategy_name} 未找到")
    else:
        cls = strategies[req.strategy_name]

    # 决定回测模式
    is_ml = req.strategy_name in ("ml_predictor",)
    effective_method = req.method
    if effective_method == "auto":
        effective_method = "walk_forward" if is_ml else "simple"

    # ── Walk-Forward 模式 ──
    if effective_method == "walk_forward":
        from core import RiskPipeline

        def _make_engine(bar_slice):
            """创建配置好的 BacktestEngine，用于单个 WF 窗口"""
            inst = cls()
            if req.params:
                inst.params = req.params
            cfg = BacktestConfig(
                initial_capital=req.capital,
                timeframe=_timeframe_map(req.period),
            )
            eng = BacktestEngine(cfg)
            eng.set_event_bus(EventBus())
            eng.set_strategy(inst)
            eng.set_executor(adapter.create_executor({
                "multiplier": req.multiplier,
                "margin_rate": req.margin_rate,
                "fill_ratio": req.fill_ratio,
                "execution_delay": req.execution_delay,
                "slippage_per_lot": req.slippage_per_lot,
                "dynamic_slippage": req.dynamic_slippage,
                "slippage_atr_ratio": req.slippage_atr_ratio,
            }))
            eng.set_cost_model(adapter.create_cost_model({
                "open_commission": req.open_commission,
                "close_commission": req.close_commission,
                "close_today_commission": req.close_today_commission,
            }))
            pipeline = RiskPipeline()
            for c in adapter.default_risk_checks():
                pipeline.add(c)
            eng.set_risk(pipeline)
            eng.set_data(bar_slice)
            return eng

        try:
            wfv = WalkForwardValidator(
                train_window=req.wf_train_window,
                test_window=req.wf_test_window,
                purge_days=req.wf_purge_days,
                embargo_days=req.wf_embargo_days,
            )
            wf_result = await _run_in_thread(wfv.run, bars, _make_engine, req.wf_warmup_bars)
        except Exception as e:
            logger.error(f"AuroraCore WF 回测失败: {e}")
            raise HTTPException(500, detail=str(e))

        # 合并各窗口权益曲线用于估算总收益
        all_equity = [100_000]
        for w in wf_result["windows"]:
            test_ret = w["test_return"]
            if test_ret != 0:
                all_equity.append(all_equity[-1] * (1 + test_ret))
        full_equity = all_equity[1:] if len(all_equity) > 1 else [0]
        metrics = MetricsCalculator.calculate(full_equity)
        metrics.total_trades = wf_result["total_test_trades"]

        return {
            "success": True,
            "data": {
                "strategy": req.strategy_name,
                "engine": "aurora_core",
                "method": "walk_forward",
                "metadata": {
                    "symbol": req.symbol,
                    "bar_count": len(bars),
                    "period": f"{req.start_date} ~ {req.end_date}",
                    "wf_windows": wf_result["n_windows"],
                },
                "metrics": {
                    "total_return": metrics.total_return,
                    "annual_return": metrics.annual_return,
                    "volatility": metrics.volatility,
                    "max_drawdown": metrics.max_drawdown,
                    "sharpe_ratio": metrics.sharpe_ratio,
                    "sortino_ratio": metrics.sortino_ratio,
                    "calmar_ratio": metrics.calmar_ratio,
                    "total_trades": metrics.total_trades,
                },
                "walk_forward": wf_result,
            },
        }

    # ── Simple 模式（原逻辑 + 基准对比） ──
    strategy = cls()
    if req.params:
        strategy.params = req.params

    cfg = BacktestConfig(
        initial_capital=req.capital,
        start_date=bars[0].datetime.date(),
        end_date=bars[-1].datetime.date(),
        timeframe=_timeframe_map(req.period),
    )

    engine = BacktestEngine(cfg)
    engine.set_event_bus(EventBus())
    engine.set_strategy(strategy)
    engine.set_executor(adapter.create_executor({
        "multiplier": req.multiplier,
        "margin_rate": req.margin_rate,
        "fill_ratio": req.fill_ratio,
        "execution_delay": req.execution_delay,
        "slippage_per_lot": req.slippage_per_lot,
        "dynamic_slippage": req.dynamic_slippage,
        "slippage_atr_ratio": req.slippage_atr_ratio,
    }))
    engine.set_cost_model(adapter.create_cost_model({
        "open_commission": req.open_commission,
        "close_commission": req.close_commission,
        "close_today_commission": req.close_today_commission,
    }))
    engine.set_data(bars)

    from core import RiskPipeline
    pipeline = RiskPipeline()
    for c in adapter.default_risk_checks():
        pipeline.add(c)
    engine.set_risk(pipeline)

    try:
        report = await _run_in_thread(engine.run)
    except Exception as e:
        logger.error(f"AuroraCore 黄金回测失败: {e}")
        raise HTTPException(500, detail=str(e))

    equity = [e["equity"] for e in report.equity_curve]

    # 基准收益率（买入持有）
    benchmark_returns = None
    if len(bars) > 1:
        closes = [b.close for b in bars]
        benchmark_returns = [(closes[i] - closes[i - 1]) / closes[i - 1]
                            for i in range(1, len(closes))]

    metrics = MetricsCalculator.calculate(
        equity, benchmark_returns=benchmark_returns,
    )
    metrics.total_trades = report.total_trades

    return {
        "success": True,
        "data": {
            "strategy": req.strategy_name,
            "engine": "aurora_core",
            "method": "simple",
            "metadata": {
                "symbol": req.symbol,
                "bar_count": len(bars),
                "period": f"{req.start_date} ~ {req.end_date}",
            },
            "metrics": {
                "total_return": metrics.total_return,
                "annual_return": metrics.annual_return,
                "volatility": metrics.volatility,
                "max_drawdown": metrics.max_drawdown,
                "sharpe_ratio": metrics.sharpe_ratio,
                "sortino_ratio": metrics.sortino_ratio,
                "calmar_ratio": metrics.calmar_ratio,
                "var_95": metrics.var_95,
                "total_trades": metrics.total_trades,
                "win_rate": metrics.win_rate,
            },
            "benchmark": {
                "total_return": metrics.benchmark_return,
                "information_ratio": metrics.information_ratio,
            } if hasattr(metrics, 'benchmark_return') else None,
            "equity_curve": equity,
            "trade_count": report.total_trades,
        },
    }


def _timeframe_map(period: str) -> str:
    return {"1m": "1m", "5m": "5m", "d": "1d", "w": "1w", "mo": "1M"}.get(period, "1d")


async def _run_in_thread(fn, *args, **kwargs):
    import asyncio
    return await asyncio.to_thread(fn, *args, **kwargs)
