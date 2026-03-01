from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def ensure_state_db(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL;")
    default_interval = 300
    info = con.execute("PRAGMA table_info(executed)").fetchall()
    cols = {r[1] for r in info}
    pk_cols = [r[1] for r in sorted([r for r in info if r[5]], key=lambda x: x[5])]

    desired_pk = ["asset", "interval_sec", "day", "ts"]
    if cols and (("asset" not in cols) or ("interval_sec" not in cols) or (pk_cols != desired_pk)):
        con.execute("ALTER TABLE executed RENAME TO executed_legacy")

    con.execute(
        """
        CREATE TABLE IF NOT EXISTS executed (
          asset TEXT NOT NULL,
          interval_sec INTEGER NOT NULL,
          day TEXT NOT NULL,
          ts INTEGER NOT NULL,
          action TEXT NOT NULL,
          conf REAL NOT NULL,
          score REAL,
          PRIMARY KEY(asset, interval_sec, day, ts)
        )
        """
    )

    legacy = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='executed_legacy'"
    ).fetchone()
    if legacy:
        old_cols = {r[1] for r in con.execute("PRAGMA table_info(executed_legacy)").fetchall()}
        try:
            if "interval_sec" in old_cols:
                con.execute(
                    """
                    INSERT OR IGNORE INTO executed(asset, interval_sec, day, ts, action, conf, score)
                    SELECT COALESCE(NULLIF(asset,''),'LEGACY'), COALESCE(interval_sec, ?), day, ts, action, conf, score
                    FROM executed_legacy
                    """,
                    (int(default_interval),),
                )
            else:
                con.execute(
                    """
                    INSERT OR IGNORE INTO executed(asset, interval_sec, day, ts, action, conf, score)
                    SELECT COALESCE(NULLIF(asset,''),'LEGACY'), ?, day, ts, action, conf, score
                    FROM executed_legacy
                    """,
                    (int(default_interval),),
                )
        except Exception:
            pass
        con.execute("DROP TABLE executed_legacy")

    con.execute("CREATE INDEX IF NOT EXISTS idx_exe_asset_interval_day ON executed(asset, interval_sec, day)")
    con.commit()


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def reconcile(days: int = 7, source_db: str = "runs/live_signals.sqlite3", state_db: str = "runs/live_topk_state.sqlite3") -> dict[str, Any]:
    src_path = Path(source_db)
    st_path = Path(state_db)

    summary: dict[str, Any] = {
        "days": int(max(1, days)),
        "source_db": str(src_path),
        "state_db": str(st_path),
        "source_exists": src_path.exists(),
        "state_exists_before": st_path.exists(),
        "days_scanned": [],
        "signals_trades": 0,
        "inserted": 0,
        "existing": 0,
        "source_table_present": False,
    }

    if not src_path.exists():
        return summary

    st_path.parent.mkdir(parents=True, exist_ok=True)

    sig_con = sqlite3.connect(str(src_path))
    sig_con.row_factory = sqlite3.Row
    try:
        summary["source_table_present"] = _has_table(sig_con, "signals_v2")
        if not summary["source_table_present"]:
            return summary

        day_rows = sig_con.execute(
            "SELECT DISTINCT day FROM signals_v2 WHERE day IS NOT NULL AND day<>'' ORDER BY day DESC LIMIT ?",
            (int(max(1, days)),),
        ).fetchall()
        days_scanned = [str(r[0]) for r in day_rows if r and r[0] is not None]
        summary["days_scanned"] = days_scanned
        if not days_scanned:
            return summary

        marks = ",".join("?" * len(days_scanned))
        try:
            rows = sig_con.execute(
                f"""
                SELECT
                  COALESCE(NULLIF(asset, ''), 'UNKNOWN') AS asset,
                  COALESCE(interval_sec, 300) AS interval_sec,
                  day,
                  ts,
                  action,
                  conf,
                  score
                FROM signals_v2
                WHERE action IN ('CALL','PUT')
                  AND day IN ({marks})
                ORDER BY day ASC, ts ASC
                """,
                tuple(days_scanned),
            ).fetchall()
        except Exception:
            rows = sig_con.execute(
                f"""
                SELECT
                  COALESCE(NULLIF(asset, ''), 'UNKNOWN') AS asset,
                  300 AS interval_sec,
                  day,
                  ts,
                  action,
                  conf,
                  score
                FROM signals_v2
                WHERE action IN ('CALL','PUT')
                  AND day IN ({marks})
                ORDER BY day ASC, ts ASC
                """,
                tuple(days_scanned),
            ).fetchall()
    finally:
        sig_con.close()

    summary["signals_trades"] = int(len(rows))
    if not rows:
        return summary

    st_con = sqlite3.connect(str(st_path))
    try:
        ensure_state_db(st_con)
        before_changes = st_con.total_changes
        existing = 0
        for r in rows:
            asset = str(r["asset"] or "UNKNOWN")
            interval_sec = int(r["interval_sec"] or 300)
            day = str(r["day"] or "")
            ts = int(r["ts"] or 0)
            action = str(r["action"] or "").upper()
            conf = float(r["conf"] or 0.0)
            score = r["score"]

            prev_changes = st_con.total_changes
            st_con.execute(
                "INSERT OR IGNORE INTO executed(asset, interval_sec, day, ts, action, conf, score) VALUES(?,?,?,?,?,?,?)",
                (asset, int(interval_sec), day, ts, action, conf, score),
            )
            if st_con.total_changes == prev_changes:
                existing += 1
        st_con.commit()
        inserted = st_con.total_changes - before_changes
    finally:
        st_con.close()

    summary["inserted"] = int(inserted)
    summary["existing"] = int(existing)
    summary["state_exists_after"] = st_path.exists()
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="Reconcile live_topk_state.sqlite3 from signals_v2 emitted trades")
    ap.add_argument("--days", type=int, default=7, help="Number of most recent distinct days to scan from signals_v2")
    ap.add_argument("--source-db", default="runs/live_signals.sqlite3")
    ap.add_argument("--state-db", default="runs/live_topk_state.sqlite3")
    args = ap.parse_args()

    out = reconcile(days=max(1, int(args.days)), source_db=args.source_db, state_db=args.state_db)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
