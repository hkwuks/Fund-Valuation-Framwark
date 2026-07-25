"""存量基金数据 fund_type 迁移脚本

将旧的 FundType 值（stock/hybrid/index/bond/qdii/money/fof/etf/etf_link）
迁移到新的 8 类分类值（equity/index/balanced/bond/money/qdii/commodity/fof）。

运行方式: python -m backend.scripts.migrate_fund_type
"""

import sys
import sqlite3
from pathlib import Path

# 添加 backend 到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# 旧→新静态映射（不要用 classify_fund_for_quant，它只认中文关键字）
_OLD_TO_NEW = {
    "stock": "equity",
    "hybrid": "equity",     # 旧 hybrid 中大部分是偏股混合，安全归 equity
    "index": "index",
    "bond": "bond",
    "money": "money",
    "qdii": "qdii",
    "fof": "fof",
    "etf": "index",         # ETF → index
    "etf_link": "index",    # ETF联接 → index
}


def migrate(db_path: str, dry_run: bool = True) -> dict:
    """迁移 fund_metadata 表中的 fund_type 列

    Args:
        db_path: SQLite 数据库路径
        dry_run: True 只输出不修改, False 执行 UPDATE

    Returns:
        {"updated": N, "skipped": N, "errors": [fund_code, ...]}
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    rows = cursor.execute(
        "SELECT fund_code, fund_name, fund_type FROM fund_metadata"
    ).fetchall()

    updated = 0
    skipped = 0
    errors = []

    for row in rows:
        code = row["fund_code"]
        name = row["fund_name"] or ""
        old_type = row["fund_type"] or ""

        if not old_type:
            skipped += 1
            continue

        # 如果在新的 8 类集合中，跳过
        if old_type in ("equity", "index", "balanced", "bond",
                        "money", "qdii", "commodity", "fof"):
            skipped += 1
            continue

        # 查映射表（不要调用 classify_fund_for_quant — 它只认中文）
        new_type = _OLD_TO_NEW.get(old_type)
        if new_type is None:
            errors.append(code)
            continue

        if dry_run:
            print(f"[DRY RUN] {code} ({name}): {old_type} → {new_type}")
        else:
            cursor.execute(
                "UPDATE fund_metadata SET fund_type = ? WHERE fund_code = ?",
                (new_type, code)
            )
        updated += 1

    if not dry_run:
        conn.commit()

    conn.close()
    return {"updated": updated, "skipped": skipped, "errors": errors}


def get_db_path() -> str:
    from fund_quant.core.config import fund_quant_settings
    return fund_quant_settings.FUND_QUANT_DB_PATH


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="迁移基金类型到新的 8 类分类")
    parser.add_argument("--db", default=None, help="数据库路径，默认使用配置路径")
    parser.add_argument("--apply", action="store_true", help="执行更新（默认 dry-run）")
    args = parser.parse_args()

    db_path = args.db or get_db_path()
    print(f"数据库: {db_path}")
    if not Path(db_path).exists():
        print(f"错误: 数据库文件不存在 ({db_path})")
        sys.exit(1)

    result = migrate(db_path, dry_run=not args.apply)
    print(f"\n完成: 更新 {result['updated']}, 跳过 {result['skipped']}, 错误 {len(result['errors'])}")
    if result['errors']:
        print(f"错误基金: {result['errors']}")
    if not args.apply:
        print("\n(这是 dry-run，加 --apply 执行实际更新)")
