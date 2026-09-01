from backend.fund_quant.core.trading import infer_trading_profile, validate_strategy_funds


def test_etf_rotation_rejects_off_exchange_fund():
    issues = validate_strategy_funds("etf_rotation_aurora", [{
        "fund_code": "000001", "market_type": "off_exchange", "trade_mode": "t1",
    }])
    assert issues and "调整持仓" in issues[0]["reason"]


def test_etf_rotation_accepts_on_exchange_t1_fund():
    assert not validate_strategy_funds("etf_rotation_aurora", [{
        "fund_code": "510300", "market_type": "on_exchange", "trade_mode": "t1",
    }])



def test_profile_uses_etf_quote_as_on_exchange_confirmation():
    profile = infer_trading_profile("510300", "沪深300ETF", "ETF", {"price": 4})
    assert profile["market_type"] == "on_exchange"
    assert profile["trade_mode"] == "t1"
    assert not profile["trading_profile_needs_confirmation"]


def test_profile_marks_off_exchange_rules_for_confirmation():
    profile = infer_trading_profile("000001", "普通基金", "混合型")
    assert profile["market_type"] == "off_exchange"
    assert profile["trading_profile_needs_confirmation"]

    issues = validate_strategy_funds("all_weather_aurora", [{"fund_code": "000001"}])
    assert issues and "补充" in issues[0]["reason"]
