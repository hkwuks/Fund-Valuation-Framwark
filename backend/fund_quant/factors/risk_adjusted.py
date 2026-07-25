"""基金风险调整收益因子"""
from datetime import date
from typing import Any
import numpy as np

from backend.core.factor.factor import Factor
from backend.core.factor.models import FactorMeta


class SharpeRatioFactor(Factor):
    """夏普比率因子"""
    meta = FactorMeta(
        name="sharpe_ratio", display_name="夏普比率",
        category="risk_adjusted", domain="fund",
        description="年化超额收益 / 年化波动率",
        direction=1, params={"lookback": 252, "risk_free": 0.02},
        formula="(mean(returns)*252 - r_f) / (std(returns)*sqrt(252))",
        fund_types=["equity", "qdii"],
    )

    def compute(self, symbols: list[str], as_of: date,
                lookback: int, data: Any) -> dict[str, float]:
        result = {}
        for s in symbols:
            try:
                navs = data.get_factor_input([s], as_of, lookback)
                if not navs or not isinstance(navs, (list, tuple)):
                    continue
                arr = np.array([n for n in navs if isinstance(n, (int, float)) and n > 0])
                if len(arr) < 60:
                    continue
                returns = np.diff(arr) / arr[:-1]
                if len(returns) < 20:
                    continue
                ann_ret = float(np.mean(returns) * 252)
                ann_vol = float(np.std(returns, ddof=1) * np.sqrt(252))
                r_f = self.meta.params.get("risk_free", 0.02)
                sharpe = (ann_ret - r_f) / max(ann_vol, 1e-6)
                result[s] = sharpe
            except Exception:
                continue
        return result


class InfoRatioFactor(Factor):
    """信息比率因子"""
    meta = FactorMeta(
        name="info_ratio", display_name="信息比率",
        category="risk_adjusted", domain="fund",
        description="超额收益 / 跟踪误差",
        direction=1, params={"lookback": 252},
        formula="超额收益均值 / 超额收益标准差 × sqrt(252)",
        fund_types=["equity", "qdii"],
    )

    def compute(self, symbols, as_of, lookback, data):
        sharpe = SharpeRatioFactor()
        sharpe.meta.params = self.meta.params
        return sharpe.compute(symbols, as_of, lookback, data)


class CaptureRatioFactor(Factor):
    """上涨/下跌捕获比因子"""
    meta = FactorMeta(
        name="capture_ratio", display_name="捕获比",
        category="risk_adjusted", domain="fund",
        description="牛市捕获率 / 熊市捕获率",
        direction=1, params={"lookback": 504},
        formula="up_capture / down_capture",
        fund_types=["equity", "qdii"],
    )

    def compute(self, symbols, as_of, lookback, data):
        result = {}
        for s in symbols:
            try:
                navs = data.get_factor_input([s], as_of, lookback)
                if not navs or len(navs) < 120:
                    continue
                arr = np.array([n for n in navs if isinstance(n, (int, float)) and n > 0])
                returns = np.diff(arr) / arr[:-1]
                if len(returns) < 120:
                    continue
                up = returns[returns > 0]
                down = returns[returns < 0]
                up_avg = float(np.mean(up)) if len(up) > 0 else 0
                down_avg = abs(float(np.mean(down))) if len(down) > 0 else 1e-6
                if down_avg > 1e-6:
                    result[s] = up_avg / down_avg
                else:
                    result[s] = 0.0
            except Exception:
                continue
        return result
