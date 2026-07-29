"""
CTP 客户端 — 封装 CTP 行情/交易 API，桥接到 asyncio

线程模型:
  CTP API → 原生线程 → call_soon_threadsafe → asyncio → 系统队列

双模块支持:
  simnow  → 标准 ctp 模块 (v6.7.7)
  openctp → openctp_ctp 模块 (v6.7.11, TTS 兼容)
"""
import asyncio
import os
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from loguru import logger

from backend.gold.core.models import GoldTickData, SignalDirection, OrderStatus
from backend.gold.trading.connectors.ctp_config import CtpConfig


# ── 交易方向/开平映射（CTP → 系统） ────────────────────────────

_THOST_OPT_LONG = "0"      # THOST_FTDC_OPT_Long
_THOST_OPT_SHORT = "1"     # THOST_FTDC_OPT_Short
_THOST_F_OPEN = "0"        # THOST_FTDC_F_Open
_THOST_F_CLOSE = "1"       # THOST_FTDC_F_Close
_THOST_F_CLOSETODAY = "2"  # THOST_FTDC_F_CloseToday

# 订单状态映射
_ORDER_STATUS_MAP = {
    "0": "all_traded",     # 全部成交
    "1": "part_traded",    # 部分成交
    "2": "not_traded",     # 未成交
    "3": "canceled",       # 已撤销
    "4": "part_canceled",  # 部分撤销
    "5": "unknown",        # 未知
    "a": "not_traded",     # 未成交
    "b": "trading",        # 正在申报
}


# ── 模块选择 ──────────────────────────────────────────────────

def _setup_locale():
    """openctp_ctp/openctp_tts 的 C++ DSO 需要 zh_CN.GB18030

    尝试多种中文 locale，都不存在时保留当前 locale，
    避免 C++ 层 locale::facet 崩溃。
    """
    import locale
    candidates = ['zh_CN.GB18030', 'zh_CN.UTF-8', 'zh_CN']
    # 尝试从 LOCPATH 或临时目录加载自定义 locale
    for locpath in [os.environ.get('LOCPATH', ''), '/tmp/locale']:
        if locpath and os.path.isdir(locpath):
            for name in candidates:
                full = os.path.join(locpath, name)
                if os.path.isdir(full) or os.path.isfile(full + '/LC_CTYPE'):
                    os.environ['LOCPATH'] = locpath
                    break
    for name in candidates:
        try:
            locale.setlocale(locale.LC_ALL, name)
            os.environ['LANG'] = name
            os.environ['LC_ALL'] = name
            return
        except locale.Error:
            continue
    logger.warning("无中文 locale 可用，CTP 中文消息可能显示异常")


def _get_ctp_modules(mode: str):
    """
    根据 trading_mode 返回对应的 CTP 模块元组 (md_module, td_module)

    simnow → 标准 ctp 模块 (v6.7.7)
    openctp → openctp_tts 模块 (v6.7.2, TTS 协议)
    """
    if mode == "openctp":
        _setup_locale()
        import openctp_tts
        logger.info("[CTP] 使用 openctp_tts 模块 (TTS 协议)")
        return openctp_tts.mdapi, openctp_tts.tdapi
    else:
        import ctp as m
        return m, m


# ── 客户端类 ──────────────────────────────────────────────────

class CtpClient:
    """
    CTP 客户端 — 行情订阅 + 交易下单

    使用方式:
        client = CtpClient(config, loop)
        await client.start()
        # ... 使用 ...
        await client.stop()
    """

    def __init__(self, config: CtpConfig, loop: asyncio.AbstractEventLoop = None):
        self._cfg = config
        self._loop = loop or asyncio.get_event_loop()

        # 按模式选择 CTP 模块（标准 ctp vs openctp_tts）
        self._md_module, self._td_module = _get_ctp_modules(config.mode)

        # 动态创建 SPI 类（基类因模块而异）
        self._MdSpiCls = self._make_md_spi_class()
        self._TdSpiCls = self._make_td_spi_class()

        # CTP API 实例
        self._md_api = None
        self._td_api = None

        # SPI 实例（必须保持引用，防止 GC）
        self._md_spi = None
        self._td_spi = None

        # 状态
        self._running = False
        self._md_connected = False
        self._td_connected = False
        self._md_logged_in = False
        self._td_logged_in = False
        self._front_id = 0
        self._session_id = 0

        # 异步队列
        self.tick_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.event_callback: Optional[Callable] = None

        # 查询同步（CTP 查询是异步回调，用 Event 同步）
        self._position_result: list = []
        self._position_done: threading.Event = threading.Event()
        self._account_result: dict = {}
        self._account_done: threading.Event = threading.Event()
        self._order_result: list = []
        self._order_done: threading.Event = threading.Event()

        # 行情回调挂钩（给 BarAssembler 用）
        self.on_tick_callback: Optional[Callable] = None

        # 主力合约检测
        self._open_interest_map: dict[str, float] = {}
        self._main_contract: str = ""

    def _make_md_spi_class(self):
        """创建行情 SPI 类，基类取自 self._md_module"""
        client = self  # captured for callbacks

        class CtpMdSpi(client._md_module.CThostFtdcMdSpi):
            def __init__(self):
                super().__init__()

            def OnFrontConnected(self):
                logger.info("[CTP Md] 行情前置连接成功")
                client._md_connected = True
                client._notify({"type": "md_connected", "ok": True})
                field = client._md_module.CThostFtdcReqUserLoginField()
                field.BrokerID = client._cfg.broker_id
                field.UserID = client._cfg.user_id
                field.Password = client._cfg.password
                client._md_api.ReqUserLogin(field, 1)

            def OnFrontDisconnected(self, nReason):
                logger.warning(f"[CTP Md] 行情前置断开 reason={nReason}")
                client._md_connected = False
                client._notify({"type": "md_connected", "ok": False})

            def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
                if pRspInfo.ErrorID == 0:
                    logger.info(f"[CTP Md] 行情登录成功")
                    client._md_logged_in = True
                    client._subscribe_symbols()
                else:
                    logger.error(f"[CTP Md] 行情登录失败: {pRspInfo.ErrorID} {pRspInfo.ErrorMsg}")

            def OnRtnDepthMarketData(self, pData):
                tick = GoldTickData(
                    symbol=pData.InstrumentID,
                    exchange="SHFE",
                    datetime=datetime.now(),
                    last_price=pData.LastPrice if pData.LastPrice < 1e8 else 0,
                    last_volume=pData.Volume,
                    open_interest=pData.OpenInterest,
                )
                client._on_tick(tick)

        return CtpMdSpi

    def _make_td_spi_class(self):
        """创建交易 SPI 类，基类取自 self._td_module"""
        client = self

        class CtpTraderSpi(client._td_module.CThostFtdcTraderSpi):
            def __init__(self):
                super().__init__()
                self._authenticated = False

            def OnFrontConnected(self):
                logger.info("[CTP Trader] 交易前置连接成功")
                client._td_connected = True
                client._notify({"type": "td_connected", "ok": True})
                if client._cfg.needs_auth:
                    self._authenticate()
                else:
                    logger.info("[CTP Trader] 无需认证，直接登录")
                    self._login()

            def OnFrontDisconnected(self, nReason):
                logger.warning(f"[CTP Trader] 交易前置断开 reason={nReason}")
                client._td_connected = False
                client._notify({"type": "td_connected", "ok": False})

            def OnRspAuthenticate(self, pRspAuthenticate, pRspInfo, nRequestID, bIsLast):
                if pRspInfo.ErrorID == 0:
                    logger.info("[CTP Trader] 认证成功")
                    self._authenticated = True
                    self._login()
                else:
                    logger.warning(f"[CTP Trader] 认证失败 ({pRspInfo.ErrorID}:{pRspInfo.ErrorMsg}), 不登录")

            def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
                if pRspInfo.ErrorID == 0:
                    logger.info(f"[CTP Trader] 交易登录成功 front_id={pRspUserLogin.FrontID} session_id={pRspUserLogin.SessionID}")
                    client._td_logged_in = True
                    client._front_id = pRspUserLogin.FrontID
                    client._session_id = pRspUserLogin.SessionID
                else:
                    logger.error(f"[CTP Trader] 交易登录失败: {pRspInfo.ErrorID} {pRspInfo.ErrorMsg}")

            def OnRspOrderInsert(self, pInputOrder, pRspInfo, nRequestID, bIsLast):
                ref = pInputOrder.RequestID
                if pRspInfo.ErrorID != 0:
                    logger.warning(f"[CTP] 下单失败 ref={ref}: {pRspInfo.ErrorMsg}")
                    client._notify({"type": "order_rejected", "ref": ref, "error": pRspInfo.ErrorMsg})
                else:
                    logger.info(f"[CTP] 下单成功 ref={ref}")

            def OnRtnOrder(self, pOrder):
                ref = pOrder.OrderRef
                status = _ORDER_STATUS_MAP.get(pOrder.OrderStatus, "unknown")
                logger.info(f"[CTP] 订单状态 ref={ref} status={status} traded={pOrder.VolumeTraded}")
                client._notify({
                    "type": "order_status",
                    "ref": ref,
                    "status": status,
                    "traded_volume": pOrder.VolumeTraded,
                    "price": pOrder.LimitPrice,
                })

            def OnRtnTrade(self, pTrade):
                ref = pTrade.OrderRef
                logger.info(f"[CTP] 成交 ref={ref} {pTrade.InstrumentID} {pTrade.Direction} {pTrade.Volume}手 @{pTrade.Price}")
                client._notify({
                    "type": "trade",
                    "ref": ref,
                    "symbol": pTrade.InstrumentID,
                    "direction": "long" if pTrade.Direction == _THOST_OPT_LONG else "short",
                    "volume": pTrade.Volume,
                    "price": pTrade.Price,
                    "trade_id": pTrade.TradeID,
                })

            def OnRspQryInvestorPosition(self, pPosition, pRspInfo, nRequestID, bIsLast):
                if pPosition:
                    client._position_result.append({
                        "symbol": pPosition.InstrumentID,
                        "direction": "long" if pPosition.PosiDirection == _THOST_OPT_LONG else "short",
                        "volume": pPosition.Position,
                        "avg_price": pPosition.OpenCost / pPosition.Position if pPosition.Position > 0 else 0,
                        "pnl": pPosition.PositionProfit,
                        "margin": pPosition.UseMargin,
                        "yd_volume": pPosition.YdPosition,
                    })
                if bIsLast:
                    client._position_done.set()

            def OnRspQryTradingAccount(self, pAccount, pRspInfo, nRequestID, bIsLast):
                if pAccount:
                    client._account_result = {
                        "balance": pAccount.Balance,
                        "available": pAccount.Available,
                        "margin": pAccount.CurrMargin,
                        "pnl": pAccount.PositionProfit,
                        "close_pnl": pAccount.CloseProfit,
                        "frozen_margin": pAccount.FrozenMargin,
                        "frozen_commission": pAccount.FrozenCommission,
                    }
                if bIsLast:
                    client._account_done.set()

            def OnRspQryOrder(self, pOrder, pRspInfo, nRequestID, bIsLast):
                if pOrder:
                    client._order_result.append({
                        "ref": pOrder.OrderRef,
                        "symbol": pOrder.InstrumentID,
                        "direction": "buy" if pOrder.CombOffsetFlag[0] == _THOST_F_OPEN and pOrder.Direction == _THOST_OPT_LONG else "sell",
                        "price": pOrder.LimitPrice,
                        "volume": pOrder.VolumeTotalOriginal,
                        "traded": pOrder.VolumeTraded,
                        "status": _ORDER_STATUS_MAP.get(pOrder.OrderStatus, "unknown"),
                        "insert_time": f"{pOrder.InsertDate} {pOrder.InsertTime}",
                    })
                if bIsLast:
                    client._order_done.set()

            def _authenticate(self):
                try:
                    field = client._td_module.CThostFtdcReqAuthenticateField()
                    field.BrokerID = client._cfg.broker_id
                    field.UserID = client._cfg.user_id
                    field.AppID = client._cfg.app_id
                    field.AuthCode = client._cfg.auth_code
                    client._td_api.ReqAuthenticate(field, 1)
                except Exception as e:
                    logger.warning(f"[CTP Trader] 认证失败 ({e}), 不登录")

            def _login(self):
                field = client._td_module.CThostFtdcReqUserLoginField()
                field.BrokerID = client._cfg.broker_id
                field.UserID = client._cfg.user_id
                field.Password = client._cfg.password
                client._td_api.ReqUserLogin(field, 2)

        return CtpTraderSpi

    # ── 主力合约检测 ──────────────────────────────────────────

    def get_main_contract(self) -> str:
        if not self._main_contract and self._open_interest_map:
            self._main_contract = max(self._open_interest_map, key=self._open_interest_map.get)
        return self._main_contract

    def _update_open_interest(self, symbol: str, oi: float):
        if oi > 0:
            old_main = self._main_contract
            self._open_interest_map[symbol] = max(self._open_interest_map.get(symbol, 0), oi)
            new_main = max(self._open_interest_map, key=self._open_interest_map.get)
            if new_main != old_main and old_main:
                logger.info(f"[CTP] 主力合约切换: {old_main} → {new_main} (OI: {self._open_interest_map})")
            self._main_contract = new_main

    # ── 生命周期 ──────────────────────────────────────────────

    async def start(self):
        if self._running:
            logger.warning("[CTP] 已在运行")
            return

        valid, msg = self._cfg.is_valid()
        if not valid:
            logger.warning(f"[CTP] {msg}")
            return

        self._running = True
        self._position_result = []
        self._account_result = {}
        self._order_result = []

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._init_sync)

        self._start_monitor()
        logger.info("[CTP] 初始化完成（后台线程连接中）")

    def _init_sync(self):
        """CTP API 同步初始化（在线程池中运行）"""
        from pathlib import Path
        flow_dir = str(Path(__file__).parent.parent.parent.parent.parent / "data" / "backend" / "gold" / "ctp_flow")
        os.makedirs(flow_dir, exist_ok=True)

        self._md_api = self._md_module.CThostFtdcMdApi.CreateFtdcMdApi(
            os.path.join(flow_dir, "md").encode("utf-8").decode("utf-8")
        )
        self._md_spi = self._MdSpiCls()
        self._md_api.RegisterSpi(self._md_spi)
        self._md_api.RegisterFront(self._cfg.md_address)
        self._md_api.Init()

        self._td_api = self._td_module.CThostFtdcTraderApi.CreateFtdcTraderApi(
            os.path.join(flow_dir, "td").encode("utf-8").decode("utf-8")
        )
        self._td_spi = self._TdSpiCls()
        self._td_api.RegisterSpi(self._td_spi)
        self._td_api.RegisterFront(self._cfg.td_address)
        self._td_api.SubscribePrivateTopic(self._td_module.THOST_TERT_QUICK)  # type: ignore
        self._td_api.SubscribePublicTopic(self._td_module.THOST_TERT_QUICK)   # type: ignore
        self._td_api.Init()

    async def stop(self):
        """关闭 CTP 连接"""
        self._running = False
        # ponytail: openctp_tts 的 Release() 会 segfault，只置空引用
        if self._cfg.mode == "openctp":
            self._md_api = None
            self._td_api = None
        else:
            if self._md_api:
                self._md_api.Release()
                self._md_api = None
            if self._td_api:
                self._td_api.Release()
                self._td_api = None
        self._md_connected = False
        self._td_connected = False
        self._md_logged_in = False
        self._td_logged_in = False
        logger.info("[CTP] 已关闭")

    # ── 下单 ───────────────────────────────────────────────────

    def send_order(self, symbol: str, direction: SignalDirection,
                   price: float, volume: int,
                   order_ref: int = 0) -> int:
        if not self._td_logged_in:
            logger.error("[CTP] 未登录，无法下单")
            return -1

        ref = order_ref or int(time.time() * 1000) % 1000000

        field = self._td_module.CThostFtdcInputOrderField()
        field.BrokerID = self._cfg.broker_id
        field.InvestorID = self._cfg.user_id
        field.InstrumentID = symbol
        field.LimitPrice = price
        field.VolumeTotalOriginal = volume
        field.OrderRef = str(ref)
        field.UserID = self._cfg.user_id

        if direction in (SignalDirection.LONG, SignalDirection.SHORT):
            field.CombOffsetFlag = _THOST_F_OPEN
        else:
            field.CombOffsetFlag = _THOST_F_CLOSE

        if direction in (SignalDirection.LONG, SignalDirection.CLOSE_SHORT):
            field.Direction = _THOST_OPT_LONG
        else:
            field.Direction = _THOST_OPT_SHORT

        field.OrderPriceType = "2"  # 限价
        field.CombHedgeFlag = "1"  # 投机
        field.ContingentCondition = "1"  # 立即
        field.ForceCloseReason = "0"
        field.IsAutoSuspend = 0
        field.TimeCondition = "3"  # 当日有效
        field.VolumeCondition = "1"
        field.MinVolume = 1

        result = self._td_api.ReqOrderInsert(field, ref)
        if result == 0:
            logger.info(f"[CTP] 下单成功: {symbol} {direction.value} {volume}手 @{price} ref={ref}")
        else:
            logger.error(f"[CTP] 下单失败: result={result}")

        return ref

    def cancel_order(self, symbol: str, order_ref: int,
                     front_id: int = 0, session_id: int = 0) -> int:
        field = self._td_module.CThostFtdcInputOrderActionField()
        field.BrokerID = self._cfg.broker_id
        field.InvestorID = self._cfg.user_id
        field.InstrumentID = symbol
        field.OrderRef = str(order_ref)
        field.FrontID = front_id or self._front_id
        field.SessionID = session_id or self._session_id
        field.ActionFlag = "0"  # 删除
        return self._td_api.ReqOrderAction(field, 1)

    # ── 查询 ───────────────────────────────────────────────────

    async def query_positions(self, symbol: str = "") -> list[dict]:
        if not self._td_logged_in:
            return []
        self._position_result = []
        self._position_done.clear()
        field = self._td_module.CThostFtdcQryInvestorPositionField()
        field.BrokerID = self._cfg.broker_id
        field.InvestorID = self._cfg.user_id
        if symbol:
            field.InstrumentID = symbol
        self._td_api.ReqQryInvestorPosition(field, 1)
        done = await self._loop.run_in_executor(None, lambda: self._position_done.wait(5.0))
        if not done:
            logger.warning("[CTP] 查询持仓超时")
        return self._position_result

    async def query_account(self) -> dict:
        if not self._td_logged_in:
            return {}
        self._account_result = {}
        self._account_done.clear()
        field = self._td_module.CThostFtdcQryTradingAccountField()
        field.BrokerID = self._cfg.broker_id
        field.InvestorID = self._cfg.user_id
        self._td_api.ReqQryTradingAccount(field, 1)
        done = await self._loop.run_in_executor(None, lambda: self._account_done.wait(5.0))
        if not done:
            logger.warning("[CTP] 查询资金超时")
        return self._account_result

    async def query_orders(self, symbol: str = "") -> list[dict]:
        if not self._td_logged_in:
            return []
        self._order_result = []
        self._order_done.clear()
        field = self._td_module.CThostFtdcQryOrderField()
        field.BrokerID = self._cfg.broker_id
        field.InvestorID = self._cfg.user_id
        if symbol:
            field.InstrumentID = symbol
        self._td_api.ReqQryOrder(field, 1)
        done = await self._loop.run_in_executor(None, lambda: self._order_done.wait(5.0))
        if not done:
            logger.warning("[CTP] 查询委托超时")
        return self._order_result

    # ── 状态 ───────────────────────────────────────────────────

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "md_connected": self._md_connected,
            "td_connected": self._td_connected,
            "md_logged_in": self._md_logged_in,
            "td_logged_in": self._td_logged_in,
            "symbols": self._cfg.symbols,
            "main_contract": self.get_main_contract(),
            "open_interest": dict(self._open_interest_map),
        }

    # ── 内部 ───────────────────────────────────────────────────

    def _on_tick(self, tick: GoldTickData):
        self._update_open_interest(tick.symbol, tick.open_interest)
        try:
            self.tick_queue.put_nowait(tick)
        except asyncio.QueueFull:
            pass
        if self.on_tick_callback:
            try:
                self.on_tick_callback(tick)
            except Exception as e:
                logger.debug(f"[CTP] tick callback error: {e}")

    def _notify(self, msg: dict):
        if self.event_callback:
            self._loop.call_soon_threadsafe(self.event_callback, msg)
        else:
            logger.debug(f"[CTP] event (no callback): {msg.get('type')}")

    def _subscribe_symbols(self):
        ret = self._md_api.SubscribeMarketData(self._cfg.symbols)
        logger.info(f"[CTP] 订阅行情: {self._cfg.symbols} ret={ret}")

    def _start_monitor(self):
        def _monitor():
            while self._running:
                time.sleep(30)
                if not self._md_connected and self._running:
                    logger.warning("[CTP] 行情断开，等待自动重连...")
                if not self._td_connected and self._running:
                    logger.warning("[CTP] 交易断开，等待自动重连...")
        t = threading.Thread(target=_monitor, daemon=True, name="ctp-monitor")
        t.start()
