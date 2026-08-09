"""基于 nav_history 的基金因子评价数据源

替换 _DummyFeed，提供真实净值序列和前瞻收益。
解决 multi_factor 策略的 IC 评价跑在假数据上的问题。
"""
from bisect import bisect_right
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


class NavFactorFeed:
    """基金因子评价数据源

    同时实现 EvaluationEngine 需要的两个接口:
      - get_factor_input(symbols, as_of, lookback) → as_of 之前的净值序列
      - get_forward_returns(symbols, from_date, to_date) → 真实前瞻收益
    """

    def __init__(self, horizon: int = 21, get_nav_history=None):
        self.horizon = horizon
        self._get_nav_history = get_nav_history or self._default_nav
        # code -> (sorted_dates, sorted_navs)
        self._cache: Dict[str, Tuple[List[str], List[float]]] = {}

    @staticmethod
    def _default_nav(code: str) -> list:
        from .storage import get_nav_history
        return get_nav_history(code)

    def _series(self, code: str) -> Tuple[List[str], List[float]]:
        if code not in self._cache:
            raw = self._get_nav_history(code) or []
            items = sorted(
                (r["date"], r["nav"]) for r in raw
                if r.get("date") and r.get("nav") and r["nav"] > 0
            )
            self._cache[code] = (
                [d for d, _ in items],
                [v for _, v in items],
            )
        return self._cache[code]

    def get_factor_input(self, symbols: List[str],
                         as_of: date, lookback: int) -> Any:
        """as_of 之前（含）最近 lookback 个净值

        Factors 传单个基金代码 [s]，返回 flat list of floats。
        """
        as_key = as_of.isoformat()
        results = []
        for s in symbols:
            dates, navs = self._series(s)
            idx = bisect_right(dates, as_key)  # 第一个 > as_of 的位置
            vals = navs[:idx]  # 所有 <= as_of 的净值
            results.append(vals[-lookback:] if lookback and lookback > 0 else vals)
        return results[0] if len(symbols) == 1 else results

    def get_forward_returns(self, symbols: List[str],
                            from_date: date, to_date: date) -> Dict[str, float]:
        """真实前瞻收益: nav(from_date) → nav(from_date + horizon 交易日)

        使用实际交易日历（nav_history 中的日期），不假定 calendar 天数。
        """
        result: Dict[str, float] = {}
        from_key = from_date.isoformat()
        for s in symbols:
            dates, navs = self._series(s)
            idx = bisect_right(dates, from_key) - 1  # 最后一个 <= from_date
            if idx < 0:
                continue
            end_idx = idx + self.horizon
            if end_idx >= len(dates):
                continue  # 前瞻窗口超出数据范围
            nav_now = navs[idx]
            nav_fwd = navs[end_idx]
            if nav_now > 0:
                result[s] = nav_fwd / nav_now - 1.0
        return result