"""基金领域适配 — FundDomainAdapter + FundCostModel"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np

from core import (
    DomainAdapter, ExecutionEngine, CostModel,
    RiskCheck, Strategy, StrategyRegistry,
    T1ExecutionEngine, NoSlippage,
    MaxDrawdownCheck, DailyLossCheck, SignalFrequencyCheck,
    ConsecutiveLossCheck, PositionLimitCheck,
    Signal, Direction, Fill,
    DataFeed, FundNavPoint, Bar,
)
from backend.fund_quant.data.storage import get_fee_rates


# ── 风控检查索引（每类基金独立配置） ──

def _risk_check_builder(fund_type: str) -> list[RiskCheck]:
    """按基金类型构造差异化默认风险检查组合"""
    from .risk.risk_checks import (
        ConfidenceCheck, CooldownCheck, MinHoldingCheck,
        FundPositionLimitCheck, ConcentrationCheck,
        LiquidityCheck, CashReserveCheck,
        RelatedFundConcentrationCheck, ScaleDropCheck,
        StyleDriftCheck, FundTypeCheck, BondDrawdownCheck,
        QdiiFxRiskCheck, FofUnderlyingCheck, ClosedEndCheck,
    )

    # ── 通用层（所有类型都有的基础检查） ──
    universal = [
        SignalFrequencyCheck(max_per_day=5),
    ]

    # ── 统计层（置信度 / 冷却期 / 最小持仓） ──
    stat_layer = [
        ConfidenceCheck(min_confidence=0.6),
        CooldownCheck(cooldown_days=5),
        MinHoldingCheck(min_days=7),
    ]

    # ── 组合层（仓位 / 集中度 / 流动性 / 现金） ──
    portfolio_layer = [
        FundPositionLimitCheck(max_position_pct=0.3),
        ConcentrationCheck(max_pct=0.4),
        LiquidityCheck(max_redemption_pct=0.2),
        CashReserveCheck(min_cash_pct=0.05),
        RelatedFundConcentrationCheck(max_pct=0.5),
        ScaleDropCheck(min_scale=10_000_000),
    ]

    # ── 回撤相关 ──
    drawdown_checks = []
    type_checks = []

    if fund_type in ("equity", "index"):
        drawdown_checks = [
            MaxDrawdownCheck(drawdown_limit=0.20),
            DailyLossCheck(limit=0.05),
            ConsecutiveLossCheck(max_losses=7),
            PositionLimitCheck(max_positions=20),
            StyleDriftCheck(),
        ]

    elif fund_type == "balanced":
        drawdown_checks = [
            MaxDrawdownCheck(drawdown_limit=0.15),
            DailyLossCheck(limit=0.05),
            ConsecutiveLossCheck(max_losses=7),
            PositionLimitCheck(max_positions=20),
            StyleDriftCheck(),
        ]
        type_checks = [BondDrawdownCheck(limit=0.05)]

    elif fund_type == "bond":
        drawdown_checks = [
            MaxDrawdownCheck(drawdown_limit=0.05),
            DailyLossCheck(limit=0.02),
            ConsecutiveLossCheck(max_losses=4),
            PositionLimitCheck(max_positions=30),
            StyleDriftCheck(),
        ]
        type_checks = [BondDrawdownCheck(limit=0.05), ClosedEndCheck()]

    elif fund_type == "money":
        portfolio_layer = [
            LiquidityCheck(max_redemption_pct=0.1),
            CashReserveCheck(min_cash_pct=0.2),
        ]
        drawdown_checks = [
            MaxDrawdownCheck(drawdown_limit=0.01),
            DailyLossCheck(limit=0.01),
        ]
        type_checks = [FundTypeCheck()]

    elif fund_type == "qdii":
        drawdown_checks = [
            MaxDrawdownCheck(drawdown_limit=0.20),
            DailyLossCheck(limit=0.05),
            ConsecutiveLossCheck(max_losses=7),
            PositionLimitCheck(max_positions=20),
            StyleDriftCheck(),
        ]
        type_checks = [QdiiFxRiskCheck(fx_vol_limit=0.05)]

    elif fund_type == "commodity":
        drawdown_checks = [
            MaxDrawdownCheck(drawdown_limit=0.15),
            DailyLossCheck(limit=0.05),
            ConsecutiveLossCheck(max_losses=5),
            PositionLimitCheck(max_positions=10),
            StyleDriftCheck(),
        ]
        portfolio_layer = [
            FundPositionLimitCheck(max_position_pct=0.2),
            ConcentrationCheck(max_pct=0.5),
            LiquidityCheck(max_redemption_pct=0.15),
            CashReserveCheck(min_cash_pct=0.05),
            RelatedFundConcentrationCheck(max_pct=0.5),
            ScaleDropCheck(min_scale=10_000_000),
        ]

    elif fund_type == "fof":
        drawdown_checks = [
            MaxDrawdownCheck(drawdown_limit=0.15),
            DailyLossCheck(limit=0.05),
            ConsecutiveLossCheck(max_losses=7),
            PositionLimitCheck(max_positions=10),
            StyleDriftCheck(),
        ]
        portfolio_layer = [
            FundPositionLimitCheck(max_position_pct=0.3),
            ConcentrationCheck(max_pct=0.3),
            LiquidityCheck(max_redemption_pct=0.2),
            CashReserveCheck(min_cash_pct=0.05),
            RelatedFundConcentrationCheck(max_pct=0.4),
            ScaleDropCheck(min_scale=10_000_000),
        ]
        type_checks = [FofUnderlyingCheck(), ClosedEndCheck()]
        portfolio_layer = [
            FundPositionLimitCheck(max_position_pct=0.3),
            ConcentrationCheck(max_pct=0.3),               # 更严的集中度
            LiquidityCheck(max_redemption_pct=0.2),
            CashReserveCheck(min_cash_pct=0.05),
            RelatedFundConcentrationCheck(max_pct=0.4),    # 更严的关联集中度
            ScaleDropCheck(min_scale=10_000_000),
        ]
        type_checks = [FofUnderlyingCheck(), ClosedEndCheck()]

    return universal + stat_layer + portfolio_layer + drawdown_checks + type_checks


class FundCostModel(CostModel):
    """基金费率模型 — 费率从 DB 读取，DB 无数据时回退静态默认值"""

    # 兜底默认值（DB 无数据时使用）
    _FALLBACK = {
        "sub_fee": 0.0, "mgmt_fee": 0.0, "custody_fee": 0.0,
        "c_class_service_fee": 0.0,
        "redemption_tiers": {7: 1.50, 30: 0.75, 365: 0.50, 730: 0.25, 999999: 0.0},
    }

    def __init__(self, fund_type: str = "equity",
                 is_c_class: bool = False,
                 fof_underlying_fee: float = 0.0,
                 dividend_tax_short: float = 0.10,
                 dividend_tax_long: float = 0.0):
        self.fund_type = fund_type
        self._is_c_class = is_c_class
        self._fof_underlying_fee = fof_underlying_fee
        self._div_tax_short = dividend_tax_short
        self._div_tax_long = dividend_tax_long
        self._load_rates()

    def _load_rates(self):
        """从 DB 加载费率，失败时回退默认"""
        rates = get_fee_rates(self.fund_type)
        if rates is None:
            rates = self._FALLBACK
        self._sub_rate = rates.get("sub_fee", self._FALLBACK["sub_fee"])
        self._mgmt_rate = rates.get("mgmt_fee", self._FALLBACK["mgmt_fee"])
        self._custody_rate = rates.get("custody_fee", self._FALLBACK["custody_fee"])
        self._c_service_fee = rates.get("c_class_service_fee",
                                        self._FALLBACK["c_class_service_fee"])
        self._redemption_tiers = rates.get("redemption_tiers",
                                           self._FALLBACK["redemption_tiers"])

    def calc(self, signal: Signal, fill: Fill) -> float:
        """计算单笔交易成本。

        基金净值已内扣管理费、托管费及 C 类销售服务费；回测只扣申购、赎回和
        可明确量化的 FOF 穿透费用，避免重复计费。
        """
        amount = fill.price * fill.volume
        holding_days = int(signal.extra.get("holding_days", 0))

        if signal.direction in (Direction.LONG, Direction.SHORT):
            return round(self._get_subscription(amount), 4)
        return round(self._get_redemption(amount, holding_days), 4)

    def _get_subscription(self, amount: float) -> float:
        """申购费"""
        fee = amount * self._sub_rate
        # FOF穿透
        if self.fund_type == "fof" and self._fof_underlying_fee > 0:
            fee += amount * self._fof_underlying_fee
        return fee

    def _get_redemption(self, amount: float, holding_days: int) -> float:
        """赎回费（含A/C类区分）"""
        if self._is_c_class:
            # C类持有超过阈值免赎回费
            return 0.0 if holding_days >= 30 else amount * 0.005
        for limit, pct in sorted(self._redemption_tiers.items()):
            if holding_days < limit:
                return amount * (pct / 100)
        return 0.0

    def _get_annual_carry(self, amount: float) -> float:
        """年化管理+托管费"""
        mgmt = amount * self._mgmt_rate
        custody = amount * self._custody_rate
        if self._is_c_class:
            mgmt += amount * 0.008  # C类销售服务费 ~0.8%/年
        if self.fund_type == "fof":
            mgmt += amount * self._fof_underlying_fee  # FOF穿透
        return mgmt + custody

    def _get_dividend_tax(self, amount: float, holding_days: int) -> float:
        """分红税"""
        rate = self._div_tax_long if holding_days >= 365 else self._div_tax_short
        return amount * rate * 0.01  # 假设1%分红率


class FundDomainAdapter(DomainAdapter):
    """基金领域适配器"""

    @property
    def name(self) -> str:
        return "fund"

    def create_data_feed(self, config: dict) -> DataFeed:
        raise NotImplementedError("使用 fund_quant 现有数据层，Phase 3 迁移")

    def create_executor(self, config: dict) -> ExecutionEngine:
        return T1ExecutionEngine(confirmation_delay=config.get("confirmation_delay", 1))

    def create_cost_model(self, config: dict) -> CostModel:
        return FundCostModel(
            fund_type=config.get("fund_type", "equity"),
        )

    def default_risk_checks(self) -> list[RiskCheck]:
        """返回 equity 级别默认检查（向后兼容）"""
        return _risk_check_builder("equity")

    def get_risk_checks(self, fund_type: str = "equity") -> list[RiskCheck]:
        """按基金类型返回匹配的风险检查组合

        Args:
            fund_type: FundType 枚举值字符串

        Returns:
            该基金类型适用的 RiskCheck 列表
        """
        valid_types = {"equity", "index", "balanced", "bond",
                       "money", "qdii", "commodity", "fof"}
        if fund_type not in valid_types:
            fund_type = "equity"
        return _risk_check_builder(fund_type)

    def get_available_strategies(self) -> dict[str, type[Strategy]]:
        return {}

    def register_factors(self):
        """注册基金域因子"""
        from backend.core.factor.registry import FactorRegistry
        from backend.fund_quant.factors.risk_adjusted import (
            SharpeRatioFactor, InfoRatioFactor, CaptureRatioFactor,
        )
        from backend.fund_quant.factors.risk import MaxDrawdownFactor
        from backend.fund_quant.factors.structural import (
            FundScaleFactor, FeeRateFactor,
        )
        from backend.fund_quant.factors.flow import FundFlowFactor
        from backend.fund_quant.factors.concentration import (
            HoldingConcentrationFactor,
        )
        from backend.fund_quant.factors.manager import ManagerTenureFactor
        from backend.fund_quant.factors.behavioral import CalendarReturnFactor

        FactorRegistry.register_factors([
            (SharpeRatioFactor, SharpeRatioFactor.meta),
            (MaxDrawdownFactor, MaxDrawdownFactor.meta),
            (InfoRatioFactor, InfoRatioFactor.meta),
            (FundScaleFactor, FundScaleFactor.meta),
            (FeeRateFactor, FeeRateFactor.meta),
            (FundFlowFactor, FundFlowFactor.meta),
            (HoldingConcentrationFactor, HoldingConcentrationFactor.meta),
            (ManagerTenureFactor, ManagerTenureFactor.meta),
            (CaptureRatioFactor, CaptureRatioFactor.meta),
            (CalendarReturnFactor, CalendarReturnFactor.meta),
        ])


@StrategyRegistry.register("etf_rotation_aurora")
class AuroraEtfRotation(Strategy):
    """ETF 全球资产轮动（AuroraCore 版）— 多基金动量评分 + Top-N 持仓

    与 fund_quant.allocation.etf_rotation 同逻辑，但走统一引擎:
      - on_data 逐点积累多基金净值（只用已到数据，无前视）
      - 每 rebalance_days 个交易日调仓，emit LONG/CLOSE_LONG 信号
      - 目标金额 = 权重 × ctx.portfolio_value（引擎每 bar 更新）
    """
    name = "etf_rotation_aurora"
    strategy_type = "allocation"
    description = "ETF全球资产轮动: 动量评分(年化收益×R²) + Top-N 轮动"
    default_params = {
        "etf_pool": {
            "518880": "黄金ETF", "513100": "纳指ETF", "159915": "创业板ETF",
            "510180": "上证180ETF", "510300": "沪深300ETF", "510500": "中证500ETF",
            "511880": "银华日利ETF",
        },
        "momentum_days": 25,
        "top_n": 1,
        "rebalance_days": 5,
        "buy_threshold": 0.0,
        "max_single_weight": 1.0,
    }
    min_history_days = 60

    def __init__(self):
        merged = {**self.default_params}
        super().__init__()
        self.params = merged
        self._hist: dict[str, list[tuple[str, float]]] = {}  # fund_code -> [(date, nav)]
        self._cur_date = ""
        self._day_count = 0
        self._last_rebalance_day = 0

    def _pool_codes(self) -> list[str]:
        """候选池 = 配置 etf_pool ∪ 实际收到数据的基金（自适应传入的 fund_codes）"""
        config_pool = list(self.params.get("etf_pool", {}))
        seen = list(self._hist.keys())
        # 保持稳定顺序：先配置后实际，去重
        merged = list(dict.fromkeys(config_pool + seen))
        return merged or config_pool

    def on_data(self, data):
        code = getattr(data, "fund_code", "") or getattr(data, "symbol", "")
        nav = getattr(data, "nav", getattr(data, "close", 0))
        date_str = str(getattr(data, "date", ""))
        if not code or not nav or nav <= 0:
            return
        self._hist.setdefault(code, []).append((date_str, nav))

        # 新交易日 → 计数 + 判断是否调仓
        if date_str != self._cur_date:
            self._cur_date = date_str
            self._day_count += 1
            freq = max(int(self.params.get("rebalance_days", 5)), 1)
            if (self._day_count - self._last_rebalance_day) >= freq:
                self._rebalance(date_str)
                self._last_rebalance_day = self._day_count

    def _score(self, navs: list[float], momentum_days: int) -> float:
        """动量得分 = 年化收益率 × R²（log价格线性回归）"""
        import numpy as np
        recent = navs[-(momentum_days + 5):]
        arr = np.array(recent, dtype=np.float64)
        log_prices = np.log(arr)
        x = np.arange(len(log_prices))
        slope, intercept = np.polyfit(x, log_prices, 1)
        annualized = np.exp(slope * 250) - 1
        y_pred = slope * x + intercept
        ss_res = float(np.sum((log_prices - y_pred) ** 2))
        ss_tot = float(np.sum((log_prices - np.mean(log_prices)) ** 2))
        r2 = max(0.0, min(1.0, 1 - (ss_res / max(ss_tot, 1e-10))))
        return annualized * r2

    def _rebalance(self, date_str: str):
        momentum_days = int(self.params.get("momentum_days", 25))
        top_n = int(self.params.get("top_n", 1))
        buy_th = float(self.params.get("buy_threshold", 0.0))
        pool = self._pool_codes()

        # 各基金截至该日的净值序列
        navs: dict[str, list[float]] = {}
        for code in pool:
            series = [n for d, n in self._hist.get(code, []) if d <= date_str]
            if len(series) >= momentum_days + 5:
                navs[code] = series
        if not navs:
            return

        scores = {c: self._score(v, momentum_days) for c, v in navs.items()}
        ranked = sorted(scores, key=lambda c: scores[c], reverse=True)
        top = ranked[:top_n]
        all_codes = self._hist.keys()

        # 无前视：本调仓日用的是截至 date_str 的净值，T+1 由引擎按最新价确认
        if not top or scores[top[0]] < buy_th:
            # 全部卖出持币
            self._emit_all_close(all_codes, f"最高动量 {scores[top[0]]:.4f}" if top else "无数据")
            return

        target = set(top)
        # 先清仓不在目标内的，再买入目标
        for code in list(all_codes):
            pos = self.ctx.execution.get_position(code) if self.ctx.execution else None
            if code not in target and pos is not None and pos.volume > 0:
                self.ctx.emit(Signal(
                    id="", strategy=self.name, symbol=code,
                    direction=Direction.CLOSE_LONG, price=0,
                    volume=pos.volume, confidence=1.0,
                    reason=f"跌出Top{top_n}, 清仓",
                ))
        for code in target:
            pos = self.ctx.execution.get_position(code) if self.ctx.execution else None
            if pos is not None and pos.volume > 0:
                continue  # 已持有
            nav = self._hist[code][-1][1] if self._hist.get(code) else 0
            if nav <= 0:
                continue
            weight = min(float(self.params.get("max_single_weight", 1.0)),
                         1.0 / max(top_n, 1))
            target_amt = self.ctx.portfolio_value * weight
            self.ctx.emit(Signal(
                id="", strategy=self.name, symbol=code,
                direction=Direction.LONG, price=nav,
                volume=target_amt / nav, confidence=1.0,
                reason=f"动量Top{top_n}: score={scores[code]:.4f}",
            ))

    def _emit_all_close(self, codes, reason: str):
        for code in list(codes):
            pos = self.ctx.execution.get_position(code) if self.ctx.execution else None
            if pos is not None and pos.volume > 0:
                self.ctx.emit(Signal(
                    id="", strategy=self.name, symbol=code,
                    direction=Direction.CLOSE_LONG, price=0,
                    volume=pos.volume, confidence=1.0,
                    reason=f"全部清仓({reason})",
                ))


# ── 全天候策略（AuroraCore 版）──

@StrategyRegistry.register("all_weather_aurora")
class AllWeatherAurora(Strategy):
    """桥水全天候策略（AuroraCore 版）— 月度再平衡 + 固定权重/风险平价双模式

    on_data 逐点积累多基金净值，每月调用 AllWeatherStrategy.optimize() 算权重，
    然后 emit LONG/CLOSE_LONG 信号使持仓回到目标权重。
    """
    name = "all_weather_aurora"
    strategy_type = "allocation"
    description = "全天候策略: 四象限风险平价 + 月度再平衡, 走统一引擎"
    default_params = {
        "mode": "fixed",
        "asset_template": {
            "510300": {"name": "沪深300ETF", "asset_class": "equity", "fixed_weight": 0.10},
            "513500": {"name": "标普500ETF", "asset_class": "equity", "fixed_weight": 0.10},
            "513100": {"name": "纳指ETF",   "asset_class": "equity", "fixed_weight": 0.10},
            "511520": {"name": "5年国债ETF", "asset_class": "bond_medium", "fixed_weight": 0.15},
            "511260": {"name": "10年国债ETF","asset_class": "bond_long",   "fixed_weight": 0.40},
            "518880": {"name": "黄金ETF",    "asset_class": "gold",     "fixed_weight": 0.075},
            "159985": {"name": "豆粕ETF",    "asset_class": "commodity","fixed_weight": 0.075},
        },
        "rebalance_freq": "monthly",
        "rebalance_threshold": 0.05,
        "lookback_days": 756,
        "max_weight": 0.50,
        "min_weight": 0.02,
        "bond_vol_multiplier": "auto",
        "leverage": 1.0,
    }
    param_choices = {"mode": ["fixed", "risk_parity"]}
    min_history_days = 60

    def __init__(self):
        merged = {**self.default_params}
        super().__init__()
        self.params = merged
        self._hist: dict[str, list[tuple[str, float]]] = {}  # fund_code -> [(date, nav)]
        self._cur_date = ""
        self._last_rebalance_month = ""
        self._first_rebalance = True  # 首次数据够就立即再平衡

    def _pool_codes(self) -> list[str]:
        template = self.params.get("asset_template", {})
        config_pool = list(template.keys())
        seen = list(self._hist.keys())
        merged = list(dict.fromkeys(config_pool + seen))
        return merged or config_pool

    def _compute_weights(self, nav_series=None, codes=None):
        """固定模板权重（nav_series 不用于计算，仅保持接口一致）"""
        from .strategy.allocation.all_weather import AllWeatherStrategy
        strategy = AllWeatherStrategy(params=dict(self.params))
        result = strategy.optimize(fund_codes=None)
        return result.get("weights", {})

    def on_data(self, data):
        code = getattr(data, "fund_code", "") or getattr(data, "symbol", "")
        nav = getattr(data, "nav", getattr(data, "close", 0))
        date_str = str(getattr(data, "date", ""))
        if not code or not nav or nav <= 0:
            return
        self._hist.setdefault(code, []).append((date_str, nav))

        if date_str != self._cur_date:
            self._cur_date = date_str
            self._check_rebalance(date_str)

    def _check_rebalance(self, date_str: str):
        """判断是否触发再平衡：首次或跨月"""
        cur_month = date_str[:7]
        if self._first_rebalance and len(self._hist) >= 3:
            if sum(len(self._hist.get(code, [])) >= 20 for code in self._pool_codes()) < 2:
                return
            self._first_rebalance = False
            self._last_rebalance_month = cur_month
            self._rebalance(date_str)
            return
        if cur_month != self._last_rebalance_month and self._last_rebalance_month:
            self._last_rebalance_month = cur_month
            self._rebalance(date_str)

    def _rebalance(self, date_str: str):
        """用 AllWeatherStrategy 算权重 → 调仓至目标"""
        from .strategy.allocation.all_weather import AllWeatherStrategy

        pool = self._pool_codes()
        # 收集截至该日的净值序列
        nav_series: dict[str, list[float]] = {}
        for code in pool:
            series = [n for d, n in self._hist.get(code, []) if d <= date_str]
            if len(series) >= 20:
                nav_series[code] = series

        if not nav_series:
            return

        valid_codes = list(nav_series.keys())
        if len(valid_codes) < 2:
            return

        # 调用 AllWeatherStrategy 计算权重
        strategy = AllWeatherStrategy(params=dict(self.params))
        result = strategy.optimize(fund_codes=valid_codes, nav_series=nav_series)
        weights = result.get("weights", {})
        if not weights:
            return

        # 总权益
        pv = self.ctx.portfolio_value or 0
        if pv <= 0:
            return

        all_codes = self._hist.keys()
        target_codes = {c for c, w in weights.items() if w > 0}

        # 先清仓不在目标内的
        for code in list(all_codes):
            pos = self.ctx.execution.get_position(code) if self.ctx.execution else None
            if code not in target_codes and pos is not None and pos.volume > 0:
                self.ctx.emit(Signal(
                    id="", strategy=self.name, symbol=code,
                    direction=Direction.CLOSE_LONG, price=0,
                    volume=pos.volume, confidence=1.0,
                    reason=f"全天候调仓: 权重为0, 清仓",
                ))

        # 再平衡：调整目标基金的持仓至目标权重
        for code, weight in weights.items():
            if weight <= 0:
                continue
            last_nav = nav_series.get(code, [])
            if not last_nav:
                continue
            price = last_nav[-1]
            if price <= 0:
                continue

            target_amt = pv * weight
            pos = self.ctx.execution.get_position(code) if self.ctx.execution else None
            current_amt = pos.volume * price if pos and pos.volume > 0 else 0
            diff = target_amt - current_amt
            threshold = self.params.get("rebalance_threshold", 0.05) * pv

            if abs(diff) < threshold:
                continue  # 偏离不超阈值，跳过

            if diff > 0:
                # 买入
                self.ctx.emit(Signal(
                    id="", strategy=self.name, symbol=code,
                    direction=Direction.LONG, price=price,
                    volume=diff / price, confidence=1.0,
                    reason=f"全天候调仓: 目标权重{weight:.1%}, 当前不足",
                ))
            else:
                # 卖出超配部分
                sell_vol = min(-diff / price, pos.volume) if pos else 0
                if sell_vol > 0:
                    self.ctx.emit(Signal(
                        id="", strategy=self.name, symbol=code,
                        direction=Direction.CLOSE_LONG, price=price,
                        volume=sell_vol, confidence=1.0,
                        reason=f"全天候调仓: 超配, 减仓至{weight:.1%}",
                    ))


# ── BL+四象限观点策略（AuroraCore 版）──

@StrategyRegistry.register("bl_quadrant_aurora")
class AuroraBlQuadrant(Strategy):
    """BL+四象限观点（AuroraCore 版）— 月度再平衡 + 四象限观点注入

    on_data 逐点积累多基金净值，每月调用 BlackLittermanQuadrant.optimize()
    算权重，然后 emit LONG/CLOSE_LONG 信号使持仓回到目标权重。
    优化只用截至当日的净值（nav_series），无前视偏差。
    """
    name = "bl_quadrant_aurora"
    strategy_type = "allocation"
    description = "BL+四象限观点: 桥水30/40/15/15比例作为相对观点 + 月度再平衡, 走统一引擎"
    default_params = {
        "mode": "quadrant",
        "rebalance_freq": "monthly",
        "rebalance_threshold": 0.05,
        "lookback_days": 756,
        "risk_aversion": 2.5,
        "tau": 0.05,
        "max_weight": 0.4,
        "min_weight": 0.05,
        "view_confidence": 0.6,
        "growth_underperform": -0.03,
        "inflation_outperform": 0.04,
        "leverage": 1.0,
    }
    min_history_days = 60

    def __init__(self):
        merged = {**self.default_params}
        super().__init__()
        self.params = merged
        self._hist: dict[str, list[tuple[str, float]]] = {}  # fund_code -> [(date, nav)]
        self._cur_date = ""
        self._last_rebalance_month = ""
        self._first_rebalance = True  # 首次数据够就立即再平衡

    def _pool_codes(self) -> list[str]:
        return list(self._hist.keys()) or list(self.params.get("fund_codes", []))

    def _compute_weights(self, nav_series=None, codes=None):
        """通过 BlackLittermanQuadrant 优化器计算权重（保持接口一致）"""
        from .strategy.allocation.bl_quadrant import BlackLittermanQuadrant
        valid = codes or list((nav_series or {}).keys())
        if len(valid) < 2:
            return {}
        strategy = BlackLittermanQuadrant(params=dict(self.params))
        result = strategy.optimize(fund_codes=valid, nav_series=nav_series or {})
        return result.get("weights", {})

    def on_data(self, data):
        code = getattr(data, "fund_code", "") or getattr(data, "symbol", "")
        nav = getattr(data, "nav", getattr(data, "close", 0))
        date_str = str(getattr(data, "date", ""))
        if not code or not nav or nav <= 0:
            return
        self._hist.setdefault(code, []).append((date_str, nav))

        if date_str != self._cur_date:
            self._cur_date = date_str
            self._check_rebalance(date_str)

    def _check_rebalance(self, date_str: str):
        """判断是否触发再平衡：首次或跨月"""
        cur_month = date_str[:7]
        if self._first_rebalance and len(self._hist) >= 3:
            self._first_rebalance = False
            self._last_rebalance_month = cur_month
            self._rebalance(date_str)
            return
        if cur_month != self._last_rebalance_month and self._last_rebalance_month:
            self._last_rebalance_month = cur_month
            self._rebalance(date_str)

    def _rebalance(self, date_str: str):
        from .strategy.allocation.bl_quadrant import BlackLittermanQuadrant

        pool = self._pool_codes()
        # 收集截至该日的净值序列（只用已到数据，无前视）
        nav_series: dict[str, list[float]] = {}
        for code in pool:
            series = [n for d, n in self._hist.get(code, []) if d <= date_str]
            if len(series) >= 20:
                nav_series[code] = series

        if not nav_series:
            return

        valid_codes = list(nav_series.keys())
        if len(valid_codes) < 2:
            return

        # 调用 BlackLittermanQuadrant 计算权重（nav_series 避免 DB 前视）
        strategy = BlackLittermanQuadrant(params=dict(self.params))
        result = strategy.optimize(fund_codes=valid_codes, nav_series=nav_series)
        weights = result.get("weights", {})
        if not weights:
            return

        # 总权益
        pv = self.ctx.portfolio_value or 0
        if pv <= 0:
            return

        all_codes = self._hist.keys()
        target_codes = {c for c, w in weights.items() if w > 0}

        # 先清仓不在目标内的
        for code in list(all_codes):
            pos = self.ctx.execution.get_position(code) if self.ctx.execution else None
            if code not in target_codes and pos is not None and pos.volume > 0:
                self.ctx.emit(Signal(
                    id="", strategy=self.name, symbol=code,
                    direction=Direction.CLOSE_LONG, price=0,
                    volume=pos.volume, confidence=1.0,
                    reason=f"BL四象限调仓: 权重为0, 清仓",
                ))

        # 再平衡：调整目标基金的持仓至目标权重
        for code, weight in weights.items():
            if weight <= 0:
                continue
            last_nav = nav_series.get(code, [])
            if not last_nav:
                continue
            price = last_nav[-1]
            if price <= 0:
                continue

            target_amt = pv * weight
            pos = self.ctx.execution.get_position(code) if self.ctx.execution else None
            current_amt = pos.volume * price if pos and pos.volume > 0 else 0
            diff = target_amt - current_amt
            threshold = self.params.get("rebalance_threshold", 0.05) * pv

            if abs(diff) < threshold:
                continue  # 偏离不超阈值，跳过

            if diff > 0:
                # 买入
                self.ctx.emit(Signal(
                    id="", strategy=self.name, symbol=code,
                    direction=Direction.LONG, price=price,
                    volume=diff / price, confidence=1.0,
                    reason=f"BL四象限调仓: 目标权重{weight:.1%}, 当前不足",
                ))
            else:
                # 卖出超配部分
                sell_vol = min(-diff / price, pos.volume) if pos else 0
                if sell_vol > 0:
                    self.ctx.emit(Signal(
                        id="", strategy=self.name, symbol=code,
                        direction=Direction.CLOSE_LONG, price=price,
                        volume=sell_vol, confidence=1.0,
                        reason=f"BL四象限调仓: 超配, 减仓至{weight:.1%}",
                    ))


# ── 月度再平衡配置策略基类（AuroraCore 版）──

class _AuroraAllocationBase(Strategy):
    """月度再平衡配置策略公共骨架 — 子类实现 _compute_weights()

    逐点积累多基金净值（只用已到数据，无前视），跨月调用 optimize 算权重，
    然后 emit LONG/CLOSE_LONG 使持仓回到目标权重。
    """
    min_history_days = 60

    def __init__(self):
        super().__init__()
        self.params = {**self.default_params}
        self._hist: dict[str, list[tuple[str, float]]] = {}
        self._cur_date = ""
        self._last_rebalance_month = ""
        self._first_rebalance = True

    def _pool_codes(self) -> list[str]:
        return list(self._hist.keys())

    def on_data(self, data):
        code = getattr(data, "fund_code", "") or getattr(data, "symbol", "")
        nav = getattr(data, "nav", getattr(data, "close", 0))
        date_str = str(getattr(data, "date", ""))
        if not code or not nav or nav <= 0:
            return
        self._hist.setdefault(code, []).append((date_str, nav))

        if date_str != self._cur_date:
            self._cur_date = date_str
            self._check_rebalance(date_str)

    def _check_rebalance(self, date_str: str):
        cur_month = date_str[:7]
        if self._first_rebalance and len(self._hist) >= 3:
            self._first_rebalance = False
            self._last_rebalance_month = cur_month
            self._rebalance(date_str)
            return
        if cur_month != self._last_rebalance_month and self._last_rebalance_month:
            self._last_rebalance_month = cur_month
            self._rebalance(date_str)

    def _compute_weights(self, nav_series: dict[str, list[float]], codes: list[str]) -> dict:
        raise NotImplementedError

    def _apply_vol_targeting(self, weights: dict, nav_series: dict, date_str: str) -> dict:
        """波动率目标叠加层 — 高波降仓、低波加仓

        仅当 params.vol_target > 0 时生效。scale = target / realized，
        上限 max_scale 防止过度杠杆。窗口/上下限可配（vol_targeting_aurora 用）。
        """
        target = self.params.get("vol_target", 0)
        if not target or target <= 0:
            return weights

        window_days = int(self.params.get("window_days", 60))
        max_scale = float(self.params.get("max_scale", 2.0))
        min_scale = float(self.params.get("min_scale", 0.1))

        # 用 window_days 天窗口估算组合实现波动率
        portfolio_navs = []
        for code, w in weights.items():
            if w <= 0:
                continue
            series = nav_series.get(code, [])
            if len(series) >= window_days:
                portfolio_navs.append((np.array(series[-window_days:], dtype=np.float64), w))

        if not portfolio_navs:
            return weights

        # 加权组合收益序列：先逐资产计算收益，避免不同净值基数扭曲波动率。
        ref_len = min(len(s) for s, _ in portfolio_navs)
        combined_returns = sum(
            w * np.diff(s[-ref_len:]) / s[-ref_len:-1]
            for s, w in portfolio_navs
        )
        realized_vol = float(np.std(combined_returns, ddof=1) * np.sqrt(252)) if len(combined_returns) > 1 else 0.0

        if realized_vol <= 0.01:
            return weights

        scale = max(min_scale, min(target / realized_vol, max_scale))
        return {c: w * scale for c, w in weights.items()}

    def _rebalance(self, date_str: str):
        nav_series: dict[str, list[float]] = {}
        for code in self._pool_codes():
            series = [n for d, n in self._hist.get(code, []) if d <= date_str]
            if len(series) >= 20:
                nav_series[code] = series

        valid_codes = list(nav_series.keys())
        if len(valid_codes) < 2:
            return

        weights = self._compute_weights(nav_series, valid_codes)
        if not weights:
            return

        # 波动率目标叠加层
        weights = self._apply_vol_targeting(weights, nav_series, date_str)

        # 总权益
        pv = self.ctx.portfolio_value or 0
        if pv <= 0:
            return

        all_codes = self._hist.keys()
        target_codes = {c for c, w in weights.items() if w > 0}

        # 先清仓不在目标内的
        for code in list(all_codes):
            pos = self.ctx.execution.get_position(code) if self.ctx.execution else None
            if code not in target_codes and pos is not None and pos.volume > 0:
                self.ctx.emit(Signal(
                    id="", strategy=self.name, symbol=code,
                    direction=Direction.CLOSE_LONG, price=0,
                    volume=pos.volume, confidence=1.0,
                    reason=f"{self.name}调仓: 权重为0, 清仓",
                ))

        # 再平衡：调整目标基金的持仓至目标权重
        for code, weight in weights.items():
            if weight <= 0:
                continue
            last_nav = nav_series.get(code, [])
            if not last_nav:
                continue
            price = last_nav[-1]
            if price <= 0:
                continue

            target_amt = pv * weight
            pos = self.ctx.execution.get_position(code) if self.ctx.execution else None
            current_amt = pos.volume * price if pos and pos.volume > 0 else 0
            diff = target_amt - current_amt
            threshold = self.params.get("rebalance_threshold", 0.05) * pv

            if abs(diff) < threshold:
                continue  # 偏离不超阈值，跳过

            if diff > 0:
                self.ctx.emit(Signal(
                    id="", strategy=self.name, symbol=code,
                    direction=Direction.LONG, price=price,
                    volume=diff / price, confidence=1.0,
                    reason=f"{self.name}调仓: 目标权重{weight:.1%}, 当前不足",
                ))
            else:
                sell_vol = min(-diff / price, pos.volume) if pos else 0
                if sell_vol > 0:
                    self.ctx.emit(Signal(
                        id="", strategy=self.name, symbol=code,
                        direction=Direction.CLOSE_LONG, price=price,
                        volume=sell_vol, confidence=1.0,
                        reason=f"{self.name}调仓: 超配, 减仓至{weight:.1%}",
                    ))


# ── Black-Litterman（AuroraCore 版）──

@StrategyRegistry.register("black_litterman_aurora")
class AuroraBlackLitterman(_AuroraAllocationBase):
    """Black-Litterman（AuroraCore 版）— 月度再平衡 + 均衡收益/观点后验"""
    name = "black_litterman_aurora"
    strategy_type = "allocation"
    description = "Black-Litterman: 均衡收益 + 观点后验 + 均值-方差优化, 走统一引擎"
    default_params = {
        "rebalance_freq": "monthly",
        "rebalance_threshold": 0.05,
        "lookback_days": 756,
        "risk_aversion": 2.5,
        "tau": 0.05,
        "max_weight": 0.4,
        "min_weight": 0.05,
        "views": [],
    }

    def _compute_weights(self, nav_series, codes):
        from .strategy.allocation.black_litterman import BlackLittermanStrategy
        strategy = BlackLittermanStrategy(params=dict(self.params))
        result = strategy.optimize(fund_codes=codes, nav_series=nav_series)
        return result.get("weights", {})


# ── 风险平价（AuroraCore 版）──

@StrategyRegistry.register("risk_parity_aurora")
class AuroraRiskParity(_AuroraAllocationBase):
    """风险平价（AuroraCore 版）— 月度再平衡 + 约束风险平价权重"""
    name = "risk_parity_aurora"
    strategy_type = "allocation"
    description = "约束风险平价: Ledoit-Wolf协方差 + SLSQP, 走统一引擎"
    default_params = {
        "rebalance_freq": "monthly",
        "rebalance_threshold": 0.05,
        "lookback_years": 3,
        "shrinkage": "auto",
        "max_weight": 0.4,
        "min_weight": 0.05,
        "min_weight_bond": 0.10,
        "bond_vol_multiplier": "auto",
        "fee_penalty_threshold": 0.02,
    }

    def _compute_weights(self, nav_series, codes):
        from .strategy.allocation.risk_parity import RiskParityStrategy
        strategy = RiskParityStrategy(params=dict(self.params))
        result = strategy.optimize(fund_codes=codes, nav_series=nav_series)
        return result.get("weights", {})


# ── 动态风险平价（AuroraCore 版）──

@StrategyRegistry.register("dynamic_risk_parity_aurora")
class AuroraDynamicRiskParity(_AuroraAllocationBase):
    """动态风险平价 — 每次再平衡只用最近 window_months 个月滚动协方差

    静态 RP 用全程 3 年协方差；动态版截断到滚动窗口，权重跟随近期波动结构
    变化（MDPI 2026：滚动窗口 RP 夏普 1.418 / 回撤 27.7%，均优于静态）。
    求解仍复用 RiskParityStrategy（Ledoit-Wolf + SLSQP）。
    """
    name = "dynamic_risk_parity_aurora"
    strategy_type = "allocation"
    description = "动态风险平价: 最近N月滚动协方差 + Ledoit-Wolf + SLSQP, 走统一引擎"
    default_params = {
        "rebalance_freq": "monthly",
        "rebalance_threshold": 0.05,
        "window_months": 12,        # 滚动窗口（PRD 推荐 12 个月）
        "shrinkage": "auto",
        "max_weight": 0.4,
        "min_weight": 0.05,
        "min_weight_bond": 0.10,
        "bond_vol_multiplier": "auto",
        "fee_penalty_threshold": 0.02,
    }

    def _compute_weights(self, nav_series, codes):
        from .strategy.allocation.risk_parity import RiskParityStrategy
        n = self.params.get("window_months", 12) * 21  # 月→交易日
        truncated = {c: s[-n:] for c, s in nav_series.items() if len(s) >= 60}
        if len(truncated) < 2:
            return {}
        strategy = RiskParityStrategy(params=dict(self.params))
        result = strategy.optimize(fund_codes=codes, nav_series=truncated)
        return result.get("weights", {})


# ── 波动率目标（AuroraCore 版）──

@StrategyRegistry.register("vol_targeting_aurora")
class AuroraVolTargeting(_AuroraAllocationBase):
    """波动率目标 — 等权打底 + 反比缩放钉住目标波动率

    _compute_weights 返回等权，随后基类 _rebalance 调 _apply_vol_targeting
    按 60 日实现波动率反比缩放（vol_target=0.12 覆盖基类默认关闭值）。
    """
    name = "vol_targeting_aurora"
    strategy_type = "allocation"
    description = "波动率目标: 等权打底 + 60日实现波动率反比缩放, 走统一引擎"
    default_params = {
        "rebalance_freq": "monthly",
        "rebalance_threshold": 0.05,
        "vol_target": 0.12,          # 年化目标波动率（默认 12%）
        "max_scale": 2.0,            # 缩放上限，防过度杠杆
        "min_scale": 0.1,            # 缩放下限，保留最低仓位
        "window_days": 60,           # 实现波动率估计窗口
    }

    def _compute_weights(self, nav_series, codes):
        return {c: 1.0 / len(codes) for c in codes}


# ── 趋势跟踪（AuroraCore 版）──

@StrategyRegistry.register("trend_following_aurora")
class AuroraTrendFollowing(_AuroraAllocationBase):
    """时间序列动量 — 各基金独立判断趋势，趋势转弱即保留现金"""
    name = "trend_following_aurora"
    strategy_type = "allocation"
    description = "趋势跟踪: 各基金独立时间序列动量 + 收益阈值, 趋弱保留现金"
    default_params = {
        "rebalance_freq": "monthly",
        "rebalance_threshold": 0.05,
        "lookback_days": 200,
        "buy_threshold": 0.0,
        "min_history_days": 200,
    }
    min_history_days = 200

    def _compute_weights(self, nav_series, codes):
        """对每只基金独立计算窗口收益；不满足阈值的基金权重为0。"""
        lookback = int(self.params.get("lookback_days", 200))
        threshold = float(self.params.get("buy_threshold", 0.0))
        active = {}
        for code in codes:
            series = nav_series.get(code, [])
            if len(series) <= lookback:
                continue
            start, end = float(series[-lookback - 1]), float(series[-1])
            if start > 0 and (end / start - 1.0) > threshold:
                active[code] = 1.0
        if not active:
            # 返回显式零权重，让基类执行清仓，而不是把空权重当作优化失败直接跳过。
            return {code: 0.0 for code in codes}
        weight = 1.0 / len(active)
        return {code: weight for code in active}


# ── 最小方差（AuroraCore 版）──

@StrategyRegistry.register("gmv_aurora")
class AuroraGlobalMinimumVariance(_AuroraAllocationBase):
    """全局最小方差 — 仅最小化组合方差，不预测收益"""
    name = "gmv_aurora"
    strategy_type = "allocation"
    description = "全局最小方差(GMV): Ledoit-Wolf协方差 + 长-only约束优化, 走统一引擎"
    default_params = {
        "rebalance_freq": "monthly",
        "rebalance_threshold": 0.05,
        "lookback_days": 756,
        "max_weight": 0.4,
        "min_weight": 0.02,
    }
    min_history_days = 60

    def _compute_weights(self, nav_series, codes):
        from .strategy.allocation.black_litterman import BlackLittermanStrategy

        lookback = int(self.params.get("lookback_days", 756))
        returns = {}
        for code in codes:
            values = [float(v) for v in nav_series.get(code, []) if v and v > 0][-lookback:]
            if len(values) > 20:
                arr = np.asarray(values, dtype=np.float64)
                returns[code] = np.diff(arr) / arr[:-1]

        valid = list(returns)
        if len(valid) < 2:
            return {}
        n_obs = min(len(r) for r in returns.values())
        if n_obs < 20:
            return {}
        matrix = np.column_stack([returns[c][-n_obs:] for c in valid])
        cov = BlackLittermanStrategy._ledoit_wolf_covariance(matrix)
        n = len(valid)
        max_weight = float(self.params.get("max_weight", 0.4))
        min_weight = float(self.params.get("min_weight", 0.02))
        max_weight = max(max_weight, 1.0 / n)
        min_weight = min(min_weight, 1.0 / n)
        bounds = [(min_weight, max_weight)] * n
        initial = np.full(n, 1.0 / n)

        def is_feasible(weights):
            return (
                np.all(np.isfinite(weights))
                and abs(float(np.sum(weights)) - 1.0) <= 1e-6
                and np.all(weights >= min_weight - 1e-6)
                and np.all(weights <= max_weight + 1e-6)
            )

        try:
            from scipy.optimize import minimize
            result = minimize(
                lambda weights: float(weights @ cov @ weights),
                initial,
                method="SLSQP",
                bounds=bounds,
                constraints={"type": "eq", "fun": lambda weights: np.sum(weights) - 1.0},
                options={"ftol": 1e-10, "maxiter": 1000},
            )
            weights = result.x if result.success and is_feasible(result.x) else initial
        except ImportError:
            weights = initial

        return {code: round(float(weight), 4) for code, weight in zip(valid, weights)}


# ── HRP层次风险平价（AuroraCore 版）──

@StrategyRegistry.register("hrp_aurora")
class AuroraHRP(_AuroraAllocationBase):
    """HRP层次风险平价（AuroraCore 版）— 月度再平衡 + 层次聚类递归二分"""
    name = "hrp_aurora"
    strategy_type = "allocation"
    description = "层次风险平价(HRP): 层次聚类 + 递归二分, 不依赖协方差求逆, 走统一引擎"
    default_params = {
        "rebalance_freq": "monthly",
        "rebalance_threshold": 0.05,
        "max_weight": 0.4,
        "min_weight": 0.02,
        "linkage_method": "ward",
    }

    def _compute_weights(self, nav_series, codes):
        from .strategy.allocation.hrp import HRPStrategy
        strategy = HRPStrategy(params=dict(self.params))
        result = strategy.optimize(fund_codes=codes, nav_series=nav_series)
        return result.get("weights", {})


# ── 最大多元化（AuroraCore 版）──

@StrategyRegistry.register("max_diversification_aurora")
class AuroraMaxDiversification(_AuroraAllocationBase):
    """最大多元化（AuroraCore 版）— 月度再平衡 + 最大化多元化比率"""
    name = "max_diversification_aurora"
    strategy_type = "allocation"
    description = "最大多元化(MDP): 最大化加权平均波动率/组合波动率, 不依赖收益率预测, 走统一引擎"
    default_params = {
        "rebalance_freq": "monthly",
        "rebalance_threshold": 0.05,
        "max_weight": 0.4,
        "min_weight": 0.02,
    }

    def _compute_weights(self, nav_series, codes):
        from .strategy.allocation.max_diversification import MaxDiversificationStrategy
        strategy = MaxDiversificationStrategy(params=dict(self.params))
        result = strategy.optimize(fund_codes=codes, nav_series=nav_series)
        return result.get("weights", {})


# ── 多因子与指数选基（AuroraCore 注册入口）──

class _AuroraSelectionAdapter(Strategy):
    """选基策略的 Aurora 注册入口；历史回测保持静默以避免无时点截面的全库查询。"""
    selection_cls = None

    def __init__(self):
        super().__init__()
        self.params = {**self.default_params}
        self._state: dict = {}

    def screen(self, fund_type="all", top_n=5, params=None):
        scorer = self.selection_cls()
        self._copy_state_to(scorer)
        return scorer.screen(fund_type=fund_type, top_n=top_n, params=params)

    def score(self, fund_type="all", params=None):
        scorer = self.selection_cls()
        self._copy_state_to(scorer)
        return scorer.score(fund_type=fund_type, params=params)

    def _copy_state_to(self, scorer):
        scorer._state.update(getattr(self, "_state", {}))

    def on_data(self, data):
        if self.ctx is None or self.ctx.mode == "backtest":
            return
        scorer = self.selection_cls()
        result = scorer.screen(
            fund_type=self.params.get("fund_type", "all"),
            top_n=int(self.params.get("top_n", 5)),
            params=self.params,
        )
        scores = {row["fund_code"]: float(row["total_score"])
                  for row in result.get("rankings", [])}
        code = getattr(data, "fund_code", "") or getattr(data, "symbol", "")
        nav = getattr(data, "nav", getattr(data, "close", 0))
        if code in scores and nav and nav > 0:
            self.ctx.emit(Signal(
                id="", strategy=self.name, symbol=code, direction=Direction.LONG,
                price=float(nav), volume=1.0, confidence=scores[code],
                reason=f"选基评分={scores[code]:.4f}",
            ))


@StrategyRegistry.register("multi_factor_aurora")
class AuroraMultiFactorSelection(_AuroraSelectionAdapter):
    name = "multi_factor_aurora"
    strategy_type = "selection"
    description = "多因子选基: AuroraCore 统一注册入口"
    default_params = {"fund_type": "all", "top_n": 5}

    @property
    def selection_cls(self):
        from .strategy.selection.multi_factor import MultiFactorSelection
        return MultiFactorSelection


@StrategyRegistry.register("index_selection_aurora")
class AuroraIndexSelection(_AuroraSelectionAdapter):
    name = "index_selection_aurora"
    strategy_type = "selection"
    description = "指数基金五维评分: AuroraCore 统一注册入口"
    default_params = {"fund_type": "index", "top_n": 5}

    @property
    def selection_cls(self):
        from .strategy.selection.index_selection import IndexSelectionStrategy
        return IndexSelectionStrategy



@StrategyRegistry.register("rating_enhanced_aurora")
class AuroraRatingEnhancedSelection(Strategy):
    """评级增强选基的 AuroraCore 信号入口。

    复用既有截面评分器，仅在实盘/模拟模式按固定频率刷新排名；历史回测不调用
    无日期截点的存储层查询，避免引入前视偏差。
    """
    name = "rating_enhanced_aurora"
    strategy_type = "selection"
    description = "评级增强选基: 按固定频率将截面评分转换为 AuroraCore 信号"
    default_params = {
        "fund_type": "all",
        "top_n": 5,
        "evaluation_days": 20,
        "score_threshold": 0.5,
    }

    def __init__(self):
        super().__init__()
        self.params = {**self.default_params}
        self._current_date = ""
        self._day_count = 0
        self._scores: dict[str, float] = {}

    def on_data(self, data):
        if self.ctx is None or self.ctx.mode == "backtest":
            return
        code = getattr(data, "fund_code", "") or getattr(data, "symbol", "")
        nav = getattr(data, "nav", getattr(data, "close", 0))
        date_str = str(getattr(data, "date", ""))
        if not code or not nav or nav <= 0:
            return

        if date_str != self._current_date:
            self._current_date = date_str
            self._day_count += 1
            if (self._day_count - 1) % max(int(self.params["evaluation_days"]), 1) == 0:
                self._refresh_scores()

        score = self._scores.get(code)
        if score is None:
            return
        direction = Direction.LONG if score >= float(self.params["score_threshold"]) else Direction.HOLD
        self.ctx.emit(Signal(
            id="", strategy=self.name, symbol=code, direction=direction,
            price=float(nav), volume=1.0, confidence=score,
            reason=f"评级增强评分={score:.4f}",
        ))

    def _refresh_scores(self):
        from .strategy.selection.rating_enhanced import RatingEnhancedSelection

        scorer = RatingEnhancedSelection()
        result = scorer.screen(
            fund_type=self.params["fund_type"],
            top_n=int(self.params["top_n"]),
        )
        self._scores = {
            row["fund_code"]: float(row["total_score"])
            for row in result.get("rankings", [])
        }

class FundCostModelAdapter(CostModel):
    """Adapt FundCostModel to core.CostModel interface — 薄适配层，逻辑复用 backtest.cost_model"""

    def __init__(self, fund_cost_model=None):
        from .backtest.cost_model import FundCostModel as _Base
        self._model = fund_cost_model or _Base()

    def calc(self, signal: Signal, fill: Fill) -> float:
        from .data.storage import get_fund_meta
        from .core.models import FEE_TIER_COMPAT
        fund_code = signal.symbol
        try:
            meta = get_fund_meta(fund_code) if fund_code else None
        except Exception:
            meta = None
        fund_type = (meta or {}).get("fund_type", "equity")
        # 费率表键为 stock/hybrid/bond/...，DB 可能存新值 equity/index — 归一化
        fund_type = FEE_TIER_COMPAT.get(fund_type, fund_type)
        amount = fill.price * fill.volume
        # 按方向计费：开仓只付申购费，平仓只付赎回费（按持有天数档位）。
        # 管理费/托管费已内扣在 NAV 里，不重复计。
        holding_days = 0
        if hasattr(signal, "extra") and isinstance(signal.extra, dict):
            holding_days = int(signal.extra.get("holding_days", 0) or 0)
        if signal.direction in (Direction.LONG, Direction.SHORT):
            return self._model.get_subscription_fee(fund_type, amount, fund_code=fund_code)
        red_fee_rate = self._model.get_redemption_fee(fund_type, holding_days)
        return red_fee_rate * amount


def demo():
    """基金领域适配自检"""
    from core import BacktestEngine, BacktestConfig

    adapter = FundDomainAdapter()
    assert adapter.name == "fund"
    cost = adapter.create_cost_model({"fund_type": "equity"})
    signal = Signal(id="", strategy="test", symbol="000001",
                    direction=Direction.LONG, price=1.5, volume=10000)
    fill = Fill(order_id="o1", price=1.5, volume=10000)
    c = cost.calc(signal, fill)
    assert c > 0, f"expected cost > 0, got {c}"
    print(f"[fund_adapter] ✅ FundCostModel: 申购1万份@1.5元 = {c} 元")

    adapter.get_available_strategies()
    default = adapter.default_risk_checks()
    assert len(default) > 5, f"expected > 5 checks, got {len(default)}"
    print(f"[fund_adapter] ✅ default_risk_checks: {len(default)} 项")

    # 验证每种类型有不同的检查组合
    types = ["equity", "index", "balanced", "bond", "money", "qdii", "commodity", "fof"]
    lengths = {t: len(adapter.get_risk_checks(t)) for t in types}
    print(f"[fund_adapter] ✅ get_risk_checks: {lengths}")
    assert len(set(lengths.values())) > 3, "类型间检查数应不同"

    print("[fund_adapter] ✅ FundDomainAdapter 接口通过")


if __name__ == "__main__":
    demo()
