"""FOF 穿透分析 — 多数据源融合估算底层资产配置

数据源矩阵:
  S1 — 天天基金分类: fund_name_em → FOF-稳健型/均衡型/进取型  ✅
  S2 — 雪球业绩基准: fund_individual_basic_info_xq → 业绩比较基准解析  ✅
  S3 — 雪球实际持仓: fund_individual_detail_hold_xq → 报告期股/债/现金  ✅
  S4 — 净值回归法: estimate_position_ols + CSI300/CBI  ✅
  S5 — 定期报告解析: fund_announcement_report_em → HTML/PDF  ❌ (待实现)

多源融合策略: 可用源越多 → 置信度越高，按优先级加权融合
"""
from typing import Optional
from dataclasses import dataclass, field
import re


# ═══════════════════════════════════════════════
# S1 — FOF 子类先验
# ═══════════════════════════════════════════════

FOF_SUBTYPE_PRIORS = {
    "稳健型": 0.20,        # 20% 权益
    "均衡型": 0.50,        # 50% 权益
    "进取型": 0.80,        # 80% 权益
    "偏债混合": 0.20,      # 雪球分类
    "偏股混合": 0.70,
    "QDII-FOF": 0.60,
}

DEFAULT_EQUITY_RATIO = 0.50


# ═══════════════════════════════════════════════
# 结果模型
# ═══════════════════════════════════════════════

@dataclass
class PenetrationResult:
    """穿透分析结果"""
    fund_code: str
    fund_name: str = ""
    fund_type: str = "FOF"
    subtype: str = "unknown"
    equity_ratio: float = 0.5
    bond_ratio: float = 0.5
    method: str = "prior"
    confidence: float = 0.4
    ols_r_squared: Optional[float] = None
    prior_equity: Optional[float] = None
    source_benchmark: Optional[float] = None    # S2 解析结果
    source_actual: Optional[float] = None        # S3 解析结果
    details: Optional[dict] = field(default_factory=dict)


# ═══════════════════════════════════════════════
# S1 — 子类判定
# ═══════════════════════════════════════════════

def _parse_subtype(fund_type_raw: str) -> str:
    """从基金类型原始字符串提取 FOF 子类

    输入: "FOF-稳健型", "FOF-均衡型", "FOF-进取型"
           "FOF-偏债混合", "FOF-偏股混合" (雪球分类)
    Returns: 子类字符串
    """
    if not fund_type_raw:
        return "unknown"
    if "QDII" in fund_type_raw.upper():
        return "QDII-FOF"
    for st in ("稳健型", "均衡型", "进取型", "偏债混合", "偏股混合"):
        if st in fund_type_raw:
            return st
    return "unknown"


def _prior_equity(subtype: str) -> float:
    """子类 → 先验权益仓位"""
    return FOF_SUBTYPE_PRIORS.get(subtype, DEFAULT_EQUITY_RATIO)


# ═══════════════════════════════════════════════
# S2 — 业绩基准解析
# ═══════════════════════════════════════════════

def _parse_benchmark(benchmark_str: str) -> Optional[float]:
    """解析业绩比较基准字符串，提取权益仓位比例

    输入: "中证800股票指数收益率×20%+中债综合财富指数收益率×70%..."
    输出: 权益仓位 (0.20) 或 None

    识别逻辑: 找到含有"股票"/"权益"关键词的指数比例
    """
    if not benchmark_str:
        return None

    # 模式: "指数名×数字%" 或 "指数名收益率×数字%"
    parts = re.split(r'[+＋]', benchmark_str)
    equity_pct = 0.0
    found = False

    for part in parts:
        # 提取 ×NN.NN% 或 ×NN%
        if "股票" not in part and "权益" not in part and "沪深300" not in part and "中证500" not in part and "中证800" not in part:
            continue
        match = re.search(r'[×Xx](\d+(?:\.\d+)?)%', part)
        if match:
            equity_pct += float(match.group(1)) / 100
            found = True

    return equity_pct if found else None


def _fetch_benchmark_online(fund_code: str) -> Optional[float]:
    """从雪球获取业绩基准并解析权益仓位

    返回: 权益比例 (0~1) 或 None
    """
    try:
        import akshare as ak
        df = ak.fund_individual_basic_info_xq(symbol=fund_code)
        row = df[df['item'] == '业绩比较基准']
        if row.empty:
            return None
        return _parse_benchmark(row['value'].values[0])
    except Exception:
        return None


# ═══════════════════════════════════════════════
# S3 — 实际持仓解析（雪球）
# ═══════════════════════════════════════════════

def _fetch_actual_holdings_online(fund_code: str) -> Optional[dict]:
    """从雪球获取报告期实际资产配置

    返回: {"equity": float, "bond": float, "cash": float} 或 None
    """
    try:
        import akshare as ak
        import pandas as pd
        df = ak.fund_individual_detail_hold_xq(symbol=fund_code, date="20241231")
        if df.empty:
            return None
        result = {"equity": 0.0, "bond": 0.0, "cash": 0.0}
        for _, row in df.iterrows():
            asset = str(row.get("资产类型", ""))
            ratio = float(row.get("仓位占比", 0))
            if "股票" in asset: result["equity"] = ratio / 100
            elif "债券" in asset: result["bond"] = ratio / 100
            elif "现金" in asset: result["cash"] = ratio / 100
        if result["equity"] + result["bond"] > 0:
            # 归一化（排除现金）
            total = result["equity"] + result["bond"]
            result["equity"] = result["equity"] / total if total > 0 else result["equity"]
            result["bond"] = result["bond"] / total if total > 0 else result["bond"]
            return result
        return None
    except Exception:
        return None


# ═══════════════════════════════════════════════
# 核心分析函数
# ═══════════════════════════════════════════════

def analyze_fof_penetration(
    fund_code: str,
    subtype: str = "unknown",
    nav_values: Optional[list[float]] = None,
    ols_result: Optional[dict] = None,
    benchmark_eq: Optional[float] = None,
    actual_eq: Optional[float] = None,
) -> PenetrationResult:
    """多数据源加权融合的 FOF 穿透分析

    融合策略:
      - S3 实际持仓可用 → 直接采用（最准）
      - S2 业绩基准可用 → 与 S1 先验加权（7:3）
      - S4 OLS 结果可用 (R²>0.15) → 再与上述结果加权
      - 否则 → S1 先验

    Args:
        fund_code: 基金代码
        subtype: FOF 子类（S1 输出）
        nav_values: 净值历史（仅统计用）
        ols_result: estimate_position_ols 结果（S4 输出）
        benchmark_eq: 业绩基准解析的权益比例（S2 输出）
        actual_eq: 实际报告持仓的权益比例（S3 输出）

    Returns:
        PenetrationResult
    """
    prior_eq = _prior_equity(subtype)
    prior_bd = 1 - prior_eq

    # ── S3 实际持仓最高优先级 ──
    if actual_eq is not None:
        eq = actual_eq
        bd = 1 - eq
        method = "actual"
        confidence = 0.85
        ols_r2 = ols_result.get("r_squared") if ols_result else None
        return PenetrationResult(
            fund_code=fund_code, subtype=subtype,
            fund_type=f"FOF-{subtype}" if subtype != "unknown" else "FOF",
            equity_ratio=round(eq, 4), bond_ratio=round(bd, 4),
            method=method, confidence=round(confidence, 4),
            ols_r_squared=round(ols_r2, 4) if ols_r2 is not None else None,
            prior_equity=prior_eq, source_benchmark=benchmark_eq,
            source_actual=actual_eq,
            details={"nav_count": len(nav_values) if nav_values else 0,
                     "prior_equity": prior_eq, "source": "S3_actual"},
        )

    # ── S1 + S2 先验融合 ──
    if benchmark_eq is not None:
        # 基准融合: S1 先验 × 0.3 + S2 基准 × 0.7
        fused_eq = prior_eq * 0.3 + benchmark_eq * 0.7
        confidence_base = 0.6
    else:
        fused_eq = prior_eq
        confidence_base = 0.4

    # ── S4 OLS 融合 ──
    if ols_result and ols_result.get("r_squared", 0) > 0.15:
        ols_eq = ols_result["equity_ratio"]
        ols_bd = ols_result["bond_ratio"]
        ols_r2 = ols_result["r_squared"]

        ols_weight = min(ols_r2 / (ols_r2 + 0.1), 0.6)
        prior_w = 1 - ols_weight
        eq = ols_eq * ols_weight + fused_eq * prior_w
        bd = ols_bd * ols_weight + (1 - fused_eq) * prior_w
        method = "hybrid"
        confidence = min(confidence_base + ols_r2, 1.0)
    else:
        eq = fused_eq
        bd = 1 - eq
        method = "benchmark" if benchmark_eq is not None else "prior"
        confidence = confidence_base
        ols_r2 = ols_result.get("r_squared") if ols_result else None

    return PenetrationResult(
        fund_code=fund_code, subtype=subtype,
        fund_type=f"FOF-{subtype}" if subtype != "unknown" else "FOF",
        equity_ratio=round(eq, 4), bond_ratio=round(bd, 4),
        method=method, confidence=round(confidence, 4),
        ols_r_squared=round(ols_r2, 4) if ols_r2 is not None else None,
        prior_equity=prior_eq, source_benchmark=benchmark_eq,
        source_actual=actual_eq,
        details={"nav_count": len(nav_values) if nav_values else 0,
                 "prior_equity": prior_eq,
                 "benchmark_equity": benchmark_eq,
                 "actual_equity": actual_eq},
    )


def analyze_fof_penetration_full(
    fund_code: str,
    fund_type_raw: str = "",
    nav_values: Optional[list[float]] = None,
    window: int = 120,
    enable_online_sources: bool = True,
) -> PenetrationResult:
    """完整穿透分析 — 自动拉取所有数据源

    Args:
        fund_code: 基金代码
        fund_type_raw: 基金类型字符串（如 "FOF-稳健型"）
        nav_values: 净值历史序列
        window: OLS 窗口
        enable_online_sources: 是否启用 S2/S3 在线源

    Returns:
        PenetrationResult
    """
    # S1: 子类判定
    subtype = _parse_subtype(fund_type_raw)

    # S2 + S3: 在线数据（可禁用，用于测试或离线环境）
    benchmark_eq = None
    actual_eq = None
    if enable_online_sources:
        try:
            benchmark_eq = _fetch_benchmark_online(fund_code)
        except Exception:
            pass
        try:
            actual_data = _fetch_actual_holdings_online(fund_code)
            if actual_data:
                actual_eq = actual_data["equity"]
        except Exception:
            pass

    # S4: OLS
    ols_result = None
    if nav_values and len(nav_values) >= 60:
        try:
            from backend.fund_quant.data.storage import get_index_nav_prices
            from backend.api.fund_quant import _prices_to_returns
            from backend.fund_quant.analysis.position_estimator import estimate_position_ols

            fund_rets = _prices_to_returns(nav_values)
            index_data = {}
            for key in ("csi300", "cbi"):
                prices = get_index_nav_prices(key)
                if prices and len(prices) >= len(nav_values):
                    aligned = prices[-len(nav_values):]
                    index_data[key] = _prices_to_returns(aligned)
            if len(index_data) == 2 and len(fund_rets) >= 20:
                ols_result = estimate_position_ols(fund_rets, index_data, window=window)
        except Exception:
            pass

    return analyze_fof_penetration(
        fund_code=fund_code, subtype=subtype,
        nav_values=nav_values, ols_result=ols_result,
        benchmark_eq=benchmark_eq, actual_eq=actual_eq,
    )
