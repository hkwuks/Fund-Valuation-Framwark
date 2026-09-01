"""基金交易属性与策略适用性校验。"""

from typing import Any

from backend.models import MarketType

SUPPORTED_MARKET_TYPES = {"on_exchange", "off_exchange"}
SUPPORTED_TRADE_MODES = {"t0", "t1", "t2"}

# 规则故意保守：交易属性未知时不让回测产生看似精确的错误结果。
STRATEGY_RULES: dict[str, dict[str, set[str]]] = {
    "etf_rotation_aurora": {
        "market_types": {"on_exchange"},
        "trade_modes": {"t0", "t1"},
    },
    "all_weather_aurora": {
        "market_types": SUPPORTED_MARKET_TYPES,
        "trade_modes": SUPPORTED_TRADE_MODES,
    },
}
DEFAULT_RULE = {
    "market_types": SUPPORTED_MARKET_TYPES,
    "trade_modes": SUPPORTED_TRADE_MODES,
}


def infer_trading_profile(fund_code: str, fund_name: str = "", fund_type: str = "",
                          etf_quote: dict[str, Any] | None = None) -> dict[str, Any]:
    """用现有东方财富基金/F10/ETF行情接口交叉确认交易属性。"""
    is_etf = bool(etf_quote) or "ETF" in f"{fund_name}{fund_type}".upper()
    market_type = "on_exchange" if is_etf else MarketType.UNKNOWN.value
    if not is_etf:
        from backend.market_data import determine_market_type
        market_type = determine_market_type(fund_code, fund_name, fund_type).value

    if market_type == "on_exchange":
        return {
            "market_type": market_type, "trade_mode": "t1",
            "subscription_confirm_days": 0, "redemption_confirm_days": 0,
            "cash_arrival_days": 0,
            "trading_profile_source": "东方财富基金资料 + ETF行情",
            "trading_profile_confidence": 0.95 if is_etf else 0.8,
            "trading_profile_needs_confirmation": not is_etf,
        }
    if market_type == "off_exchange":
        is_qdii = "QDII" in f"{fund_name}{fund_type}".upper()
        return {
            "market_type": market_type, "trade_mode": "t2" if is_qdii else "t1",
            "subscription_confirm_days": 2 if is_qdii else 1,
            "redemption_confirm_days": 2 if is_qdii else 1,
            "cash_arrival_days": 7 if is_qdii else 3,
            "trading_profile_source": "东方财富基金资料/F10",
            "trading_profile_confidence": 0.7,
            "trading_profile_needs_confirmation": True,
        }
    return {
        "market_type": "unknown", "trade_mode": None,
        "subscription_confirm_days": None, "redemption_confirm_days": None,
        "cash_arrival_days": None,
        "trading_profile_source": "未识别",
        "trading_profile_confidence": 0.0,
        "trading_profile_needs_confirmation": True,
    }


def validate_strategy_funds(strategy_name: str, funds: list[dict[str, Any]]) -> list[dict[str, str]]:
    """返回所有不适用基金及明确调整原因；空列表表示可运行。"""
    rule = STRATEGY_RULES.get(strategy_name, DEFAULT_RULE)
    issues: list[dict[str, str]] = []
    for fund in funds:
        code = str(fund.get("fund_code", ""))
        market_type = fund.get("market_type", "unknown")
        trade_mode = fund.get("trade_mode")
        if fund.get("trading_profile_needs_confirmation", False):
            issues.append({"fund_code": code, "reason": "交易属性来源不足，请在基金管理中确认场内/场外与T+0/T+1/T+2"})
            continue
        if market_type not in SUPPORTED_MARKET_TYPES:
            issues.append({"fund_code": code, "reason": "市场类型未知，请在基金管理中补充场内/场外"})
            continue
        if trade_mode not in SUPPORTED_TRADE_MODES:
            issues.append({"fund_code": code, "reason": "交易时序未知，请在基金管理中补充T+0/T+1/T+2"})
            continue
        if market_type not in rule["market_types"]:
            issues.append({"fund_code": code, "reason": f"策略不支持{market_type}基金，请调整持仓或更换策略"})
        elif trade_mode not in rule["trade_modes"]:
            issues.append({"fund_code": code, "reason": f"策略不支持{trade_mode}交易时序，请调整持仓或更换策略"})
    return issues


def demo() -> None:
    assert validate_strategy_funds("etf_rotation_aurora", [
        {"fund_code": "000001", "market_type": "off_exchange", "trade_mode": "t1"},
    ])
    assert not validate_strategy_funds("etf_rotation_aurora", [
        {"fund_code": "510300", "market_type": "on_exchange", "trade_mode": "t1"},
    ])


if __name__ == "__main__":
    demo()
    print("[trading] ✅ 基金交易属性校验通过")
