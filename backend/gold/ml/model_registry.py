"""模型版本管理 — 自动保存/排名/回滚/清理"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

import torch
from loguru import logger


class ModelRegistry:
    """模型版本管理

    - 自动保存checkpoint (含metrics/超参/训练时间)
    - 按Sharpe/Return排名
    - 支持回滚到历史版本
    - 模型元数据持久化（JSON索引）
    """

    def __init__(self, registry_dir: str = None):
        self.registry_dir = Path(registry_dir or self._default_dir())
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.registry_dir / "index.json"
        self._index: dict = self._load_index()

    # ── public API ──────────────────────────────────────────────

    def save(self, model: "PPOAgent", name: str, metrics: dict,
             params: dict = None, tags: list = None) -> str:
        """保存模型版本，返回版本ID"""
        ver = f"{name}_{datetime.now():%Y%m%d_%H%M%S}"
        ver_dir = self.registry_dir / ver
        ver_dir.mkdir(parents=True, exist_ok=True)

        # 保存模型checkpoint
        pt_path = ver_dir / "model.pt"
        model.save(str(pt_path), metrics)

        # 保存配置
        if params is None:
            params = self._extract_params(model)
        with open(ver_dir / "config.json", "w") as f:
            json.dump(params, f, indent=2, default=str)

        # 保存指标
        with open(ver_dir / "metrics.json", "w") as f:
            json.dump(metrics, f, indent=2, default=str)

        # 保存标签
        if tags:
            with open(ver_dir / "tags.txt", "w") as f:
                f.write("\n".join(tags))

        # 更新索引
        self._index[ver] = {
            "version": ver,
            "name": name,
            "created": datetime.now().isoformat(),
            "metrics": metrics,
            "params": params,
            "tags": tags or [],
            "path": str(ver_dir),
        }
        self._save_index()
        logger.info(f"[Registry] saved {ver}")
        return ver

    def load(self, version_id: str) -> tuple:
        """加载指定版本，返回 (agent, metadata)"""
        entry = self._index.get(version_id)
        if not entry:
            raise ValueError(f"Version {version_id} not found")
        pt_path = os.path.join(entry["path"], "model.pt")
        if not os.path.isfile(pt_path):
            raise FileNotFoundError(f"Checkpoint not found: {pt_path}")
        # 导入在调用时做，避免循环 import
        from .agent import PPOAgent
        agent = PPOAgent(
            obs_dim=entry["params"].get("obs_dim", 30),
            n_actions=entry["params"].get("n_actions", 12),
            lr=entry["params"].get("lr", 3e-4),
            hidden_dim=entry["params"].get("hidden_dim", 256),
            device=entry["params"].get("device", "auto"),
        )
        agent.load(pt_path)
        return agent, entry

    def list(self, sort_by: str = "sharpe", limit: int = 10) -> list[dict]:
        """按指标排序列出模型"""
        versions = list(self._index.values())
        # 提取排序值，不存在的指标排最后
        def _sort_key(v):
            m = v.get("metrics", {})
            val = m.get(sort_by, m.get(sort_by.replace("sharpe", "sharpe_ratio"), None))
            return val if val is not None else float("-inf")
        versions.sort(key=_sort_key, reverse=True)
        return versions[:limit]

    def get_best(self, metric: str = "sharpe") -> dict:
        """获取最佳版本"""
        ranked = self.list(sort_by=metric, limit=1)
        return ranked[0] if ranked else {}

    def get(self, version_id: str) -> dict:
        """获取指定版本的元数据"""
        return self._index.get(version_id, {})

    def rollback(self, version_id: str) -> bool:
        """回滚到指定版本（标记为当前版本）"""
        if version_id not in self._index:
            logger.error(f"[Registry] rollback failed: {version_id} not found")
            return False
        self._index["_current"] = version_id
        self._save_index()
        logger.info(f"[Registry] rollback to {version_id}")
        return True

    def delete_old(self, max_versions: int = 20):
        """删除旧版本，保留最新N个"""
        versions = sorted(self._index.keys(), key=lambda v: self._index[v].get("created", ""), reverse=True)
        keep = set(versions[:max_versions])
        for ver in versions:
            if ver in keep or ver.startswith("_"):
                continue
            self._delete_version(ver)
        logger.info(f"[Registry] pruned to {max_versions} versions")

    # ── internal ────────────────────────────────────────────────

    @staticmethod
    def _default_dir() -> str:
        base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "backend", "gold", "model_registry")
        return os.path.abspath(base)

    def _load_index(self) -> dict:
        if self._index_path.exists():
            try:
                with open(self._index_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_index(self):
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(self._index, f, indent=2, default=str)
        tmp.replace(self._index_path)

    def _delete_version(self, ver: str):
        entry = self._index.pop(ver, None)
        if entry and os.path.isdir(entry["path"]):
            shutil.rmtree(entry["path"], ignore_errors=True)
        self._save_index()

    def _extract_params(self, model) -> dict:
        """从PPOAgent抽取超参"""
        return {
            "obs_dim": getattr(model, "obs_dim", None),
            "n_actions": getattr(model, "n_actions", None),
            "lr": getattr(model, "lr", None),
            "gamma": getattr(model, "gamma", None),
            "gae_lambda": getattr(model, "gae_lambda", None),
            "clip_epsilon": getattr(model, "clip_epsilon", None),
            "hidden_dim": getattr(model, "hidden_dim", None),
            "model_type": getattr(model, "model_type", None),
            "device": str(getattr(model, "device", None)),
        }