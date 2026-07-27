"""
XGBoost 方向预测器 — 验证特征预测力（PRD前置条件：OOS方向准确率 > 53%才值得上RL）
"""
import numpy as np
import pandas as pd
import json
import os
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from sklearn.metrics import accuracy_score, confusion_matrix, classification_report
from loguru import logger

from backend.gold.ml.features import FeatureEngineer


@dataclass
class FoldResult:
    """单折验证结果"""
    fold: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    accuracy: float
    conf_matrix: list
    report: dict
    n_train: int
    n_test: int


@dataclass
class WalkForwardResult:
    """Walk-Forward 整体结果"""
    fold_results: List[FoldResult]
    mean_accuracy: float
    std_accuracy: float
    accuracies: List[float]
    total_train: int
    total_test: int
    feature_importance: Dict[str, float]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class XGBDirectionPredictor:
    """XGBoost 方向预测器（二分类：涨/跌）"""

    def __init__(self, model_dir: Optional[str] = None):
        self.model_dir = model_dir or "/tmp/gold_xgb_models"
        os.makedirs(self.model_dir, exist_ok=True)
        self.feature_engineer = FeatureEngineer()
        self._model = None
        self._feature_cols: List[str] = []

    # ── Public API ──

    def train_walk_forward(
        self,
        df: pd.DataFrame,
        n_splits: int = 5,
        train_window: int = 252,
        test_window: int = 63,
    ) -> WalkForwardResult:
        """Walk-Forward训练+验证，返回每折指标"""
        df = self._prepare_data(df)
        total_len = len(df)
        min_required = train_window + test_window
        if total_len < min_required:
            raise ValueError(
                f"Need at least {min_required} rows (train={train_window}, test={test_window}), "
                f"got {total_len}"
            )

        folds: List[FoldResult] = []
        all_importances: List[Dict[str, float]] = []

        for i in range(n_splits):
            test_end = total_len - i * test_window
            test_start = test_end - test_window
            train_end = test_start
            train_start = train_end - train_window

            if train_start < 0:
                logger.warning(f"Fold {i+1}: insufficient data, stopping early")
                break

            train_idx = df.index[train_start:train_end]
            test_idx = df.index[test_start:test_end]

            X_train = df.loc[train_idx, self._feature_cols]
            y_train = df.loc[train_idx, "target"]
            X_test = df.loc[test_idx, self._feature_cols]
            y_test = df.loc[test_idx, "target"]

            model = self._train_xgb(X_train, y_train)
            y_pred = model.predict(X_test)

            acc = float(accuracy_score(y_test, y_pred))
            cm = confusion_matrix(y_test, y_pred).tolist()
            cr = classification_report(y_test, y_pred, output_dict=True, zero_division=0)

            folds.append(FoldResult(
                fold=i + 1,
                train_start=str(df.index[train_start]),
                train_end=str(df.index[train_end - 1]),
                test_start=str(df.index[test_start]),
                test_end=str(df.index[test_end - 1]),
                accuracy=acc,
                conf_matrix=cm,
                report=cr,
                n_train=len(y_train),
                n_test=len(y_test),
            ))

            if hasattr(model, "feature_importances_"):
                importances = dict(zip(self._feature_cols, model.feature_importances_))
                all_importances.append(importances)

        accuracies = [f.accuracy for f in folds]
        mean_acc = float(np.mean(accuracies)) if accuracies else 0.0
        std_acc = float(np.std(accuracies)) if len(accuracies) > 1 else 0.0

        # 平均特征重要性
        feature_importance: Dict[str, float] = {}
        if all_importances:
            keys = all_importances[0].keys()
            for k in keys:
                feature_importance[k] = float(np.mean([imp[k] for imp in all_importances]))
            feature_importance = dict(sorted(feature_importance.items(), key=lambda x: -x[1]))

        # 用所有数据训练最终模型（供后续 predict 使用）
        self._train_final(df)

        result = WalkForwardResult(
            fold_results=folds,
            mean_accuracy=mean_acc,
            std_accuracy=std_acc,
            accuracies=accuracies,
            total_train=sum(f.n_train for f in folds),
            total_test=sum(f.n_test for f in folds),
            feature_importance=feature_importance,
        )

        logger.info(
            f"Walk-Forward done: {n_splits} folds, "
            f"mean accuracy={mean_acc:.4f} ± {std_acc:.4f}"
        )
        return result

    def train(self, df: pd.DataFrame, test_size: float = 0.2) -> Dict[str, Any]:
        """单次训练+验证"""
        df = self._prepare_data(df)
        split_idx = int(len(df) * (1 - test_size))

        X_train = df.iloc[:split_idx][self._feature_cols]
        y_train = df.iloc[:split_idx]["target"]
        X_test = df.iloc[split_idx:][self._feature_cols]
        y_test = df.iloc[split_idx:]["target"]

        self._model = self._train_xgb(X_train, y_train)
        y_pred = self._model.predict(X_test)

        acc = float(accuracy_score(y_test, y_pred))
        cm = confusion_matrix(y_test, y_pred).tolist()
        cr = classification_report(y_test, y_pred, output_dict=True, zero_division=0)

        # 特征重要性
        importance = {}
        if hasattr(self._model, "feature_importances_"):
            importance = dict(zip(self._feature_cols, self._model.feature_importances_))
            importance = dict(sorted(importance.items(), key=lambda x: -x[1]))

        return {
            "accuracy": acc,
            "confusion_matrix": cm,
            "classification_report": cr,
            "feature_importance": importance,
            "n_train": len(y_train),
            "n_test": len(y_test),
            "test_size": test_size,
        }

    def predict(self, features: np.ndarray) -> Tuple[int, float]:
        """预测方向 (1=涨, 0=跌) + 置信度"""
        if self._model is None:
            raise RuntimeError("Model not trained. Call train() or train_walk_forward() first.")

        features_2d = features.reshape(1, -1) if features.ndim == 1 else features
        pred = int(self._model.predict(features_2d)[0])

        if hasattr(self._model, "predict_proba"):
            proba = self._model.predict_proba(features_2d)[0]
            classes = self._model.classes_
            class_idx = list(classes).index(pred)
            confidence = float(proba[class_idx])
        else:
            confidence = 0.5

        return pred, confidence

    def get_feature_importance(self) -> Dict[str, float]:
        """返回特征重要性排序"""
        if self._model is None or not hasattr(self._model, "feature_importances_"):
            return {}
        importance = dict(zip(self._feature_cols, self._model.feature_importances_))
        return dict(sorted(importance.items(), key=lambda x: -x[1]))

    # ── Internal ──

    def _prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成特征和标签，返回 index 为位置序的 DataFrame"""
        df_sorted = df.sort_values("datetime" if "datetime" in df.columns else "date").reset_index(drop=True)

        # 用 FeatureEngineer 生成特征
        X, _ = self.feature_engineer.prepare_features(df_sorted, target_horizon=1)
        # 移除非数值列（datetime, date 等）
        X = X.select_dtypes(include=[np.number])
        # 移除任何可能残留的 datetime/date 列名
        drop_cols = [c for c in X.columns if 'datetime' in c.lower() or 'date' in c.lower() or 'time' in c.lower()]
        if drop_cols:
            X = X.drop(columns=drop_cols)
        self._feature_cols = list(X.columns)

        # 构建方向标签，对齐 X 的 index
        # prepare_features 的 target 是 shift(-1) 的 return，我们用原始 close 算方向
        original_idx = X.index  # 这些行在 df_sorted 中的位置
        close = df_sorted.loc[original_idx, "close"].values
        # 下一根 close（最后一行无下一根 → 用自身填充，后续 dropna 去掉）
        next_close = np.roll(close, -1)
        next_close[-1] = close[-1]
        target = (next_close > close).astype(float)
        target[-1] = np.nan  # 最后一行无下一根

        result = X.copy()
        result["target"] = target
        result = result.dropna()
        result["target"] = result["target"].astype(int)
        return result

    def _train_final(self, df: pd.DataFrame):
        """用全部数据训练最终模型"""
        X = df[self._feature_cols]
        y = df["target"]
        self._model = self._train_xgb(X, y)

    def _train_xgb(self, X: pd.DataFrame, y: pd.Series):
        """训练 XGBoost 分类器"""
        try:
            import xgboost as xgb
        except ImportError:
            raise ImportError("xgboost is required. Install with: pip install xgboost")

        # 计算类别权重
        n_pos = int(y.sum())
        n_neg = int(len(y) - n_pos)
        scale_pos_weight = n_neg / n_pos if n_pos > 0 else 1.0

        model = xgb.XGBClassifier(
            max_depth=4,
            n_estimators=200,
            learning_rate=0.05,
            scale_pos_weight=scale_pos_weight,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=0.1,
            random_state=42,
            verbosity=0,
            use_label_encoder=False,
        )

        model.fit(
            X, y,
            verbose=False,
        )
        return model