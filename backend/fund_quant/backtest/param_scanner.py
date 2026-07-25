"""
参数敏感性扫描 — 支持单参数曲线、网格搜索、随机搜索及 CSV 导出。

功能:
  - single_param:  单参数遍历，计算敏感性分数
  - grid_search:   多参数笛卡尔积，可选稳定区域识别
  - random_search: 参数空间均匀采样
  - to_csv:        结果导出到 CSV 文件
"""

from __future__ import annotations

import csv
import itertools
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger

__all__ = ["ScanResult", "ParameterScanner"]


@dataclass
class ScanResult:
    """参数扫描结果"""

    mode: str  # "single_param" | "grid_search" | "random_search"
    param_names: List[str]  # 扫描的参数名称列表
    results: List[Dict]  # 每条记录 = 参数值 + 指标值
    n_iterations: int  # 实际迭代次数
    sensitivity_score: Optional[Dict[str, float]] = None  # max - min per metric (single_param)
    stability_region: Optional[List[Tuple]] = None  # (p1, p2) pairs where sharpe > 0.5


class ParameterScanner:
    """参数敏感性扫描器

    Args:
        func: Callable[[Dict[str, Any]], Dict[str, float]]
              接收参数字典，返回指标字典（至少包含 "sharpe" 键）
    """

    def __init__(self, func: Callable[[Dict[str, Any]], Dict[str, float]]) -> None:
        self._func = func
        logger.debug("ParameterScanner initialized")

    # ── 公共方法 ─────────────────────────────────────

    def single_param(
        self,
        param: str,
        values: List[Any],
        fixed_params: Optional[Dict[str, Any]] = None,
    ) -> ScanResult:
        """单参数扫描：固定其他参数，遍历 param 的 values"""
        if not values:
            logger.warning("single_param: empty values list")
            return ScanResult(mode="single_param", param_names=[param], results=[], n_iterations=0)

        fixed = fixed_params or {}
        results: List[Dict] = []
        for v in values:
            params = {**fixed, param: v}
            metrics = self._func(params)
            row = {param: v, **metrics}
            results.append(row)
            logger.debug(f"single_param: {param}={v} -> {metrics}")

        # 按 param value 排序
        results.sort(key=lambda r: r[param])

        # 提取所有指标 key（排除 param key）
        metric_keys = [k for k in results[0] if k != param] if results else []

        # 计算敏感性分数
        sensitivity_score: Optional[Dict[str, float]] = None
        if metric_keys:
            sensitivity_score = {}
            for mk in metric_keys:
                vals = [r[mk] for r in results if isinstance(r.get(mk), (int, float))]
                if vals:
                    sensitivity_score[mk] = round(max(vals) - min(vals), 4)
                else:
                    sensitivity_score[mk] = float("nan")

        return ScanResult(
            mode="single_param",
            param_names=[param],
            results=results,
            n_iterations=len(results),
            sensitivity_score=sensitivity_score,
        )

    def grid_search(
        self,
        param_grid: Dict[str, List[Any]],
        fixed_params: Optional[Dict[str, Any]] = None,
    ) -> ScanResult:
        """网格搜索：所有参数的笛卡尔积"""
        if not param_grid:
            logger.warning("grid_search: empty param_grid")
            return ScanResult(mode="grid_search", param_names=[], results=[], n_iterations=0)

        # 检查任何列表为空
        for name, vals in param_grid.items():
            if not vals:
                logger.warning(f"grid_search: empty list for param '{name}'")
                return ScanResult(
                    mode="grid_search",
                    param_names=list(param_grid.keys()),
                    results=[],
                    n_iterations=0,
                )

        param_names = list(param_grid.keys())
        fixed = fixed_params or {}
        results: List[Dict] = []

        keys = list(param_grid.keys())
        for combo in itertools.product(*[param_grid[k] for k in keys]):
            params = {**fixed}
            for i, k in enumerate(keys):
                params[k] = combo[i]
            metrics = self._func(params)
            row = {**dict(zip(keys, combo)), **metrics}
            results.append(row)
            logger.debug(f"grid_search: {dict(zip(keys, combo))} -> {metrics}")

        # 按前两个参数排序
        results.sort(key=lambda r: tuple(r[k] for k in keys[:2]))

        # 稳定区域（仅 2 参数时）
        stability_region: Optional[List[Tuple]] = None
        if len(param_names) == 2:
            stability_region = [
                (r[param_names[0]], r[param_names[1]])
                for r in results
                if r.get("sharpe", float("-inf")) > 0.5
            ]

        return ScanResult(
            mode="grid_search",
            param_names=param_names,
            results=results,
            n_iterations=len(results),
            stability_region=stability_region,
        )

    def random_search(
        self,
        param_dist: Dict[str, Any],
        n_iter: int = 100,
        seed: int = 42,
        fixed_params: Optional[Dict[str, Any]] = None,
    ) -> ScanResult:
        """随机搜索：从参数分布中均匀采样

        param_dist 中每个条目的值可以是:
          - List: 从列表中均匀采样
          - Tuple[float, float]: 在该范围内均匀采样浮点数
        """
        if n_iter <= 0:
            raise ValueError(f"n_iter must be > 0, got {n_iter}")

        param_names = list(param_dist.keys())
        fixed = fixed_params or {}
        rng = random.Random(seed)
        results: List[Dict] = []

        for _ in range(n_iter):
            params = {**fixed}
            for name in param_names:
                dist = param_dist[name]
                if isinstance(dist, tuple) and len(dist) == 2:
                    low, high = dist
                    params[name] = low + (high - low) * rng.random()
                else:
                    params[name] = rng.choice(list(dist))
            metrics = self._func(params)
            row = {**params, **metrics}
            results.append(row)

        logger.debug(f"random_search: {n_iter} iterations completed")

        return ScanResult(
            mode="random_search",
            param_names=param_names,
            results=results,
            n_iterations=len(results),
        )

    @staticmethod
    def to_csv(result: ScanResult, filepath: str) -> None:
        """将扫描结果导出到 CSV 文件"""
        if not result.results:
            logger.warning("to_csv: empty results, writing header only")
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(result.param_names)
            return

        first = result.results[0]
        header = list(first.keys())
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(result.results)

        logger.info(f"Exported {len(result.results)} rows to {filepath}")
