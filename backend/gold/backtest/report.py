import numpy as np
from loguru import logger


def _safe_round(val, digits: int = 2):
    """round that converts NaN/Inf to None for JSON serialization"""
    if val is None:
        return None
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return None
        return round(f, digits)
    except (TypeError, ValueError):
        return None


class BacktestReport:
    """回测报告生成器"""

    def generate(self, equity_curve: list[float], trades: list[dict],
                 capital: float, start_date: str, end_date: str,
                 risk_free_rate: float = 0.025,
                 benchmark_returns: list[float] = None) -> dict:
        """
        Args:
            benchmark_returns: 基准每日收益率列表（买入持有），用于对比
        """
        if len(equity_curve) < 2:
            return self._empty_report(capital, start_date, end_date, risk_free_rate)

        equity = np.array(equity_curve, dtype=float)
        returns = np.diff(equity) / equity[:-1]
        days = len(equity_curve) - 1

        # 性能指标
        total_return = equity[-1] / capital - 1
        annualized_return = np.exp(np.log(1 + total_return) * 252 / days) - 1 if days > 0 and total_return > -1 else 0
        volatility = float(np.std(returns) * np.sqrt(252)) if len(returns) > 1 else 0
        sharpe = (annualized_return - risk_free_rate) / volatility if volatility > 0 else 0

        downside = returns[returns < 0]
        downside_dev = float(np.std(downside) * np.sqrt(252)) if len(downside) > 1 else volatility
        sortino = (annualized_return - risk_free_rate) / downside_dev if downside_dev > 0 else 0

        peak = np.maximum.accumulate(equity)
        drawdown = (equity - peak) / peak
        max_dd = float(np.min(drawdown)) if len(drawdown) > 0 else 0
        calmar = annualized_return / abs(max_dd) if max_dd != 0 else 0

        # 交易统计
        close_trades = [t for t in trades if t.get("type") == "close"]
        trade_pnls = [t.get("pnl", 0) for t in close_trades]
        trade_returns = [p / capital for p in trade_pnls] if trade_pnls else []
        wins = sum(1 for p in trade_pnls if p > 0)
        win_rate = wins / len(trade_pnls) if trade_pnls else 0
        gross_profit = sum(r for r in trade_returns if r > 0)
        gross_loss = abs(sum(r for r in trade_returns if r < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

        # 风险指标
        mu = float(np.mean(returns)) if len(returns) > 0 else 0
        sigma = float(np.std(returns)) if len(returns) > 0 else 0
        var_95 = -(mu - 1.645 * sigma) * equity[-1] if sigma > 0 else 0
        threshold = mu - 1.645 * sigma
        tail_returns = returns[returns < threshold]
        cvar_95 = -float(np.mean(tail_returns)) * equity[-1] if len(tail_returns) > 0 else var_95

        sk = 0.0
        ku = 0.0
        if len(returns) > 2:
            from scipy.stats import skew, kurtosis
            sk = float(skew(returns))
            ku = float(kurtosis(returns))

        # 成本分析
        total_commission = sum(t.get("commission", 0) for t in trades)
        total_slippage = sum(t.get("slippage", 0) for t in trades)
        net_pnl = sum(t.get("pnl", 0) for t in close_trades)
        gross_pnl = net_pnl + total_commission + total_slippage

        # 基准对比 (买入持有)
        benchmark = {}
        if benchmark_returns is not None and len(benchmark_returns) > 0:
            bh = np.array(benchmark_returns, dtype=float)
            bh_equity = capital * np.cumprod(1 + bh)
            bh_total_return = bh_equity[-1] / capital - 1
            bh_days = len(bh_equity) - 1
            bh_ann_return = np.exp(np.log(1 + bh_total_return) * 252 / bh_days) - 1 if bh_days > 0 and bh_total_return > -1 else 0
            bh_vol = float(np.std(bh) * np.sqrt(252)) if len(bh) > 1 else 0
            bh_sharpe = (bh_ann_return - risk_free_rate) / bh_vol if bh_vol > 0 else 0
            bh_peak = np.maximum.accumulate(bh_equity)
            bh_dd = float(np.min((bh_equity - bh_peak) / bh_peak))

            # 超额收益 — 对齐到 strategy returns 的长度
            if len(bh) < len(returns):
                # benchmark_returns 是 bar-to-bar 收益率，比 equity_curve 少 1
                pad = len(returns) - len(bh)
                bh_padded = np.pad(bh, (pad, 0), 'constant', constant_values=0)
            else:
                bh_padded = bh[:len(returns)]
            excess_returns = returns - bh_padded
            excess_ann = annualized_return - bh_ann_return
            tracking_error = float(np.std(excess_returns) * np.sqrt(252)) if len(excess_returns) > 1 else 0
            information_ratio = excess_ann / tracking_error if tracking_error > 0 else 0

            benchmark = {
                "total_return": _safe_round(bh_total_return * 100, 2),
                "annualized_return": _safe_round(bh_ann_return * 100, 2),
                "sharpe_ratio": _safe_round(bh_sharpe, 2),
                "max_drawdown": _safe_round(bh_dd * 100, 2),
                "volatility": _safe_round(bh_vol * 100, 2),
            }

        return {
            "performance": {
                "total_return": _safe_round(total_return * 100, 2),
                "annualized_return": _safe_round(annualized_return * 100, 2),
                "sharpe_ratio": _safe_round(sharpe, 2),
                "sortino_ratio": _safe_round(sortino, 2),
                "calmar_ratio": _safe_round(calmar, 2),
                "win_rate": _safe_round(win_rate * 100, 2),
                "profit_factor": _safe_round(profit_factor, 2) if profit_factor else None,
            },
            "risk": {
                "max_drawdown": _safe_round(max_dd * 100, 2),
                "var_95": _safe_round(var_95, 2),
                "cvar_95": _safe_round(cvar_95, 2),
                "volatility": _safe_round(volatility * 100, 2),
                "downside_deviation": _safe_round(downside_dev * 100, 2),
                "skewness": _safe_round(sk, 4),
                "kurtosis": _safe_round(ku, 4),
            },
            "benchmark": benchmark if benchmark else None,
            "excess": {
                "excess_return": _safe_round(total_return * 100 - benchmark.get("total_return", 0), 2) if benchmark else None,
                "information_ratio": _safe_round(information_ratio, 2) if benchmark else None,
                "tracking_error": _safe_round(tracking_error * 100, 2) if benchmark else None,
            } if benchmark else None,
            "trades": {
                "total_return": _safe_round(total_return * 100, 2),
                "annualized_return": _safe_round(annualized_return * 100, 2),
                "sharpe_ratio": _safe_round(sharpe, 2),
                "sortino_ratio": _safe_round(sortino, 2),
                "calmar_ratio": _safe_round(calmar, 2),
                "win_rate": _safe_round(win_rate * 100, 2),
                "profit_factor": _safe_round(profit_factor, 2) if profit_factor else None,
            },
            "risk": {
                "max_drawdown": _safe_round(max_dd * 100, 2),
                "var_95": _safe_round(var_95, 2),
                "cvar_95": _safe_round(cvar_95, 2),
                "volatility": _safe_round(volatility * 100, 2),
                "downside_deviation": _safe_round(downside_dev * 100, 2),
                "skewness": _safe_round(sk, 4),
                "kurtosis": _safe_round(ku, 4),
            },
            "trades": {
                "total_count": len(close_trades),
                "avg_holding_bars": _safe_round(np.mean([t.get("holding_bars", 0) for t in close_trades]), 1) if close_trades else 0,
                "avg_profit": _safe_round(np.mean([r for r in trade_returns if r > 0]) * 100, 2) if wins > 0 else 0,
                "avg_loss": _safe_round(np.mean([r for r in trade_returns if r < 0]) * 100, 2) if (len(trade_returns) - wins) > 0 else 0,
                "max_single_loss": _safe_round(min(trade_returns) * 100, 2) if trade_returns else 0,
            },
            "cost": {
                "total_commission": _safe_round(total_commission, 2),
                "total_slippage": _safe_round(total_slippage, 2),
                "gross_pnl": _safe_round(gross_pnl, 2),
                "net_pnl": _safe_round(net_pnl, 2),
            },
            "meta": {
                "capital": capital,
                "start_date": start_date,
                "end_date": end_date,
                "total_days": days,
                "risk_free_rate": risk_free_rate,
                "n_bars": len(equity_curve),
                "n_close_trades": len(close_trades),
                "insufficient": len(equity_curve) < 2 or len(close_trades) < 5,
                "insufficiency_reason": (
                    "权益曲线样本不足" if len(equity_curve) < 2
                    else ("交易数不足" if len(close_trades) < 5 else "")
                ),
            }
        }

    def _empty_report(self, capital, start_date, end_date, risk_free_rate) -> dict:
        return {
            "performance": {"total_return": 0, "annualized_return": 0, "sharpe_ratio": 0,
                           "sortino_ratio": 0, "calmar_ratio": 0, "win_rate": 0, "profit_factor": None},
            "risk": {"max_drawdown": 0, "var_95": 0, "cvar_95": 0, "volatility": 0,
                    "downside_deviation": 0, "skewness": 0, "kurtosis": 0},
            "trades": {"total_count": 0, "avg_holding_bars": 0, "avg_profit": 0, "avg_loss": 0, "max_single_loss": 0},
            "cost": {"total_commission": 0, "total_slippage": 0, "gross_pnl": 0, "net_pnl": 0},
            "meta": {"capital": capital, "start_date": start_date, "end_date": end_date,
                    "total_days": 0, "risk_free_rate": risk_free_rate,
                    "n_bars": 0, "n_close_trades": 0,
                    "insufficient": True, "insufficiency_reason": "权益曲线样本不足"},
        }
