"""
自动交易引擎 — CTP数据管道 + RL增量训练 + 信号自动执行 闭环

启动:
    python -m backend.gold.trading.auto_trade [--mode simnow|openctp]

流程:
  CTP Tick → BarAssembler(1m Bar) → GoldDataStore
    → 每N个新Bar触发RL增量训练
    → 每K个新Bar触发信号生成
    → 风控检查 → LiveExecutor下单 → CTP执行
"""
import argparse
import asyncio
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import locale as _locale
for _p in ['/tmp/locale', '/usr/lib/locale', '/usr/share/locale']:
    if os.path.isdir(os.path.join(_p, 'zh_CN.GB18030')):
        os.environ['LOCPATH'] = _p
        break
for _name in ['zh_CN.GB18030', 'C.UTF-8']:
    try:
        _locale.setlocale(_locale.LC_ALL, _name)
        break
    except _locale.Error:
        continue

import numpy as np
from loguru import logger

from backend.gold.core.config import gold_settings
from backend.gold.core.models import GoldSignal, SignalDirection
from backend.gold.data.storage import GoldDataStore
from backend.gold.data.gateway import GoldDataGateway
from backend.gold.ml.rl import RLTrainer, bars_to_dataframe
from backend.gold.ml.model_registry import ModelRegistry
from backend.gold.risk.checks import RiskChecker
from backend.gold.trading.connectors import create_adapter
from backend.gold.trading.connectors.bar_assembler import BarAssembler
from backend.gold.trading.execution.executor import LiveExecutor
from backend.gold.trading.execution.sim_account import InternalSimAccount
from backend.gold.risk.order_manager import OrderManager

_STOP_FILE = "/tmp/auto_trade_stop"


class AutoTradeEngine:
    """自动交易引擎 — 数据→训练→信号→执行 闭环"""

    def __init__(self, mode: str = "simnow",
                 train_interval_bars: int = 100,
                 signal_interval_bars: int = 20,
                 rl_iterations: int = 10,
                 rl_steps: int = 512,
                 max_history_bars: int = 2000):
        self.mode = mode
        self.train_interval = train_interval_bars
        self.signal_interval = signal_interval_bars
        self.rl_iterations = rl_iterations
        self.rl_steps = rl_steps
        self.max_history_bars = max_history_bars

        self.adapter = None
        self.store = GoldDataStore()
        self.gateway = GoldDataGateway()
        self.registry = ModelRegistry()
        self._running = False
        self._bar_count = 0
        self._last_train_bar = 0
        self._last_signal_bar = 0
        self._start_time: Optional[float] = None
        self._rl_ready = False

        # 组件
        self._trainer = RLTrainer()
        self._risk_checker = RiskChecker()
        self._bar_assembler = BarAssembler(on_bar=self._on_bar_complete)

        # 每日收盘后加载历史数据训练一次
        self._history_loaded = False

        if os.path.exists(_STOP_FILE):
            os.remove(_STOP_FILE)

    @property
    def status(self) -> dict:
        return {
            "running": self._running,
            "mode": self.mode,
            "rl_ready": self._rl_ready,
            "bar_count": self._bar_count,
            "last_train_bar": self._last_train_bar,
            "last_signal_bar": self._last_signal_bar,
            "uptime_seconds": time.time() - (self._start_time or time.time()),
        }

    async def start(self):
        """启动引擎"""
        logger.info(f"[AutoTrade] 启动 (mode={self.mode}, train_interval={self.train_interval}, signal_interval={self.signal_interval})")
        self._running = True
        self._start_time = time.time()

        # 1. 加载历史数据做初始训练
        await self._load_history_and_train()

        # 2. 连接CTP
        self.adapter = create_adapter(self.mode)
        self.adapter.on_tick_callback = self._on_tick
        self.adapter.event_callback = self._on_event
        await self.adapter.start()
        logger.info(f"[AutoTrade] CTP已连接，等待行情...")

        # 3. 主循环
        while self._running:
            await asyncio.sleep(30)
            self._print_stats()
            if os.path.exists(_STOP_FILE):
                logger.info("[AutoTrade] 检测到停服标记")
                break

        await self.stop()

    async def stop(self):
        self._running = False
        if self.adapter:
            await self.adapter.stop()
        elapsed = time.time() - (self._start_time or time.time())
        logger.info(f"[AutoTrade] 已停止 (运行 {elapsed:.0f}s, bar={self._bar_count})")

    # ── 回调 ──────────────────────────────────────────────

    def _on_tick(self, tick):
        self._bar_assembler.update_tick(tick)

    def _on_event(self, msg: dict):
        et = msg.get("type", "unknown")
        if et in ("md_connected", "td_connected"):
            logger.info(f"[AutoTrade] 事件: {et} ok={msg.get('ok')}")

    def _on_bar_complete(self, bar):
        """1m Bar完成 → 保存 + 触发训练/信号"""
        try:
            self.store.save_bars([bar], period="1m", source="ctp")
            self._bar_count += 1

            # 检查是否需要训练
            if self._bar_count - self._last_train_bar >= self.train_interval:
                asyncio.create_task(self._incremental_train())

            # 检查是否需要生成信号
            if self._bar_count - self._last_signal_bar >= self.signal_interval:
                asyncio.create_task(self._generate_and_execute())

        except Exception as e:
            logger.warning(f"[AutoTrade] bar处理失败: {e}")

    # ── 初始训练 ──────────────────────────────────────────

    async def _load_history_and_train(self):
        """从历史数据训练初始模型"""
        logger.info("[AutoTrade] 加载历史数据训练初始模型...")
        try:
            bars = await self.gateway.get_bars("AU0", period="d", limit=self.max_history_bars)
            if not bars or len(bars) < 60:
                logger.warning(f"[AutoTrade] 历史数据不足 ({len(bars or [])}条), 跳过初始训练")
                return

            # 在后台线程训练
            def _train():
                history = self._trainer.train(bars, n_iterations=30, n_steps=1024)
                # 保存模型
                model_path = os.path.join(self._trainer.model_path or "", "ppo_auto_trade_init.pt")
                self._trainer.agent.save(model_path, {"type": "auto_trade_init", "bars": len(bars)})
                # 注册到ModelRegistry
                metrics = history.get("iterations", [{}])[-1] if history.get("iterations") else {}
                self.registry.save(self._trainer.agent, "ppo_auto_trade",
                                   {"sharpe": metrics.get("sharpe", 0), "return": metrics.get("avg_return_pct", 0)})
                return history

            history = await asyncio.to_thread(_train)
            self._rl_ready = True
            metrics = history.get("iterations", [{}])[-1] if history.get("iterations") else {}
            logger.info(f"[AutoTrade] 初始训练完成: sharpe={metrics.get('sharpe', '?')} return={metrics.get('avg_return_pct', '?')}%")
        except Exception as e:
            logger.error(f"[AutoTrade] 初始训练失败: {e}")

    # ── 增量训练 ──────────────────────────────────────────

    async def _incremental_train(self):
        """增量训练：用CTP实时Bar数据微调模型"""
        if not self._rl_ready:
            logger.warning("[AutoTrade] RL未就绪，跳过增量训练")
            return

        self._last_train_bar = self._bar_count
        logger.info(f"[AutoTrade] 增量训练开始 (bar={self._bar_count})")

        try:
            # 读取最近的历史数据 + CTP实时Bar
            hist_bars = await self.gateway.get_bars("AU0", period="d", limit=self.max_history_bars)

            def _train():
                # 增量训练：用少量iterations微调
                history = self._trainer.train(hist_bars, n_iterations=self.rl_iterations, n_steps=self.rl_steps)
                # 保存
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                model_path = os.path.join(self._trainer.model_path or "", f"ppo_auto_trade_{ts}.pt")
                self._trainer.agent.save(model_path, {"type": "incremental", "bar_count": self._bar_count})
                return history

            history = await asyncio.to_thread(_train)
            metrics = history.get("iterations", [{}])[-1] if history.get("iterations") else {}
            logger.info(f"[AutoTrade] 增量训练完成: sharpe={metrics.get('sharpe', '?')} return={metrics.get('avg_return_pct', '?')}%")
        except Exception as e:
            logger.error(f"[AutoTrade] 增量训练失败: {e}")

    # ── 信号生成 + 自动执行 ──────────────────────────────

    async def _generate_and_execute(self):
        """生成RL信号 → 风控 → 自动执行"""
        if not self._rl_ready:
            return

        self._last_signal_bar = self._bar_count
        logger.info(f"[AutoTrade] 生成信号 (bar={self._bar_count})")

        try:
            # 获取最新行情
            bars = await self.gateway.get_bars("AU0", period="d", limit=200)
            if not bars or len(bars) < 30:
                return

            current_price = bars[-1].close
            atr = self._calc_atr(bars)

            # 生成信号（在后台线程）
            def _signal():
                return self._trainer.generate_signal(bars)

            result = await asyncio.to_thread(_signal)
            signal_data = result.get("signal", {})
            direction = signal_data.get("direction", "hold")

            if direction == "hold":
                logger.debug(f"[AutoTrade] 信号: 观望 (conf={signal_data.get('confidence', 0):.2%})")
                return

            # 创建GoldSignal
            signal = GoldSignal(
                signal_id=f"auto_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                strategy_id="rl_ppo", strategy_name="RL PPO Auto",
                symbol="AU0",
                direction=SignalDirection(direction),
                price=round(current_price, 2),
                volume=1,
                stop_loss=round(signal_data.get("stop_loss", current_price * 0.97), 2) if direction == "long" else round(signal_data.get("stop_loss", current_price * 1.03), 2),
                confidence=signal_data.get("confidence", 0.5),
                reason=signal_data.get("reason", "RL自动交易信号"),
                created_at=datetime.now(),
            )

            # 风控检查
            positions = await self._query_positions()
            account = await self._query_account()
            risk_result = self._risk_checker.check(
                signal, positions=positions, account=account,
                atr_value=atr, current_price=current_price,
            )

            if not risk_result.passed:
                logger.warning(f"[AutoTrade] 风控拒绝: {risk_result.reason}")
                return

            # 执行到CTP
            await self._execute(signal, current_price)
            logger.info(f"[AutoTrade] 信号已执行: {direction} {current_price} conf={signal_data.get('confidence', 0):.2%}")

        except Exception as e:
            logger.error(f"[AutoTrade] 信号生成/执行失败: {e}")

    async def _execute(self, signal: GoldSignal, market_price: float):
        """执行信号到CTP"""
        try:
            om = OrderManager(self.store)
            sim = InternalSimAccount()
            executor = LiveExecutor(self.adapter, om, sim)
            result = executor.execute(signal, market_price=market_price)
            if result["executed"]:
                self._risk_checker.record_signal(signal)
                logger.info(f"[AutoTrade] 执行成功: ref={result.get('ctp_ref')}")
            else:
                logger.warning(f"[AutoTrade] 执行失败: {result.get('reason')}")
        except Exception as e:
            logger.error(f"[AutoTrade] 执行异常: {e}")

    async def _query_positions(self) -> list:
        if self.adapter:
            try:
                return await self.adapter.query_positions()
            except Exception:
                return []
        return []

    async def _query_account(self) -> dict:
        if self.adapter:
            try:
                return await self.adapter.query_account()
            except Exception:
                return {}
        return {}

    # ── 工具 ──────────────────────────────────────────────

    def _calc_atr(self, bars: list, period: int = 14) -> float:
        if len(bars) < period + 1:
            return 0
        trs = []
        for i in range(1, len(bars)):
            b, p = bars[i], bars[i - 1]
            tr = max(b.high - b.low, abs(b.high - p.close), abs(b.low - p.close))
            trs.append(tr)
        return sum(trs[-period:]) / period

    def _print_stats(self):
        elapsed = time.time() - (self._start_time or time.time())
        logger.info(
            f"[AutoTrade] 运行 {elapsed:.0f}s | bar={self._bar_count} | "
            f"rl={'就绪' if self._rl_ready else '未就绪'} | "
            f"训练间隔={self._bar_count - self._last_train_bar}/{self.train_interval} | "
            f"信号间隔={self._bar_count - self._last_signal_bar}/{self.signal_interval}"
        )


async def main():
    parser = argparse.ArgumentParser(description="自动交易引擎")
    parser.add_argument("--mode", choices=["simnow", "openctp"], default="simnow")
    parser.add_argument("--train-interval", type=int, default=100, help="增量训练间隔(bar数)")
    parser.add_argument("--signal-interval", type=int, default=20, help="信号生成间隔(bar数)")
    parser.add_argument("--rl-iterations", type=int, default=10, help="每次增量训练迭代次数")
    parser.add_argument("--rl-steps", type=int, default=512, help="每次增量训练步数")
    args = parser.parse_args()

    stop_event = asyncio.Event()
    def _handler():
        stop_event.set()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handler)
        except NotImplementedError:
            pass

    engine = AutoTradeEngine(
        mode=args.mode,
        train_interval_bars=args.train_interval,
        signal_interval_bars=args.signal_interval,
        rl_iterations=args.rl_iterations,
        rl_steps=args.rl_steps,
    )
    task = asyncio.create_task(engine.start())
    await stop_event.wait()
    engine._running = False
    await task


if __name__ == "__main__":
    asyncio.run(main())