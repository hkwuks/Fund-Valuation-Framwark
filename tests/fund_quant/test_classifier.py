from backend.fund_quant.data.classifier import (
    classify_fund_for_quant, classify_qdii_subtype,
)
from backend.fund_quant.core.enums import FundType


def test_equtiy_from_piaogu():
    assert classify_fund_for_quant("股票型") == FundType.EQUITY
    assert classify_fund_for_quant("偏股混合型") == FundType.EQUITY


def test_index():
    assert classify_fund_for_quant("被动指数型") == FundType.INDEX
    assert classify_fund_for_quant("增强指数型") == FundType.INDEX
    assert classify_fund_for_quant("ETF") == FundType.INDEX
    assert classify_fund_for_quant("ETF联接") == FundType.INDEX


def test_balanced():
    assert classify_fund_for_quant("债券型-混合二级") == FundType.BALANCED
    assert classify_fund_for_quant("平衡混合型") == FundType.BALANCED
    assert classify_fund_for_quant("灵活配置型") == FundType.BALANCED


def test_bond():
    assert classify_fund_for_quant("债券型-纯债") == FundType.BOND
    assert classify_fund_for_quant("债券型-长债") == FundType.BOND
    assert classify_fund_for_quant("债券型-中短债") == FundType.BOND
    assert classify_fund_for_quant("债券型-混合一级") == FundType.BOND


def test_money():
    assert classify_fund_for_quant("货币型") == FundType.MONEY


def test_qdii():
    assert classify_fund_for_quant("QDII") == FundType.QDII
    assert classify_fund_for_quant("股票型", "某QDII基金") == FundType.QDII


def test_commodity():
    assert classify_fund_for_quant("商品型") == FundType.COMMODITY
    assert classify_fund_for_quant("黄金ETF") == FundType.COMMODITY


def test_fof():
    assert classify_fund_for_quant("FOF") == FundType.FOF
    assert classify_fund_for_quant("基金中基金") == FundType.FOF


def test_default():
    assert classify_fund_for_quant("") == FundType.EQUITY
    assert classify_fund_for_quant("未知类型") == FundType.EQUITY


def test_qdii_subtype():
    assert classify_qdii_subtype("某纳斯达克100指数") == "index"
    assert classify_qdii_subtype("某标普500ETF") == "index"
    assert classify_qdii_subtype("某优选精选混合") == "equity"
    assert classify_qdii_subtype("") == "equity"


def test_qdii_subtype_bond():
    """QDII 债券子类"""
    assert classify_qdii_subtype("某海外债券基金") == "bond"
    assert classify_qdii_subtype("某亚洲债券ETF") == "bond"  # 债券优先于 ETF
