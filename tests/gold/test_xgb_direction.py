"""
Tests for XGBDirectionPredictor
"""
import numpy as np
import pandas as pd
import pytest
from datetime import datetime, timedelta

from backend.gold.ml.xgb_direction_predictor import XGBDirectionPredictor


def _make_synthetic_data(n_days: int = 500) -> pd.DataFrame:
    """生成模拟K线数据"""
    np.random.seed(42)
    base = 2000.0
    dates = [datetime.now() - timedelta(days=i) for i in range(n_days, 0, -1)]
    closes = base + np.cumsum(np.random.randn(n_days) * 2)
    closes = np.maximum(closes, 1800)  # 避免负值
    df = pd.DataFrame({
        "datetime": dates,
        "open": closes * 0.999,
        "high": closes * 1.005,
        "low": closes * 0.995,
        "close": closes,
        "volume": np.random.randint(1000, 5000, n_days),
    })
    # 加入一些宏观列（部分模拟）
    df["DXY_value"] = 100 + np.random.randn(n_days) * 2
    df["VIX_value"] = 20 + np.random.randn(n_days) * 3
    df["US10Y_value"] = 4 + np.random.randn(n_days) * 0.2
    return df


class TestXGBDirectionPredictor:
    def test_train_basic(self):
        """单次训练验证"""
        df = _make_synthetic_data(500)
        predictor = XGBDirectionPredictor()
        result = predictor.train(df, test_size=0.2)

        assert "accuracy" in result
        assert 0 <= result["accuracy"] <= 1
        assert result["n_train"] > 0
        assert result["n_test"] > 0
        assert len(result["confusion_matrix"]) == 2
        assert len(result["feature_importance"]) > 0

    def test_predict(self):
        """预测方向+置信度"""
        df = _make_synthetic_data(500)
        predictor = XGBDirectionPredictor()
        predictor.train(df, test_size=0.2)

        # 构造特征向量
        n_features = len(predictor._feature_cols)
        features = np.random.randn(n_features)

        direction, confidence = predictor.predict(features)
        assert direction in (0, 1)
        assert 0 <= confidence <= 1

    def test_predict_requires_model(self):
        """未训练时predict应报错"""
        predictor = XGBDirectionPredictor()
        with pytest.raises(RuntimeError, match="not trained"):
            predictor.predict(np.random.randn(5))

    def test_walk_forward_structure(self):
        """Walk-Forward返回结构验证"""
        df = _make_synthetic_data(600)
        predictor = XGBDirectionPredictor()
        result = predictor.train_walk_forward(
            df, n_splits=3, train_window=252, test_window=63
        )

        assert result.mean_accuracy >= 0
        assert result.mean_accuracy <= 1
        assert len(result.fold_results) == 3
        assert len(result.accuracies) == 3
        assert result.std_accuracy >= 0

        for fold in result.fold_results:
            assert 1 <= fold.fold <= 3
            assert 0 <= fold.accuracy <= 1
            assert len(fold.conf_matrix) == 2
            assert fold.n_train > 0
            assert fold.n_test > 0

    def test_walk_forward_insufficient_data(self):
        """数据不足时应报错"""
        df = _make_synthetic_data(100)
        predictor = XGBDirectionPredictor()
        with pytest.raises(ValueError, match="Need at least"):
            predictor.train_walk_forward(df, n_splits=5, train_window=252, test_window=63)

    def test_feature_importance(self):
        """特征重要性返回"""
        df = _make_synthetic_data(500)
        predictor = XGBDirectionPredictor()
        predictor.train(df, test_size=0.2)

        importance = predictor.get_feature_importance()
        assert len(importance) > 0
        # 按重要性降序
        values = list(importance.values())
        assert all(values[i] >= values[i + 1] for i in range(len(values) - 1))

    def test_feature_importance_before_train(self):
        """未训练时特征重要性应返回空"""
        predictor = XGBDirectionPredictor()
        assert predictor.get_feature_importance() == {}

    def test_walk_forward_predict_after(self):
        """Walk-Forward后predict可用"""
        df = _make_synthetic_data(600)
        predictor = XGBDirectionPredictor()
        predictor.train_walk_forward(df, n_splits=3, train_window=252, test_window=63)

        n_features = len(predictor._feature_cols)
        direction, confidence = predictor.predict(np.random.randn(n_features))
        assert direction in (0, 1)
        assert 0 <= confidence <= 1