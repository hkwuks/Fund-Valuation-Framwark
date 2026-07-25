"""Vectorized backtest engine for formula-based fund strategies.

Full numpy-vectorized computation — no for-loops over trading days.
Supports any strategy expressible as a weight function f(nav_matrix) -> weight_matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

import numpy as np


@dataclass
class VectorizedBacktestResult:
    """Result of a vectorized backtest run."""

    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    volatility: float = 0.0
    equity_curve: np.ndarray = field(default_factory=lambda: np.array([]))
    daily_returns: np.ndarray = field(default_factory=lambda: np.array([]))
    n_trading_days: int = 0


class VectorizedBacktestEngine:
    """Fully vectorized backtest engine for formula-based strategies.

    The entire computation uses numpy array ops — no event loop, no per-day iteration.
    Strategies must be expressible as a weight function:
        weights_func(nav_matrix: (n_funds, n_days)) -> weights: (n_funds, n_days)

    Does NOT support strategies with conditional branching (if-else per fund).
    Does NOT simulate T+1 settlement, fees, or fund-level restrictions.
    """

    def run(
        self,
        nav_matrix: np.ndarray,
        weights_func: Callable[[np.ndarray], np.ndarray],
        initial_capital: float = 1.0,
    ) -> VectorizedBacktestResult:
        """Run vectorized backtest.

        Args:
            nav_matrix: shape (n_funds, n_days) — each row is a fund's NAV history.
            weights_func: f(nav_matrix) -> weight matrix of shape (n_funds, n_days).
                          weight_matrix[:, t] = weights at time t (must sum to 1).
            initial_capital: starting portfolio value.

        Returns:
            VectorizedBacktestResult with metrics + equity curve.
        """
        n_funds, n_days = nav_matrix.shape
        if n_funds == 0 or n_days == 0:
            raise ValueError("nav_matrix must have at least 1 fund and 1 day")

        # Daily returns: (n_funds, n_days-1)
        daily_returns = nav_matrix[:, 1:] / nav_matrix[:, :-1] - 1

        # Weights: (n_funds, n_days) — weights_func decides how weights evolve
        weights = weights_func(nav_matrix)

        # Portfolio daily returns: (n_days-1,)
        port_returns = np.sum(weights[:, 1:] * daily_returns, axis=0)

        # Equity curve — fully vectorized, no for-loop
        eq = np.empty(n_days)
        eq[0] = initial_capital
        if n_days > 1:
            eq[1:] = initial_capital * np.cumprod(1 + port_returns)

        # — Metrics —
        total_return = eq[-1] / eq[0] - 1
        n = len(port_returns)
        ann_return = (1 + total_return) ** (252 / n) - 1 if n > 0 else 0.0
        std_ret = float(np.std(port_returns, ddof=1)) if n > 1 else 0.0
        ann_vol = std_ret * np.sqrt(252)
        sharpe = (ann_return - 0.025) / ann_vol if ann_vol > 1e-10 else 0.0
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        max_drawdown = float(np.min(dd))

        return VectorizedBacktestResult(
            total_return=total_return,
            annual_return=ann_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_drawdown,
            volatility=ann_vol,
            equity_curve=eq,
            daily_returns=port_returns,
            n_trading_days=n,
        )

    def single_param_sweep(
        self,
        nav_matrix: np.ndarray,
        param_name: str,
        param_values: List[Any],
        weights_func: Callable,
    ) -> List[VectorizedBacktestResult]:
        """Sweep a single parameter and return results for each value.

        Args:
            nav_matrix: shape (n_funds, n_days).
            param_name: name of the parameter (informational, used for results).
            param_values: list of values to sweep.
            weights_func: callable with signature (nav_matrix, param_value) -> weight_matrix.

        Returns:
            List of VectorizedBacktestResult, one per param value.
        """
        results: List[VectorizedBacktestResult] = []
        for val in param_values:
            result = self.run(
                nav_matrix,
                lambda nm, v=val: weights_func(nm, v),
            )
            results.append(result)
        return results

    def benchmark_vs_event_driven(
        self,
        nav_matrix: np.ndarray,
        weights_func: Callable[[np.ndarray], np.ndarray],
        event_driven_result: dict,
    ) -> dict:
        """Compare vectorized vs event-driven results.

        Both should produce Sharpe within 1% of each other.

        Args:
            nav_matrix: shape (n_funds, n_days).
            weights_func: f(nav_matrix) -> weight matrix.
            event_driven_result: dict with keys total_return, sharpe_ratio, max_drawdown.

        Returns:
            Dict with vectorized/event_driven values and pct_diff for each metric.
        """
        vb = self.run(nav_matrix, weights_func)

        vec = {
            "total_return": vb.total_return,
            "sharpe_ratio": vb.sharpe_ratio,
            "max_drawdown": vb.max_drawdown,
        }

        comparison: Dict[str, float] = {}
        for key in vec:
            ed_val = event_driven_result.get(key, 0.0)
            vec_val = vec[key]
            pct_diff = abs(vec_val - ed_val) / max(abs(ed_val), 1e-10)
            comparison[f"{key}_vectorized"] = vec_val
            comparison[f"{key}_event_driven"] = ed_val
            comparison[f"{key}_pct_diff"] = pct_diff

        comparison["vectorized_result"] = vb  # type: ignore[assignment]
        return comparison
