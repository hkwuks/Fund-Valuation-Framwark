"""
市场状态检测 — 基于滚动波动率聚类的市场状态识别。

通过分析基金日收益率的滚动年化波动率，将市场划分为
高波动、正常、低波动三种状态，用于评估回测是否覆盖多个市场状态。
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List

from loguru import logger

__all__ = ["Regime", "RegimeReport", "RegimeDetector"]

_WARNING_TEMPLATE = "回测区间覆盖 {n} 个市场状态，不同状态下的策略表现可能不一致。"


@dataclass
class Regime:
    """单个市场状态。"""

    label: str  # "low_volatility" | "normal" | "high_volatility"
    start_idx: int
    end_idx: int
    duration_days: int
    ann_return: float
    ann_vol: float
    sharpe: float


@dataclass
class RegimeReport:
    """市场状态检测报告。"""

    regimes: List[Regime]
    n_regimes: int
    warning: str


class RegimeDetector:
    """基于滚动波动率聚类的市场状态检测器。"""

    def detect(
        self,
        daily_returns: np.ndarray,
        window: int = 60,
        z_score_high: float = 1.5,
        z_score_low: float = 1.0,
        min_days: int = 20,
    ) -> RegimeReport:
        """
        检测市场状态。

        Args:
            daily_returns: 日收益率序列（小数形式，如 0.01 表示 1%）。
            window: 滚动波动率计算窗口。
            z_score_high: 高波动阈值（标准差倍数），默认 1.5。
            z_score_low: 低波动阈值（标准差倍数），默认 1.0。
            min_days: 最小持续时间（少于该天数的状态被合并到相邻状态）。

        Returns:
            RegimeReport: 包含所有检测到的市场状态。
        """
        n = len(daily_returns)

        # Edge case: 数据不足一个完整窗口
        if n < window + 1:
            logger.warning(
                f"数据长度 {n} < 窗口 {window} + 1，仅返回单个 normal 状态"
            )
            return self._single_normal_report(daily_returns)

        # 1. 计算滚动年化波动率
        rolling_vol = self._rolling_annualized_vol(daily_returns, window)

        # 2. 分类
        labels = self._classify(rolling_vol, z_score_high, z_score_low, window)

        # 3. 合并短持续时间状态
        labels = self._filter_short_regimes(labels, min_days)

        # 4. 构建 Regime 对象
        regimes = self._build_regimes(labels, daily_returns)

        # 5. 生成警告
        n_regimes = len(regimes)
        warning = _WARNING_TEMPLATE.format(n=n_regimes) if n_regimes > 1 else ""

        if n_regimes > 1:
            labels_list = [r.label for r in regimes]
            logger.info(f"检测到 {n_regimes} 个市场状态: {labels_list}")

        return RegimeReport(regimes=regimes, n_regimes=n_regimes, warning=warning)

    def _rolling_annualized_vol(
        self, returns: np.ndarray, window: int
    ) -> np.ndarray:
        """
        计算滚动年化波动率。

        - 前 window-1 天使用扩展窗口标准差 (expanding std)
        - 第 window 天起使用固定窗口标准差 (rolling window)
        """
        vol = (
            pd.Series(returns)
            .rolling(window, min_periods=1)
            .std(ddof=1)
            .fillna(0.0)
            * np.sqrt(252)
        )
        return vol.values

    def _classify(
        self, rolling_vol: np.ndarray, z_high: float, z_low: float,
        window: int = 60,
    ) -> np.ndarray:
        """根据滚动波动率分类每一天的市场状态。

        仅使用满窗口期的波动率值计算统计量（排除前 window-1 个扩展窗口值），
        避免早期小样本的极端值干扰阈值。
        """
        valid_start = min(window - 1, len(rolling_vol) - 1)
        valid_vol = rolling_vol[valid_start:]
        if len(valid_vol) == 0:
            return np.full(len(rolling_vol), "normal", dtype=object)

        mean_vol = np.mean(valid_vol)
        std_vol = np.std(valid_vol, ddof=1)

        # 对前 window-1 天使用靠近的 valid 分类
        labels = np.full(len(rolling_vol), "normal", dtype=object)
        labels[valid_start:][rolling_vol[valid_start:] > mean_vol + z_high * std_vol] = "high_volatility"
        labels[valid_start:][rolling_vol[valid_start:] < mean_vol - z_low * std_vol] = "low_volatility"

        # 前 window-1 天跟随其后的第一个 valid 分类
        if valid_start > 0 and valid_start < len(labels):
            labels[:valid_start] = labels[valid_start]

        return labels

    def _filter_short_regimes(
        self, labels: np.ndarray, min_days: int
    ) -> np.ndarray:
        """
        合并持续时间短于 min_days 的状态到相邻状态。

        如果两侧都存在，合并到持续时间更长的那一侧。
        """
        n = len(labels)
        if n == 0:
            return labels

        labels = labels.copy()

        while True:
            groups = self._get_groups(labels)
            if len(groups) <= 1:
                break

            merged = False
            for idx, (start, end, _label) in enumerate(groups):
                duration = end - start
                if duration >= min_days:
                    continue

                merged = True
                if idx == 0:
                    new_label = groups[1][2]
                elif idx == len(groups) - 1:
                    new_label = groups[idx - 1][2]
                else:
                    left_dur = groups[idx - 1][1] - groups[idx - 1][0]
                    right_dur = groups[idx + 1][1] - groups[idx + 1][0]
                    new_label = (
                        groups[idx - 1][2]
                        if left_dur >= right_dur
                        else groups[idx + 1][2]
                    )

                labels[start:end] = new_label
                break  # 每次合并后重新扫描

            if not merged:
                break

        return labels

    def _get_groups(self, labels: np.ndarray) -> List[tuple]:
        """获取连续相同标签的分组。返回 List[(start, end, label)]。"""
        groups = []
        i = 0
        n = len(labels)
        while i < n:
            j = i
            while j < n and labels[j] == labels[i]:
                j += 1
            groups.append((i, j, labels[i]))
            i = j
        return groups

    def _build_regimes(
        self, labels: np.ndarray, returns: np.ndarray
    ) -> List[Regime]:
        """从标签数组构建 Regime 对象列表。"""
        groups = self._get_groups(labels)
        regimes = []
        for start, end, label in groups:
            returns_slice = returns[start:end]
            ann_return, ann_vol, sharpe = self._regime_metrics(returns_slice)
            regimes.append(
                Regime(
                    label=label,
                    start_idx=start,
                    end_idx=end - 1,
                    duration_days=end - start,
                    ann_return=round(ann_return, 6),
                    ann_vol=round(ann_vol, 6),
                    sharpe=round(sharpe, 4),
                )
            )
        return regimes

    def _regime_metrics(self, returns: np.ndarray) -> tuple:
        """计算区间的年化收益、年化波动率和夏普比率。"""
        if len(returns) == 0:
            return 0.0, 0.0, 0.0

        mean_ret = float(np.mean(returns))
        ann_return = (1 + mean_ret) ** 252 - 1
        ann_vol = (
            float(np.std(returns, ddof=1) * np.sqrt(252))
            if len(returns) > 1
            else 0.0
        )
        sharpe = (ann_return - 0.025) / ann_vol if ann_vol > 1e-10 else 0.0
        return ann_return, ann_vol, sharpe

    def _single_normal_report(self, returns: np.ndarray) -> RegimeReport:
        """数据不足时返回单个 normal 状态。"""
        n = len(returns)
        if n == 0:
            regime = Regime("normal", 0, 0, 1, 0.0, 0.0, 0.0)
        else:
            ann_return, ann_vol, sharpe = self._regime_metrics(returns)
            regime = Regime(
                "normal", 0, n - 1, n, ann_return, ann_vol, sharpe
            )
        return RegimeReport(regimes=[regime], n_regimes=1, warning="")
