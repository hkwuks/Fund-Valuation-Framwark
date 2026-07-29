"""策略退化监控 — 熵漂移、动作分布偏移、收益衰减"""

import numpy as np
from scipy.stats import entropy as kl_divergence


class StrategyDecayMonitor:
    """
    策略退化监控

    监控指标：
    - 策略熵值漂移 (entropy moving average)
    - 动作分布偏移 (action distribution relative entropy)
    - 特征重要性漂移
    - 收益衰减
    """

    def __init__(self, entropy_window: int = 50, z_score_threshold: float = 2.0):
        self.entropy_window = entropy_window
        self.z_score_threshold = z_score_threshold
        self._entropy_baseline: dict = {}

    def check_entropy(self, entropy_history: list[float]) -> dict:
        """
        检测熵值异常

        使用滑动窗口Z-score检测: 最新熵值是否偏离基线2个标准差以上

        Args:
            entropy_history: 熵值历史列表

        Returns:
            dict: {anomaly_detected, z_score, mean, std, current_entropy, trend}
        """
        if len(entropy_history) < 10:
            return {"anomaly_detected": False, "z_score": 0.0}

        arr = np.array(entropy_history)
        recent = arr[-min(self.entropy_window, len(arr)):]
        mean = float(np.mean(recent))
        std = float(np.std(recent)) + 1e-10
        current = float(arr[-1])
        z = (current - mean) / std

        # 趋势: 最近20个点线性回归斜率
        trend_window = min(20, len(arr))
        if trend_window >= 5:
            x = np.arange(trend_window)
            y = arr[-trend_window:]
            slope = np.polyfit(x, y, 1)[0]
        else:
            slope = 0.0

        return {
            "anomaly_detected": abs(z) > self.z_score_threshold,
            "z_score": round(z, 3),
            "mean": round(mean, 4),
            "std": round(std, 4),
            "current_entropy": round(current, 4),
            "trend": "rising" if slope > 0.01 else ("falling" if slope < -0.01 else "stable"),
            "trend_slope": round(slope, 6),
        }

    def check_action_distribution(
        self, current: np.ndarray, baseline: np.ndarray
    ) -> dict:
        """
        KL散度检测动作分布偏移

        Args:
            current: 当前动作分布 (n_actions,)
            baseline: 基线动作分布 (n_actions,)

        Returns:
            dict: {kl_divergence, drift_detected, threshold}
        """
        eps = 1e-10
        p = np.array(current, dtype=np.float64) + eps
        q = np.array(baseline, dtype=np.float64) + eps
        p = p / p.sum()
        q = q / q.sum()

        kl = float(np.sum(p * np.log(p / q)))

        # 阈值: 0.1 为显著偏移
        threshold = 0.1
        return {
            "kl_divergence": round(kl, 6),
            "drift_detected": kl > threshold,
            "threshold": threshold,
        }

    def check_return_decay(
        self, recent_returns: list[float], window: int = 20
    ) -> dict:
        """
        收益衰减检测

        使用滚动窗口比较近期收益 vs 历史收益:
        - 近期均值 < 历史均值 - 1标准差 => 衰减

        Args:
            recent_returns: 收益序列
            window: 近期窗口

        Returns:
            dict: {decay_detected, recent_mean, historical_mean, decline_pct}
        """
        if len(recent_returns) < window * 2:
            return {"decay_detected": False, "reason": "insufficient_data"}

        arr = np.array(recent_returns)
        recent = arr[-window:]
        historical = arr[:-window]

        recent_mean = float(np.mean(recent))
        hist_mean = float(np.mean(historical))
        hist_std = float(np.std(historical)) + 1e-10

        decay_detected = recent_mean < hist_mean - hist_std
        decline_pct = (
            (hist_mean - recent_mean) / abs(hist_mean) if abs(hist_mean) > 1e-10 else 0.0
        )

        return {
            "decay_detected": decay_detected,
            "recent_mean": round(recent_mean, 6),
            "historical_mean": round(hist_mean, 6),
            "decline_pct": round(float(decline_pct), 4),
            "z_score": round((recent_mean - hist_mean) / hist_std, 3),
        }

    def comprehensive_check(
        self,
        entropy_history: list[float] = None,
        action_distribution: np.ndarray = None,
        baseline_distribution: np.ndarray = None,
        return_history: list[float] = None,
    ) -> dict:
        """
        综合退化检测

        Args:
            entropy_history: 熵值历史
            action_distribution: 当前动作分布
            baseline_distribution: 基线动作分布
            return_history: 收益历史

        Returns:
            dict: 综合检测结果
        """
        result = {}

        if entropy_history:
            result["entropy"] = self.check_entropy(entropy_history)

        if action_distribution is not None and baseline_distribution is not None:
            result["action_distribution"] = self.check_action_distribution(
                action_distribution, baseline_distribution
            )

        if return_history:
            result["return_decay"] = self.check_return_decay(return_history)

        # 综合判定
        flags = []
        if result.get("entropy", {}).get("anomaly_detected"):
            flags.append("熵值异常")
        if result.get("action_distribution", {}).get("drift_detected"):
            flags.append("动作分布偏移")
        if result.get("return_decay", {}).get("decay_detected"):
            flags.append("收益衰减")

        result["decay_detected"] = len(flags) >= 2
        result["flags"] = flags
        result["verdict"] = (
            "策略退化风险高" if len(flags) >= 2
            else "策略退化预警" if len(flags) == 1
            else "策略状态正常"
        )
        return result