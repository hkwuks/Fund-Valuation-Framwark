"""市场状态检测单元测试"""

import sys

sys.path.insert(0, "backend/..")

import numpy as np
import pytest

from backend.fund_quant.backtest.regime_detector import (
    Regime,
    RegimeDetector,
    RegimeReport,
)


class TestRegimeDetector:
    """RegimeDetector 单元测试"""

    @pytest.fixture
    def detector(self) -> RegimeDetector:
        return RegimeDetector()

    def _generate_returns(
        self, segments: list, seed: int = 42
    ) -> np.ndarray:
        """
        生成合成收益率序列。

        segments: [(n_days, daily_std, daily_mean), ...]
        """
        rng = np.random.RandomState(seed)
        returns = []
        for n_days, std, mean in segments:
            returns.extend(rng.normal(mean, std, n_days).tolist())
        return np.array(returns, dtype=float)

    def test_detects_high_volatility(self, detector):
        """中间插入高波动段 -> high_volatility 状态被检测到。"""
        returns = self._generate_returns([
            (150, 0.008, 0.0005),   # 正常波动
            (80, 0.10, 0.001),      # 高波动
            (150, 0.008, 0.0005),   # 恢复正常
        ])
        report = detector.detect(returns, window=60)
        labels = [r.label for r in report.regimes]
        assert "high_volatility" in labels, (
            f"未检测到 high_volatility 状态，实际标签: {labels}"
        )

    def test_detects_low_volatility(self, detector):
        """中间插入低波动段 -> low_volatility 状态被检测到。"""
        returns = self._generate_returns([
            (120, 0.015, 0.0005),   # 正常波动 (≈23.8% 年化)
            (200, 0.002, 0.0003),   # 低波动 (≈3.2% 年化)
            (120, 0.015, 0.0005),   # 恢复正常
        ])
        report = detector.detect(returns, window=60)
        labels = [r.label for r in report.regimes]
        assert "low_volatility" in labels, (
            f"未检测到 low_volatility 状态，实际标签: {labels}"
        )

    def test_multiple_regimes_report(self, detector):
        """三个不同波动率段 -> 至少 3 个状态，警告非空。"""
        returns = self._generate_returns([
            (150, 0.002, 0.0003),   # 低波动
            (150, 0.015, 0.0005),   # 正常波动
            (150, 0.08, 0.001),     # 高波动
        ])
        report = detector.detect(returns, window=60, z_score_low=0.5)
        assert report.n_regimes >= 3, (
            f"期望至少 3 个状态，实际: {report.n_regimes}"
        )
        assert report.warning != "", "多状态时应包含警告"
        assert "回测区间覆盖" in report.warning

    def test_single_regime_no_warning(self, detector):
        """恒定波动率 -> 1 个状态，无警告。"""
        returns = np.ones(500) * 0.001
        report = detector.detect(returns, window=60)
        assert report.n_regimes == 1, (
            f"恒定波动率应得到 1 个状态，实际: {report.n_regimes}"
        )
        assert report.warning == "", "单状态时不应有警告"

    def test_too_short_data_single_regime(self, detector):
        """数据长度 < window + 1 -> 单个 normal 状态。"""
        returns = np.random.RandomState(42).normal(0.0005, 0.015, 50)
        report = detector.detect(returns, window=60)
        assert report.n_regimes == 1
        assert report.regimes[0].label == "normal"
        assert report.warning == ""

    def test_regime_metrics_computed(self, detector):
        """每个状态有非 NaN 的 ann_return, ann_vol, sharpe。"""
        returns = self._generate_returns([
            (150, 0.008, 0.0005),
            (80, 0.10, 0.001),
            (150, 0.008, 0.0005),
        ])
        report = detector.detect(returns, window=60)
        assert report.n_regimes >= 2
        for regime in report.regimes:
            assert not np.isnan(regime.ann_return), (
                f"{regime.label} ann_return 为 NaN"
            )
            assert not np.isnan(regime.ann_vol), (
                f"{regime.label} ann_vol 为 NaN"
            )
            assert not np.isnan(regime.sharpe), (
                f"{regime.label} sharpe 为 NaN"
            )
