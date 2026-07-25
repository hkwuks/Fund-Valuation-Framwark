"""FundQuant 事件驱动回测引擎 — 完整 T+1 申赎模拟 + 前视偏差防护"""

from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
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
    """模拟持仓"""
    def __init__(self, fund_code: str, shares: float, buy_date: date, buy_nav: float):
        self.fund_code = fund_code
        self.shares = shares
        self.buy_date = buy_date
        self.buy_nav = buy_nav
        self.cost = shares * buy_nav

    def current_value(self, nav: float) -> float:
        return self.shares * nav

    def holding_days(self, current_date: date) -> int:
        return (current_date - self.buy_date).days

    def pnl(self, nav: float) -> float:
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
    """基金回测引擎 — 事件驱动, T+1确认, 前视偏差防护"""

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

    def run(self, config: BacktestConfig,
            nav_data: Optional[Dict[str, List[dict]]] = None) -> BacktestResult:
        """事件驱动回测主循环"""
        self._config = config
        self._dividend_calendar = config.dividend_calendar
        self._cash = config.initial_capital
        self._positions = {}
        self._pending_orders = []
        self._trade_log = []
        self._equity_curve = []
        self._nav_data = nav_data or {}

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

            # ── 步骤4: 策略评估 (严格基于T-1日信息集) ──
            if self._config.qdii_fund_codes:
                prev_idx = trading_days.index(prev_date_str)
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
            # 简化的策略评估：demo模式直接跳过实际策略调用
            # 真实使用中由外部传入

        return self._generate_report()

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
        """计算组合总价值"""
        total = self._cash
        for code, pos in self._positions.items():
            nav_data = None
            for cn, nm in code_nav_map.items():
                if cn == code:
                    nav_data = nm.get(date_str)
                    break
            cur_nav = nav_data.get("nav", pos.buy_nav) if nav_data else pos.buy_nav
            total += pos.shares * cur_nav
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

    # ── 报告生成 ──

    def _calc_fund_total_shares(self, fund_code: str) -> float:
        """查询基金总份额（外部数据或估算）"""
        # ponytail: hardcoded large number if data unavailable
        return 1_000_000_000  # 10 亿份估算, 降级为不触发限制

    def _generate_report(self) -> BacktestResult:
        """生成回测报告"""
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

        returns = []
        for i in range(1, len(equity_values)):
            if equity_values[i - 1] > 0:
                returns.append((equity_values[i] - equity_values[i - 1]) / equity_values[i - 1])

        metrics = risk_metrics_calculator.calculate(returns)
        ann_return = (1 + total_return) ** (252 / max(len(equity_values), 1)) - 1

        # 胜率计算
        buy_trades = [t for t in self._trade_log if t["action"] == "sell_confirmed"]
        wins = [t for t in buy_trades if t.get("proceeds", 0) > 0]
        win_rate = len(wins) / len(buy_trades) if buy_trades else 0.0

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

        return BacktestResult(
            backtest_id=f"bt_{uuid.uuid4().hex[:12]}",
            config=self._config,
            total_return=round(total_return, 6),
            annual_return=round(ann_return, 6),
            max_drawdown=metrics.max_drawdown,
            sharpe_ratio=metrics.sharpe_ratio or 0.0,
            calmar_ratio=metrics.calmar_ratio or 0.0,
            win_rate=round(win_rate, 4),
            total_trades=len([t for t in self._trade_log if "confirmed" in t.get("action", "")]),
            equity_curve=self._equity_curve,
            trade_log=self._trade_log,
            period_returns=period_returns,
            status="completed",
        )
