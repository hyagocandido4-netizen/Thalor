import sqlite3
from pathlib import Path

import pandas as pd

from natbin.dataset2 import build_dataset


def _seed_candles(db_path: Path, *, asset: str, interval_sec: int, n: int, ts0: int = 1700000000) -> list[int]:
    """Create a minimal candles table compatible with natbin.dataset2._load_candles."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candles (
                asset TEXT NOT NULL,
                interval_sec INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (asset, interval_sec, ts)
            );
            """
        )

        # Align to the interval grid so we don't couple this regression test
        # to snap-to-grid behavior.
        ts0 = int(ts0) - (int(ts0) % int(interval_sec))

        ts_list: list[int] = []
        price = 1.1000
        for i in range(n):
            ts = ts0 + i * interval_sec
            # Deterministic but non-trivial price path.
            op = price
            cl = price + (0.0001 if (i % 2 == 0) else -0.00005)
            hi = max(op, cl) + 0.0002
            lo = min(op, cl) - 0.0002
            vol = 1000.0 + i

            conn.execute(
                "INSERT OR REPLACE INTO candles(asset, interval_sec, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?)",
                (asset, interval_sec, ts, op, hi, lo, cl, vol),
            )
            ts_list.append(ts)
            price = cl

        conn.commit()

    return ts_list


def test_dataset_includes_last_candle_and_incremental_updates(tmp_path: Path, monkeypatch):
    # Force the incremental code path (P11) to be enabled.
    monkeypatch.setenv("DATASET_INCREMENTAL", "1")

    asset = "EURUSD-OTC"
    interval_sec = 300

    db_path = tmp_path / "data" / "market.sqlite3"
    out_csv = tmp_path / "dataset.csv"

    # Need enough candles to satisfy the rolling windows + dropna(feature_cols).
    ts_list = _seed_candles(db_path, asset=asset, interval_sec=interval_sec, n=220)

    # Full build (no existing meta/csv).
    res1 = build_dataset(str(db_path), asset, interval_sec, str(out_csv))
    assert Path(res1.path).exists()

    df1 = pd.read_csv(out_csv)
    assert int(df1["ts"].iloc[-1]) == ts_list[-1]

    # The last candle should be present for inference, but its label is NaN.
    assert pd.isna(df1["y_open_close"].iloc[-1])

    # With a continuous synthetic series, only the last candle should have NaN label.
    assert int(df1["y_open_close"].isna().sum()) == 1

    # Incremental update: add one more candle and rebuild.
    ts_list2 = _seed_candles(db_path, asset=asset, interval_sec=interval_sec, n=221)
    assert len(ts_list2) == len(ts_list) + 1

    res2 = build_dataset(str(db_path), asset, interval_sec, str(out_csv))
    assert Path(res2.path).exists()

    df2 = pd.read_csv(out_csv)
    assert int(df2["ts"].iloc[-1]) == ts_list2[-1]
    assert pd.isna(df2["y_open_close"].iloc[-1])
    assert int(df2["y_open_close"].isna().sum()) == 1
