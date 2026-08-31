"""
Walk-Forward + CPCV 回测验证

Purging + Embargo + CPCV 方法论，
适配 gold/ 量化体系的 Backtester + StrategyBase + GoldBarData。

两种用法:
  1. WalkForwardValidator — 滚动窗口，单策略
  2. CPCVValidator — Combinatorial Purged Cross-Validation，多策略对比
"""

from itertools import combinations
from typing import Optional, Any
from datetime import datetime
import numpy as np
from loguru import logger

from backend.gold.core.models import GoldBarData
from backend.gold.strategy.base import StrategyBase
from backend.gold.backtest.engine import Backtester
from backend.gold.core.config import GoldSettings


class WalkForwardValidator:
    """Walk-Forward 回测验证 — 带 Purging + Embargo 的滚动窗口。

    时间线:
      |---- Train ----|--Embargo--|--Purge--|--Test--|
                       ↑ 去掉尾部    ↑ 去掉头部
    """

    def __init__(
        self,
        train_window: int = 252,
        test_window: int = 20,
        embargo_days: int = 20,
        purge_days: int = 1,
        capital: float = 1_000_000,
        config: GoldSettings = None,
    ):
        self.train_window = train_window
        self.test_window = test_window
        self.embargo_days = embargo_days
        self.purge_days = purge_days
        self.capital = capital
        self.config = config or GoldSettings()

    def validate(
        self,
        strategy_cls: type[StrategyBase],
        bars: list[GoldBarData],
        params: dict = None,
    ) -> dict:
        """执行滚动窗口回测。

        Args:
            strategy_cls: 策略类（未实例化，每个窗口新实例）
            bars: 全量 K 线
            params: 策略参数

        Returns:
            {windows,总指标,逐窗口详情}
        """
        if len(bars) < self.train_window + 20:
            return {"error": f"数据不足: {len(bars)} 根 (最少 {self.train_window + 20})"}

        windows = []
        equity_curves = []
        all_trades = []

        for i, wf_start in enumerate(range(self.train_window, len(bars), self.test_window)):
            result = self._run_window(strategy_cls, bars, wf_start, params)
            if result is None:
                break

            windows.append(result)
            equity_curves.append(result["equity"])
            all_trades.extend(result.get("trades", []))

            logger.info(
                f"WF窗口 {i + 1}: train={result['train_bars']}bars "
                f"→ test={result['test_bars']}bars "
                f"return={result.get('total_return_pct', 0):.2f}%"
            )

        if not windows:
            return {"error": "无有效窗口"}

        return self._aggregate(windows, equity_curves, all_trades)

    def _run_window(
        self,
        strategy_cls: type[StrategyBase],
        bars: list[GoldBarData],
        wf_start: int,
        params: dict = None,
    ) -> Optional[dict]:
        """运行单个 WF 窗口。 wf_start 是测试集起始索引。"""
        embargo_end = wf_start - self.embargo_days
        if embargo_end <= wf_start - self.train_window:
            return None

        train_bars = bars[wf_start - self.train_window : embargo_end]
        test_start = wf_start + self.purge_days
        test_end = test_start + self.test_window
        if test_end >= len(bars):
            return None

        test_bars = bars[test_start:test_end]
        if not test_bars:
            return None

        strategy = strategy_cls()
        bt = Backtester(config=self.config)
        # 在训练数据上运行策略，学习参数
        result = bt.run(strategy, train_bars, capital=self.capital, params=params)

        # 捕获 ML 策略的预训练 predictor（避免测试窗口重新训练 + 消除泄漏）
        predictor_to_inject = getattr(strategy, '_predictor', None)

        # 在测试数据上运行
        test_strategy = strategy_cls()
        if predictor_to_inject is not None:
            test_strategy._injected_predictor = predictor_to_inject

        # 为测试策略提供预热数据（训练集尾部），让 MA/RSI 等指标有足够历史
        # 策略只会在 test_bars 范围内产生信号，但预热数据确保指标计算正确
        warmup_bars = train_bars[-max(60, self.embargo_days * 2):]
        combined_test_bars = warmup_bars + test_bars
        test_result = bt.run(test_strategy, combined_test_bars, capital=self.capital, params=params)

        # 过滤掉预热区间的交易（只保留 test_start 之后的）
        test_start_date = bars[test_start].datetime if test_start < len(bars) else None
        test_trades = []
        for t in test_result.get("trades", []):
            ts = t.get("timestamp", "")
            if test_start_date and ts >= test_start_date.isoformat():
                test_trades.append(t)

        # 计算测试区间的收益率（从测试区间的close trades计算）
        test_close_trades = [t for t in test_trades if t.get("type") == "close"]
        test_pnl = sum(t.get("pnl", 0) for t in test_close_trades)
        test_return_pct = round(test_pnl / self.capital * 100, 2) if self.capital else 0

        # 过滤信号
        def _signal_ts(s):
            if isinstance(s, dict):
                ts = s.get("created_at", "") or ""
                if isinstance(ts, datetime):
                    return ts.isoformat()
                return str(ts)
            return s.created_at.isoformat() if s.created_at else ""

        test_signals_raw = test_result.get("signals", [])
        test_start_ts = test_start_date.isoformat() if test_start_date else ""
        test_signals = [s for s in test_signals_raw if _signal_ts(s) >= test_start_ts]
        test_trade_count = len([t for t in test_trades if t.get("type") == "close"])

        return {
            "window_index": wf_start,
            "train_bars": len(train_bars),
            "test_bars": len(test_bars),
            "total_return_pct": test_return_pct,
            "sharpe_ratio": None,
            "max_drawdown_pct": None,
            "win_rate": None,
            "trade_count": test_trade_count,
            "equity": test_return_pct,
            "trades": test_trades,
            "signals": test_signals[-20:],
        }

    @staticmethod
    def _calc_equity_return(result: dict) -> float:
        report = result.get("report", {})
        meta = report.get("meta", {})
        cost = report.get("cost", {})
        capital = meta.get("capital", 1_000_000)
        net_pnl = cost.get("net_pnl", 0)
        return (net_pnl / capital * 100) if capital else 0

    @staticmethod
    def _aggregate(windows: list[dict], equity_curves: list[float],
                   all_trades: list[dict]) -> dict:
        returns = [w.get("total_return_pct") or 0 for w in windows]
        sharpes = [w.get("sharpe_ratio") or 0 for w in windows]

        # 稀疏窗口保护 — 窗口/交易太少时聚合指标不可靠
        total_trades = sum(w.get("trade_count", 0) for w in windows)
        n_tested = sum(1 for w in windows if (w.get("trade_count") or 0) > 0)
        avg_trades_per_window = total_trades / len(windows) if windows else 0.0
        insufficient = len(windows) < 5 or total_trades < 10
        reason_parts = []
        if len(windows) < 5:
            reason_parts.append(f"窗口数 {len(windows)} < 5")
        if total_trades < 10:
            reason_parts.append(f"总交易数 {total_trades} < 10")
        insufficiency_reason = "; ".join(reason_parts) if reason_parts else ""

        result = {
            "method": "walk_forward",
            "n_windows": len(windows),
            "avg_return_pct": round(np.mean(returns), 2),
            "std_return_pct": round(np.std(returns), 2),
            "min_return_pct": round(min(returns), 2),
            "max_return_pct": round(max(returns), 2),
            "avg_sharpe": round(np.mean(sharpes), 2),
            "std_sharpe": round(np.std(sharpes), 2),
            "positive_window_ratio": round(
                sum(1 for r in returns if r > 0) / len(returns) * 100, 1
            ),
            "total_trades": total_trades,
            "windows": windows,
            "aggregated_equity_pct": round(
                (equity_curves[-1] if equity_curves else 0), 2
            ),
            # 稀疏数据元数据
            "insufficient": insufficient,
            "insufficiency_reason": insufficiency_reason,
            "avg_trades_per_window": round(avg_trades_per_window, 2),
            "windows_with_trades": n_tested,
        }
        # 不统计超额但需报告的不确定性: 平均每窗口交易数太少 → 均值/比率不可信
        if insufficient:
            result["statistical_warning"] = (
                "样本不足: " + (insufficiency_reason or "窗口样本过少")
            )
        return result


class CPCVValidator:
    """Combinatorial Purged Cross-Validation。

    将数据分 N 组，枚举 C(N, k) 训练/测试组合，
    每条路径独立回测，聚合后计算 PBO。
    """

    def __init__(
        self,
        n_groups: int = 6,
        k_test: int = 2,
        embargo_days: int = 20,
        purge_days: int = 1,
        capital: float = 1_000_000,
        config: GoldSettings = None,
    ):
        self.n_groups = n_groups
        self.k_test = k_test
        self.embargo_days = embargo_days
        self.purge_days = purge_days
        self.capital = capital
        self.config = config or GoldSettings()

    def validate(
        self,
        strategy_cls: type[StrategyBase],
        bars: list[GoldBarData],
        params: dict = None,
    ) -> dict:
        """执行 CPCV 回测。"""
        if len(bars) < 200:
            return {"error": f"数据不足: {len(bars)} 根"}

        groups = self._split_groups(bars)
        n_paths = len(list(combinations(range(self.n_groups), self.k_test)))
        logger.info(f"CPCV: {self.n_groups} groups, {self.k_test} test, {n_paths} paths")

        path_results = []
        bt = Backtester(config=self.config)

        for test_indices in combinations(range(self.n_groups), self.k_test):
            train_indices = [i for i in range(self.n_groups) if i not in test_indices]
            train_bars, test_bars = self._build_fold(train_indices, test_indices,
                                                     groups, bars)
            if not train_bars or not test_bars:
                continue

            strategy = strategy_cls()
            result = bt.run(strategy, train_bars, capital=self.capital, params=params)
            test_strategy = strategy_cls()
            test_result = bt.run(test_strategy, test_bars,
                                 capital=self.capital, params=params)

            report = test_result.get("report", {})
            perf = report.get("performance", {})
            path_results.append({
                "train_groups": len(train_indices),
                "test_groups": len(test_indices),
                "train_bars": len(train_bars),
                "test_bars": len(test_bars),
                "total_return_pct": perf.get("total_return"),
                "sharpe_ratio": perf.get("sharpe_ratio"),
                "max_drawdown_pct": report.get("risk", {}).get("max_drawdown"),
                "win_rate": perf.get("win_rate"),
                "trade_count": report.get("trades", {}).get("total_count", 0),
                "trades": test_result.get("trades", []),
                "signals": test_result.get("signals", []),
            })

        if not path_results:
            return {"error": "CPCV 无有效路径"}

        return self._aggregate(path_results)

    def _split_groups(self, bars: list[GoldBarData]) -> list[list[int]]:
        """将 bar 索引近似均匀分到 n_groups 组。"""
        indices = list(range(len(bars)))
        groups = []
        for g in range(self.n_groups):
            start = int(g * len(indices) / self.n_groups)
            end = int((g + 1) * len(indices) / self.n_groups)
            groups.append(indices[start:end])
        return groups

    def _build_fold(
        self,
        train_indices: list[int],
        test_indices: list[int],
        groups: list[list[int]],
        bars: list[GoldBarData],
    ) -> tuple[list[GoldBarData], list[GoldBarData]]:
        """构建一个 fold 的训练/测试数据，含 Purging + Embargo。"""
        train_idx = []
        for gi in train_indices:
            train_idx.extend(groups[gi])
        test_idx = []
        for gi in test_indices:
            test_idx.extend(groups[gi])

        train_idx = sorted(set(train_idx))
        test_idx = sorted(set(test_idx))

        # Embargo: 去除训练集尾部 embargo_days
        if train_idx and self.embargo_days > 0:
            cutoff = train_idx[-1] - self.embargo_days
            train_idx = [i for i in train_idx if i <= cutoff]

        # Purge: 去除测试集头部 purge_days
        if test_idx and self.purge_days > 0:
            cutoff = test_idx[0] + self.purge_days
            test_idx = [i for i in test_idx if i >= cutoff]

        train_bars = [bars[i] for i in train_idx if i < len(bars)]
        test_bars = [bars[i] for i in test_idx if i < len(bars)]

        return train_bars, test_bars

    @staticmethod
    def _aggregate(path_results: list[dict]) -> dict:
        returns = [p.get("total_return_pct") or 0 for p in path_results]
        sharpes = [p.get("sharpe_ratio") or 0 for p in path_results]

        # PBO (Probability of Backtest Overfitting):
        # 超过半数路径 Sharpe < 0 → PBO = 1
        negative_sharpe = sum(1 for s in sharpes if s <= 0)
        pbo = negative_sharpe / len(sharpes) if sharpes else 1.0

        # 稀疏路径保护 — 路径太少时 PBO/聚合指标不可靠
        total_trades = sum(p.get("trade_count", 0) for p in path_results)
        insufficient = len(path_results) < 5 or total_trades < 10
        reason_parts = []
        if len(path_results) < 5:
            reason_parts.append(f"路径数 {len(path_results)} < 5")
        if total_trades < 10:
            reason_parts.append(f"总交易数 {total_trades} < 10")
        insufficiency_reason = "; ".join(reason_parts) if reason_parts else ""

        result = {
            "method": "cpcv",
            "n_paths": len(path_results),
            "avg_return_pct": round(np.mean(returns), 2),
            "std_return_pct": round(np.std(returns), 2),
            "min_return_pct": round(min(returns), 2),
            "max_return_pct": round(max(returns), 2),
            "avg_sharpe": round(np.mean(sharpes), 2),
            "std_sharpe": round(np.std(sharpes), 2),
            "positive_path_ratio": round(
                sum(1 for r in returns if r > 0) / len(returns) * 100, 1
            ),
            "pbo": round(pbo, 2),
            "pbo_verdict": "高过拟合风险" if pbo > 0.5 else "低过拟合风险",
            "total_trades": total_trades,
            "paths": path_results,
            # 稀疏数据元数据
            "insufficient": insufficient,
            "insufficiency_reason": insufficiency_reason,
        }
        if insufficient:
            result["statistical_warning"] = (
                "样本不足: " + (insufficiency_reason or "路径样本过少")
            )
        return result
