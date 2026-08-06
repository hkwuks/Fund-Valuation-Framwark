"""
CTP 行情数据管道 — Tick → Bar → SQLite 持久化

启动:
    python -m backend.gold.data.run_ctp_pipeline [--mode simnow|openctp]

停止: Ctrl+C 或另一个终端运行 touch /tmp/ctp_pipeline_stop
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

# 尽早配 locale（openctp 需要 GB18030，不报错即可）
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

from loguru import logger

from backend.gold.data.storage import GoldDataStore
from backend.gold.trading.connectors import create_adapter
from backend.gold.trading.connectors.bar_assembler import BarAssembler

_STOP_FILE = "/tmp/ctp_pipeline_stop"


class CtpDataPipeline:
    """CTP 行情数据管道 — Tick → 1m Bar → SQLite"""

    def __init__(self, mode: str = "simnow"):
        self.mode = mode
        self.adapter = None
        self.store = GoldDataStore()
        self._running = False
        self._bar_count = 0
        self._tick_count = 0
        self._start_time: Optional[float] = None

        # Bar 合成器：1m Bar 完成后回调
        self._bar_assembler = BarAssembler(on_bar=self._on_bar_complete)

        # 移除停服标记
        if os.path.exists(_STOP_FILE):
            os.remove(_STOP_FILE)

    async def start(self):
        """启动管道"""
        logger.info(f"[CTP Pipeline] 启动 (mode={self.mode})")
        self._running = True
        self._start_time = time.time()

        self.adapter = create_adapter(self.mode)
        self.adapter.on_tick_callback = self._on_tick
        self.adapter.event_callback = self._on_event

        await self.adapter.start()
        logger.info(f"[CTP Pipeline] 已连接，等待行情...")

        # 每分钟打印一次统计
        while self._running:
            await asyncio.sleep(60)
            self._print_stats()

            # 检查停服标记
            if os.path.exists(_STOP_FILE):
                logger.info("[CTP Pipeline] 检测到停服标记")
                break

        await self.stop()

    async def stop(self):
        """停止管道"""
        self._running = False
        if self.adapter:
            await self.adapter.stop()
        elapsed = time.time() - (self._start_time or time.time())
        logger.info(f"[CTP Pipeline] 已停止 (运行 {elapsed:.0f}s, tick={self._tick_count}, bar={self._bar_count})")

    # ── 回调 ──────────────────────────────────────────────

    def _on_tick(self, tick):
        """收到 Tick → 送入 BarAssembler"""
        self._tick_count += 1
        self._bar_assembler.update_tick(tick)

    def _on_event(self, msg: dict):
        """事件通知"""
        et = msg.get("type", "unknown")
        if et in ("md_connected", "td_connected"):
            logger.info(f"[CTP Pipeline] 事件: {et} ok={msg.get('ok')}")
        elif et in ("order_status", "trade", "order_rejected"):
            pass  # 交易事件由 LiveExecutor 处理
        else:
            logger.debug(f"[CTP Pipeline] 事件: {msg}")

    def _on_bar_complete(self, bar):
        """1m Bar 完成 → 写入 SQLite"""
        try:
            self.store.save_bars([bar], period="1m", source="ctp")
            self._bar_count += 1
        except Exception as e:
            logger.warning(f"[CTP Pipeline] 保存 bar 失败: {e}")

    def _print_stats(self):
        """打印运行统计"""
        elapsed = time.time() - (self._start_time or time.time())
        rate = self._tick_count / elapsed if elapsed > 0 else 0
        logger.info(
            f"[CTP Pipeline] 运行 {elapsed:.0f}s | "
            f"tick={self._tick_count} ({rate:.1f}/s) | "
            f"bar={self._bar_count}"
        )


async def main():
    parser = argparse.ArgumentParser(description="CTP 行情数据管道")
    parser.add_argument("--mode", choices=["simnow", "openctp"], default="simnow",
                        help="交易模式 (默认 simnow)")
    args = parser.parse_args()

    # 设置信号处理
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("[CTP Pipeline] 收到退出信号")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass

    pipeline = CtpDataPipeline(mode=args.mode)
    # 并行运行管道和 stop_event 等待
    pipeline_task = asyncio.create_task(pipeline.start())
    await stop_event.wait()
    pipeline._running = False
    await pipeline_task


if __name__ == "__main__":
    asyncio.run(main())