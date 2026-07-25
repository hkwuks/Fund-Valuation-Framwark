"""费率模型 — 基金类型差异化 + C类份额 + FOF穿透 + 优先级"""

from __future__ import annotations

from datetime import date
from typing import Dict, Optional, Union, List, Tuple
from ..core.models import CostModelConfig


class FundCostModel:
    """基金费率模型

    费率优先级: 具体基金meta数据 > 类型默认 > 全局默认
    """

    def __init__(self, config: Optional[CostModelConfig] = None):
        self.config = config or CostModelConfig()
        self._discount: float = 0.10  # 默认 1 折

    def set_discount(self, discount: float):
        """设置申购费折扣系数 (0.0 ~ 1.0)"""
        self._discount = max(0.0, min(1.0, discount))

    # ── 历史费率解析 ──

    def _resolve_rate(self, rates: Union[Dict[str, float], Dict[str, List[Tuple[str, float]]]],
                       fund_type: str, as_of: date) -> float:
        """从费率字典中查找指定日期对应的费率"""
        entry = rates.get(fund_type, 0.0)
        if isinstance(entry, (int, float)):
            return float(entry)
        # entry is List[Tuple[str, float]] — sorted by date, find applicable segment
        sorted_periods = sorted(entry, key=lambda x: x[0])
        for period_date_str, fee in reversed(sorted_periods):
            period_date = date.fromisoformat(period_date_str)
            if as_of >= period_date:
                return float(fee)
        return float(sorted_periods[0][1]) if sorted_periods else 0.0

    # ── 申购费率 ──

    def get_subscription_fee(self, fund_type: str, amount: float = 100000.0,
                             fund_code: Optional[str] = None,
                             as_of: Optional[date] = None) -> float:
        """获取申购费率（支持基金级覆盖和历史时间段）"""
        rate = self._type_or_meta(fund_type, fund_code, "subscription_fee",
                                  self._resolve_rate(self.config.subscription_fee_tiers, fund_type, as_of or date.today()))
        if self.config.max_subscription_amount and amount > self.config.max_subscription_amount:
            rate = min(rate, 0.001)  # 大额申购折扣
        return rate * amount * self._discount

    # ── 赎回费率（区分A/C类） ──

    def get_redemption_fee(self, fund_type: str, holding_days: int,
                           is_c_class: bool = False) -> float:
        """获取赎回费率（按持有期，A/C类独立）"""
        if is_c_class:
            if holding_days >= self.config.ac_class_threshold_years * 365:
                return 0.0
            return self.config.c_class_redemption_fee

        discounts = self.config.holding_period_discount
        for period in sorted(discounts.keys()):
            if holding_days < period:
                return discounts[period] / 100.0
        return discounts.get(9999, 0.0)

    # ── C类销售服务费 ──

    def get_c_class_service_fee(self, holding_days: int) -> float:
        return self.config.c_class_service_fee * holding_days / 365

    # ── 管理费 + 托管费 ──

    def get_management_fee(self, fund_type: str, as_of: Optional[date] = None) -> float:
        return self._resolve_rate(self.config.management_fee_rate, fund_type, as_of or date.today())

    def get_custody_fee(self, fund_type: str, as_of: Optional[date] = None) -> float:
        return self._resolve_rate(self.config.custody_fee_rate, fund_type, as_of or date.today())

    # ── 综合交易成本 ──

    def estimate_trade_cost(self, fund_type: str, amount: float,
                            holding_days: int,
                            is_c_class: bool = False,
                            fund_code: Optional[str] = None) -> Dict[str, float]:
        """估算一次交易的综合成本"""
        sub_fee = self.get_subscription_fee(fund_type, amount, fund_code)
        red_fee = self.get_redemption_fee(fund_type, holding_days, is_c_class) * amount
        mgmt_fee = self.get_management_fee(fund_type) * amount * holding_days / 365
        custody_fee = self.get_custody_fee(fund_type) * amount * holding_days / 365
        total = sub_fee + red_fee + mgmt_fee + custody_fee
        return {
            "subscription_fee": round(sub_fee, 2),
            "redemption_fee": round(red_fee, 2),
            "management_fee_accrued": round(mgmt_fee, 2),
            "custody_fee_accrued": round(custody_fee, 2),
            "total_cost": round(total, 2),
            "cost_pct": round(total / amount * 100, 4) if amount > 0 else 0,
        }

    # ── 基金转换费用 ──

    def calc_conversion_cost(self, source_fund_code: str, target_fund_code: str,
                             source_fund_type: str, target_fund_type: str,
                             amount: float) -> dict:
        """计算基金转换/超级转换费用

        Returns:
            {
                "conversion_fee": float,     # total cost
                "redemption_fee": float,     # redemption fee component
                "fee_diff": float,           # subscription fee difference (补差费)
                "source_rate": float,        # source fund's subscription rate
                "target_rate": float,        # target fund's subscription rate
                "path": str,                 # "conversion" or "redeem_buy"
            }
        """
        if source_fund_code == target_fund_code:
            raise ValueError("Cannot convert to same fund")

        if amount <= 0:
            return {
                "conversion_fee": 0.0,
                "redemption_fee": 0.0,
                "fee_diff": 0.0,
                "source_rate": 0.0,
                "target_rate": 0.0,
                "path": "redeem_buy",
            }

        # Check same management company (first 6 chars)
        same_company = source_fund_code[:6] == target_fund_code[:6]

        if not same_company:
            # Different company → redeem_buy path (超级转换)
            redemption_fee = self.get_redemption_fee(source_fund_type, 0) * amount
            subscription_fee = self.get_subscription_fee(target_fund_type, amount)
            total = redemption_fee + subscription_fee
            return {
                "conversion_fee": round(total, 2),
                "redemption_fee": round(redemption_fee, 2),
                "fee_diff": 0.0,
                "source_rate": 0.0,
                "target_rate": 0.0,
                "path": "redeem_buy",
            }

        # Same company → conversion path (基金转换)
        source_rate = self._resolve_rate(
            self.config.subscription_fee_tiers, source_fund_type, date.today()
        )
        target_rate = self._resolve_rate(
            self.config.subscription_fee_tiers, target_fund_type, date.today()
        )

        redemption_fee = self.get_redemption_fee(source_fund_type, 0) * amount
        fee_diff = max(0.0, target_rate - source_rate) * amount * self._discount
        total = redemption_fee + fee_diff

        return {
            "conversion_fee": round(total, 2),
            "redemption_fee": round(redemption_fee, 2),
            "fee_diff": round(fee_diff, 2),
            "source_rate": round(source_rate, 4),
            "target_rate": round(target_rate, 4),
            "path": "conversion",
        }

    # ── A/C份额选择 ──

    @staticmethod
    def should_use_c_class(holding_days: int) -> bool:
        """判断是否应使用C类份额（持有<1.5年用C类更划算）"""
        return holding_days < 547  # 1.5年 * 365

    # ── FOF双重费率穿透 ──

    def fof_effective_fee(self, fund_type: str, underlying_fee: float = 0.01) -> float:
        """FOF穿透计算实际费率 = FOF自身费率 + 底层基金费率"""
        if fund_type != "fof":
            return self.get_management_fee(fund_type)
        fof_fee = self.get_management_fee("fof")
        return fof_fee + underlying_fee  # 双重费率

    # ── 分红税 ──

    def get_dividend_tax(self, holding_days: int) -> float:
        """获取分红税率"""
        if holding_days >= 365:
            return self.config.dividend_tax_holding_over_1y
        return self.config.dividend_tax_holding_under_1y

    # ── 费率优先级: 具体基金 > 类型默认 ──

    def _type_or_meta(self, fund_type: str, fund_code: Optional[str],
                      fee_field: str, default: float) -> float:
        """尝试从基金meta获取具体费率，否则用类型默认"""
        if fund_code:
            try:
                from ..data.storage import get_fund_meta
                meta = get_fund_meta(fund_code)
                if meta:
                    if fee_field == "subscription_fee":
                        val = meta.get("subscription_fee_tiers")
                    elif fee_field == "management_fee":
                        val = meta.get("management_fee")
                    else:
                        val = None
                    if val is not None:
                        return float(val)
            except Exception:
                pass
        return default


fund_cost_model = FundCostModel()
