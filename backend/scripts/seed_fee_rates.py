"""初始化费率数据 — 用于新部署或重置费率表

运行方式: python -m backend.scripts.seed_fee_rates
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from fund_quant.data.storage import get_conn

SEED_DATA = [
    ("equity", 0.015, 0.015, 0.0025, 0.004, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
    ("index", 0.010, 0.005, 0.0010, 0.0025, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
    ("balanced", 0.012, 0.012, 0.0020, 0.0035, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
    ("bond", 0.008, 0.006, 0.0015, 0.0025, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
    ("money", 0.0, 0.003, 0.0005, 0.0025, '{"7":0,"30":0,"365":0,"730":0,"999999":0}'),
    ("qdii", 0.015, 0.015, 0.0025, 0.004, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
    ("commodity", 0.010, 0.010, 0.0020, 0.0035, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
    ("fof", 0.012, 0.010, 0.0020, 0.0035, '{"7":1.5,"30":0.75,"365":0.5,"730":0.25,"999999":0}'),
]


def seed(conn=None):
    if conn is None:
        with get_conn() as conn:
            return _seed(conn)
    return _seed(conn)


def _seed(conn):
    inserted = 0
    for row in SEED_DATA:
        conn.execute("""
            INSERT OR IGNORE INTO fund_type_defaults
            (fund_type, sub_fee, mgmt_fee, custody_fee, c_class_service_fee, redemption_tiers_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, row)
        inserted += 1
    conn.commit()
    print(f"已写入 {inserted} 条费率数据")


if __name__ == "__main__":
    seed()
    print("OK")
