"""测试策略退化监控"""
import numpy as np
from backend.gold.ml.strategy_decay import StrategyDecayMonitor


class TestStrategyDecayMonitor:
    def test_entropy_normal(self):
        m = StrategyDecayMonitor()
        # 稳定熵值
        history = [1.0] * 100
        result = m.check_entropy(history)
        assert "anomaly_detected" in result
        assert result["anomaly_detected"] == False

    def test_entropy_drop(self):
        m = StrategyDecayMonitor()
        # 熵值突然下降到几乎为零
        history = [1.0] * 50 + [0.05] * 50
        result = m.check_entropy(history)
        assert "anomaly_detected" in result

    def test_action_distribution_identical(self):
        m = StrategyDecayMonitor()
        dist = np.array([0.5, 0.3, 0.2])
        result = m.check_action_distribution(dist, dist)
        assert result["kl_divergence"] < 0.01

    def test_action_distribution_different(self):
        m = StrategyDecayMonitor()
        current = np.array([0.1, 0.1, 0.8])
        baseline = np.array([0.5, 0.3, 0.2])
        result = m.check_action_distribution(current, baseline)
        assert result["kl_divergence"] > 0.1

    def test_return_decay(self):
        m = StrategyDecayMonitor()
        # 收益逐渐下降
        returns = [0.1] * 20 + [0.01] * 20 + [-0.05] * 20
        result = m.check_return_decay(returns)
        # 至少能检测到衰减
        assert "recent_mean" in result
        assert result["recent_mean"] < result["historical_mean"]

    def test_return_decay_stable(self):
        m = StrategyDecayMonitor()
        returns = [0.05] * 60
        result = m.check_return_decay(returns)
        assert "recent_mean" in result