"""ETF全球资产轮动 — 多资产动量评分 + 轮动持仓

策略逻辑：
  1. 维护一个ETF池（可配置），覆盖股票/债券/商品/海外
  2. 每个ETF计算动量得分：log价格线性回归 → 年化收益率 × R²
  3. 按得分排序，持前N名，其余清仓
  4. 可选大盘择时（动量为负时全部清仓）

参考：聚宽社区经典策略，10年15-18倍，年化30%，最大回撤12-20%
"""
from typing import Optional, List
import numpy as np
from ..base import FundStrategyBase, StrategyRegistry
from ...core.enums import SignalType, Direction
from ...core.models import FundSignal, Portfolio, InformationSet


class EtfGlobalRotationStrategy(FundStrategyBase):
    """ETF全球资产轮动策略: 多资产动量评分 + Top-N持仓"""

    strategy_name = "etf_global_rotation"
    strategy_type = "allocation"
    description = "ETF全球资产轮动: 多资产动量评分(年化收益×R²) + Top-N轮动 + 大盘择时保护"
    default_params = {
        # ETF池: code -> name
        "etf_pool": {
            "518880": "黄金ETF",
            "513100": "纳指ETF",
            "159915": "创业板ETF",
            "510180": "上证180ETF",
            "510300": "沪深300ETF",
            "510500": "中证500ETF",
            "511880": "银华日利ETF",
        },
        "momentum_days": 25,          # 动量参考天数
        "top_n": 1,                    # 持仓ETF数量
        "rebalance_days": 1,           # 调仓间隔（1=每日）
        "buy_threshold": 0.0,          # 买入动量阈值（>0即买入）
        "sell_threshold": -0.02,       # 全部清仓阈值
        "enable_market_timing": True,  # 是否启用大盘择时保护
        "market_index_code": "000300", # 大盘择时参考指数
        "market_momentum_days": 60,    # 大盘动量参考天数
        "market_min_score": -0.01,     # 大盘最低动量得分（低于此全仓卖出）
        "max_single_weight": 1.0,      # 单只ETF最大权重
        "fee_rate": 0.0002,            # 交易费率
        "slippage": 0.003,            # 滑点
    }
    applicable_fund_types = []         # 全部类型可用（本质是资产配置策略）
    min_history_days = 60

    def on_evaluate(self, portfolio: Optional[Portfolio],
                    info_set: Optional[InformationSet]) -> List[FundSignal]:
        """评估"""
        return []

    def optimize(self, fund_codes: Optional[List[str]] = None,
                 params: Optional[dict] = None) -> dict:
        """执行ETF轮动优化

        Args:
            fund_codes: 若指定，覆盖默认ETF池
            params: 覆盖默认参数

        Returns:
            dict: 含 weights (code->weight), 各ETF得分, 排名
        """
        if params:
            self.params.update(params)

        etf_pool = self.params["etf_pool"]
        if fund_codes:
            etf_pool = {c: c for c in fund_codes}

        momentum_days = self.params["momentum_days"]
        top_n = self.params["top_n"]

        # 1. 计算每个ETF的动量得分
        scores = self._compute_scores(etf_pool, momentum_days)
        if not scores:
            return self._empty_result(etf_pool, "净值数据不足")

        # 2. 按得分排序
        sorted_codes = sorted(scores.keys(), key=lambda c: scores[c]["score"], reverse=True)
        ranked = [{"code": c, "score": scores[c]["score"],
                    "annualized_return": scores[c]["annualized_return"],
                    "r_squared": scores[c]["r_squared"]}
                  for c in sorted_codes]

        # 3. 大盘择时保护（可选）
        market_ok = True
        if self.params.get("enable_market_timing", True):
            market_ok = self._check_market_timing()
            if not market_ok:
                # 大盘动量不足 → 全部清仓
                return {
                    "strategy": self.strategy_name,
                    "status": "market_timing_hold",
                    "reason": f"大盘动量低于阈值，全部清仓等待",
                    "weights": {c: 0.0 for c in etf_pool},
                    "rankings": ranked,
                    "market_signal": "bearish",
                }

        # 4. 选Top-N
        top_codes = sorted_codes[:top_n]
        # 检查最高得分是否满足买入阈值
        if top_codes:
            top_score = scores[top_codes[0]]["score"]
            if top_score < self.params["sell_threshold"]:
                # 所有ETF动量为负 → 全部卖出
                return {
                    "strategy": self.strategy_name,
                    "status": "all_sell",
                    "reason": f"最高动量得分 {top_score:.4f} 低于卖出阈值，全部卖出",
                    "weights": {c: 0.0 for c in etf_pool},
                    "rankings": ranked,
                }

        # 5. 分配权重
        weights = {}
        max_w = self.params["max_single_weight"]
        if top_n == 1:
            # 单只ETF满仓
            for c in etf_pool:
                weights[c] = max_w if c in top_codes else 0.0
        else:
            # 多只等权
            w = min(max_w, 1.0 / top_n)
            for c in etf_pool:
                weights[c] = w if c in top_codes else 0.0

        # 6. 计算组合统计
        if top_codes:
            weighted_score = sum(scores[c]["score"] * weights[c] for c in etf_pool if weights.get(c, 0) > 0)
            total_w = sum(weights.values())
            avg_score = weighted_score / max(total_w, 1e-10) if total_w > 0 else 0
        else:
            avg_score = 0

        return {
            "strategy": self.strategy_name,
            "status": "success",
            "method": f"momentum_top{top_n}_of_{len(etf_pool)}",
            "weights": {c: round(float(w), 4) for c, w in weights.items()},
            "rankings": ranked,
            "holding_count": len(top_codes),
            "avg_momentum_score": round(float(avg_score), 6),
            "rebalance_freq": f"每{self.params['rebalance_days']}天",
        }

    def _compute_scores(self, etf_pool: dict, momentum_days: int) -> dict:
        """计算每个ETF的动量得分"""
        from ...data.storage import get_nav_history

        scores = {}
        for code, name in etf_pool.items():
            nav_data = get_nav_history(code)
            if not nav_data or len(nav_data) < momentum_days + 5:
                continue

            nav_values = [r.get("nav", 0) for r in nav_data if r.get("nav") and r["nav"] > 0]
            if len(nav_values) < momentum_days + 5:
                continue

            # 取最近N天的收盘价
            recent = nav_values[-(momentum_days + 5):]
            arr = np.array(recent, dtype=np.float64)

            # log价格线性回归
            log_prices = np.log(arr)
            x = np.arange(len(log_prices))
            y = log_prices

            slope, intercept = np.polyfit(x, y, 1)

            # 年化收益率 = e^(slope * 250) - 1
            annualized_return = np.exp(slope * 250) - 1

            # R² 判定系数
            y_pred = slope * x + intercept
            ss_res = np.sum((y - y_pred) ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r_squared = 1 - (ss_res / max(ss_tot, 1e-10))
            r_squared = max(0.0, min(1.0, r_squared))

            # 动量得分 = 年化收益率 × R²
            score = annualized_return * r_squared

            scores[code] = {
                "score": float(score),
                "annualized_return": float(annualized_return),
                "r_squared": float(r_squared),
                "name": name,
            }

        return scores

    def _check_market_timing(self) -> bool:
        """大盘择时保护: 大盘指数动量足够则返回True"""
        from ...data.storage import get_index_nav_prices

        index_code = self.params["market_index_code"]
        market_days = self.params["market_momentum_days"]
        min_score = self.params["market_min_score"]

        try:
            prices = get_index_nav_prices(index_code)
            if not prices or len(prices) < market_days:
                return True  # 无数据时不拦截

            # 计算指数动量得分
            arr = np.array(prices[-market_days:], dtype=np.float64)
            log_prices = np.log(arr)
            x = np.arange(len(log_prices))
            slope, _ = np.polyfit(x, log_prices, 1)
            annualized = np.exp(slope * 250) - 1
            market_momentum = float(annualized)

            return market_momentum >= min_score

        except Exception:
            return True

    def _empty_result(self, etf_pool: dict, reason: str) -> dict:
        """无数据时的空结果"""
        return {
            "strategy": self.strategy_name,
            "status": "insufficient_data",
            "reason": reason,
            "weights": {c: 0.0 for c in etf_pool},
            "rankings": [],
        }


StrategyRegistry.register(EtfGlobalRotationStrategy)
