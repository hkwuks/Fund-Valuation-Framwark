"""测试对抗性验证"""
import pytest
import numpy as np
import pandas as pd
from backend.gold.ml.adversarial_validation import AdversarialValidator


class TestAdversarialValidator:
    def test_same_distribution(self):
        """来自同一分布的数据应返回低AUC"""
        X1 = pd.DataFrame(np.random.randn(200, 10))
        X2 = pd.DataFrame(np.random.randn(200, 10))
        v = AdversarialValidator()
        result = v.validate(X1, X2)
        # 理想情况下AUC≈0.5，但200样本下可能在0.4-0.6之间
        assert result["auc"] < 0.75
        assert result["severity"] in ("ok", "warning")

    def test_different_distribution(self):
        """来自不同分布的数据应返回高AUC"""
        X1 = pd.DataFrame(np.random.randn(200, 10))
        X2 = pd.DataFrame(np.random.randn(200, 10) + 3.0)  # 均值偏移
        v = AdversarialValidator()
        result = v.validate(X1, X2)
        assert result["auc"] > 0.6
        assert "auc" in result
        assert "feature_importance" in result

    def test_severity(self):
        v = AdversarialValidator()
        # 完全不可区分
        X1 = pd.DataFrame(np.random.randn(100, 5))
        r = v.validate(X1, X1.sample(frac=0.5, random_state=42))
        assert r["severity"] == "ok"

    def test_validate_folds(self):
        v = AdversarialValidator()
        pairs = [
            (pd.DataFrame(np.random.randn(100, 5)), pd.DataFrame(np.random.randn(50, 5))),
            (pd.DataFrame(np.random.randn(100, 5)), pd.DataFrame(np.random.randn(50, 5))),
        ]
        result = v.validate_folds(pairs)
        assert "mean_auc" in result
        assert "n_folds" in result
        assert result["n_folds"] == 2