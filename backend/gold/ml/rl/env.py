"""强化学习交易环境 — 黄金期货PnL优化"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import numpy as np
import pandas as pd


@dataclass
class TradeRecord:
    step: int
    action: int
    position: int
    price: float
    pnl: float
    cumulative_pnl: float
    reward_breakdown: dict = field(default_factory=dict)
    reason: str = ""


class GoldTradingEnv:
    """
    黄金期货RL交易环境

    状态 (obs_dim=39):
      - 价格特征: 归一化OHLC, 多周期收益率 (15)
      - 技术指标: RSI, MACD, ATR/price, Bollinger position, Volume ratio (10)
      - 持仓状态: position(one-hot 3), unrealized_pnl, days_in_position, 浮盈比例 (6)
      - 订单流: tick_count, buy_ratio, vol_imbalance, spread (4)
      - 宏观: DXY_change, US10Y_change, VIX_level, gold_DXY_ratio (4)

    动作 (12离散):
      0: 空仓等待  1-2: 开1/4多  1/2多  3-5: 开3/4多/满多
      6: 空仓等待  7-8: 开1/4空  1/2空  9-11: 开3/4空/满空
      (0和6效果相同=空仓, 但提供了bidirectional的起点对称性)

    奖励:
      step_reward = ΔPnL - slippage - commission + position_holding_penalty
      terminal_bonus = Sharpe_ratio_annualized * 0.1 (如果episode结束)

    合约参数:
      AU: 1000克/手, 最小变动0.02元/克, 保证金8%
    """

    def __init__(
        self,
        df: pd.DataFrame,
        initial_capital: float = 1_000_000,
        multiplier: int = 1000,
        margin_rate: float = 0.08,
        commission_per_lot: float = 10.0,
        slippage_per_lot: float = 20.0,
        max_position: int = 10,
        window_size: int = 30,
        reward_scale: float = 1e-6,
        reward_config: dict = None,
        action_space: str = "discrete",
    ):
        self.df = df.reset_index(drop=True)
        self.initial_capital = initial_capital
        self.multiplier = multiplier
        self.margin_rate = margin_rate
        self.commission_per_lot = commission_per_lot
        self.slippage_per_lot = slippage_per_lot
        self.max_position = max_position
        self.window_size = window_size
        self.reward_scale = reward_scale
        self.reward_config = reward_config or {}
        self.action_space = action_space

        self.n_actions = 12 if action_space == "discrete" else 1
        self.obs_dim = 30

        self.reset()

    def _compute_obs_dim(self) -> int:
        """计算状态维度"""
        return 39

    def reset(self) -> np.ndarray:
        """重置环境，返回初始状态"""
        self.idx = self.window_size
        self.position = 0
        self.last_action = 6  # 初始为观望
        self.cumulative_pnl = 0.0
        self.trades: list[TradeRecord] = []
        self.pnl_history: list[float] = []
        self.equity_curve: list[float] = [self.initial_capital]
        self.peak_equity = self.initial_capital
        return self._get_obs()

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict]:
        """执行动作，返回 (next_obs, reward, done, info)"""
        price = self.df.iloc[self.idx]["close"]
        prev_price = self.df.iloc[self.idx - 1]["close"]
        price_change = price - prev_price
        info = {}

        # ---- 1. 解析动作 ----
        target_pos = self._action_to_position(action)

        # ---- 2. 交易成本 ----
        trade_cost = 0.0
        if target_pos != self.position:
            volume = abs(target_pos - self.position)
            trade_cost = volume * (self.commission_per_lot + self.slippage_per_lot)
            if target_pos == 0:
                self.pnl_history.append(self.cumulative_pnl)
            self.position = target_pos

        # ---- 3. Mark-to-market PnL ----
        mtm_pnl = self.position * price_change * self.multiplier
        self.cumulative_pnl += mtm_pnl - trade_cost
        self.equity_curve.append(self.initial_capital + self.cumulative_pnl)
        self.peak_equity = max(self.peak_equity, self.equity_curve[-1])

        # ---- 4. 复合奖励函数 ----
        rc = self.reward_config
        rs = rc.get("reward_scale", self.reward_scale)

        # 4a. PnL成分
        pnl_component = mtm_pnl * rs

        # 4b. 交易成本惩罚
        cost_penalty = rc.get("cost_penalty", 1.5)
        cost_component = trade_cost * rs * cost_penalty

        # 4c. 频繁交易惩罚
        freq_penalty = rc.get("freq_penalty", 0.2)
        if action != self.last_action:
            freq_component = freq_penalty * rs * 1000
        else:
            freq_component = 0.0
        self.last_action = action

        # 4d. 回撤渐进惩罚
        dd = self._current_drawdown()
        dd_penalty_start = rc.get("dd_penalty_start", 0.05)
        dd_penalty_steep = rc.get("dd_penalty_steep", 0.5)
        dd_penalty_steep2 = rc.get("dd_penalty_steep2", 1.0)
        dd_terminate = rc.get("dd_terminate", 0.15)

        dd_component = 0.0
        if dd > dd_terminate:
            dd_component = dd_penalty_steep2 * (dd - dd_terminate) + dd_penalty_steep * (dd_terminate - dd_penalty_start)
        elif dd > dd_penalty_start:
            dd_component = dd_penalty_steep * (dd - dd_penalty_start)

        reward = pnl_component - cost_component - freq_component - dd_component

        # ---- 5. 记录 ----
        reward_breakdown = {
            "pnl": round(pnl_component, 8),
            "cost": round(-cost_component, 8),
            "freq": round(-freq_component, 8),
            "drawdown": round(-dd_component, 8),
        }

        self.trades.append(TradeRecord(
            step=self.idx, action=action, position=self.position,
            price=price, pnl=mtm_pnl, cumulative_pnl=self.cumulative_pnl,
            reward_breakdown=reward_breakdown,
        ))

        # ---- 6. 终止条件 ----
        done = False
        if dd > dd_terminate:
            reward -= 0.05
            done = True
            info["termination"] = "max_drawdown"
        elif self.cumulative_pnl < -self.initial_capital * 0.3:
            reward -= 0.05
            done = True
            info["termination"] = "bankruptcy"
        elif self.idx >= len(self.df) - 1:
            done = True
            info["termination"] = "end_of_data"
            # terminal bonus: sharpe
            sharpe_bonus_scale = rc.get("sharpe_bonus_scale", 0.01)
            if len(self.pnl_history) > 5:
                sharpe = self._sharpe_ratio()
                terminal_bonus = min(max(sharpe, -1), 3) * sharpe_bonus_scale
                reward += terminal_bonus
                reward_breakdown["terminal"] = round(terminal_bonus, 8)

        self.idx += 1

        info["position"] = self.position
        info["pnl"] = mtm_pnl
        info["cumulative_pnl"] = self.cumulative_pnl
        info["equity"] = self.equity_curve[-1]
        info["drawdown"] = dd
        info["reward_breakdown"] = reward_breakdown

        return self._get_obs(), reward, done, info

    def _action_to_position(self, action) -> int:
        """动作 → 目标仓位手数"""
        if self.action_space == "continuous":
            # action ∈ [-1, 1], 映射到 [-max_position, max_position]
            return int(round(action * self.max_position))
        # 离散动作
        if action == 0 or action == 6:
            return 0
        if 1 <= action <= 4:
            sizes = {1: 2, 2: 4, 3: 6, 4: 10}
            return sizes.get(action, 0)
        if 7 <= action <= 10:
            sizes = {7: -2, 8: -4, 9: -6, 10: -10}
            return sizes.get(action, 0)
        return 0

    def _get_obs(self) -> np.ndarray:
        """构建状态向量"""
        i = self.idx
        if i >= len(self.df):
            i = len(self.df) - 1

        row = self.df.iloc[i]
        price = row["close"]

        # 价格特征
        features = []
        for offset in [1, 2, 5, 10, 20]:
            if i >= offset:
                ret = (price / self.df.iloc[i - offset]["close"] - 1)
            else:
                ret = 0.0
            features.append(ret)

        # OHL归一化
        features.append((row.get("open", price) / price - 1))
        features.append((row.get("high", price) / price - 1))
        features.append((row.get("low", price) / price - 1))
        features.append(row.get("volume", 0) / (row.get("volume", 0) + 1e-8))

        # 波动率
        if i >= 20:
            returns = [self.df.iloc[j]["close"] / self.df.iloc[j - 1]["close"] - 1 for j in range(i - 19, i + 1)]
            features.append(np.std(returns) * np.sqrt(252))
        else:
            features.append(0.0)

        # 技术指标
        for key in ["rsi_14", "macd_diff", "atr_ratio", "bb_position", "volume_ma_ratio"]:
            val = row.get(key, 0.0)
            features.append(np.clip(val, -5, 5) if not np.isnan(val) else 0.0)

        # HV比率
        features.append(row.get("hv_ratio", 0.0))

        # 持仓状态
        pos_onehot = [0, 0, 0]
        if self.position > 0:
            pos_onehot[0] = 1
        elif self.position < 0:
            pos_onehot[1] = 1
        else:
            pos_onehot[2] = 1
        features.extend(pos_onehot)
        features.append(self.cumulative_pnl * self.reward_scale)
        features.append(self.position / self.max_position)

        # 订单流特征 (来自模拟tick)
        features.append(row.get("tick_count", 0.0))
        features.append(row.get("buy_ratio", 0.5))
        features.append(row.get("vol_imbalance", 0.0))
        features.append(row.get("spread", 0.0))

        # 宏观
        for key in ["DXY_change", "US10Y_change", "VIX_value", "gold_dxy_ratio"]:
            val = row.get(key, 0.0)
            features.append(np.clip(val, -5, 5) if not np.isnan(val) else 0.0)

        # 仓位归一化
        features.append(self.position / self.max_position)

        arr = np.array(features, dtype=np.float32)
        arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
        return arr

    def _current_drawdown(self) -> float:
        """返回当前回撤比例（正数，如0.05=5%回撤）"""
        peak = max(self.equity_curve) if self.equity_curve else self.initial_capital
        current = self.equity_curve[-1] if self.equity_curve else self.initial_capital
        if peak <= 0:
            return 0.0
        return max(0.0, (peak - current) / peak)

    def _sharpe_ratio(self) -> float:
        if len(self.pnl_history) < 5:
            return 0.0
        returns = np.array(self.pnl_history) / self.initial_capital
        if np.std(returns) < 1e-10:
            return 0.0
        return np.mean(returns) / np.std(returns) * np.sqrt(252)

    def render(self) -> dict:
        return {
            "step": self.idx,
            "position": self.position,
            "capital": self.capital,
            "cumulative_pnl": round(self.cumulative_pnl, 2),
            "equity": round(self.equity_curve[-1], 2),
            "drawdown": round(self._current_drawdown() * 100, 2),
            "trades_count": len([t for t in self.trades if t.pnl != 0]),
        }

    def get_metrics(self) -> dict:
        """返回完整绩效指标"""
        equity = np.array(self.equity_curve)
        returns = np.diff(equity) / equity[:-1] if len(equity) > 1 else [0]
        pnl_array = np.array(self.pnl_history) if self.pnl_history else np.array([0])

        total_return = (equity[-1] - self.initial_capital) / self.initial_capital * 100
        n_trades = max(len([t for t in self.trades if abs(t.pnl) > 0.01]), 1)
        wins = sum(1 for p in self.pnl_history if p > 0)
        win_rate = wins / max(len(self.pnl_history), 1) * 100

        return {
            "total_return_pct": round(total_return, 2),
            "total_pnl": round(self.cumulative_pnl, 2),
            "sharpe_ratio": round(self._sharpe_ratio(), 3),
            "max_drawdown_pct": round(self._current_drawdown() * 100, 2),
            "win_rate_pct": round(win_rate, 1),
            "total_trades": n_trades,
            "final_equity": round(float(equity[-1]), 2),
            "volatility": round(float(np.std(returns) * np.sqrt(252) * 100), 3) if len(returns) > 1 else 0,
        }
