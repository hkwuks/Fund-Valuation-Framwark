"""FundQuant 策略引擎"""
# 导入所有策略模块以触发注册
from . import base
from . import fusion
from .timing import valuation_deviation
from .timing import momentum
from .timing import interest_rate
from .timing import fx_momentum
from .timing import smart_dca
from .timing import commodity
from .timing import credit_spread
from .selection import multi_factor
from .selection import rating_enhanced
from .selection import index_selection
from .allocation import risk_parity
from .allocation import black_litterman
from .allocation import etf_rotation
from .allocation import all_weather
