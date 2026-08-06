"""COMEX 黄金期货 RL 交易环境

特点：
- USD 计价，直接对接 XAU/USD 订单流信号
- 可做多/做空
- 杠杆交易（保证金 5%）
- 跨市场信号无需汇率转换
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class TradeRecord:
    step: int = 0
    action: int = 0
    position: float = 0.0
    price: float = 0.0
    pnl: float = 0.0
    cumulative_pnl: float = 0.0


class ComexGoldEnv:
    """COMEX 黄金期货交易环境

    动作空间（离散 5 个）:
    0: 平仓
    1: 做多 1 份
    2: 做多 2 份
    3: 做空 1 份
    4: 做空 2 份

    保证金 = 5%，允许杠杆 20 倍
    """

    def __init__(
        self,
        df: pd.DataFrame,
        initial_capital: float = 100_000,
        margin_rate: float = 0.05,
        commission_pct: float = 0.0003,
        slippage_pct: float = 0.0005,
        max_position: float = 1.0,
        window_size: int = 20,
        reward_scale: float = 1e-5,
        reward_config: dict = None,
        feature_cols: list[str] = None,
    ):
        self.df = df.reset_index(drop=True)
        self.initial_capital = initial_capital
        self.margin_rate = margin_rate
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct
        self.max_position = max_position
        self.window_size = window_size
        self.reward_scale = reward_scale
        self.reward_config = reward_config or {}

        self.n_actions = 5
        # 如果指定了特征列，用它们构建 obs；否则用默认的 22 维
        self.feature_cols = feature_cols or []
        self.obs_dim = len(self.feature_cols) + 2 if self.feature_cols else self._compute_obs_dim()
        self.reset()

    def _compute_obs_dim(self) -> int:
        """状态维度: 5个价格收益率 + 5个技术指标 + 8个隔夜特征 + 2个信号 + 2个持仓"""
        return 22

    def reset(self) -> np.ndarray:
        self.idx = self.window_size
        self.position = 0.0
        self.last_action = 0
        self.cumulative_pnl = 0.0
        self.trades: list = []
        self.pnl_history: list = []
        self.equity_curve: list = [self.initial_capital]
        self.peak_equity = self.initial_capital
        return self._get_obs()

    def step(self, action: int) -> tuple:
        if self.idx >= len(self.df):
            return self._get_obs(), 0.0, True, {"termination": "end_of_data"}

        price = self.df.iloc[self.idx].get("close", 0)
        prev_price = self.df.iloc[self.idx - 1].get("close", 0)
        info = {}

        # ---- 1. PnL：前一步持仓在当日的收益率 ----
        if prev_price > 0:
            ret = price / prev_price - 1
            # COMEX 期货：PnL = 仓位方向 × 持仓金额 × 日收益率
            # 仓位用初始资金的比例表示，收益按实际仓位大小计算
            position_value = self.position * self.initial_capital / self.margin_rate
            mtm_pnl = self.position * position_value * ret
        else:
            mtm_pnl = 0.0

        # ---- 2. 解析动作 ----
        target_pos = self._action_to_position(action)

        # ---- 3. 交易成本 ----
        trade_cost = 0.0
        if target_pos != self.position:
            delta = abs(target_pos - self.position)
            trade_cost = self.initial_capital * delta * (self.commission_pct + self.slippage_pct)
            if abs(target_pos) < 0.01:
                self.pnl_history.append(self.cumulative_pnl)
            self.position = target_pos

        self.cumulative_pnl += mtm_pnl - trade_cost
        self.equity_curve.append(self.initial_capital + self.cumulative_pnl)
        self.peak_equity = max(self.peak_equity, self.equity_curve[-1])

        # ---- 4. 奖励 ----
        rc = self.reward_config
        rs = rc.get("reward_scale", self.reward_scale)
        pnl_r = mtm_pnl * rs
        cost_r = rc.get("cost_penalty", 1.0) * trade_cost * rs
        freq_r = rc.get("freq_penalty", 0.1) * (0.0 if action == self.last_action else 1.0) * rs
        self.last_action = action

        dd = self._current_drawdown()
        dd_penalty = 0.0
        dd_start = rc.get("dd_penalty_start", 0.10)
        dd_steep = rc.get("dd_penalty_steep", 0.5)
        if dd > dd_start:
            dd_penalty = dd_steep * (dd - dd_start)

        reward = pnl_r - cost_r - freq_r - dd_penalty

        # ---- 5. 终止 ----
        done = False
        if dd > rc.get("dd_terminate", 0.25):
            done = True
            info["termination"] = "max_drawdown"
        elif self.cumulative_pnl < -self.initial_capital * 0.30:
            done = True
            info["termination"] = "bankruptcy"
        elif self.idx >= len(self.df) - 1:
            done = True
            info["termination"] = "end_of_data"

        self.idx += 1
        info["position"] = self.position
        info["pnl"] = mtm_pnl
        info["cumulative_pnl"] = self.cumulative_pnl
        info["equity"] = self.equity_curve[-1]
        info["drawdown"] = dd

        return self._get_obs(), reward, done, info

    def _action_to_position(self, action: int) -> float:
        """动作 → 目标仓位 [-max_position, max_position]"""
        mapping = {0: 0.0, 1: 0.5, 2: 1.0, 3: -0.5, 4: -1.0}
        return mapping.get(action, 0.0)

    def _get_obs(self) -> np.ndarray:
        i = min(self.idx, len(self.df) - 1)
        row = self.df.iloc[i]
        price = row.get("close", 0)

        if self.feature_cols:
            # 使用指定的特征列（小时级）
            features = []
            for col in self.feature_cols:
                if col in self.df.columns:
                    val = row.get(col, 0.0)
                    features.append(np.clip(val, -10, 10) if not np.isnan(val) else 0.0)
                else:
                    features.append(0.0)
        else:
            features = []
            # 价格收益率（多周期）
            for offset in [1, 2, 5, 10, 20]:
                ret = (price / self.df.iloc[i - offset].get("close", 1) - 1) * 100 if i >= offset else 0.0
                features.append(np.clip(ret, -10, 10))

            # 技术指标
            for key in ["rsi_14", "macd_diff", "atr_ratio", "bb_position", "volume_ma_ratio"]:
                val = row.get(key, 0.0)
                features.append(np.clip(val, -5, 5) if not np.isnan(val) else 0.0)

            # 隔夜特征
            for key in ["on_return", "on_volatility", "on_volume_imbalance", "on_spread_avg",
                         "on_range", "on_tick_count", "pm_return", "pm_volatility"]:
                val = row.get(key, 0.0)
                features.append(np.clip(val, -10, 10) if not np.isnan(val) else 0.0)

            # 信号
            features.append(row.get("flow_signal", 0.5))
            features.append(row.get("flow_confidence", 0.0))

        # 持仓状态
        features.append(self.position)
        features.append(self.cumulative_pnl * self.reward_scale)

        arr = np.array(features, dtype=np.float32)
        return np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)

    def _current_drawdown(self) -> float:
        peak = max(self.equity_curve) if self.equity_curve else self.initial_capital
        current = self.equity_curve[-1]
        return max(0.0, (peak - current) / peak) if peak > 0 else 0.0

    def get_metrics(self) -> dict:
        equity = np.array(self.equity_curve)
        returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else [0]
        total_return = (equity[-1] - self.initial_capital) / self.initial_capital * 100
        sharpe = 0.0
        if len(returns) > 1 and np.std(returns) > 1e-10:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
        wins = sum(1 for p in self.pnl_history if p > 0)
        win_rate = wins / max(len(self.pnl_history), 1) * 100

        return {
            "total_return_pct": round(total_return, 2),
            "total_pnl": round(self.cumulative_pnl, 2),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(self._current_drawdown() * 100, 2),
            "win_rate_pct": round(win_rate, 1),
            "final_equity": round(float(equity[-1]), 2),
        }
