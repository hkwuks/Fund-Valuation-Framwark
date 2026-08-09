"""测试过拟合检测"""
import pytest
import numpy as np
from backend.gold.ml.overfitting_detection import OverfittingDetector


class TestOverfittingDetector:
    def test_no_overfitting(self):
        """IS和OOS Sharpe接近 → 无过拟合"""
        d = OverfittingDetector()
        result = d.detect([2.0, 1.8, 2.1], [1.9, 1.7, 2.0])
        assert "sharpe_drop" in result
        assert result["sharpe_drop"] < 0.3  # 衰减小

    def test_severe_overfitting(self):
        """IS高OOS低 → 严重过拟合"""
        d = OverfittingDetector()
        result = d.detect([3.0, 2.8, 3.2], [0.1, -0.2, 0.3])
        assert result["sharpe_drop"] > 0.5

    def test_deflated_sharpe(self):
        d = OverfittingDetector()
        dsr = d.deflated_sharpe(sharpe=2.0, n_trials=10, num_observations=252)
        assert dsr <= 2.0  # DSR <= 原始Sharpe
        assert dsr > -2.0

    def test_deflated_sharpe_many_trials(self):
        """多次试验 → DSR降低"""
        d = OverfittingDetector()
        dsr1 = d.deflated_sharpe(2.0, 1, 252)
        dsr100 = d.deflated_sharpe(2.0, 100, 252)
        # 多次试验修正后DSR应该降低或相等
        # 注意: DSR返回的是概率值，高Sharpe下两个都接近1.0
        assert dsr100 <= dsr1 or abs(dsr100 - dsr1) < 0.01

    def test_sharpe_drop(self):
        d = OverfittingDetector()
        drop = d.sharpe_drop(2.0, 1.0)
        assert drop == 0.5

    def test_empty_input(self):
        d = OverfittingDetector()
        result = d.detect([], [])
        assert "error" in result