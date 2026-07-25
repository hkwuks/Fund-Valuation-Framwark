"""基金领域适配 — FundDomainAdapter + FundCostModel"""
from __future__ import annotations

from datetime import date
from typing import Optional

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
        "sub_fee": 0.015, "mgmt_fee": 0.015, "custody_fee": 0.0025,
        "c_class_service_fee": 0.004,
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
        """计算单笔交易综合成本

        开仓: 申购费 + 管理费+托管费（按180日估计）
        平仓: 赎回费 + 分红税 + 管理费+托管费（按180日估计）
        FOF: 额外穿透底层费率
        """
        amount = fill.price * fill.volume
        est_days = 180  # 估计持有期

        if signal.direction in (Direction.LONG, Direction.SHORT):
            # 开仓: 申购费 + 管理/托管费（前半段）
            sub = self._get_subscription(amount)
            daily = self._get_annual_carry(amount) * est_days / 365 / 2
            return round(sub + daily, 4)
        else:
            # 平仓: 赎回费 + 管理/托管费（后半段）+ 分红税
            red = self._get_redemption(amount, est_days)
            daily = self._get_annual_carry(amount) * est_days / 365 / 2
            div_tax = self._get_dividend_tax(amount, est_days)
            return round(red + daily + div_tax, 4)

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
        return {
            "momentum_fund": AdpatedMomentumFund,
        }

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


# ── 适配后的基金动量策略 ──

@StrategyRegistry.register("momentum_fund")
class AdpatedMomentumFund(Strategy):
    """动量择时策略 — 移植自 fund_quant.strategy.timing.momentum.MomentumStrategy
    TSMOM 多周期融合 + 反转修正
    """
    name = "momentum_fund"
    strategy_type = "timing"
    description = "基于净值时间序列动量的择时策略"
    default_params = {
        "momentum_periods": [20, 60, 120],
        "weights": [0.5, 0.3, 0.2],
        "skip_days": 5,
        "buy_threshold": 0.02,
        "sell_threshold": -0.02,
    }
    min_history_days = 120

    def __init__(self):
        super().__init__()
        self._nav_history: list[float] = []
        self._fund_code = ""

    def on_data(self, data):
        """接收 FundNavPoint 或 Bar，提取净值"""
        nav = data.nav if hasattr(data, "nav") else data.close
        if not self._fund_code:
            self._fund_code = getattr(data, "fund_code", "") or getattr(data, "symbol", "")
        self._nav_history.append(nav)

        max_period = max(self.params.get("momentum_periods", [120]))
        if len(self._nav_history) < max_period + self.params.get("skip_days", 5):
            return

        arr = self._nav_history[:]
        returns = [(arr[i] - arr[i - 1]) / arr[i - 1] for i in range(1, len(arr))]

        skip = self.params.get("skip_days", 5)
        periods = self.params.get("momentum_periods", [20, 60, 120])
        weights = self.params.get("weights", [0.5, 0.3, 0.2])

        scores = []
        for n, w in zip(periods, weights):
            if len(returns) < n + skip:
                scores.append(0.0)
                continue
            period_rets = returns[-(n + skip):-skip] if skip > 0 else returns[-n:]
            scores.append(sum(period_rets))

        total_w = sum(weights[:len(scores)])
        if total_w <= 0:
            return

        weighted = sum(w * s for w, s in zip(weights, scores)) / total_w
        buy_th = self.params.get("buy_threshold", 0.02)
        sell_th = self.params.get("sell_threshold", -0.02)

        if weighted > buy_th:
            self.ctx.emit(Signal(
                id="", strategy=self.name, symbol=self._fund_code,
                direction=Direction.LONG,
                price=nav, volume=1,
                confidence=min(abs(weighted) / (buy_th * 2), 1.0),
                reason=f"动量得分 {weighted:.4f} > {buy_th}",
            ))
        elif weighted < sell_th:
            # ponytail: 基金做空用 CLOSE_LONG 表示减仓，Phase 3 完善语义
            self.ctx.emit(Signal(
                id="", strategy=self.name, symbol=self._fund_code,
                direction=Direction.CLOSE_LONG,
                price=nav, volume=1,
                confidence=min(abs(weighted) / (abs(sell_th) * 2), 1.0),
                reason=f"动量得分 {weighted:.4f} < {sell_th}",
            ))


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
