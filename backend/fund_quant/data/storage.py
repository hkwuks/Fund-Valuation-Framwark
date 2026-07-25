"""FundQuant SQLite 存储层"""

import json
import sqlite3
from datetime import date, datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from contextlib import contextmanager
from ..core.config import fund_quant_settings
from ..core.models import NavPoint, FundHolding, FundMeta, BacktestResult, FundSignal
from ..core.errors import StorageError


def _get_db_path() -> str:
    return fund_quant_settings.FUND_QUANT_DB_PATH


def _ensure_db():
    """确保数据库文件和目录存在"""
    path = Path(_get_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_conn():
    """获取数据库连接（上下文管理器）"""
    _ensure_db()
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """初始化数据库表"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS nav_history (
            fund_code TEXT NOT NULL,
            date TEXT NOT NULL,
            nav REAL,
            adjusted_nav REAL,
            source TEXT DEFAULT 'eastmoney',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (fund_code, date)
        );

        CREATE TABLE IF NOT EXISTS holdings_history (
            fund_code TEXT NOT NULL,
            report_period TEXT NOT NULL,
            publish_date TEXT NOT NULL,
            holdings_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (fund_code, report_period)
        );

        CREATE TABLE IF NOT EXISTS fund_metadata (
            fund_code TEXT PRIMARY KEY,
            fund_name TEXT,
            fund_type TEXT,
            management_fee REAL,
            custody_fee REAL,
            subscription_fee_tiers_json TEXT,
            scale REAL,
            rating INTEGER,
            tracking_index TEXT,
            established_date TEXT,
            is_listed INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS fund_type_defaults (
            fund_type TEXT PRIMARY KEY,
            sub_fee REAL DEFAULT 0.015,
            mgmt_fee REAL DEFAULT 0.015,
            custody_fee REAL DEFAULT 0.0025,
            c_class_service_fee REAL DEFAULT 0.004,
            redemption_tiers_json TEXT DEFAULT '{"7": 1.5, "30": 0.75, "365": 0.5, "730": 0.25}',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS signals (
            signal_id TEXT PRIMARY KEY,
            fund_code TEXT NOT NULL,
            fund_name TEXT,
            signal_type TEXT,
            direction TEXT,
            confidence REAL,
            reason TEXT,
            strategy_name TEXT,
            suggested_amount REAL,
            risk_check_passed INTEGER DEFAULT 1,
            risk_warnings_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS backtest_results (
            backtest_id TEXT PRIMARY KEY,
            strategy_name TEXT,
            fund_codes TEXT,
            start_date TEXT,
            end_date TEXT,
            config_json TEXT,
            result_json TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS collection_status (
            fund_code TEXT NOT NULL,
            data_type TEXT NOT NULL,
            last_collected_date TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            next_retry TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (fund_code, data_type)
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_value REAL,
            cash REAL,
            positions_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_nav_fund_date ON nav_history(fund_code, date);
        CREATE INDEX IF NOT EXISTS idx_signals_fund ON signals(fund_code);
        CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at);
        CREATE INDEX IF NOT EXISTS idx_backtest_strategy ON backtest_results(strategy_name);
        """)

        # 兼容存量数据库：删除旧列，添加新列
        _OLD_FEE_COLS = ["mgmt_fee_default", "custody_fee_default", "sub_fee_default"]
        for col in _OLD_FEE_COLS:
            try:
                conn.execute(f"ALTER TABLE fund_type_defaults DROP COLUMN {col}")
            except Exception:
                pass

        _NEW_FEE_COLS = {
            "sub_fee": "REAL DEFAULT 0.015",
            "mgmt_fee": "REAL DEFAULT 0.015",
            "custody_fee": "REAL DEFAULT 0.0025",
            "c_class_service_fee": "REAL DEFAULT 0.004",
            "redemption_tiers_json": "TEXT DEFAULT '{\"7\": 1.5, \"30\": 0.75, \"365\": 0.5, \"730\": 0.25}'",
        }
        for col, col_type in _NEW_FEE_COLS.items():
            try:
                conn.execute(f"ALTER TABLE fund_type_defaults ADD COLUMN {col} {col_type}")
            except Exception:
                pass

        # 写入默认费率数据
        conn.executemany("""
            INSERT OR IGNORE INTO fund_type_defaults
            (fund_type, sub_fee, mgmt_fee, custody_fee, c_class_service_fee, redemption_tiers_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [
            ("equity", 0.015, 0.015, 0.0025, 0.004, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
            ("index", 0.010, 0.005, 0.0010, 0.0025, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
            ("balanced", 0.012, 0.012, 0.0020, 0.0035, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
            ("bond", 0.008, 0.006, 0.0015, 0.0025, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
            ("money", 0.0, 0.003, 0.0005, 0.0025, '{"7":0,"30":0,"365":0,"730":0,"999999":0}'),
            ("qdii", 0.015, 0.015, 0.0025, 0.004, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
            ("commodity", 0.010, 0.010, 0.0020, 0.0035, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
            ("fof", 0.012, 0.010, 0.0020, 0.0035, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
        ])


# ═══════════════════════════════════════════
# NAV 操作
# ═══════════════════════════════════════════

def save_nav_points(points: List[NavPoint]):
    """批量保存净值数据（UPSERT）"""
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO nav_history (fund_code, date, nav, adjusted_nav, source)
               VALUES (?, ?, ?, ?, ?)""",
            [(p.fund_code, p.date.isoformat(), p.nav, p.adjusted_nav, p.source) for p in points],
        )


def get_nav_history(fund_code: str, start_date: Optional[str] = None,
                    end_date: Optional[str] = None, limit: Optional[int] = None) -> List[dict]:
    """获取基金净值历史"""
    with get_conn() as conn:
        query = "SELECT * FROM nav_history WHERE fund_code = ?"
        params: List[Any] = [fund_code]
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        query += " ORDER BY date ASC"
        if limit:
            query += f" LIMIT {limit}"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_latest_nav(fund_code: str) -> Optional[dict]:
    """获取最新净值"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM nav_history WHERE fund_code = ? ORDER BY date DESC LIMIT 1",
            (fund_code,),
        ).fetchone()
        return dict(row) if row else None


# ═══════════════════════════════════════════
# 持仓操作
# ═══════════════════════════════════════════

def save_holdings(holdings: List[FundHolding]):
    """批量保存持仓数据"""
    with get_conn() as conn:
        for h in holdings:
            conn.execute(
                """INSERT OR REPLACE INTO holdings_history (fund_code, report_period, publish_date, holdings_json)
                   VALUES (?, ?, ?, ?)""",
                (h.fund_code, h.report_period.isoformat(), h.publish_date.isoformat(),
                 json.dumps([h2.model_dump() for h2 in h.holdings], default=str)),
            )


def get_holdings(fund_code: str, report_period: Optional[str] = None,
                 as_of_date: Optional[str] = None) -> List[dict]:
    """获取持仓数据

    Args:
        fund_code: 基金代码
        report_period: 指定报告期
        as_of_date: 前视偏差防护 — 只返回 publish_date <= as_of_date 的持仓
    """
    with get_conn() as conn:
        if report_period:
            query = "SELECT * FROM holdings_history WHERE fund_code = ? AND report_period = ?"
            params: List[Any] = [fund_code, report_period]
        else:
            query = "SELECT * FROM holdings_history WHERE fund_code = ?"
            params = [fund_code]

        if as_of_date:
            query += " AND publish_date <= ?"
            params.append(as_of_date)

        query += " ORDER BY report_period DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════
# 基金元数据操作
# ═══════════════════════════════════════════

def upsert_fund_meta(meta: FundMeta):
    """插入或更新基金元数据"""
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO fund_metadata
               (fund_code, fund_name, fund_type, management_fee, custody_fee,
                subscription_fee_tiers_json, scale, rating, tracking_index,
                established_date, is_listed, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (meta.fund_code, meta.fund_name, meta.fund_type,
             meta.management_fee, meta.custody_fee,
             meta.subscription_fee_tiers, meta.scale, meta.rating,
             meta.tracking_index,
             meta.established_date.isoformat() if meta.established_date else None,
             int(meta.is_listed)),
        )


def get_fund_meta(fund_code: str) -> Optional[dict]:
    """获取基金元数据"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM fund_metadata WHERE fund_code = ?", (fund_code,)
        ).fetchone()
        return dict(row) if row else None


def get_all_fund_codes() -> List[str]:
    """获取所有已记录的基金代码"""
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT fund_code FROM nav_history ORDER BY fund_code").fetchall()
        return [r["fund_code"] for r in rows]


def get_funds_by_type(fund_type: str) -> list[str]:
    """按基金类型获取基金代码列表

    Args:
        fund_type: FundType 枚举值字符串，如 "equity", "bond"

    Returns:
        匹配该类型的基金代码列表

    >>> codes = get_funds_by_type("bond")
    >>> isinstance(codes, list)
    True
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT fund_code FROM fund_metadata WHERE fund_type = ? ORDER BY fund_code",
            (fund_type,)
        ).fetchall()
        return [r["fund_code"] for r in rows]


def get_fee_rates(fund_type: str) -> Optional[dict]:
    """查询某基金类型的费率配置

    Args:
        fund_type: FundType 枚举值字符串

    Returns:
        dict 包含 sub_fee/mgmt_fee/custody_fee/c_class_service_fee/redemption_tiers
        或 None（未找到）
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM fund_type_defaults WHERE fund_type = ?",
            (fund_type,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("redemption_tiers_json"):
            result["redemption_tiers"] = json.loads(result["redemption_tiers_json"])
        return result


# ═══════════════════════════════════════════
# 指数历史数据
# ═══════════════════════════════════════════

# 平衡混合基金仓位分解所需指数代理
INDEX_CODES = {
    "csi300": "sh000300",  # 沪深 300（权益部分）
    "cbi": "sh000012",     # 中证国债（固收部分代理）
}

_index_nav_cache: dict = {}

def get_index_nav_prices(index_key: str) -> Optional[list[float]]:
    """获取指数历史收盘价（带内存缓存）

    Args:
        index_key: "csi300" 或 "cbi"

    Returns:
        收盘价列表（从早到晚），或 None
    """
    code = INDEX_CODES.get(index_key)
    if not code:
        return None

    cache_key = f"index_{index_key}"
    if cache_key in _index_nav_cache:
        return _index_nav_cache[cache_key]

    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol=code)
        closes = df["close"].astype(float).tolist()
        _index_nav_cache[cache_key] = closes
        return closes
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"获取指数 {index_key}({code}) 失败: {e}")
        return None

def clear_index_nav_cache():
    """清空指数缓存（外部测试用）"""
    _index_nav_cache.clear()


# ═══════════════════════════════════════════
# 债券收益率数据（信用利差策略用）
# ═══════════════════════════════════════════

YIELD_CURVE_NAMES = {
    "corp_aaa": "中债中短期票据收益率曲线(AAA)",
    "bank_aaa": "中债商业银行普通债收益率曲线(AAA)",
    "govt": "中债国债收益率曲线",
}

_bond_yield_cache: dict = {}

def get_bond_yield_data() -> dict:
    """获取债券收益率数据：信用利差 + 收益率曲线斜率

    数据源: akshare bond_china_yield()

    Returns:
        {
            "credit_spread_history": [d1, d2, ...],  # AAA企业债5Y - 国债5Y (bp)
            "yield_curve_history": [d1, d2, ...],     # 国债10Y - 3M (bp)
        } 或空字典（数据不可用时）
    """
    if _bond_yield_cache:
        return _bond_yield_cache

    try:
        import akshare as ak
        df = ak.bond_china_yield()
        if df.empty:
            return {}

        # 按曲线名称分组
        curves = {}
        for _, row in df.iterrows():
            name = row["曲线名称"]
            date_val = row["日期"]
            if name not in curves:
                curves[name] = {}
            curves[name][date_val] = {
                "3m": row.get("3月"),
                "1y": row.get("1年"),
                "5y": row.get("5年"),
                "10y": row.get("10年"),
            }

        govt = curves.get(YIELD_CURVE_NAMES["govt"], {})
        corp = curves.get(YIELD_CURVE_NAMES["corp_aaa"], {})

        # 对齐日期
        all_dates = sorted(set(govt.keys()) & set(corp.keys()))
        if not all_dates:
            return {}

        spread_hist = []
        curve_hist = []
        for d in all_dates:
            g = govt[d]
            c = corp[d]
            if g["5y"] is not None and c["5y"] is not None:
                spread_hist.append(c["5y"] - g["5y"])
            if g["10y"] is not None and g["3m"] is not None:
                curve_hist.append(g["10y"] - g["3m"])

        result = {
            "credit_spread_history": spread_hist,
            "yield_curve_history": curve_hist,
        }
        _bond_yield_cache.update(result)
        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"获取债券收益率数据失败: {e}")
        return {}


# ═══════════════════════════════════════════
# 黄金 COT 信号
# ═══════════════════════════════════════════

_gold_cot_cache: dict = {}

def get_gold_cot_signal() -> Optional[dict]:
    """获取黄金 COT 持仓信号

    Returns:
        {"signal": float, "spec_net": float, "date": str} 或 None
        signal 在 [-1, 1] 范围，正值=看多
    """
    if _gold_cot_cache:
        return _gold_cot_cache

    try:
        import pandas as pd
        from backend.gold.data.gateway import GoldDataGateway
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            df = loop.run_until_complete(GoldDataGateway().get_cot_data())
        finally:
            loop.close()

        if df.empty:
            return None

        # 取最新一条
        latest = df.iloc[-1]
        spec_net = latest.get("spec_long", 0) - latest.get("spec_short", 0)
        total_oi = latest.get("total_oi", 1)
        # 标准化到 [-1, 1]: 投机净多 / 总持仓
        signal_val = float(max(min(spec_net / max(total_oi, 1) * 10, 1.0), -1.0))

        result = {
            "signal": round(signal_val, 4),
            "spec_net": int(spec_net),
            "date": str(latest.get("date", "")),
        }
        _gold_cot_cache.update(result)
        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"获取 COT 数据失败: {e}")
        return None


# ═══════════════════════════════════════════
# 指数基金外部数据（跟踪误差、流动性、折溢价）
# ═══════════════════════════════════════════

_etf_market_cache: dict = {}

def get_etf_market_data() -> dict:
    """获取 ETF 市场数据（流动性、折溢价）

    数据源: akshare fund_etf_spot_em()

    Returns:
        {
            "liquidity": {"fund_code": 日均成交额, ...},
            "premium": {"fund_code": 折溢价率, ...},
        } 或空字典
    """
    if _etf_market_cache:
        return _etf_market_cache

    try:
        import akshare as ak
        df = ak.fund_etf_spot_em()
        if df.empty:
            return {}

        result = {"liquidity": {}, "premium": {}}
        # 基金代码列可能叫 "基金代码" 或 "代码"
        code_col = "基金代码" if "基金代码" in df.columns else "代码"
        for _, row in df.iterrows():
            code = str(row.get(code_col, ""))
            # 成交额（万元）
            turnover = row.get("成交额", 0) or 0
            result["liquidity"][code] = float(turnover)
            # 折溢价率（如果存在）
            premium = row.get("折溢价率")
            if premium is not None:
                result["premium"][code] = float(premium)

        _etf_market_cache.update(result)
        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"获取ETF市场数据失败: {e}")
        return {}

def compute_tracking_errors(fund_code: str, nav_values: list[float]) -> Optional[float]:
    """计算指数基金的跟踪误差

    跟踪误差 = std(fund_daily_return - index_daily_return) over 60 days
    使用 CSI300 作为权益指数代理

    Returns:
        年化跟踪误差（如 0.005 = 0.5%），或 None
    """
    if len(nav_values) < 60:
        return None
    try:
        import numpy as np
        fund_arr = np.array(nav_values, dtype=np.float64)
        fund_rets = (fund_arr[1:] - fund_arr[:-1]) / fund_arr[:-1]

        csi_prices = get_index_nav_prices("csi300")
        if not csi_prices or len(csi_prices) < len(nav_values):
            return None
        csi_aligned = csi_prices[-len(nav_values):]
        csi_arr = np.array(csi_aligned, dtype=np.float64)
        csi_rets = (csi_arr[1:] - csi_arr[:-1]) / csi_arr[:-1]

        n = min(len(fund_rets), len(csi_rets))
        if n < 20:
            return None
        diff = fund_rets[-n:] - csi_rets[-n:]
        te = float(np.std(diff)) * np.sqrt(252)
        return round(te, 6)
    except Exception:
        return None


# ═══════════════════════════════════════════
# 信号操作
# ═══════════════════════════════════════════

def save_signal(signal: FundSignal):
    """保存信号"""
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO signals
               (signal_id, fund_code, fund_name, signal_type, direction,
                confidence, reason, strategy_name, suggested_amount,
                risk_check_passed, risk_warnings_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (signal.signal_id, signal.fund_code, signal.fund_name,
             signal.signal_type.value, signal.direction.value,
             signal.confidence, signal.reason, signal.strategy_name,
             signal.suggested_amount, int(signal.risk_check_passed),
             json.dumps(signal.risk_warnings)),
        )


def get_signals(fund_code: Optional[str] = None,
                signal_type: Optional[str] = None,
                limit: int = 50, offset: int = 0) -> List[dict]:
    """获取信号历史"""
    with get_conn() as conn:
        query = "SELECT * FROM signals WHERE 1=1"
        params: List[Any] = []
        if fund_code:
            query += " AND fund_code = ?"
            params.append(fund_code)
        if signal_type:
            query += " AND signal_type = ?"
            params.append(signal_type)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════
# 回测结果操作
# ═══════════════════════════════════════════

def save_backtest_result(result: BacktestResult):
    """保存回测结果"""
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO backtest_results
               (backtest_id, strategy_name, fund_codes, start_date, end_date,
                config_json, result_json, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (result.backtest_id, result.config.strategy_name,
             json.dumps(result.config.fund_codes),
             result.config.start_date, result.config.end_date,
             result.config.model_dump_json(),
             result.model_dump_json(), result.status),
        )


def get_backtest_result(backtest_id: str) -> Optional[dict]:
    """获取回测结果"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM backtest_results WHERE backtest_id = ?", (backtest_id,)
        ).fetchone()
        return dict(row) if row else None


def list_backtest_results(strategy_name: Optional[str] = None, limit: int = 20) -> List[dict]:
    """列出回测结果"""
    with get_conn() as conn:
        query = "SELECT backtest_id, strategy_name, fund_codes, start_date, end_date, status, created_at FROM backtest_results"
        params: List[Any] = []
        if strategy_name:
            query += " WHERE strategy_name = ?"
            params.append(strategy_name)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════
# 采集状态操作
# ═══════════════════════════════════════════

def upsert_collection_status(fund_code: str, data_type: str, status: str,
                              last_collected_date: Optional[str] = None,
                              error_msg: Optional[str] = None):
    """更新采集状态"""
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO collection_status
               (fund_code, data_type, last_collected_date, status, error_msg)
               VALUES (?, ?, ?, ?, ?)""",
            (fund_code, data_type, last_collected_date, status, error_msg),
        )


def get_pending_collections() -> List[dict]:
    """获取待采集任务"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM collection_status WHERE status IN ('pending', 'failed') ORDER BY fund_code"
        ).fetchall()
        return [dict(r) for r in rows]
