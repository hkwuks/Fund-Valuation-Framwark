import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.gold.data.storage import GoldDataStore


def test_get_bars_keeps_au0_on_shfe(tmp_path):
    store = GoldDataStore(str(tmp_path / "gold.db"))
    store.save_bars = lambda bars, period: None
    with store._get_conn() as conn:
        conn.executemany(
            """INSERT INTO bars (symbol, exchange, period, datetime, open, high, low, close)
               VALUES (?, ?, 'd', ?, 1, 1, 1, 1)""",
            [
                ("AU0", "SHFE", "2026-01-12T00:00:00"),
                ("AU0", "COMEX", "2026-01-12T00:00:00-05:00"),
            ],
        )
        conn.commit()

    bars = store.get_bars("AU0", "d")
    assert len(bars) == 1
    assert bars[0].exchange == "SHFE"
