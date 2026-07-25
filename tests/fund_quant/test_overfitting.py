"""过拟合检测测试 — OverfittingDetector"""

from __future__ import annotations

import numpy as np
import pytest

from backend.fund_quant.backtest.overfitting import OverfittingDetector


class TestOverfittingDetector:
    def test_deflated_lower_than_raw(self):
        """50 次记录后 deflated < raw"""
        detector = OverfittingDetector()
        for _ in range(50):
            detector.record({"p": 1}, {"sharpe": 0.5})
        assert detector.adjusted_sharpe(0.5) < 0.5

    def test_min_btl_scales_with_trials(self):
        """相同 Sharpe 下 50 次试验的 MinBTL > 5 次试验"""
        det_few = OverfittingDetector()
        det_many = OverfittingDetector()
        for _ in range(5):
            det_few.record({}, {"sharpe": 0.3})
        for _ in range(50):
            det_many.record({}, {"sharpe": 0.3})

        btl_few = det_few.min_btl(0.3)
        btl_many = det_many.min_btl(0.3)
        assert btl_many > btl_few, (
            f"Expected more trials ({btl_many}) > fewer trials ({btl_few})"
        )

    def test_shuffle_random_returns(self):
        """随机正态收益率 (均值≈0) -> p_value > 0.05"""
        rng = np.random.RandomState(42)
        returns = rng.normal(0, 0.01, 500)
        detector = OverfittingDetector()
        detector.record({}, {"sharpe": 0.0})

        p = detector.shuffle_test(returns, n_shuffles=200)
        assert p > 0.05, f"Expected p > 0.05 for random returns, got {p}"

    def test_shuffle_known_signal(self):
        """强正漂移收益率 -> p_value < 0.05"""
        rng = np.random.RandomState(42)
        base = np.full(500, 0.001)
        noise = rng.normal(0, 0.0001, 500)
        returns = base + noise  # strong positive drift
        detector = OverfittingDetector()
        detector.record({}, {"sharpe": 0.0})

        p = detector.shuffle_test(returns, n_shuffles=200)
        assert p < 0.05, f"Expected p < 0.05 for strong signal, got {p}"

    def test_trial_count_increments(self):
        """每次 record() 调用递增试验计数"""
        detector = OverfittingDetector()
        assert len(detector._history) == 0
        detector.record({"a": 1}, {"sharpe": 0.5})
        assert len(detector._history) == 1
        detector.record({"b": 2}, {"sharpe": 0.6})
        assert len(detector._history) == 2

    def test_n_trials_in_report(self):
        """report() 的 n_trials 与 record 次数一致"""
        detector = OverfittingDetector()
        for _ in range(10):
            detector.record({}, {"sharpe": 0.3})

        rng = np.random.RandomState(42)
        returns = rng.normal(0.0005, 0.01, 500)
        report = detector.report(returns, sharpe=0.8, years=3.0)

        assert report.n_trials == 10
        assert report.total_attempts == 10

    def test_report_fields_complete(self):
        """report() 返回所有预期字段"""
        detector = OverfittingDetector()
        detector.record({}, {"sharpe": 0.5})

        rng = np.random.RandomState(42)
        returns = rng.normal(0.0005, 0.01, 500)
        report = detector.report(returns, sharpe=0.8, years=2.0)

        assert hasattr(report, "latest_sharpe")
        assert hasattr(report, "deflated_sharpe")
        assert hasattr(report, "min_btl_years")
        assert hasattr(report, "actual_years")
        assert hasattr(report, "min_btl_warning")
        assert hasattr(report, "shuffle_p_value")
        assert hasattr(report, "is_significant")
        assert hasattr(report, "n_trials")
        assert hasattr(report, "total_attempts")

    def test_min_btl_warning_when_insufficient(self):
        """actual_years < MinBTL -> 警告非空"""
        detector = OverfittingDetector()
        for _ in range(50):
            detector.record({}, {"sharpe": 0.5})

        rng = np.random.RandomState(42)
        returns = rng.normal(0.001, 0.01, 500)
        report = detector.report(returns, sharpe=0.5, years=0.5)

        assert "WARNING" in report.min_btl_warning

    def test_min_btl_no_warning_when_sufficient(self):
        """actual_years >= MinBTL -> 警告为空"""
        detector = OverfittingDetector()
        detector.record({}, {"sharpe": 1.5})

        rng = np.random.RandomState(42)
        returns = rng.normal(0.001, 0.01, 500)
        report = detector.report(returns, sharpe=1.5, years=10.0)

        assert report.min_btl_warning == ""

    def test_shuffle_p_value_in_range(self):
        """shuffle_p_value 总是在 [0, 1] 范围内"""
        detector = OverfittingDetector()
        detector.record({}, {"sharpe": 0.0})

        rng = np.random.RandomState(42)
        returns = rng.normal(0, 0.01, 500)
        p = detector.shuffle_test(returns, n_shuffles=50)

        assert 0.0 <= p <= 1.0

    def test_zero_sharpe_min_btl(self):
        """Sharpe <= 0 时 MinBTL 返回 0"""
        detector = OverfittingDetector()
        detector.record({}, {"sharpe": -0.1})
        assert detector.min_btl(0.0) == 0.0
        assert detector.min_btl(-0.5) == 0.0

    def test_no_attempts_adjusted_sharpe(self):
        """无 record 时 adjusted_sharpe 返回原始值"""
        detector = OverfittingDetector()
        assert detector.adjusted_sharpe(0.5) == 0.5

    def test_is_significant_false_for_random(self):
        """随机收益率 -> is_significant = False"""
        detector = OverfittingDetector()
        detector.record({}, {"sharpe": 0.0})

        rng = np.random.RandomState(42)
        returns = rng.normal(0, 0.01, 500)
        report = detector.report(returns, sharpe=0.1, years=2.0)

        assert report.is_significant is False

    def test_is_significant_true_for_strong_signal(self):
        """强信号 -> is_significant = True"""
        detector = OverfittingDetector()
        detector.record({}, {"sharpe": 0.0})

        rng = np.random.RandomState(42)
        base = np.full(500, 0.001)
        noise = rng.normal(0, 0.0001, 500)
        returns = base + noise
        report = detector.report(returns, sharpe=2.0, years=3.0)

        assert report.is_significant is True
