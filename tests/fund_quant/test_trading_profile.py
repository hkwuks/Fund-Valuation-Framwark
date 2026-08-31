from backend.fund_quant.core.trading import validate_strategy_funds


def test_etf_rotation_rejects_off_exchange_fund():
    issues = validate_strategy_funds("etf_rotation_aurora", [{
        "fund_code": "000001", "market_type": "off_exchange", "trade_mode": "t1",
    }])
    assert issues and "调整持仓" in issues[0]["reason"]


def test_etf_rotation_accepts_on_exchange_t1_fund():
    assert not validate_strategy_funds("etf_rotation_aurora", [{
        "fund_code": "510300", "market_type": "on_exchange", "trade_mode": "t1",
    }])


def test_unknown_trading_profile_is_rejected():
    issues = validate_strategy_funds("all_weather_aurora", [{"fund_code": "000001"}])
    assert issues and "补充" in issues[0]["reason"]
