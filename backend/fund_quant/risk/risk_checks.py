"""基金领域风控检查 — 继承 core.RiskCheck，作为独立子类

模式说明（你问的"回调或其他方式调用"）：
  - 每个检查是 RiskCheck 子类 → RiskPipeline 遍历调用 check()
  - 有状态的检查（冷却期、持仓期）→ 子类自己维护内部状态
  - 需要 DB 的检查（基金类型、规模）→ 直接 import storage 层
  - 集成外部分析器（风格漂移）→ 组合模式，check() 内调用 detector
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional

from core import RiskCheck, RiskContext, RiskVerdict, RiskLevel, Signal, Direction
from core.style_drift import StyleDriftDetector


# ═══════════════════════════════════════════════════════════════
# 统计层检查
# ═══════════════════════════════════════════════════════════════

class ConfidenceCheck(RiskCheck):
    """信号置信度过滤

    >>> c = ConfidenceCheck(min_confidence=0.6)
    >>> s = Signal(id="", strategy="t", symbol="000001", direction="long", price=1, volume=1, confidence=0.8)
    >>> c.check(RiskContext(), s).passed
    True
    >>> s.confidence = 0.3
    >>> c.check(RiskContext(), s).passed
    False
    """
    name = "confidence"

    def __init__(self, min_confidence: float = 0.6):
        self._min = min_confidence

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None or signal.confidence >= self._min:
            return RiskVerdict(passed=True, check_name=self.name)
        return RiskVerdict(
            passed=False, level=RiskLevel.REJECT,
            reason=f"置信度 {signal.confidence:.2f} < {self._min}",
            check_name=self.name,
        )


class CooldownCheck(RiskCheck):
    """冷却期检查 — 防止重复信号（有状态）

    每个 fund_code 记录信号时间戳，冷却期内再次信号被拦截。
    """
    name = "cooldown"

    def __init__(self, cooldown_days: int = 5):
        self._cooldown = cooldown_days
        self._signal_history: dict[str, list[datetime]] = {}

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None:
            return RiskVerdict(passed=True, check_name=self.name)
        key = signal.symbol
        now = datetime.now()
        history = self._signal_history.get(key, [])
        recent = [t for t in history if (now - t).total_seconds() < self._cooldown * 86400]
        if recent:
            elapsed = (now - recent[-1]).total_seconds() / 86400
            return RiskVerdict(
                passed=False, level=RiskLevel.REJECT,
                reason=f"距上次信号 {elapsed:.1f} 天, 冷却期 {self._cooldown} 天",
                check_name=self.name,
            )
        self._signal_history.setdefault(key, []).append(now)
        return RiskVerdict(passed=True, check_name=self.name)


class MinHoldingCheck(RiskCheck):
    """最小持仓期检查 — 卖出信号需要满足最低持有天数（有状态）"""
    name = "min_holding"

    def __init__(self, min_days: int = 7):
        self._min_days = min_days
        self._holding_start: dict[str, date] = {}

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None:
            return RiskVerdict(passed=True, check_name=self.name)
        # 非卖出信号默认通过
        if signal.direction in ("long", "buy", "BUY", Direction.LONG):
            return RiskVerdict(passed=True, check_name=self.name)

        buy_date = self._holding_start.get(signal.symbol)
        if buy_date is None:
            return RiskVerdict(passed=True, check_name=self.name)
        holding = (date.today() - buy_date).days
        if holding >= self._min_days:
            return RiskVerdict(passed=True, check_name=self.name)
        return RiskVerdict(
            passed=False, level=RiskLevel.REJECT,
            reason=f"持仓 {holding} 天 < 最低 {self._min_days} 天 (惩罚性赎回费)",
            check_name=self.name,
        )

    def register_buy(self, fund_code: str, buy_date: date):
        """供外部调用：记录建仓日期"""
        self._holding_start[fund_code] = buy_date


class FundPositionLimitCheck(RiskCheck):
    """单基金仓位上限检查"""
    name = "fund_position_limit"

    def __init__(self, max_position_pct: float = 0.3):
        self._max_pct = max_position_pct

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None:
            return RiskVerdict(passed=True, check_name=self.name)
        # 非买入信号不检查
        if signal.direction in ("close_long", "sell", "SELL", Direction.CLOSE_LONG):
            return RiskVerdict(passed=True, check_name=self.name)
        # 从 ctx.extra 读取 portfolio 信息
        portfolio = ctx.extra.get("portfolio")
        if not portfolio:
            return RiskVerdict(passed=True, check_name=self.name)
        total = portfolio.get("total_value", 1.0) or 1.0
        weight = sum(portfolio.get("positions", {}).values()) / total
        if weight <= self._max_pct:
            return RiskVerdict(passed=True, check_name=self.name)
        return RiskVerdict(
            passed=False, level=RiskLevel.REJECT,
            reason=f"当前仓位 {weight:.1%} > 上限 {self._max_pct:.0%}",
            check_name=self.name,
        )


# ═══════════════════════════════════════════════════════════════
# 组合层检查
# ═══════════════════════════════════════════════════════════════

class ConcentrationCheck(RiskCheck):
    """行业集中度检查"""
    name = "concentration"

    def __init__(self, max_pct: float = 0.4):
        self._max_pct = max_pct

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        portfolio = ctx.extra.get("portfolio")
        if not portfolio or not portfolio.get("positions"):
            return RiskVerdict(passed=True, check_name=self.name)
        total = portfolio.get("total_value", 1.0) or 1.0
        for code, val in portfolio["positions"].items():
            if val / total > self._max_pct:
                return RiskVerdict(
                    passed=False, level=RiskLevel.REJECT,
                    reason=f"基金 {code} 权重 {val/total:.1%} > 上限 {self._max_pct:.0%}",
                    check_name=self.name,
                )
        return RiskVerdict(passed=True, check_name=self.name)


class LiquidityCheck(RiskCheck):
    """流动性检查（赎回限制）"""
    name = "liquidity"

    def __init__(self, max_redemption_pct: float = 0.2):
        self._max_redemption_pct = max_redemption_pct

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None or signal.direction in ("long", "buy", "BUY", Direction.LONG):
            return RiskVerdict(passed=True, check_name=self.name)
        portfolio = ctx.extra.get("portfolio")
        if not portfolio:
            return RiskVerdict(passed=True, check_name=self.name)
        total = portfolio.get("total_value", 1.0) or 1.0
        pos_val = portfolio.get("positions", {}).get(signal.symbol, 0.0)
        pct = pos_val / total if total > 0 else 0
        if pct <= self._max_redemption_pct:
            return RiskVerdict(passed=True, check_name=self.name)
        return RiskVerdict(
            passed=False, level=RiskLevel.REJECT,
            reason=f"赎回比例 {pct:.1%} > 上限 {self._max_redemption_pct:.0%}",
            check_name=self.name,
        )


class CashReserveCheck(RiskCheck):
    """现金储备检查"""
    name = "cash_reserve"

    def __init__(self, min_cash_pct: float = 0.05):
        self._min_cash_pct = min_cash_pct

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None or signal.direction in ("close_long", "sell", "SELL", Direction.CLOSE_LONG):
            return RiskVerdict(passed=True, check_name=self.name)
        portfolio = ctx.extra.get("portfolio")
        if not portfolio:
            return RiskVerdict(passed=True, check_name=self.name)
        total = portfolio.get("total_value", 1.0) or 1.0
        cash = portfolio.get("cash", 0.0)
        ratio = cash / total if total > 0 else 1.0
        if ratio >= self._min_cash_pct:
            return RiskVerdict(passed=True, check_name=self.name)
        return RiskVerdict(
            passed=False, level=RiskLevel.REJECT,
            reason=f"现金比例 {ratio:.1%} < 要求 {self._min_cash_pct:.0%}",
            check_name=self.name,
        )


class RelatedFundConcentrationCheck(RiskCheck):
    """关联基金集中度 — 同公司持仓不超过上限"""
    name = "related_fund_concentration"

    def __init__(self, max_pct: float = 0.5):
        self._max_pct = max_pct

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None or signal.direction in ("close_long", "sell", "SELL", Direction.CLOSE_LONG):
            return RiskVerdict(passed=True, check_name=self.name)
        portfolio = ctx.extra.get("portfolio")
        if not portfolio:
            return RiskVerdict(passed=True, check_name=self.name)
        total = portfolio.get("total_value", 1.0) or 1.0
        groups: dict[str, float] = {}
        for code, val in portfolio.get("positions", {}).items():
            company = code[:3] if len(code) >= 3 else code
            groups[company] = groups.get(company, 0.0) + val
        for company, val in groups.items():
            pct = val / total
            if pct > self._max_pct:
                return RiskVerdict(
                    passed=False, level=RiskLevel.REJECT,
                    reason=f"基金公司 {company}xxx 持仓 {pct:.1%} > {self._max_pct:.0%}",
                    check_name=self.name,
                )
        return RiskVerdict(passed=True, check_name=self.name)


class ScaleDropCheck(RiskCheck):
    """规模突降（巨额赎回风险）— 需要 DB 访问（回调模式）"""
    name = "scale_drop"

    def __init__(self, min_scale: float = 10_000_000):
        self._min_scale = min_scale

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None:
            return RiskVerdict(passed=True, check_name=self.name)
        try:
            from backend.fund_quant.data.storage import get_fund_meta
            meta = get_fund_meta(signal.symbol)
            scale = meta.get("scale") if meta else None
            if scale is None:
                return RiskVerdict(passed=True, check_name=self.name)
            if scale >= self._min_scale:
                return RiskVerdict(passed=True, check_name=self.name,
                                   reason=f"规模 {scale:.0f} 正常")
            return RiskVerdict(
                passed=False, level=RiskLevel.REJECT,
                reason=f"基金规模 {scale:.0f} < {self._min_scale:.0f}, 清盘风险",
                check_name=self.name,
            )
        except ImportError:
            return RiskVerdict(passed=True, check_name=self.name)


# ═══════════════════════════════════════════════════════════════
# 风格漂移检查 — 组合模式，check() 内调用外部分析器
# ═══════════════════════════════════════════════════════════════

class StyleDriftCheck(RiskCheck):
    """风格漂移 — 包装 StyleDriftDetector（通用核心模块）

    独特模式：check() 内部调用外部分析器，把结果映射为 RiskVerdict。
    detector 可全局共享（单例）或按需创建。
    """
    name = "style_drift"

    def __init__(self, detector: Optional[StyleDriftDetector] = None):
        self._detector = detector or StyleDriftDetector()

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None:
            return RiskVerdict(passed=True, check_name=self.name)
        nav_returns = ctx.extra.get("nav_returns", [])
        factor_returns = ctx.extra.get("factor_returns", {})
        if not nav_returns:
            return RiskVerdict(passed=True, check_name=self.name,
                               reason="无收益率数据，跳过风格漂移")
        result = self._detector.check(nav_returns, factor_returns,
                                      label=signal.symbol)
        if result.passed:
            return RiskVerdict(passed=True, check_name=self.name,
                               reason=result.reason)
        return RiskVerdict(
            passed=False, level=RiskLevel.WARNING,
            reason=result.reason,
            check_name=self.name,
        )


# ═══════════════════════════════════════════════════════════════
# 基金类型差异化规则
# ═══════════════════════════════════════════════════════════════

class FundTypeCheck(RiskCheck):
    """货币基金拦截择时信号 — 仅允许配置策略

    需要 DB 访问查询基金类型。
    """
    name = "fund_type"

    def _get_fund_type(self, fund_code: str) -> str:
        try:
            from backend.fund_quant.data.storage import get_fund_meta
            meta = get_fund_meta(fund_code)
            return (meta.get("fund_type") or "") if meta else ""
        except ImportError:
            return ""

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None:
            return RiskVerdict(passed=True, check_name=self.name)
        fund_type = self._get_fund_type(signal.symbol)
        if fund_type == "money":
            return RiskVerdict(
                passed=False, level=RiskLevel.REJECT,
                reason="货币基金不适用择时信号，仅支持配置策略",
                check_name=self.name,
            )
        return RiskVerdict(passed=True, check_name=self.name)


class BondDrawdownCheck(RiskCheck):
    """债券基金: 回撤阈值更严 (5%)"""
    name = "bond_drawdown"

    def __init__(self, limit: float = 0.05):
        self._limit = limit

    def _get_fund_type(self, fund_code: str) -> str:
        try:
            from backend.fund_quant.data.storage import get_fund_meta
            meta = get_fund_meta(fund_code)
            return (meta.get("fund_type") or "") if meta else ""
        except ImportError:
            return ""

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None:
            return RiskVerdict(passed=True, check_name=self.name)
        if self._get_fund_type(signal.symbol) not in ("bond", "balanced"):
            return RiskVerdict(passed=True, check_name=self.name)
        dd = ctx.max_drawdown
        if dd < self._limit:
            return RiskVerdict(passed=True, check_name=self.name)
        return RiskVerdict(
            passed=False, level=RiskLevel.REJECT,
            reason=f"债券基金回撤 {dd:.1%} > 严格上限 {self._limit:.0%}",
            check_name=self.name,
        )


class QdiiFxRiskCheck(RiskCheck):
    """QDII: 汇率波动率 > 5% 时暂停买入"""
    name = "qdii_fx_risk"

    def __init__(self, fx_vol_limit: float = 0.05):
        self._limit = fx_vol_limit
        self._fx_volatility: float = 0.0

    def set_fx_volatility(self, vol: float):
        self._fx_volatility = vol

    def _get_fund_type(self, fund_code: str) -> str:
        try:
            from backend.fund_quant.data.storage import get_fund_meta
            meta = get_fund_meta(fund_code)
            return (meta.get("fund_type") or "") if meta else ""
        except ImportError:
            return ""

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None:
            return RiskVerdict(passed=True, check_name=self.name)
        if self._get_fund_type(signal.symbol) != "qdii":
            return RiskVerdict(passed=True, check_name=self.name)
        if self._fx_volatility > self._limit:
            return RiskVerdict(
                passed=False, level=RiskLevel.REJECT,
                reason=f"汇率波动率 {self._fx_volatility:.1%} > {self._limit:.0%}",
                check_name=self.name,
            )
        return RiskVerdict(passed=True, check_name=self.name)


class FofUnderlyingCheck(RiskCheck):
    """FOF: 双重费率穿透提示"""
    name = "fof_underlying"

    def _get_fund_type(self, fund_code: str) -> str:
        try:
            from backend.fund_quant.data.storage import get_fund_meta
            meta = get_fund_meta(fund_code)
            return (meta.get("fund_type") or "") if meta else ""
        except ImportError:
            return ""

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None:
            return RiskVerdict(passed=True, check_name=self.name)
        if self._get_fund_type(signal.symbol) != "fof":
            return RiskVerdict(passed=True, check_name=self.name)
        return RiskVerdict(passed=True, check_name=self.name,
                           reason="FOF 双重费率: 建议穿透检查底层基金费率")


class ClosedEndCheck(RiskCheck):
    """封闭式基金: 封闭期禁卖出"""
    name = "closed_end"

    def _get_fund_type(self, fund_code: str) -> str:
        try:
            from backend.fund_quant.data.storage import get_fund_meta
            meta = get_fund_meta(fund_code)
            return (meta.get("fund_type") or "") if meta else ""
        except ImportError:
            return ""

    def check(self, ctx: RiskContext, signal: Signal | None = None) -> RiskVerdict:
        if signal is None:
            return RiskVerdict(passed=True, check_name=self.name)
        # 非卖出信号通过
        if signal.direction in ("long", "buy", "BUY", Direction.LONG):
            return RiskVerdict(passed=True, check_name=self.name)
        fund_type = self._get_fund_type(signal.symbol)
        if fund_type not in ("fof", "bond"):
            return RiskVerdict(passed=True, check_name=self.name)
        try:
            from backend.fund_quant.data.storage import get_fund_meta
            meta = get_fund_meta(signal.symbol)
            if meta and meta.get("established_date"):
                est_str = meta["established_date"]
                import datetime
                est = (datetime.date.fromisoformat(est_str)
                       if isinstance(est_str, str) else est_str)
                days_since = (date.today() - est).days
                if days_since < 365:
                    return RiskVerdict(
                        passed=False, level=RiskLevel.REJECT,
                        reason=f"基金成立 {days_since} 天 < 365天, 封闭期内禁止卖出",
                        check_name=self.name,
                    )
                if 335 <= days_since < 365:
                    return RiskVerdict(
                        passed=False, level=RiskLevel.REJECT,
                        reason=f"封闭期到期前 {365 - days_since} 天, 建议到期后操作",
                        check_name=self.name,
                    )
        except (ImportError, ValueError, TypeError):
            pass
        return RiskVerdict(passed=True, check_name=self.name)
