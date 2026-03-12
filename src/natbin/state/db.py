import sqlite3
from pathlib import Path
from typing import Iterable, Dict, Any, Tuple


DDL = """
CREATE TABLE IF NOT EXISTS candles (
  asset TEXT NOT NULL,
  interval_sec INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL,
  PRIMARY KEY (asset, interval_sec, ts)
);
CREATE INDEX IF NOT EXISTS idx_candles_asset_ts ON candles(asset, ts);
"""


def open_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    for stmt in DDL.strip().split(";"):
        s = stmt.strip()
        if s:
            con.execute(s + ";")
    con.commit()
    return con


def _normalize_ts(ts: int) -> int:
    # Se vier em milissegundos (13 dígitos), converte para segundos
    if ts > 1_000_000_000_000:
        ts = ts // 1000
    return int(ts)


def _row_from_candle(asset: str, interval_sec: int, c: Dict[str, Any]) -> Tuple:
    raw_ts = int(c.get("from") or c.get("time") or 0)
    ts = _normalize_ts(raw_ts)

    o = float(c["open"])
    cl = float(c["close"])
    lo = float(c.get("min", c.get("low")))
    hi = float(c.get("max", c.get("high")))
    vol = c.get("volume")
    vol = float(vol) if vol is not None else None

    if ts <= 0:
        raise ValueError(f"Candle sem timestamp valido: {c}")
    return (asset, interval_sec, ts, o, hi, lo, cl, vol)


def upsert_candles(con: sqlite3.Connection, asset: str, interval_sec: int, candles: Iterable[Dict[str, Any]]) -> int:
    rows = []
    for c in candles:
        try:
            rows.append(_row_from_candle(asset, interval_sec, c))
        except Exception:
            continue

    if not rows:
        return 0

    # IMPORTANT:
    # Use UPSERT (INSERT .. ON CONFLICT DO UPDATE) instead of INSERT OR IGNORE.
    # Rationale:
    # - Some collectors may accidentally ingest an in-progress candle (open candle).
    # - When the candle closes, we MUST be able to update OHLC/volume for that same ts.
    # - INSERT OR IGNORE would permanently keep the partial values, corrupting the DB and all downstream datasets.
    con.executemany(
        """
        INSERT INTO candles(asset, interval_sec, ts, open, high, low, close, volume)
        VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(asset, interval_sec, ts) DO UPDATE SET
          open=excluded.open,
          high=excluded.high,
          low=excluded.low,
          close=excluded.close,
          volume=excluded.volume
        """,
        rows,
    )
    con.commit()
    return len(rows)


def count_candles(con: sqlite3.Connection, asset: str, interval_sec: int) -> int:
    cur = con.execute("SELECT COUNT(*) FROM candles WHERE asset=? AND interval_sec=?", (asset, interval_sec))
    return int(cur.fetchone()[0])
