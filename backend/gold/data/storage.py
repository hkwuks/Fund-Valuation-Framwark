import os
import sqlite3
import json
from contextlib import contextmanager
from typing import Optional

from loguru import logger

from backend.gold.core.models import GoldBarData, GoldSignal
from backend.gold.core.config import gold_settings


class GoldDataStore:
    """SQLite存储 — 独立gold.db，WAL模式"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = gold_settings.gold_db_path
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS bars (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL DEFAULT 'SHFE',
                    period TEXT NOT NULL,
                    datetime TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL DEFAULT 0,
                    turnover REAL NOT NULL DEFAULT 0,
                    open_interest REAL NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'akshare',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(symbol, exchange, period, datetime)
                );

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT NOT NULL UNIQUE,
                    strategy_id TEXT NOT NULL,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    price REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    stop_loss REAL,
                    take_profit REAL,
                    confidence REAL NOT NULL DEFAULT 0,
                    reason TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS backtest_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    period TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    capital REAL NOT NULL,
                    params TEXT,
                    report TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_bars_lookup
                    ON bars(symbol, period, datetime);
                CREATE INDEX IF NOT EXISTS idx_signals_time
                    ON signals(created_at);
                CREATE INDEX IF NOT EXISTS idx_backtest_strategy
                    ON backtest_results(strategy_name, created_at);

                -- CTP 模拟交易表
                CREATE TABLE IF NOT EXISTS live_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    volume INTEGER NOT NULL DEFAULT 0,
                    avg_price REAL NOT NULL DEFAULT 0,
                    pnl REAL NOT NULL DEFAULT 0,
                    margin REAL NOT NULL DEFAULT 0,
                    snapshot_time TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_lp_time
                    ON live_positions(snapshot_time);

                CREATE TABLE IF NOT EXISTS live_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    balance REAL NOT NULL,
                    available REAL NOT NULL,
                    margin REAL NOT NULL,
                    pnl REAL NOT NULL,
                    snapshot_time TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ctp_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_ref INTEGER NOT NULL,
                    order_id TEXT NOT NULL UNIQUE,
                    front_id INTEGER,
                    session_id INTEGER,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
            """)

    def save_bars(self, bars: list[GoldBarData], period: str,
                  source: str = "akshare") -> int:
        if not bars:
            return 0
        with self._get_conn() as conn:
            count = 0
            for bar in bars:
                try:
                    conn.execute(
                        """INSERT OR REPLACE INTO bars
                           (symbol, exchange, period, datetime, open, high, low, close,
                            volume, turnover, open_interest, source)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (bar.symbol, bar.exchange, period,
                         bar.datetime.isoformat(),
                         bar.open, bar.high, bar.low, bar.close,
                         bar.volume, bar.turnover, bar.open_interest, source)
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"save_bars skip: {e}")
            conn.commit()
            return count

    def get_bars(self, symbol: str, period: str,
                 start: str = None, end: str = None,
                 limit: int = None) -> list[GoldBarData]:
        with self._get_conn() as conn:
            query = "SELECT * FROM bars WHERE symbol = ? AND period = ?"
            params = [symbol, period]
            if symbol == "GC=F":
                query += " AND exchange = ?"
                params.append("COMEX")
            elif symbol == "AU0" or symbol.lower().startswith("au"):
                query += " AND exchange = ?"
                params.append("SHFE")
            if start:
                query += " AND datetime >= ?"
                params.append(start)
            if end:
                query += " AND datetime <= ?"
                params.append(end)
            query += " ORDER BY datetime DESC"  # 最新的在前
            if limit:
                query += f" LIMIT {limit}"
            rows = conn.execute(query, params).fetchall()

        # 反转回正序（ASC），供回测引擎顺序消费
        rows = list(reversed(rows))
        bars = []
        for row in rows:
            bars.append(GoldBarData(
                symbol=row["symbol"], exchange=row["exchange"],
                period=row["period"],
                datetime=row["datetime"],
                open=row["open"], high=row["high"],
                low=row["low"], close=row["close"],
                volume=row["volume"], turnover=row["turnover"],
                open_interest=row["open_interest"],
            ))
        return bars

    def save_signal(self, signal: GoldSignal) -> bool:
        with self._get_conn() as conn:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO signals
                       (signal_id, strategy_id, strategy_name, symbol, direction,
                        price, volume, stop_loss, take_profit, confidence, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (signal.signal_id, signal.strategy_id, signal.strategy_name,
                     signal.symbol, signal.direction.value,
                     signal.price, signal.volume, signal.stop_loss,
                     signal.take_profit, signal.confidence, signal.reason,
                     signal.created_at.isoformat() if signal.created_at else "")
                )
                conn.commit()
                return True
            except Exception as e:
                logger.warning(f"save_signal fail: {e}")
                return False

    def get_signals(self, strategy_id: str = None,
                    limit: int = 100) -> list[GoldSignal]:
        with self._get_conn() as conn:
            if strategy_id:
                rows = conn.execute(
                    "SELECT * FROM signals WHERE strategy_id = ? ORDER BY created_at DESC LIMIT ?",
                    (strategy_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()

        from backend.gold.core.models import SignalDirection
        signals = []
        for row in rows:
            signals.append(GoldSignal(
                signal_id=row["signal_id"],
                strategy_id=row["strategy_id"],
                strategy_name=row["strategy_name"],
                symbol=row["symbol"],
                direction=SignalDirection(row["direction"]),
                price=row["price"],
                volume=row["volume"],
                stop_loss=row["stop_loss"],
                take_profit=row["take_profit"],
                confidence=row["confidence"],
                reason=row["reason"],
                created_at=row["created_at"],
            ))
        return signals

    def save_backtest_result(self, strategy_name: str, symbol: str,
                              period: str, start_date: str, end_date: str,
                              capital: float, params: dict,
                              report: dict) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO backtest_results
                   (strategy_name, symbol, period, start_date, end_date,
                    capital, params, report, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (strategy_name, symbol, period, start_date, end_date,
                 capital, json.dumps(params), json.dumps(report))
            )
            conn.commit()
            return cursor.lastrowid
