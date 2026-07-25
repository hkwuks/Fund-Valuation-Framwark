"""验证层 — WalkForwardValidator + CPCVValidator + ParamOptimizer"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WalkForwardReport:
    train_metrics: list[dict] = field(default_factory=list)
    test_metrics: list[dict] = field(default_factory=list)
    avg_test_return: float = 0.0
    avg_test_sharpe: float = 0.0
    stability: float = 0.0  # 各窗口收益的一致性（低波动 = 高稳定性）


@dataclass
class CPCVReport:
    """Combinatorial Purged Cross-Validation 报告"""
    folds: list[dict] = field(default_factory=list)
    avg_return: float = 0.0
    avg_sharpe: float = 0.0
    std_sharpe: float = 0.0


@dataclass
class ParamTrial:
    params: dict
    metric: float  # 优化目标值


@dataclass
class OptimizationReport:
    best_params: dict = field(default_factory=dict)
    best_score: float = 0.0
    trials: list[ParamTrial] = field(default_factory=list)


class WalkForwardValidator:
    """Walk-Forward 分析 — 带 Purging + Embargo

    >>> v = WalkForwardValidator(train_window=5, test_window=2, embargo_days=1, purge_days=0)
    >>> windows = list(v._split(list(range(20))))
    >>> len(windows)
    4
    >>> all(len(w["train"]) == 4 for w in windows)
    True
    >>> all(len(w["test"]) == 2 for w in windows)
    True
    """

    def __init__(self, train_window: int = 252, test_window: int = 20,
                 purge_days: int = 1, embargo_days: int = 0):
        self.train_window = train_window
        self.test_window = test_window
        self.purge_days = purge_days
        self.embargo_days = embargo_days

    def _split(self, data: list) -> list[dict]:
        """生成训练/测试窗口（基础分割，含 Embargo）"""
        windows = []
        step = self.test_window
        for i in range(self.train_window, len(data), step):
            train_end = i - self.embargo_days
            if train_end <= 0:
                continue
            test_start = i + self.purge_days
            test_end = min(test_start + self.test_window, len(data))
            if test_end <= test_start:
                continue
            windows.append({
                "train": data[i - self.train_window:train_end],
                "test": data[test_start:test_end],
            })
        return windows

    def validate(self, strategy_cls, bars: list, params: dict | None = None) -> WalkForwardReport:
        """执行 Walk-Forward 验证（基础 — 只返回窗口元数据分割）"""
        windows = self._split(bars)
        report = WalkForwardReport()

        for w in windows:
            report.train_metrics.append({
                "train_size": len(w["train"]),
                "train_start": str(w["train"][0]) if w["train"] else "",
            })
            report.test_metrics.append({
                "test_size": len(w["test"]),
                "test_start": str(w["test"][0]) if w["test"] else "",
            })

        return report

    def run(self, bars: list, engine_factory, warmup_bars: int = 60) -> dict:
        """完整 Walk-Forward 回测执行

        Args:
            bars: 全量 K 线数据
            engine_factory: callable(bar_slice) -> BacktestEngine（已配置策略+执行引擎）
            warmup_bars: 测试阶段前端附加的预热 bar 数（用于 MA/R 等指标计算）

        Returns:
            {windows, 聚合指标}
        """
        import numpy as np
        windows = self._split(bars)

        results = []
        for w in windows:
            train_bars = w["train"]
            test_bars = w["test"]

            # 训练阶段
            train_engine = engine_factory(train_bars)
            train_report = train_engine.run()

            # 测试阶段（前端附加预热数据）
            warmup = train_bars[-min(warmup_bars, len(train_bars)):] if warmup_bars > 0 else []
            combined = warmup + test_bars
            test_engine = engine_factory(combined)
            test_report = test_engine.run()

            # 过滤预热区间的权益曲线
            warmup_len = len(warmup)
            test_equity = [e for e in test_report.equity_curve if e["bar"] >= warmup_len]

            results.append({
                "train_bars": len(train_bars),
                "test_bars": len(test_bars),
                "warmup_bars": len(warmup),
                "train_return": train_report.total_return,
                "test_return": test_report.total_return,
                "train_trades": train_report.total_trades,
                "test_trades": test_report.total_trades,
                "train_equity": train_report.equity_curve,
                "test_equity": test_equity,
            })

        # 聚合
        test_returns = [r["test_return"] for r in results]
        n = len(test_returns)
        return {
            "method": "walk_forward",
            "n_windows": n,
            "avg_test_return": round(float(np.mean(test_returns)), 4) if test_returns else 0,
            "std_test_return": round(float(np.std(test_returns)), 4) if n > 1 else 0,
            "positive_window_ratio": round(sum(1 for r in test_returns if r > 0) / n * 100, 1) if n else 0,
            "total_test_trades": sum(r["test_trades"] for r in results),
            "windows": results,
        }


class CPCVValidator:
    """Combinatorial Purged Cross-Validation

    >>> c = CPCVValidator(n_splits=4, test_size=0.25)
    >>> folds = list(c._split(list(range(100))))
    >>> len(folds)
    4
    >>> all(len(f["test"]) > 0 for f in folds)
    True
    """

    def __init__(self, n_splits: int = 6, test_size: float = 0.2,
                 purge_days: int = 1, embargo_days: int = 0):
        self.n_splits = n_splits
        self.test_size = test_size
        self.purge_days = purge_days
        self.embargo_days = embargo_days

    def _split(self, data: list) -> list[dict]:
        """生成 CPCV 折"""
        n = len(data)
        test_n = max(1, int(n * self.test_size))
        folds = []
        for i in range(self.n_splits):
            test_start = i * test_n
            if test_start >= n:
                break
            test_end = min(test_start + test_n, n)
            folds.append({
                "train": data[:test_start] + data[test_end:],
                "test": data[test_start:test_end],
            })
        return folds

    def validate(self, strategy_cls, bars: list, params: dict | None = None) -> CPCVReport:
        folds = self._split(bars)
        report = CPCVReport()
        for f in folds:
            report.folds.append({
                "train_size": len(f["train"]),
                "test_size": len(f["test"]),
            })
        return report

    def run(self, bars: list, engine_factory, warmup_bars: int = 60) -> dict:
        """完整 CPCV 回测执行

        Args:
            bars: 全量数据
            engine_factory: callable(bar_slice) -> BacktestEngine
            warmup_bars: 测试阶段前端预热 bar 数

        Returns:
            {paths, 聚合指标, PBO}
        """
        import numpy as np
        from itertools import combinations

        n = len(bars)
        # 均匀分到 n_splits 组
        groups = []
        for g in range(self.n_splits):
            start = int(g * n / self.n_splits)
            end = int((g + 1) * n / self.n_splits)
            groups.append(list(range(start, end)))

        k_test = max(1, int(self.n_splits * self.test_size))
        paths = []

        for test_indices in combinations(range(self.n_splits), k_test):
            train_indices = [i for i in range(self.n_splits) if i not in test_indices]

            # 构建训练/测试索引
            train_idx, test_idx = [], []
            for gi in train_indices:
                train_idx.extend(groups[gi])
            for gi in test_indices:
                test_idx.extend(groups[gi])
            train_idx = sorted(set(train_idx))
            test_idx = sorted(set(test_idx))

            # Embargo: 训练集尾部去屑
            if train_idx and self.embargo_days > 0:
                cutoff = train_idx[-1] - self.embargo_days
                train_idx = [i for i in train_idx if i <= cutoff]
            # Purge: 测试集头部去屑
            if test_idx and self.purge_days > 0:
                cutoff = test_idx[0] + self.purge_days
                test_idx = [i for i in test_idx if i >= cutoff]

            train_bars = [bars[i] for i in train_idx if i < len(bars)]
            test_bars = [bars[i] for i in test_idx if i < len(bars)]
            if not train_bars or not test_bars:
                continue

            # 训练
            train_engine = engine_factory(train_bars)
            train_report = train_engine.run()

            # 测试（带预热）
            warmup = train_bars[-min(warmup_bars, len(train_bars)):] if warmup_bars > 0 else []
            combined = warmup + test_bars
            test_engine = engine_factory(combined)
            test_report = test_engine.run()

            warmup_len = len(warmup)
            test_equity = [e for e in test_report.equity_curve if e["bar"] >= warmup_len]

            paths.append({
                "train_groups": len(train_indices),
                "test_groups": len(test_indices),
                "train_bars": len(train_bars),
                "test_bars": len(test_bars),
                "warmup_bars": len(warmup),
                "train_return": train_report.total_return,
                "test_return": test_report.total_return,
                "test_equity": test_equity,
                "train_trades": train_report.total_trades,
                "test_trades": test_report.total_trades,
            })

        if not paths:
            return {"method": "cpcv", "error": "无有效路径", "paths": []}

        # 聚合
        test_returns = [p["test_return"] for p in paths]
        sharpes = []  # CPCV 每条路径只有一次测试，不容易算 sharpe，用 return 替代
        n_paths = len(test_returns)

        # PBO: 超过半数路径负收益 = 过拟合
        negative = sum(1 for r in test_returns if r <= 0)
        pbo = negative / n_paths if n_paths > 0 else 1.0

        return {
            "method": "cpcv",
            "n_splits": self.n_splits,
            "n_paths": n_paths,
            "avg_test_return": round(float(np.mean(test_returns)), 4) if test_returns else 0,
            "std_test_return": round(float(np.std(test_returns)), 4) if n_paths > 1 else 0,
            "positive_path_ratio": round(sum(1 for r in test_returns if r > 0) / n_paths * 100, 1) if n_paths else 0,
            "pbo": round(pbo, 4),
            "pbo_verdict": "高过拟合风险" if pbo > 0.5 else "低过拟合风险",
            "total_test_trades": sum(p["test_trades"] for p in paths),
            "paths": paths,
        }


class ParamOptimizer:
    """参数优化器 — 网格搜索 + 回测执行"""

    def __init__(self, validator: WalkForwardValidator | None = None):
        self._validator = validator

    def _grid(self, param_grid: dict) -> list[dict]:
        """生成参数网格"""
        keys = param_grid.keys()
        values = param_grid.values()
        for combo in itertools.product(*values):
            yield dict(zip(keys, combo))

    def optimize(self, strategy_cls, bars: list, param_grid: dict,
                 metric_fn: callable | None = None) -> OptimizationReport:
        """网格搜索最优参数（基础 — 返回窗口元数据分割）"""
        report = OptimizationReport()
        best_score = -float("inf")

        for params in self._grid(param_grid):
            windows = self._validator._split(bars) if self._validator else []
            test_returns = []

            for w in windows:
                test_returns.append(len(w["test"]) / len(bars))

            score = metric_fn(test_returns) if metric_fn else (sum(test_returns) / len(test_returns) if test_returns else 0)

            trial = ParamTrial(params=params, metric=score)
            report.trials.append(trial)

            if score > best_score:
                best_score = score
                report.best_params = params
                report.best_score = best_score

        return report

    def optimize_run(self, bars: list, engine_factory,
                     param_grid: dict, metric_fn: callable | None = None) -> dict:
        """网格搜索最优参数 — 执行实际回测

        Args:
            bars: 全量数据
            engine_factory: callable(bar_slice, params) -> BacktestEngine
            param_grid: {param_name: [value1, value2, ...]}
            metric_fn: callable(BacktestReport) -> float, 默认取 total_return

        Returns:
            {best_params, best_score, trials}
        """
        import numpy as np

        trials = []
        for params in self._grid(param_grid):
            engine = engine_factory(bars, params)
            report = engine.run()
            score = metric_fn(report) if metric_fn else report.total_return

            trials.append({
                "params": params,
                "score": round(float(score), 6),
                "total_return": report.total_return,
                "total_trades": report.total_trades,
                "equity_curve": report.equity_curve,
            })

        scores = [t["score"] for t in trials]
        best_idx = int(np.argmax(scores)) if scores else -1

        return {
            "best_params": trials[best_idx]["params"] if best_idx >= 0 else {},
            "best_score": trials[best_idx]["score"] if best_idx >= 0 else 0,
            "n_trials": len(trials),
            "avg_score": round(float(np.mean(scores)), 6) if scores else 0,
            "trials": trials,
        }


# ── PBO 过拟合概率 ──

def calculate_pbo(path_rank_matrix: list[list[float]]) -> dict:
    """计算回测过拟合概率 (Probability of Backtest Overfitting)

    基于 Bailey et al. (2014) "PBO: Probability of Backtest Overfitting"。

    Args:
        path_rank_matrix: M×N 矩阵
            M = 策略/参数组数, N = CPCV 路径数
            每个元素是某策略在某个路径下的样本外收益。

    Returns:
        {pbo: 过拟合概率, logit_pbo: Logit 修正概率,
         avg_rank: 各策略平均排名, rank_distribution: 排名分布}

    >>> import numpy as np
    >>> # 3 个参数组, 4 条路径, 有一个明显过拟合
    >>> m = [[0.05,  -0.02, 0.03, -0.01],
    ...      [0.01,   0.02, 0.01,  0.02],
    ...      [-0.01, -0.01, 0.00, -0.02]]
    >>> r = calculate_pbo(m)
    >>> 0 <= r['pbo'] <= 1
    True
    >>> 'avg_rank' in r
    True
    """
    import numpy as np

    arr = np.array(path_rank_matrix, dtype=float)
    M, N = arr.shape
    if M < 2 or N < 2:
        return {"pbo": 0.5, "logit_pbo": 0.5, "avg_rank": [], "rank_distribution": [],
                "reason": f"数据不足: M={M}策略, N={N}路径, 至少需要2×2"}

    # 每条路径内从高到低排名（收益最高排 1）
    ranks = np.argsort(np.argsort(-arr, axis=0), axis=0) + 1

    # 标准化排名: 最高(rank=1)→1, 最低(rank=M)→0
    norm_ranks = 1.0 - (ranks - 1) / (M - 1) if M > 1 else np.ones_like(ranks)

    # 每个策略的平均标准化排名
    avg_norm_rank = np.mean(norm_ranks, axis=1).tolist()

    # PBO = 标准化排名 < 0.5 的策略占比
    # (低于中位数 = 过拟合)
    below_median = sum(1 for r in avg_norm_rank if r < 0.5)
    pbo = below_median / M

    # Logit 修正 (De Prado, 2018)
    # 对排名分布做二项检验
    from math import log
    success = sum(1 for r in avg_norm_rank if r >= 0.5)
    if success >= M - success:
        # 策略比半数好的个数 >= 差的一半 → p 值
        from scipy import stats as _stats
        try:
            p = _stats.binom_test(success, M, 0.5, alternative='greater')
            logit_pbo = 1.0 - p
        except Exception:
            logit_pbo = pbo
    else:
        logit_pbo = pbo

    # 排名分布热图（用于可视化）
    rank_dist = []
    for i in range(M):
        row = {"strategy": i, "avg_rank": float(np.mean(ranks[i])),
               "std_rank": float(np.std(ranks[i])),
               "norm_score": round(avg_norm_rank[i], 4)}
        rank_dist.append(row)

    return {
        "pbo": round(pbo, 4),
        "logit_pbo": round(logit_pbo, 4),
        "avg_rank": [round(r, 4) for r in avg_norm_rank],
        "rank_distribution": rank_dist,
    }


def demo():
    """验证层自检"""
    data = list(range(100))

    # WalkForward
    v = WalkForwardValidator(train_window=20, test_window=5)
    wf_report = v.validate(None, data)
    assert len(wf_report.train_metrics) > 0
    print(f"[validation] ✅ WF: {len(wf_report.train_metrics)} windows")

    # CPCV
    c = CPCVValidator(n_splits=5, test_size=0.2)
    cv_report = c.validate(None, data)
    assert len(cv_report.folds) > 0
    print(f"[validation] ✅ CPCV: {len(cv_report.folds)} folds")

    # ParamOptimizer
    opt = ParamOptimizer(v)
    grid = {"threshold": [0.01, 0.02, 0.05], "window": [10, 20]}
    opt_report = opt.optimize(None, data, grid)
    assert len(opt_report.trials) == 6  # 3 x 2
    assert opt_report.best_params != {}
    print(f"[validation] ✅ 参数优化: {len(opt_report.trials)} trials, best={opt_report.best_params}")

    # ── CPCV.run 端到端自检 ──
    from core import BacktestEngine, BacktestConfig, EventBus, Bar, Strategy, StrategyRegistry, Signal, Direction, SimExecutionEngine
    from datetime import datetime, timedelta
    start = datetime(2026, 1, 1)
    bars = [Bar(symbol="TEST", exchange="DEMO", timeframe="1d",
                datetime=start + timedelta(days=i), open=float(100+i%20), high=float(101+i%20),
                low=float(99+i%20), close=float(100+i%20), volume=1000) for i in range(100)]

    @StrategyRegistry.register("demo_cpcv")
    class C(Strategy):
        def on_data(self, data):
            if data.close > 110:
                self.ctx.emit(Signal(id="", strategy="demo_cpcv", symbol=data.symbol,
                                     direction=Direction.LONG, price=data.close, volume=1))

    def mk_engine(bs):
        cfg = BacktestConfig(initial_capital=100000)
        e = BacktestEngine(cfg)
        e.set_event_bus(EventBus())
        e.set_strategy(C())
        e.set_executor(SimExecutionEngine())
        e.set_data(bs)
        return e

    cpcv = CPCVValidator(n_splits=4, test_size=0.25, purge_days=1, embargo_days=5)
    cpcv_result = cpcv.run(bars, mk_engine, warmup_bars=20)
    assert cpcv_result["n_paths"] > 0, f"CPCV 无有效路径: {cpcv_result}"
    print(f"[validation] ✅ CPCV.run: {cpcv_result['n_paths']} paths, "
          f"avg_return={cpcv_result['avg_test_return']:.2%}, PBO={cpcv_result['pbo']}")

    # ── ParamOptimizer.optimize_run 端到端自检 ──
    def mk_param_engine(bs, params):
        inst = C()
        inst.params = params
        cfg = BacktestConfig(initial_capital=100000)
        e = BacktestEngine(cfg)
        e.set_event_bus(EventBus())
        e.set_strategy(inst)
        e.set_executor(SimExecutionEngine())
        e.set_data(bs)
        return e

    opt_run = opt.optimize_run(bars, mk_param_engine, {"dummy": [1, 2]})
    assert opt_run["n_trials"] == 2
    print(f"[validation] ✅ ParamOptimizer.optimize_run: {opt_run['n_trials']} trials, "
          f"best_score={opt_run['best_score']:.4f}")

    print("[validation] ✅ 验证层通过")


if __name__ == "__main__":
    demo()
