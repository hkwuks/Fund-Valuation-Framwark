"""Walk-forward 滚动窗口验证 — 完整实现"""

from datetime import datetime, date, timedelta
from typing import List, Optional, Callable, Dict, Any
from loguru import logger
import numpy as np


class WalkForwardValidator:
    """Walk-forward 滚动窗口验证器"""

    DEFAULT_PARAMS = {
        "train_window_days": 1260,     # 5年 ≈ 1260交易日
        "test_window_days": 126,       # 6个月 ≈ 126交易日
        "step_size_days": 21,          # 1个月 ≈ 21交易日
        "expanding_window": True,      # 扩展窗口 vs 固定窗口
        "min_train_trades": 20,        # 最少交易次数
    }

    def validate(self, run_backtest_fn: Callable,
                 fund_codes: List[str],
                 start_date: str, end_date: str,
                 params: Optional[dict] = None,
                 benchmark: Optional[str] = None) -> dict:
        """执行Walk-forward验证"""
        p = {**self.DEFAULT_PARAMS, **(params or {})}

        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        total_days = (end - start).days
        if total_days < p["train_window_days"] + p["test_window_days"]:
            return {
                "status": "error",
                "message": f"数据区间 {total_days}天 不足训练+测试 ({p['train_window_days']}+{p['test_window_days']})",
            }

        # 生成滚动窗口
        windows = []
        window_start = start

        while True:
            train_end = window_start + timedelta(days=p["train_window_days"])
            test_end = train_end + timedelta(days=p["test_window_days"])

            if test_end > end:
                break

            # 扩展窗口: 训练起始保持最早日期, 训练结束逐步后移
            if p["expanding_window"]:
                train_start = start
            else:
                train_start = window_start

            windows.append({
                "train_start": train_start.strftime("%Y-%m-%d"),
                "train_end": train_end.strftime("%Y-%m-%d"),
                "test_start": (train_end + timedelta(days=1)).strftime("%Y-%m-%d"),
                "test_end": test_end.strftime("%Y-%m-%d"),
                "train_days": p["train_window_days"],
                "test_days": p["test_window_days"],
            })

            window_start += timedelta(days=p["step_size_days"])

        if not windows:
            return {"status": "error", "message": "无法生成有效窗口"}

        # 执行每个窗口的回测
        window_results = []
        test_returns = []
        test_sharpes = []
        test_drawdowns = []
        test_trades = []

        for w in windows:
            try:
                bt_config = {
                    "strategy_name": "walk_forward",
                    "fund_codes": fund_codes,
                    "start_date": w["train_start"],
                    "end_date": w["train_end"],
                }
                train_result = run_backtest_fn(bt_config)

                # 测试集
                test_config = {
                    **bt_config,
                    "start_date": w["test_start"],
                    "end_date": w["test_end"],
                    "params": train_result.get("params", {}) if hasattr(train_result, 'get') else {},
                }
                test_result = run_backtest_fn(test_config)

                tr = test_result.get("total_return", 0) if hasattr(test_result, 'get') else (
                    getattr(test_result, 'total_return', 0)
                )
                ts = test_result.get("sharpe_ratio", 0) if hasattr(test_result, 'get') else (
                    getattr(test_result, 'sharpe_ratio', 0)
                )
                td = test_result.get("max_drawdown", 0) if hasattr(test_result, 'get') else (
                    getattr(test_result, 'max_drawdown', 0)
                )
                tt = test_result.get("total_trades", 0) if hasattr(test_result, 'get') else (
                    getattr(test_result, 'total_trades', 0)
                )
                br = test_result.get("benchmark_return") if hasattr(test_result, 'get') else None
                if benchmark:
                    br = self._external_benchmark_return(benchmark, fund_codes, w["test_start"], w["test_end"])

                test_returns.append(tr)
                test_sharpes.append(ts)
                test_drawdowns.append(td)
                test_trades.append(tt)

                window_results.append({
                    "window": w,
                    "total_return": round(tr, 6),
                    "sharpe_ratio": round(ts, 4) if ts else None,
                    "max_drawdown": round(td, 6),
                    "total_trades": tt,
                    "benchmark_return": round(float(br), 6) if isinstance(br, (int, float)) else None,
                    "excess_return": round(tr - float(br), 6) if isinstance(br, (int, float)) else None,
                    "trades_ok": tt >= p["min_train_trades"],
                })

            except Exception as e:
                logger.warning(f"Walk-forward 窗口 {w['train_start']}~{w['test_end']} 失败: {e}")
                window_results.append({"window": w, "status": "failed", "error": str(e)})

        # 计算一致性得分
        valid_results = [r for r in window_results
                         if r.get("status") != "failed"
                         and r.get("total_return") is not None
                         and r.get("trades_ok")]

        if not valid_results:
            return {"method": "walk_forward", "config": p, "windows": window_results, "summary": {
                "total_windows": len(windows), "valid_windows": 0,
                "avg_return": 0.0, "avg_sharpe": 0.0, "consistency_score": 0.0,
            }}

        avg_return = float(np.mean(test_returns)) if test_returns else 0.0
        avg_sharpe = float(np.mean(test_sharpes)) if test_sharpes else 0.0
        std_return = float(np.std(test_returns)) if len(test_returns) > 1 else 0.0

        # 一致性得分: 1.0 - 标准差/平均绝对收益 (收益波动越小越一致)
        mean_abs_return = float(np.mean(np.abs(test_returns))) if test_returns else 0.0
        consistency = max(0.0, 1.0 - std_return / max(mean_abs_return, 1e-8)) if mean_abs_return > 0 else 0.0

        # 回撤控制评价
        avg_drawdown = float(np.mean(test_drawdowns)) if test_drawdowns else 1.0
        benchmark_returns = [r["benchmark_return"] for r in window_results
                             if isinstance(r.get("benchmark_return"), (int, float))]
        excess_returns = [r["excess_return"] for r in window_results
                          if isinstance(r.get("excess_return"), (int, float))]

        # 稀疏窗口保护 — 有效窗口/交易太少时一致性得分等聚合不可靠
        total_trades_list = [t for t in test_trades if t >= 0]
        n_traded = sum(1 for t in test_trades if t >= p["min_train_trades"])
        total_trades_all = int(np.sum(total_trades_list)) if total_trades_list else 0
        insufficient = len(valid_results) < 3 or total_trades_all < 10
        reason_parts = []
        if len(valid_results) < 3:
            reason_parts.append(f"有效窗口数 {len(valid_results)} < 3")
        if total_trades_all < 10:
            reason_parts.append(f"总交易数 {total_trades_all} < 10")
        insufficiency_reason = "; ".join(reason_parts) if reason_parts else ""

        return {
            "method": "walk_forward",
            "config": p,
            "windows": window_results,
            "summary": {
                "total_windows": len(windows),
                "valid_windows": len(valid_results),
                "avg_return": round(avg_return, 6),
                "avg_sharpe": round(avg_sharpe, 4),
                "avg_drawdown": round(avg_drawdown, 6),
                "avg_benchmark_return": round(float(np.mean(benchmark_returns)), 6) if benchmark_returns else None,
                "avg_excess_return": round(float(np.mean(excess_returns)), 6) if excess_returns else None,
                "consistency_score": round(consistency, 4),
                "min_return": round(float(np.min(test_returns)), 6) if test_returns else 0.0,
                "max_return": round(float(np.max(test_returns)), 6) if test_returns else 0.0,
                "total_trades_avg": int(np.mean(test_trades)) if test_trades else 0,
                "windows_passing_min_trades": n_traded,
                # 稀疏数据元数据
                "insufficient": insufficient,
                "insufficiency_reason": insufficiency_reason,
                "total_trades": total_trades_all,
            },
        }
    @staticmethod
    def _external_benchmark_return(benchmark: str, fund_codes: List[str],
                                   start_date: str, end_date: str) -> Optional[float]:
        from ..data.storage import get_index_history, get_nav_history

        history = get_index_history(benchmark)
        if not history:
            return None
        nav_dates = set()
        for code in fund_codes:
            nav_dates.update(r["date"] for r in get_nav_history(code, start_date, end_date)
                             if r.get("date"))
        rows = sorted((r["date"], float(r["close"])) for r in history
                      if start_date <= r["date"] <= end_date and r.get("close"))
        rows = [(d, close) for d, close in rows if d in nav_dates]
        if len(rows) < 2:
            return None
        return rows[-1][1] / rows[0][1] - 1.0


walk_forward_validator = WalkForwardValidator()
