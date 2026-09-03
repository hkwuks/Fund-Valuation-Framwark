"""FundQuant 事件驱动回测引擎 — ⚠️ 已废弃，仅保留供旧测试引用

历史回测全部走统一引擎 `core.backtest.BacktestEngine`（见 backend/api/fund_quant.py
`_run_backtest_sync` / `_legacy_backtest_metrics`）。本文件是迁移前的事件驱动实现，
保留 T+1 申赎模拟 + 前视偏差防护的参考语义；不再被任何生产路径调用。
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple, Any
from loguru import logger
import numpy as np

from ..core.models import (
    BacktestConfig, BacktestResult, Portfolio,
    FundSignal, CostModelConfig, NavPoint, InformationSet,
)
from ..core.enums import Direction, FundType
from .cost_model import FundCostModel
from .redemption_gate import RedemptionGate
from .liquidation import LiquidationHandler
from .disclosure import DisclosureCalendar


class SimPosition:
    """模拟持仓（支持多空）"""
    def __init__(self, fund_code: str, shares: float, buy_date: date, buy_nav: float,
                 direction: str = "long"):
        self.fund_code = fund_code
        self.shares = shares
        self.buy_date = buy_date
        self.buy_nav = buy_nav
        self.cost = shares * buy_nav
        self.direction = direction  # "long" 或 "short"

    def current_value(self, nav: float) -> float:
        """持仓市值（空头为负值，表示负债）"""
        if self.direction == "short":
            return -self.shares * nav
        return self.shares * nav

    def holding_days(self, current_date: date) -> int:
        return (current_date - self.buy_date).days

    def pnl(self, nav: float) -> float:
        """未实现盈亏（多头=nav-buy_nav, 空头=buy_nav-nav）"""
        if self.direction == "short":
            return self.shares * (self.buy_nav - nav)
        return self.shares * (nav - self.buy_nav)


class PendingOrder:
    """待确认申赎订单"""
    def __init__(self, fund_code: str, order_type: str, shares: float,
                 submit_date: date, confirmation_delay: int = 1,
                 target_fund_code: str = None,
                 source_fund_type: str = None,
                 target_fund_type: str = None):
        self.fund_code = fund_code
        self.order_type = order_type  # buy / sell / conversion
        self.shares = shares
        self.submit_date = submit_date
        self.confirmation_date = None
        self.confirmation_delay = confirmation_delay  # T+1 默认, QDII T+2
        self.target_fund_code = target_fund_code
        self.source_fund_type = source_fund_type
        self.target_fund_type = target_fund_type

    def is_ready(self, current_date: date) -> bool:
        if self.confirmation_date is None:
            return current_date > self.submit_date  # T+1 确认
        return current_date >= self.confirmation_date

    def confirm(self, current_date: date):
        self.confirmation_date = current_date


class FundBacktester:
    """基金回测引擎 — 事件驱动, T+1确认, 前视偏差防护, 策略集成, 风控管线

    继承旧引擎 (core.BacktestEngine) 的能力:
      - 策略回调: 每个交易日调用 strategy.on_evaluate()
      - 风控管线: portfolio 级 + signal 级风险检查
      - 完整指标: volatility, sortino, information_ratio, 胜率等
    新增能力:
      - T+1 申赎确认, 巨额赎回限制, 基金转换, 分红处理, 清盘检测
    """

    def __init__(self):
        self._positions: Dict[str, SimPosition] = {}
        self._pending_orders: List[PendingOrder] = []
        self._cash: float = 0.0
        self._trade_log: List[dict] = []
        self._equity_curve: List[dict] = []
        self._config: Optional[BacktestConfig] = None
        self._nav_data: Dict[str, List[dict]] = {}
        self._cost_model = FundCostModel()
        self._redemption_gate = RedemptionGate()
        self._dividend_calendar: dict = {}
        self._liquidation = LiquidationHandler()
        self._fund_type_map: Dict[str, str] = {}
        # 旧引擎合并: 策略 + 风控
        self._strategy: Any = None
        self._risk_pipeline: Any = None

    def set_strategy(self, strategy: Any) -> None:
        """设置回测策略 (FundStrategyBase 子类实例)"""
        self._strategy = strategy

    def set_risk_pipeline(self, pipeline: Any) -> None:
        """设置风控管线 (RiskPipeline, 可选)"""
        self._risk_pipeline = pipeline

    def run(self, config: BacktestConfig,
            nav_data: Optional[Dict[str, List[dict]]] = None,
            strategy: Any = None) -> BacktestResult:
        """事件驱动回测主循环"""
        self._config = config
        self._dividend_calendar = config.dividend_calendar
        self._cash = config.initial_capital
        self._positions = {}
        self._pending_orders = []
        self._trade_log = []
        self._equity_curve = []
        self._nav_data = nav_data or {}
        if strategy:
            self._strategy = strategy

        # 申购费折扣
        self._cost_model.set_discount(config.subscription_discount)

        # 从数据库补全净值
        if not self._nav_data:
            from ..data.storage import get_nav_history
            for code in config.fund_codes:
                records = get_nav_history(code, config.start_date, config.end_date)
                if records:
                    self._nav_data[code] = records

        if not self._nav_data or not any(self._nav_data.values()):
            return BacktestResult(
                backtest_id=f"bt_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                config=config, status="failed",
            )

        # 构建交易日历
        all_dates: set = set()
        code_nav_map: Dict[str, Dict[str, dict]] = {}
        for code, records in self._nav_data.items():
            day_map = {}
            for r in records:
                d = r["date"]
                all_dates.add(d)
                day_map[d] = r
            code_nav_map[code] = day_map

        trading_days = sorted(all_dates)
        if not trading_days:
            return BacktestResult(
                backtest_id=f"bt_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                config=config, status="failed",
            )

        # 只回测 config 指定窗口（注入净值仍保留窗口前数据供策略预热算动量）
        # 之前忽略 start/end → 若调用方传入全量 nav_dict，回测会从最早日期开始
        if config.start_date:
            trading_days = [d for d in trading_days if d >= str(config.start_date)]
        if config.end_date:
            trading_days = [d for d in trading_days if d <= str(config.end_date)]
        if not trading_days:
            return BacktestResult(
                backtest_id=f"bt_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                config=config, status="failed",
            )

        # ── 数据质量检查 ──
        total_trading_days = len(trading_days)
        for code, records in self._nav_data.items():
            coverage = len(records) / max(total_trading_days, 1)
            if coverage < self._config.min_nav_records_pct:
                logger.warning(f"低数据质量: {code} 数据覆盖率 {coverage:.1%} < {self._config.min_nav_records_pct:.0%}")

        # 根据 gap_policy 填充缺口
        fill_policy = getattr(self._config, 'nav_gap_policy', 'forward_fill')
        if fill_policy == 'forward_fill':
            for code in self._nav_data:
                records = self._nav_data[code]
                filled = []
                last_nav = None
                records_by_date = {r["date"]: r for r in records}
                for d in trading_days:
                    if d in records_by_date:
                        last_nav = records_by_date[d]["nav"]
                        filled.append(records_by_date[d])
                    elif last_nav is not None:
                        filled.append({"date": d, "nav": last_nav})
                self._nav_data[code] = filled
                # 重建 code_nav_map 包含填充的缺口
                code_nav_map[code] = {r["date"]: r for r in filled}

        # 注册有效基金到清盘检测器
        self._liquidation._active_funds = set(config.fund_codes)

        # 初始化策略
        if self._strategy and hasattr(self._strategy, 'on_init'):
            try:
                self._strategy.on_init(None)  # 复用现有策略的 on_init 签名
            except Exception:
                pass

        # 交易日序号映射（用于持有天数计算）
        day_index = {d: i for i, d in enumerate(trading_days)}

        # ── 逐日推进 ──
        for idx, day_str in enumerate(trading_days):
            current_date = datetime.strptime(day_str, "%Y-%m-%d").date()

            # 前一个交易日日期（用于策略可见信息集）
            prev_date_str = trading_days[idx - 1] if idx > 0 else day_str
            prev_date = datetime.strptime(prev_date_str, "%Y-%m-%d").date()

            # ── 步骤1: 确认T-1日申赎 (T+1确认) ──
            self._confirm_orders(current_date, code_nav_map)

            # ── 步骤2: 更新持仓市值 (用T-1日净值, 策略只能看到T-1日数据) ──
            self._update_positions_value(prev_date_str, code_nav_map)

            # ── 步骤 2.5: 处理分红事件 ──
            self._process_dividends(day_str, current_date, code_nav_map)

            # ── 步骤 2.6: 检查清盘/合并 ──
            self._check_liquidations(day_str, current_date)

            # ── 步骤3: 记录权益曲线 ──
            total = self._calc_total_value(prev_date_str, code_nav_map)
            self._equity_curve.append({"date": day_str, "total_value": round(total, 2)})

            # ── 步骤4: 策略评估 + 风控 + 下单 (旧引擎 merge) ──
            if self._strategy is not None and hasattr(self._strategy, 'on_evaluate'):
                # 构建信息集
                if self._config.qdii_fund_codes:
                    prev_idx = day_index.get(prev_date_str, 0)
                    qdii_prev_date_str = trading_days[prev_idx - 1] if prev_idx > 0 else prev_date_str
                    qdii_prev_date = datetime.strptime(qdii_prev_date_str, "%Y-%m-%d").date()
                else:
                    qdii_prev_date = None

                if not hasattr(self, '_disclosure_calendar'):
                    self._disclosure_calendar = DisclosureCalendar()
                holdings_date = self._disclosure_calendar.get_available_as_of(current_date)

                info_set = InformationSet(
                    nav_available_up_to=prev_date,
                    qdii_nav_available_up_to=qdii_prev_date,
                    intraday_quotes_available=prev_date,
                    holdings_disclosed_up_to=holdings_date,
                    holdings_effective_date=holdings_date,
                )

                # Portfolio 级风控
                risk_blocked = False
                if self._risk_pipeline is not None:
                    try:
                        risk_ctx = _RiskContext(
                            portfolio_value=total,
                            positions=list(self._positions.keys()),
                            daily_signal_count=0,
                        )
                        portfolio_results = self._risk_pipeline.run_portfolio(risk_ctx)
                        risk_blocked = any(
                            r.level == _REJECT_LEVEL for r in portfolio_results
                        )
                    except Exception as e:
                        logger.warning(f"风控异常(portfolio): {e}")

                if not risk_blocked:
                    # 构建策略上下文 — 只注入截至 prev_date 的主基金净值（防前视偏差）
                    # 原实现把全部基金/全部日期的净值拼接后注入，策略读 nav_values[-1]
                    # 会看到回测期末数据 = 前视。此处按 info_set.nav_available_up_to 截断。
                    if hasattr(self._strategy, '_state'):
                        target_code = list(self._nav_data.keys())[0] if self._nav_data else ""
                        records = self._nav_data.get(target_code, [])
                        visible = [r for r in records if r["date"] <= prev_date_str]
                        self._strategy._state["nav_values"] = [r.get("nav", 0) for r in visible]
                        self._strategy._state["nav_dates"] = [r.get("date", "") for r in visible]
                        self._strategy._state["fund_code"] = target_code

                    # 调用策略
                    try:
                        sigs = self._strategy.on_evaluate(
                            _build_portfolio_snapshot(self._positions, self._cash, total),
                            info_set,
                        )
                    except Exception as e:
                        logger.warning(f"策略评估异常: {e}")
                        sigs = []

                    if sigs:
                        # Signal 级风控
                        allowed_signals = []
                        if self._risk_pipeline is not None:
                            for sig in sigs:
                                try:
                                    core_sig = _signal_to_core(sig)
                                    s_results = self._risk_pipeline.run_signal(core_sig, risk_ctx)
                                    if any(r.level == _REJECT_LEVEL for r in s_results):
                                        logger.debug(f"风控拒绝信号: {sig.fund_code} {sig.direction}")
                                        continue
                                    allowed_signals.append(sig)
                                except Exception:
                                    allowed_signals.append(sig)
                        else:
                            allowed_signals = sigs

                        # 下单
                        for sig in allowed_signals:
                            self._place_signal_order(sig, total, prev_date_str,
                                                     code_nav_map, current_date, day_index)

        return self._generate_report()

    # ── 信号转下单 ──

    def _place_signal_order(self, sig, total: float, prev_date_str: str,
                            code_nav_map, current_date: date, day_index) -> None:
        """将 FundSignal 转为引擎订单"""
        nav_price = self._get_nav_for_date(sig.fund_code, prev_date_str, code_nav_map)
        if nav_price <= 0:
            return

        if sig.direction == Direction.BUY.value or sig.direction == Direction.BUY:
            # 确定目标金额
            target_amt = sig.suggested_amount
            if target_amt is None and sig.suggested_pct is not None:
                target_amt = sig.suggested_pct * total
            if target_amt is None:
                target_amt = total * 0.1  # 默认 10%
            if target_amt <= 0:
                return

            shares = target_amt / nav_price
            if shares > 0:
                self.submit_order(sig.fund_code, "buy", shares, current_date, 1)
                logger.debug(f"下单买入 {sig.fund_code}: {shares:.2f}份 @ {nav_price}")

        elif sig.direction == Direction.SELL.value or sig.direction == Direction.SELL:
            pos = self._positions.get(sig.fund_code)
            if pos is None:
                return
            pct = abs(sig.suggested_pct) if sig.suggested_pct is not None else None
            if pct is None:
                pct = 1.0  # 默认全卖
            shares = pos.shares * pct
            if shares > 0:
                self.submit_order(sig.fund_code, "sell", shares, current_date, 1)
                logger.debug(f"下单卖出 {sig.fund_code}: {shares:.2f}份")

        elif sig.direction == Direction.SHORT.value or sig.direction == Direction.SHORT:
            # 做空开仓（融券卖出）：无持仓也可卖
            target_amt = sig.suggested_amount
            if target_amt is None and sig.suggested_pct is not None:
                target_amt = abs(sig.suggested_pct) * total
            if target_amt is None:
                target_amt = total * 0.1
            if target_amt <= 0:
                return
            shares = target_amt / nav_price
            if shares > 0:
                self.submit_order(sig.fund_code, "short", shares, current_date, 1)
                logger.debug(f"下单做空 {sig.fund_code}: {shares:.2f}份 @ {nav_price}")

        elif sig.direction == Direction.CLOSE_SHORT.value or sig.direction == Direction.CLOSE_SHORT:
            # 做空平仓（买券还券）：减少空头仓位
            pos = self._positions.get(sig.fund_code)
            if pos is None or pos.direction != "short":
                return
            pct = abs(sig.suggested_pct) if sig.suggested_pct is not None else None
            if pct is None:
                pct = 1.0
            shares = pos.shares * pct
            if shares > 0:
                self.submit_order(sig.fund_code, "cover", shares, current_date, 1)
                logger.debug(f"下单平空 {sig.fund_code}: {shares:.2f}份 @ {nav_price}")

        elif sig.direction == Direction.REBALANCE.value or sig.direction == Direction.REBALANCE:
            # 再平衡: 由 strategy 负责给出每个基金的买卖信号
            pass

    def _get_nav_for_date(self, fund_code: str, date_str: str,
                          code_nav_map: Dict[str, Dict[str, dict]]) -> float:
        nav_data = code_nav_map.get(fund_code, {}).get(date_str)
        return nav_data.get("nav", 0) if nav_data else 0

    # ── 私有辅助方法 ──

    def _confirm_orders(self, current_date: date,
                        code_nav_map: Dict[str, Dict[str, dict]]):
        """确认T-1日的申赎订单 (T日确认, 按T-1日净值)"""
        still_pending = []
        for order in self._pending_orders:
            # 持仓查询使用T-1日净值（订单提交日）
            confirm_key = order.submit_date.isoformat()
            nav_data = None
            for cn, nm in code_nav_map.items():
                if cn == order.fund_code:
                    nav_data = nm.get(confirm_key)
                    break

            if nav_data is None:
                still_pending.append(order)
                continue

            nav_price = nav_data.get("nav", 0)
            if nav_price <= 0:
                still_pending.append(order)
                continue

            if order.order_type == "buy":
                cost = order.shares * nav_price
                if cost <= self._cash:
                    self._cash -= cost
                    # 累积持仓（合并同基金持仓）
                    existing = self._positions.get(order.fund_code)
                    if existing:
                        total_shares = existing.shares + order.shares
                        total_cost = existing.cost + cost
                        avg_nav = total_cost / total_shares if total_shares > 0 else 0
                        self._positions[order.fund_code] = SimPosition(
                            order.fund_code, total_shares, existing.buy_date, avg_nav,
                        )
                    else:
                        self._positions[order.fund_code] = SimPosition(
                            order.fund_code, order.shares, order.submit_date, nav_price,
                        )
                    self._trade_log.append({
                        "date": current_date.isoformat(),
                        "fund_code": order.fund_code,
                        "action": "buy_confirmed",
                        "shares": order.shares,
                        "price": nav_price,
                        "cost": round(cost, 2),
                        "nav_date": confirm_key,
                    })
            elif order.order_type == "short":
                # 做空开仓（融券卖出）：获得资金 + 建空头
                proceeds = order.shares * nav_price
                self._cash += proceeds
                existing = self._positions.get(order.fund_code)
                if existing and existing.direction == "short":
                    # 空头加仓（合并）：负债总额 = 原负债 + 新卖券所得
                    total_shares = existing.shares + order.shares
                    total_liability = existing.cost + proceeds
                    avg_nav = total_liability / total_shares if total_shares > 0 else 0
                    self._positions[order.fund_code] = SimPosition(
                        order.fund_code, total_shares, existing.buy_date, avg_nav, direction="short",
                    )
                else:
                    self._positions[order.fund_code] = SimPosition(
                        order.fund_code, order.shares, order.submit_date, nav_price, direction="short",
                    )
                self._trade_log.append({
                    "date": current_date.isoformat(),
                    "fund_code": order.fund_code,
                    "action": "short_confirmed",
                    "shares": order.shares,
                    "price": nav_price,
                    "proceeds": round(proceeds, 2),
                    "nav_date": confirm_key,
                })
            elif order.order_type == "cover":
                # 做空平仓（买券还券）：付出资金 + 减少空头
                cost = order.shares * nav_price
                pos = self._positions.get(order.fund_code)
                if pos is None or pos.direction != "short":
                    still_pending.append(order)
                    continue
                if cost > self._cash:
                    still_pending.append(order)  # 资金不足，延迟到有资金时平仓
                    continue
                self._cash -= cost
                remaining = pos.shares - order.shares
                if remaining <= 0:
                    del self._positions[order.fund_code]
                else:
                    sell_ratio = order.shares / pos.shares
                    pos.cost *= (1 - sell_ratio)
                    pos.shares = remaining
                self._trade_log.append({
                    "date": current_date.isoformat(),
                    "fund_code": order.fund_code,
                    "action": "cover_confirmed",
                    "shares": order.shares,
                    "price": nav_price,
                    "cost": round(cost, 2),
                    "nav_date": confirm_key,
                })
            elif order.order_type == "sell":
                # 巨额赎回限制检查
                total_shares = self._calc_fund_total_shares(order.fund_code)
                if total_shares > 0:
                    verdict = self._redemption_gate.check(order.fund_code, order.shares, total_shares)
                    if not verdict.passed:
                        logger.warning(f"巨额赎回拒绝: {order.fund_code}, {verdict.reason}")
                        if verdict.max_accepted and verdict.max_accepted > 0:
                            order.shares = verdict.max_accepted  # partial accept
                        else:
                            continue  # skip this order entirely
                proceeds = order.shares * nav_price
                self._cash += proceeds
                # 减少持仓
                pos = self._positions.get(order.fund_code)
                if pos:
                    remaining = pos.shares - order.shares
                    if remaining <= 0:
                        del self._positions[order.fund_code]
                    else:
                        # 按比例减少成本
                        sell_ratio = order.shares / pos.shares
                        pos.cost *= (1 - sell_ratio)
                        pos.shares = remaining
                self._trade_log.append({
                    "date": current_date.isoformat(),
                    "fund_code": order.fund_code,
                    "action": "sell_confirmed",
                    "shares": order.shares,
                    "price": nav_price,
                    "proceeds": round(proceeds, 2),
                    "nav_date": confirm_key,
                })
            elif order.order_type == "conversion":
                # 基金转换 / 超级转换
                pos = self._positions.get(order.fund_code)
                if pos is None:
                    still_pending.append(order)
                    continue
                if order.target_fund_code is None:
                    continue

                source_proceeds = order.shares * nav_price
                amount = source_proceeds

                cost_info = self._cost_model.calc_conversion_cost(
                    order.fund_code, order.target_fund_code,
                    order.source_fund_type or "stock",
                    order.target_fund_type or "stock",
                    amount,
                )
                conversion_fee = cost_info["conversion_fee"]
                available = source_proceeds - conversion_fee

                # 查找目标基金净值
                target_nav_data = None
                for cn, nm in code_nav_map.items():
                    if cn == order.target_fund_code:
                        target_nav_data = nm.get(confirm_key)
                        break

                if target_nav_data is None or target_nav_data.get("nav", 0) <= 0:
                    still_pending.append(order)
                    continue

                target_nav = target_nav_data["nav"]
                target_shares = available / target_nav if target_nav > 0 else 0

                # 减少源基金持仓
                remaining = pos.shares - order.shares
                if remaining <= 0:
                    del self._positions[order.fund_code]
                else:
                    sell_ratio = order.shares / pos.shares
                    pos.cost *= (1 - sell_ratio)
                    pos.shares = remaining

                # 增加目标基金持仓
                existing_target = self._positions.get(order.target_fund_code)
                if existing_target:
                    total_shares = existing_target.shares + target_shares
                    total_cost = existing_target.cost + available
                    avg_nav = total_cost / total_shares if total_shares > 0 else 0
                    self._positions[order.target_fund_code] = SimPosition(
                        order.target_fund_code, total_shares,
                        existing_target.buy_date, avg_nav,
                    )
                else:
                    self._positions[order.target_fund_code] = SimPosition(
                        order.target_fund_code, target_shares,
                        order.submit_date, target_nav,
                    )

                # 扣除现金（转换费从 proceeds 中已扣除）
                self._cash += source_proceeds - available

                self._trade_log.append({
                    "date": current_date.isoformat(),
                    "fund_code": order.fund_code,
                    "action": "conversion_sell",
                    "shares": order.shares,
                    "price": nav_price,
                    "proceeds": round(source_proceeds, 2),
                    "nav_date": confirm_key,
                })
                self._trade_log.append({
                    "date": current_date.isoformat(),
                    "fund_code": order.target_fund_code,
                    "action": "conversion_buy",
                    "shares": round(target_shares, 4),
                    "price": target_nav,
                    "cost": round(available, 2),
                    "conversion_fee": round(conversion_fee, 2),
                    "nav_date": confirm_key,
                })

        self._pending_orders = still_pending

    def _update_positions_value(self, date_str: str,
                                 code_nav_map: Dict[str, Dict[str, dict]]):
        """用指定日期净值更新持仓市值"""
        for code, pos in list(self._positions.items()):
            nav_data = None
            for cn, nm in code_nav_map.items():
                if cn == code:
                    nav_data = nm.get(date_str)
                    break
            if nav_data:
                pos.buy_nav = nav_data.get("nav", pos.buy_nav)

    def _process_dividends(self, day_str: str, current_date: date,
                           code_nav_map: Dict[str, Dict[str, dict]]):
        """处理分红事件"""
        from .dividend import dividend_handler
        if self._dividend_calendar is None:
            self._dividend_calendar = {}
        fund_divs = self._dividend_calendar.get(day_str, {})
        for fund_code, div_per_share in fund_divs.items():
            pos = self._positions.get(fund_code)
            if pos is None:
                continue
            nav_data = None
            for cn, nm in code_nav_map.items():
                if cn == fund_code:
                    nav_data = nm.get(day_str)
                    break
            if nav_data is None:
                continue
            nav = nav_data.get("nav", 0)
            if nav <= 0:
                continue
            result = dividend_handler.process_dividend(
                nav=nav, dividend_per_share=div_per_share,
                shares=pos.shares, holding_days=pos.holding_days(current_date),
            )
            if self._config.dividend_policy == "reinvest":
                pos.shares = dividend_handler.reinvest(result, pos.shares)
            else:  # cash
                self._cash += dividend_handler.cash_dividend(result)

    def _check_liquidations(self, day_str: str, current_date: date):
        """检查清盘/合并事件并处理持仓"""
        for code in list(self._positions.keys()):
            event = self._liquidation.check(code, current_date)
            if event is None:
                continue
            pos = self._positions[code]
            if event.reason == "基金清盘":
                self._cash += pos.shares * pos.buy_nav
                del self._positions[code]
                logger.warning(f"基金清盘: {code} 于 {day_str}")
            elif event.reason == "基金合并" and event.merge_target:
                new_code = event.merge_target
                ratio = event.merge_ratio or 1.0
                new_shares = pos.shares * ratio
                self._positions[new_code] = SimPosition(
                    new_code, new_shares, pos.buy_date, pos.buy_nav,
                )
                del self._positions[code]
                logger.info(f"基金合并: {code} -> {new_code}, 比例 {ratio}")

    def _calc_total_value(self, date_str: str,
                           code_nav_map: Dict[str, Dict[str, dict]]) -> float:
        """计算组合总价值（支持多空持仓）"""
        total = self._cash
        for code, pos in self._positions.items():
            nav_data = None
            for cn, nm in code_nav_map.items():
                if cn == code:
                    nav_data = nm.get(date_str)
                    break
            cur_nav = nav_data.get("nav", pos.buy_nav) if nav_data else pos.buy_nav
            total += pos.current_value(cur_nav)
        return total

    def submit_order(self, fund_code: str, order_type: str,
                     shares: float, submit_date: date,
                     confirmation_delay: int = 1):
        """提交申赎申请"""
        order = PendingOrder(fund_code, order_type, shares, submit_date, confirmation_delay)
        self._pending_orders.append(order)

    def get_position(self, fund_code: str) -> Optional[SimPosition]:
        return self._positions.get(fund_code)

    def get_holding_days(self, fund_code: str, current_date: date) -> int:
        pos = self._positions.get(fund_code)
        if pos:
            return pos.holding_days(current_date)
        return 0

    # ── 基金转换支持 ──

    def _get_fund_type(self, fund_code: str) -> str:
        """获取基金类型，优先从meta数据"""
        cached = self._fund_type_map.get(fund_code)
        if cached:
            return cached
        try:
            from ..data.storage import get_fund_meta
            meta = get_fund_meta(fund_code)
            if meta and meta.get("fund_type"):
                self._fund_type_map[fund_code] = meta["fund_type"]
                return meta["fund_type"]
        except Exception:
            pass
        if self._config and fund_code in self._config.fund_codes:
            fund_type = getattr(self._config, 'fund_type', None)
            if fund_type:
                self._fund_type_map[fund_code] = fund_type
                return fund_type
        # ponytail: default to stock when unknown
        self._fund_type_map[fund_code] = "stock"
        return "stock"

    def _process_conversion(self, source_code: str, target_code: str,
                            shares: float, current_date: date) -> bool:
        """尝试使用基金转换路径。True=已使用转换，False=需回退到赎回+申购。"""
        if source_code == target_code or shares <= 0:
            return False
        if source_code not in self._positions:
            return False

        source_type = self._get_fund_type(source_code)
        target_type = self._get_fund_type(target_code)

        # 估算转换金额（用持仓成本NAV近似）
        pos = self._positions[source_code]
        amount = shares * pos.buy_nav

        cost_info = self._cost_model.calc_conversion_cost(
            source_code, target_code, source_type, target_type, amount,
        )

        if cost_info["path"] != "conversion":
            return False

        # 创建转换订单（T+1确认）
        order = PendingOrder(
            fund_code=source_code,
            order_type="conversion",
            shares=shares,
            submit_date=current_date,
            confirmation_delay=1,
            target_fund_code=target_code,
            source_fund_type=source_type,
            target_fund_type=target_type,
        )
        self._pending_orders.append(order)
        return True

    def _benchmark_returns(self) -> list[float]:
        """按交易日等权合成基金池净值收益，作为可比基准。"""
        by_code = {}
        for code, records in self._nav_data.items():
            values = {
                r["date"]: float(r["nav"])
                for r in records
                if r.get("nav") and float(r["nav"]) > 0
            }
            if values:
                by_code[code] = values

        dates = sorted(set().union(*(values.keys() for values in by_code.values()))) if by_code else []
        returns = []
        for previous, current in zip(dates, dates[1:]):
            daily = [values[current] / values[previous] - 1 for values in by_code.values()
                     if previous in values and current in values]
            if daily:
                returns.append(float(np.mean(daily)))
        return returns

    # ── 报告生成 ──

    def _calc_fund_total_shares(self, fund_code: str) -> float:
        """查询基金总份额（外部数据或估算）"""
        # ponytail: hardcoded large number if data unavailable
        return 1_000_000_000  # 10 亿份估算, 降级为不触发限制

    def _generate_report(self) -> BacktestResult:
        """生成完整回测报告 (继承旧引擎 MetricsCalculator 的全部指标)"""
        import uuid
        from ..risk.metrics import risk_metrics_calculator

        if len(self._equity_curve) < 2:
            return BacktestResult(
                backtest_id=f"bt_{uuid.uuid4().hex[:12]}",
                config=self._config, status="completed", total_return=0.0,
            )

        equity_values = [e["total_value"] for e in self._equity_curve]
        initial = self._config.initial_capital
        total_return = (equity_values[-1] - initial) / initial if initial > 0 else 0.0

        # 日收益率
        returns = []
        for i in range(1, len(equity_values)):
            if equity_values[i - 1] > 0:
                returns.append((equity_values[i] - equity_values[i - 1]) / equity_values[i - 1])

        # 基础风险指标
        metrics = risk_metrics_calculator.calculate(returns)
        n_days = len(equity_values)
        ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1

        # 胜率 & 盈亏比
        buy_trades = [t for t in self._trade_log if t["action"] == "sell_confirmed"]
        wins = [t for t in buy_trades if t.get("proceeds", 0) > t.get("cost", 0)]
        losses = [t for t in buy_trades if t.get("proceeds", 0) <= t.get("cost", 0)]
        win_rate = len(wins) / len(buy_trades) if buy_trades else 0.0
        avg_win = np.mean([t["proceeds"] - t.get("cost", 0) for t in wins]) if wins else 0.0
        avg_loss = abs(np.mean([t["proceeds"] - t.get("cost", 0) for t in losses])) if losses else 1.0
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

        # 信息比率（使用基金池等权净值收益作为可比基准）
        benchmark_returns = self._benchmark_returns()
        if len(returns) > 1 and len(benchmark_returns) > 1:
            n = min(len(returns), len(benchmark_returns))
            excess = np.array(returns[-n:]) - np.array(benchmark_returns[-n:])
            tracking_error = np.std(excess, ddof=1) * np.sqrt(252)
            information_ratio = (
                (ann_return - 0.02) / tracking_error if tracking_error > 0 else 0.0
            )
        else:
            information_ratio = 0.0

        # 换手率
        total_turnover = sum(
            t.get("cost", 0) or t.get("proceeds", 0) or 0
            for t in self._trade_log if "confirmed" in t.get("action", "")
        )
        avg_equity = np.mean(equity_values) if equity_values else initial
        turnover_rate = total_turnover / avg_equity if avg_equity > 0 else 0.0

        # 费用损耗
        total_fees = sum(t.get("cost", 0) for t in self._trade_log)
        fee_leakage = total_fees / initial if initial > 0 else 0.0

        # 最大连续亏损天数
        max_consec_loss = 0
        cur_loss = 0
        for r in returns:
            if r < 0:
                cur_loss += 1
                max_consec_loss = max(max_consec_loss, cur_loss)
            else:
                cur_loss = 0

        # 分年度收益
        period_returns = {}
        yearly_curves: Dict[str, List[float]] = {}
        for e in self._equity_curve:
            year = e["date"][:4]
            yearly_curves.setdefault(year, []).append(e["total_value"])
        for year, vals in yearly_curves.items():
            if len(vals) > 1:
                yr_return = (vals[-1] - vals[0]) / vals[0] if vals[0] > 0 else 0.0
                period_returns[year] = round(yr_return, 6)

        total_trades = len([t for t in self._trade_log if "confirmed" in t.get("action", "")])

        return BacktestResult(
            backtest_id=f"bt_{uuid.uuid4().hex[:12]}",
            config=self._config,
            total_return=round(total_return, 6),
            annual_return=round(ann_return, 6),
            max_drawdown=metrics.max_drawdown,
            volatility=metrics.volatility or 0.0,
            sortino_ratio=metrics.sortino_ratio or 0.0,
            sharpe_ratio=metrics.sharpe_ratio or 0.0,
            calmar_ratio=metrics.calmar_ratio or 0.0,
            information_ratio=round(information_ratio, 4),
            win_rate=round(win_rate, 4),
            profit_loss_ratio=round(profit_loss_ratio, 4),
            total_trades=total_trades,
            turnover_rate=round(turnover_rate, 6),
            fee_leakage=round(fee_leakage, 6),
            max_consecutive_loss_days=max_consec_loss,
            equity_curve=self._equity_curve,
            trade_log=self._trade_log,
            period_returns=period_returns,
            status="completed",
        )


# ── 辅助函数 (旧引擎 compat) ──

def _build_portfolio_snapshot(positions, cash, total) -> "Portfolio":
    """构建策略可读的 Portfolio 快照 (Dict[str, float]: fund_code -> weight)"""
    from ..core.models import Portfolio
    pos_dict = {
        code: (p.shares * p.buy_nav * (1 if p.direction != "short" else -1)) / total if total > 0 else 0
        for code, p in positions.items()
    }
    return Portfolio(
        cash=cash,
        positions=pos_dict,
        total_value=total,
    )


def _signal_to_core(sig) -> Any:
    """将 FundSignal 转换为 core.Signal (风控管线需要 core 类型)"""
    try:
        from core import Signal, Direction as CoreDir
        dir_map = {
            "buy": CoreDir.LONG,
            "sell": CoreDir.CLOSE_LONG,
            "short": CoreDir.SHORT,
            "close_short": CoreDir.CLOSE_SHORT,
            "hold": CoreDir.NONE,
        }
        d = sig.direction.value if hasattr(sig.direction, 'value') else sig.direction
        return Signal(
            id=getattr(sig, 'signal_id', ''),
            strategy=getattr(sig, 'strategy_name', ''),
            symbol=sig.fund_code,
            direction=dir_map.get(str(d).lower(), CoreDir.NONE),
            price=0,
            volume=0,
            confidence=getattr(sig, 'confidence', 0.5),
        )
    except ImportError:
        return sig


class _RiskContext:
    """简化的风控上下文 (core.RiskContext 的轻量替代)"""
    def __init__(self, portfolio_value=0.0, positions=None, daily_signal_count=0):
        self.portfolio_value = portfolio_value
        self.positions = positions or []
        self.daily_signal_count = daily_signal_count


_REJECT_LEVEL = None
try:
    from core import RiskLevel
    _REJECT_LEVEL = RiskLevel.REJECT
except ImportError:
    pass
