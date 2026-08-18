"""FundQuant 策略引擎"""
# 导入所有策略模块以触发注册
from . import base
from . import fusion
from .selection import multi_factor
from .selection import rating_enhanced
from .selection import index_selection
from .allocation import risk_parity
from .allocation import black_litterman
from .allocation import etf_rotation
from .allocation import all_weather
from .allocation import hrp
from .allocation import max_diversification
