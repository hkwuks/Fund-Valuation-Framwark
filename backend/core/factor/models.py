"""FactorEngine 数据模型"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(frozen=True)
class FactorMeta:
    """因子元数据——注册时确定，不可变"""
    name: str
    display_name: str
    category: str
    domain: str
    description: str
    direction: int          # +1 值越大越好, -1 值越小越好
    params: dict = field(default_factory=dict)
    formula: str = ""
    min_history_days: int = 60
    reference: str = ""
    fund_types: list[str] = field(default_factory=list)  # 空=全部适用


@dataclass
class FactorSnapshot:
    """单期因子截面快照"""
    date: date
    factor_name: str
    values: dict[str, float]       # {symbol: value}
    meta: FactorMeta
    n_valid: int
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ICSnapshot:
    """单期 IC 快照"""
    ic: float = 0.0
    rank_ic: float = 0.0
    p_value: float = 1.0
    n: int = 0
    sign_accuracy: float = 0.5


@dataclass
class GroupReturnResult:
    """分组收益结果"""
    group_means: list[float] = field(default_factory=lambda: [0.0] * 5)
    long_short_spread: float = 0.0
    long_short_t_stat: float = 0.0
    long_short_p_value: float = 1.0
    monotonicity_score: float = 0.0


@dataclass
class FamaMacBethResult:
    """Fama-MacBeth 两步法结果"""
    beta_mean: float = 0.0
    se: float = 0.0
    t_stat: float = 0.0
    p_value: float = 1.0


@dataclass
class FactorEvaluationReport:
    """因子评价完整报告"""
    factor_name: str = ""
    domain: str = ""
    category: str = ""
    evaluation_period: tuple[date, date] = (date(2000, 1, 1), date(2000, 1, 1))
    n_periods: int = 0
    avg_n_stocks: int = 0

    # IC
    ic_mean: float = 0.0
    ic_std: float = 0.0
    ic_ir: float = 0.0
    rank_ic_mean: float = 0.0
    rank_ic_std: float = 0.0
    ic_positive_ratio: float = 0.0
    ic_ts: list[dict] = field(default_factory=list)

    # 分组
    group_mean_returns: list[float] = field(default_factory=lambda: [0.0] * 5)
    group_annual_returns: list[float] = field(default_factory=lambda: [0.0] * 5)
    long_short_spread: float = 0.0
    long_short_t_stat: float = 0.0
    long_short_p_value: float = 1.0
    monotonicity_score: float = 0.0

    # 衰减
    ic_decay: list[float] = field(default_factory=lambda: [0.0] * 4)
    decay_half_life: int = -1

    # Fama-MacBeth
    fm_beta_mean: float = 0.0
    fm_beta_t_stat: float = 0.0
    fm_beta_p_value: float = 1.0
    fm_beta_ts: list[dict] = field(default_factory=list)

    # 换手率
    factor_turnover: float = 0.0
    top_quarter_turnover: float = 0.0

    # 结论
    verdict: str = "noise"


class EvalCache:
    """评价结果缓存——避免重复计算

    缓存键: (factor_name, symbols_hash, start, end, params_hash)
    TTL: 24 小时
    """

    def __init__(self, ttl_seconds: int = 86400):
        self._cache: dict[str, FactorEvaluationReport] = {}
        self._ttl = ttl_seconds
        self._timestamps: dict[str, float] = {}

    @staticmethod
    def _make_key(factor_name: str, symbols: list[str],
                  start: date, end: date, params_hash: int = 0) -> str:
        symbols_key = hash(tuple(sorted(symbols)))
        return f"{factor_name}:{symbols_key}:{start.isoformat()}:{end.isoformat()}:{params_hash}"

    def get(self, key: str) -> FactorEvaluationReport | None:
        import time
        if key not in self._cache:
            return None
        if time.time() - self._timestamps.get(key, 0) > self._ttl:
            del self._cache[key]
            del self._timestamps[key]
            return None
        return self._cache[key]

    def set(self, key: str, report: FactorEvaluationReport):
        import time
        self._cache[key] = report
        self._timestamps[key] = time.time()

    def invalidate(self, factor_name: str):
        keys = [k for k in self._cache if k.startswith(factor_name)]
        for k in keys:
            del self._cache[k]
            del self._timestamps[k]
