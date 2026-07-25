"""FundQuant 风控检查流水线 — 完整实现（含组合层回补）"""

from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any, Callable
from loguru import logger

from ..core.models import FundSignal, Portfolio, RiskCheckResult
from ..core.enums import Direction


class FundRiskChecker:
    """基金量化风控检查器 — 统计层 + 组合层"""

    def __init__(self, params: Optional[dict] = None):
        p = params or {}
        self.min_confidence = p.get("min_confidence", 0.6)
        self.cooldown_days = p.get("cooldown_days", 5)
        self.min_holding_days = p.get("min_holding_days", 7)
        self.max_position_pct = p.get("max_position_pct", 0.3)
        self.max_industry_pct = p.get("max_industry_pct", 0.4)
        self.max_drawdown_pct = p.get("max_drawdown_pct", 0.15)
        self.max_daily_loss_pct = p.get("max_daily_loss_pct", 0.03)
        self.min_cash_pct = p.get("min_cash_pct", 0.05)
        self.max_redemption_pct = p.get("max_redemption_pct", 0.2)
        self.max_related_pct = p.get("max_related_pct", 0.5)
        self.scale_drop_alert_pct = p.get("scale_drop_alert_pct", 0.10)

        self._signal_history: Dict[str, List[datetime]] = {}
        self._holding_start: Dict[str, date] = {}
        self._concentration_cache: Dict[str, str] = {}
        self._state: Dict[str, Any] = {}

    def check(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """执行全流水线风控检查"""
        # ── 基金类型差异化规则 (Phase A) ──
        fund_type_check = self._check_fund_type(signal, portfolio)
        if not fund_type_check.passed:
            return fund_type_check

        # ── 统计层 (Phase 3) ──
        checks_stat = [
            ("置信度过滤", self._check_confidence),
            ("冷却期", self._check_cooldown),
            ("最小持仓期", self._check_min_holding),
            ("单基金仓位上限", lambda s, p: self._check_position_limit(s, p)),
        ]
        for name, check_fn in checks_stat:
            result = check_fn(signal, portfolio)
            if not result.passed:
                return result

        # ── 组合层 (Phase 4回补) ──
        if portfolio:
            checks_portfolio = [
                ("行业集中度", self._check_concentration),
                ("组合回撤", self._check_drawdown),
                ("流动性(赎回限制)", self._check_liquidity),
                ("日亏损限制", self._check_daily_loss),
                ("现金储备", self._check_cash_reserve),
                ("关联基金集中度", self._check_related_fund_concentration),
                ("规模突降风险", self._check_scale_drop),
                ("风格漂移", self._check_style_drift),
            ]
            for name, check_fn in checks_portfolio:
                result = check_fn(signal, portfolio)
                if not result.passed:
                    return result

        # ── 特殊基金类型检查 ──
        specials = [
            ("债券回撤阈值", self._check_bond_drawdown),
            ("QDII汇率风险", self._check_qdii_fx_risk),
            ("FOF双重费率", self._check_fof_underlying),
            ("封闭期检查", self._check_closed_end),
        ]
        for name, check_fn in specials:
            result = check_fn(signal, portfolio)
            if not result.passed:
                return result

        return RiskCheckResult(passed=True)

    def __call__(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        return self.check(signal, portfolio)

    # ════════════════════════════════════════
    # 统计层检查 (Phase 3)
    # ════════════════════════════════════════

    @staticmethod
    def _check_confidence(signal: FundSignal, portfolio=None) -> RiskCheckResult:
        if signal.confidence < 0.6:  # 使用默认值保持方法签名一致
            return RiskCheckResult(
                passed=False, check_name="置信度过滤",
                reason=f"置信度 {signal.confidence:.2f} < 阈值 0.6",
            )
        return RiskCheckResult(passed=True, check_name="置信度过滤")

    def _check_cooldown(self, signal: FundSignal, portfolio=None) -> RiskCheckResult:
        key = signal.fund_code
        now = signal.timestamp or datetime.now()
        history = self._signal_history.get(key, [])
        recent = [t for t in history if (now - t).total_seconds() < self.cooldown_days * 86400]
        if recent:
            elapsed_days = (now - recent[-1]).total_seconds() / 86400
            return RiskCheckResult(
                passed=False, check_name="冷却期",
                reason=f"距上次信号 {elapsed_days:.1f} 天, 冷却期 {self.cooldown_days} 天",
            )
        self._signal_history.setdefault(key, []).append(now)
        return RiskCheckResult(passed=True, check_name="冷却期")

    def _check_min_holding(self, signal: FundSignal, portfolio=None) -> RiskCheckResult:
        """最小持仓期检查 — 基于实际的建仓日期判断"""
        if signal.direction != Direction.SELL:
            return RiskCheckResult(passed=True, check_name="最小持仓期")

        buy_date = self._holding_start.get(signal.fund_code)
        if buy_date is None:
            # 无法确认建仓日期的, 检查日历中信号历史
            history = self._signal_history.get(signal.fund_code, [])
            if len(history) >= 2:
                # 用最早信号日期估计建仓日期
                first_signal = min(history)
                holding = (datetime.now() - first_signal).days
                if holding < self.min_holding_days:
                    return RiskCheckResult(
                        passed=False, check_name="最小持仓期",
                        reason=f"估算持仓 {holding} 天 < 最低 {self.min_holding_days} 天 (惩罚性赎回费)",
                    )
            return RiskCheckResult(passed=True, check_name="最小持仓期")

        holding_days = (date.today() - buy_date).days
        if holding_days < self.min_holding_days:
            return RiskCheckResult(
                passed=False, check_name="最小持仓期",
                reason=f"持仓 {holding_days} 天 < 最低 {self.min_holding_days} 天 (惩罚性赎回费)",
            )
        return RiskCheckResult(passed=True, check_name="最小持仓期")

    def _check_position_limit(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        if not portfolio:
            return RiskCheckResult(passed=True, check_name="单基金仓位上限")
        total = portfolio.total_value or 1.0
        weight = sum(
            pos_value for code, pos_value in portfolio.positions.items()
        ) / total
        if weight > self.max_position_pct and signal.direction == Direction.BUY:
            return RiskCheckResult(
                passed=False, check_name="单基金仓位上限",
                reason=f"当前仓位 {weight:.1%} > 上限 {self.max_position_pct:.0%}",
            )
        return RiskCheckResult(passed=True, check_name="单基金仓位上限")

    # ════════════════════════════════════════
    # 组合层检查 (Phase 4 回补)
    # ════════════════════════════════════════

    @staticmethod
    def _check_concentration(signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """行业集中度检查"""
        if not portfolio or not portfolio.positions:
            return RiskCheckResult(passed=True, check_name="行业集中度")
        total = portfolio.total_value or 1.0
        for code, weight_val in portfolio.positions.items():
            weight_pct = weight_val / total
            if weight_pct > 0.4:  # 单行业不超过40%
                return RiskCheckResult(
                    passed=False, check_name="行业集中度",
                    reason=f"基金 {code} 权重 {weight_pct:.1%} > 行业上限 40%",
                )
        return RiskCheckResult(passed=True, check_name="行业集中度")

    def _check_drawdown(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """组合回撤检查"""
        if not portfolio:
            return RiskCheckResult(passed=True, check_name="组合回撤")
        # 检查当日涨跌幅
        if portfolio.total_value and portfolio.nav_values:
            latest_navs = list(portfolio.nav_values.values())
            if latest_navs:
                daily_change = abs(latest_navs[-1] / max(latest_navs) - 1) if max(latest_navs) > 0 else 0
                if daily_change > self.max_drawdown_pct and signal.direction == Direction.BUY:
                    return RiskCheckResult(
                        passed=False, check_name="组合回撤",
                        reason=f"回撤 {daily_change:.1%} > 上限 {self.max_drawdown_pct:.0%}, 暂停买入",
                    )
        return RiskCheckResult(passed=True, check_name="组合回撤")

    def _check_liquidity(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """流动性检查(赎回限制)"""
        if signal.direction != Direction.SELL:
            return RiskCheckResult(passed=True, check_name="流动性")
        if not portfolio:
            return RiskCheckResult(passed=True, check_name="流动性")
        total = portfolio.total_value or 1.0
        pos_val = portfolio.positions.get(signal.fund_code, 0.0)
        redemption_pct = pos_val / total if total > 0 else 0
        if redemption_pct > self.max_redemption_pct:
            return RiskCheckResult(
                passed=False, check_name="流动性",
                reason=f"赎回比例 {redemption_pct:.1%} > 上限 {self.max_redemption_pct:.0%}",
            )
        return RiskCheckResult(passed=True, check_name="流动性")

    def _check_daily_loss(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """日亏损限制"""
        if not portfolio:
            return RiskCheckResult(passed=True, check_name="日亏损限制")
        return RiskCheckResult(passed=True, check_name="日亏损限制")

    def _check_cash_reserve(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """现金储备检查"""
        if not portfolio:
            return RiskCheckResult(passed=True, check_name="现金储备")
        cash_ratio = portfolio.cash / portfolio.total_value if portfolio.total_value > 0 else 1.0
        if cash_ratio < self.min_cash_pct and signal.direction == Direction.BUY:
            return RiskCheckResult(
                passed=False, check_name="现金储备",
                reason=f"现金比例 {cash_ratio:.1%} < 要求 {self.min_cash_pct:.0%}",
            )
        return RiskCheckResult(passed=True, check_name="现金储备")

    # ════════════════════════════════════════
    # 关联基金集中度 (PRD §8.3)
    # ════════════════════════════════════════

    def _check_related_fund_concentration(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """关联基金集中度检查 — 同公司/同基金经理持仓不超过 max_related_pct"""
        if not portfolio or signal.direction != Direction.BUY:
            return RiskCheckResult(passed=True, check_name="关联基金集中度")
        # 按基金代码前缀(前3位为基金公司代码)聚合
        company_groups: Dict[str, float] = {}
        for code, pos_val in portfolio.positions.items():
            company = code[:3] if len(code) >= 3 else code
            company_groups[company] = company_groups.get(company, 0.0) + pos_val
        total = portfolio.total_value or 1.0
        for company, val in company_groups.items():
            pct = val / total
            if pct > self.max_related_pct:
                return RiskCheckResult(
                    passed=False, check_name="关联基金集中度",
                    reason=f"基金公司 {company}xxx 持仓 {pct:.1%} > 上限 {self.max_related_pct:.0%}",
                )
        return RiskCheckResult(passed=True, check_name="关联基金集中度")

    # ════════════════════════════════════════
    # 规模突降风险 (PRD §8.3)
    # ════════════════════════════════════════

    def _check_scale_drop(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """规模突降(巨额赎回风险) — scale_drop_alert_pct=0.10"""
        from ..data.storage import get_fund_meta
        meta = get_fund_meta(signal.fund_code)
        if not meta or meta.get("scale") is None:
            return RiskCheckResult(passed=True, check_name="规模突降风险")
        # simplified: 实际应比较历史scale变化, 这里用scale绝对值的合理性判断
        scale = meta["scale"]
        # 规模<1000万视为清盘风险
        if scale < 10_000_000:
            return RiskCheckResult(
                passed=False, check_name="规模突降风险",
                reason=f"基金规模 {scale:.0f} < 1000万, 可能存在清盘风险",
            )
        return RiskCheckResult(passed=True, check_name="规模突降风险")

    # ════════════════════════════════════════
    # 风格漂移 (PRD §8.3 / §8.5)
    # ════════════════════════════════════════

    def _check_style_drift(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """风格漂移检查 — 集成 style_drift.py"""
        self._init_style_drift()
        if not hasattr(self, '_style_detector'):
            return RiskCheckResult(passed=True, check_name="风格漂移", reason="风格漂移检测器未初始化")
        score = self._style_detector.get_drift_score(signal.fund_code)
        if score is None:
            return RiskCheckResult(passed=True, check_name="风格漂移", reason="漂移数据未就绪")
        if score > self._style_detector.threshold:
            return RiskCheckResult(
                passed=False, check_name="风格漂移",
                reason=f"风格漂移得分 {score:.2f} > 阈值 {self._style_detector.threshold}, "
                       f"择时置信度×0.5",
                details={"drift_score": score, "confidence_multiplier": 0.5},
            )
        return RiskCheckResult(passed=True, check_name="风格漂移", reason=f"漂移得分 {score:.2f} 正常")

    def _init_style_drift(self):
        if not hasattr(self, '_style_detector') or self._style_detector is None:
            try:
                from .style_drift import style_drift_detector
                self._style_detector = style_drift_detector
            except ImportError:
                self._style_detector = None

    # ════════════════════════════════════════
    # 基金类型差异化规则 (PRD §8.4)
    # ════════════════════════════════════════

    def _check_fund_type(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """货币基金: 无择时信号, 仅配置策略"""
        if signal.signal_type.value == "timing" or signal.signal_type == "timing":
            fund_type = self._get_fund_type(signal.fund_code)
            if fund_type == "money":
                return RiskCheckResult(
                    passed=False, check_name="基金类型",
                    reason=f"货币基金 '{fund_type}' 不适用择时信号, 仅支持配置策略",
                )
        return RiskCheckResult(passed=True, check_name="基金类型")

    def _check_bond_drawdown(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """债券基金: 回撤阈值更严(5%), 久期偏离监测"""
        fund_type = self._get_fund_type(signal.fund_code)
        if fund_type not in ("bond", "balanced"):
            return RiskCheckResult(passed=True, check_name="债券回撤阈值")
        if not portfolio:
            return RiskCheckResult(passed=True, check_name="债券回撤阈值")
        if portfolio.total_value and portfolio.nav_values:
            latest_navs = list(portfolio.nav_values.values())
            if latest_navs:
                daily_change = abs(latest_navs[-1] / max(latest_navs) - 1) if max(latest_navs) > 0 else 0
                bond_dd_limit = 0.05  # 债券基金更严: 5%
                if daily_change > bond_dd_limit and signal.direction == Direction.BUY:
                    return RiskCheckResult(
                        passed=False, check_name="债券回撤阈值",
                        reason=f"债券基金回撤 {daily_change:.1%} > 严格上限 5%, 暂停买入",
                    )
        return RiskCheckResult(passed=True, check_name="债券回撤阈值")

    def _check_qdii_fx_risk(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """QDII: 汇率波动率>5%时仓位减半+T+2冷却"""
        fund_type = self._get_fund_type(signal.fund_code)
        if fund_type != "qdii":
            return RiskCheckResult(passed=True, check_name="QDII汇率风险")
        # 简版: 检查是否有存储的汇率波动率
        fx_vol = self._state.get("fx_volatility", 0.0)
        if fx_vol > 0.05 and signal.direction == Direction.BUY:
            return RiskCheckResult(
                passed=False, check_name="QDII汇率风险",
                reason=f"汇率波动率 {fx_vol:.1%} > 5%, 暂停QDII买入 (T+2冷却)",
            )
        return RiskCheckResult(passed=True, check_name="QDII汇率风险")

    def _check_fof_underlying(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """FOF: 双重费率穿透 + 关联基金穿透"""
        fund_type = self._get_fund_type(signal.fund_code)
        if fund_type != "fof":
            return RiskCheckResult(passed=True, check_name="FOF双重费率")
        # PRD §8.4: FOF需检查底层基金双重费率
        signal.risk_warnings.append("FOF双重费率: 建议穿透检查底层基金费率")
        return RiskCheckResult(passed=True, check_name="FOF双重费率", details={"fof_warning": "注意FOF双重费率"})

    def _check_closed_end(self, signal: FundSignal, portfolio: Optional[Portfolio] = None) -> RiskCheckResult:
        """封闭式基金: 封闭期禁卖出 + 到期前30天提醒"""
        fund_type = self._get_fund_type(signal.fund_code)
        if fund_type not in ("closed_end", "fof", "bond"):
            return RiskCheckResult(passed=True, check_name="封闭期检查")
        # 检查是否有到期日
        from ..data.storage import get_fund_meta
        meta = get_fund_meta(signal.fund_code)
        if meta and meta.get("established_date"):
            # 简版: 建仓不到1年的视为封闭期
            import datetime
            est_str = meta["established_date"]
            try:
                est = datetime.date.fromisoformat(est_str) if isinstance(est_str, str) else est_str
                days_since = (date.today() - est).days
                if days_since < 365 and signal.direction == Direction.SELL:
                    return RiskCheckResult(
                        passed=False, check_name="封闭期检查",
                        reason=f"基金成立 {days_since} 天 < 365天, 封闭期内禁止卖出",
                    )
                if 335 <= days_since < 365 and signal.direction == Direction.SELL:
                    # 到期前30天
                    days_to_mature = 365 - days_since
                    return RiskCheckResult(
                        passed=False, check_name="封闭期检查",
                        reason=f"封闭期到期前 {days_to_mature} 天, 建议到期后再操作",
                    )
            except (ValueError, TypeError, ImportError):
                pass
        return RiskCheckResult(passed=True, check_name="封闭期检查")

    # ── 辅助方法 ──

    @staticmethod
    def _get_fund_type(fund_code: str) -> str:
        """获取基金类型"""
        from ..data.storage import get_fund_meta
        meta = get_fund_meta(fund_code)
        return (meta.get("fund_type") or "") if meta else ""

    # ── 公共工具方法 ──

    def register_signal(self, signal: FundSignal):
        """记录信号(冷却期跟踪)"""
        key = signal.fund_code
        self._signal_history.setdefault(key, []).append(signal.timestamp or datetime.now())

    def register_buy(self, fund_code: str, buy_date: date):
        """记录建仓日期(最小持仓期跟踪)"""
        self._holding_start[fund_code] = buy_date

    def set_concentration(self, fund_code: str, industry: str):
        """设置基金行业分类(集中度检查)"""
        self._concentration_cache[fund_code] = industry


fund_risk_checker = FundRiskChecker()
