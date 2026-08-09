"""对抗性验证 — 检测训练集和测试集是否可区分"""
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.model_selection import StratifiedKFold
from loguru import logger


class AdversarialValidator:
    """
    对抗性验证 — 检测训练集和测试集是否存在分布偏移。

    AUC ≈ 0.5 → 不可区分 ✅
    AUC > 0.7  → 严重泄漏/偏移 ⚠️
    """

    def __init__(self, n_estimators: int = 100, max_depth: int = 5):
        self.n_estimators = n_estimators
        self.max_depth = max_depth

    def validate(self, X_train: pd.DataFrame, X_test: pd.DataFrame) -> dict:
        """
        验证两组数据是否可区分。

        Returns:
            auc: ROC AUC
            accuracy: 分类准确率
            feature_importance: 最重要特征排序
            severity: "ok" / "warning" / "critical"
        """
        n_train = len(X_train)
        n_test = len(X_test)

        # 合并后打标签
        X = pd.concat([X_train, X_test], axis=0).reset_index(drop=True)
        X = X.select_dtypes(include=[np.number]).fillna(0)
        y = np.array([0] * n_train + [1] * n_test)

        # 交叉验证
        skf = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
        aucs = []
        importances = []

        for train_idx, val_idx in skf.split(X, y):
            clf = RandomForestClassifier(
                n_estimators=self.n_estimators,
                max_depth=self.max_depth,
                random_state=42,
                n_jobs=-1,
            )
            clf.fit(X.iloc[train_idx], y[train_idx])
            y_pred = clf.predict_proba(X.iloc[val_idx])[:, 1]
            aucs.append(roc_auc_score(y[val_idx], y_pred))
            if hasattr(clf, "feature_importances_"):
                importances.append(clf.feature_importances_)

        mean_auc = float(np.mean(aucs))
        if mean_auc > 0.7:
            severity = "critical"
        elif mean_auc > 0.6:
            severity = "warning"
        else:
            severity = "ok"

        # 特征重要性
        feat_imp = {}
        if importances:
            mean_imp = np.mean(importances, axis=0)
            cols = X.columns[:len(mean_imp)]
            feat_imp = dict(zip(cols, mean_imp))
            feat_imp = dict(sorted(feat_imp.items(), key=lambda x: -x[1])[:10])

        result = {
            "auc": round(mean_auc, 4),
            "auc_std": round(float(np.std(aucs)), 4),
            "accuracy": round(float(accuracy_score(y, clf.predict(X))), 4),
            "severity": severity,
            "feature_importance": feat_imp,
            "n_train": n_train,
            "n_test": n_test,
        }
        logger.info(f"[AdversarialValidation] AUC={mean_auc:.4f} ({severity})")
        return result

    def validate_folds(self, train_test_pairs: list[tuple]) -> dict:
        """批量验证多个fold"""
        results = [self.validate(train, test) for train, test in train_test_pairs]
        aucs = [r["auc"] for r in results]
        severities = [r["severity"] for r in results]
        n_critical = sum(1 for s in severities if s == "critical")
        return {
            "fold_results": results,
            "mean_auc": round(float(np.mean(aucs)), 4),
            "std_auc": round(float(np.std(aucs)), 4),
            "max_auc": round(float(max(aucs)), 4),
            "n_folds": len(results),
            "n_critical": n_critical,
            "passed": n_critical == 0,
        }