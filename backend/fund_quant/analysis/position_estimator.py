"""仓位估算 — 基于回归方法分解平衡混合基金资产比例

支持 OLS / LASSO (L1) / Ridge (L2) 三种回归方法。
"""
from typing import Optional
import numpy as np
from sklearn.linear_model import LinearRegression, Lasso, Ridge


def _fit_and_extract(
    nav: np.ndarray,
    eq_rets: np.ndarray,
    bd_rets: np.ndarray,
    model,
) -> dict:
    """通用回归拟合 + 权重提取 + 归一化"""
    X = np.column_stack([eq_rets, bd_rets])
    model.fit(X, nav)

    equity_ratio = float(np.clip(model.coef_[0], 0, 1))
    bond_ratio = float(np.clip(model.coef_[1], 0, 1))

    total = equity_ratio + bond_ratio
    if total > 0:
        equity_ratio /= total
        bond_ratio /= total

    r_squared = float(model.score(X, nav))

    return {
        "equity_ratio": round(equity_ratio, 4),
        "bond_ratio": round(bond_ratio, 4),
        "r_squared": round(r_squared, 4),
    }


def estimate_position(
    nav_returns: list[float],
    index_returns: dict[str, list[float]],
    window: int = 60,
    method: str = "ols",
    alpha: float = 1e-6,
) -> Optional[dict]:
    """使用指定回归方法估算权益/固收仓位

    model: nav_return = β₁ × equity_index + β₂ × bond_index + ε

    Args:
        nav_returns: 基金日收益率序列
        index_returns: {"csi300": [...], "cbi": [...]} 沪深300和中债指数收益率
        window: 滚动窗口（默认 60 交易日）
        method: "ols" — 普通最小二乘（默认，2 特征场景已足够）
                "lasso" — L1 正则化（倾向稀疏解，多特征/共线性高时使用）
                "ridge" — L2 正则化（平滑收缩，特征数量多时有用）
        alpha: 正则化强度（仅 lasso/ridge 生效）。
               日收益率量级约 1e-3，默认 1e-6 为合理起点。

    Returns:
        {"equity_ratio": float, "bond_ratio": float, "r_squared": float}
        或 None（数据不足）
    """
    min_window = 20
    actual_window = min(window, len(nav_returns),
                        len(next(iter(index_returns.values()))))
    if actual_window < min_window:
        return None

    eq_rets = np.array(index_returns.get("csi300", []))[-actual_window:]
    bd_rets = np.array(index_returns.get("cbi", []))[-actual_window:]
    nav = np.array(nav_returns)[-actual_window:]

    if len(nav) < min_window or len(eq_rets) < min_window or len(bd_rets) < min_window:
        return None

    if method == "lasso":
        model = Lasso(alpha=alpha, fit_intercept=True, max_iter=10_000)
    elif method == "ridge":
        model = Ridge(alpha=alpha, fit_intercept=True)
    else:
        model = LinearRegression(fit_intercept=True)

    return _fit_and_extract(nav, eq_rets, bd_rets, model)


def estimate_position_ols(
    nav_returns: list[float],
    index_returns: dict[str, list[float]],
    window: int = 60,
) -> Optional[dict]:
    """[向后兼容] OLS 回归估算仓位"""
    return estimate_position(nav_returns, index_returns, window=window, method="ols")


def estimate_position_lasso(
    nav_returns: list[float],
    index_returns: dict[str, list[float]],
    window: int = 60,
    alpha: float = 1e-6,
) -> Optional[dict]:
    """LASSO (L1) 回归估算仓位 — 倾向稀疏解，alpha 越大 coeff 越趋于零

    注：金融日收益率量级约 1e-3，alpha 默认 1e-6 为合理起点。
    对于 2 特征（股指+债指）场景 OLS 通常已足够，Lasso 在特征增多时更有用。
    """
    return estimate_position(nav_returns, index_returns, window=window,
                             method="lasso", alpha=alpha)


def estimate_position_ridge(
    nav_returns: list[float],
    index_returns: dict[str, list[float]],
    window: int = 60,
    alpha: float = 0.01,
) -> Optional[dict]:
    """Ridge (L2) 回归估算仓位 — 平滑收缩，适合高度相关的自变量

    注：Ridge 的 alpha 相对于数据方差 scale。对于 2 特征（股指+债指），
    OLS 在多数情况下已足够。Ridge 在特征数量多或共线性高时更有效。
    """
    return estimate_position(nav_returns, index_returns, window=window,
                             method="ridge", alpha=alpha)
