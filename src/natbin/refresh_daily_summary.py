from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .observe_signal_topk_perday import load_cfg, write_daily_summary


def _target_days(*, tz: ZoneInfo, days: int) -> list[str]:
    now = datetime.now(tz=tz)
    out: list[str] = []
    for i in range(max(1, int(days))):
        out.append((now - timedelta(days=i)).strftime("%Y-%m-%d"))
    return out


def _existing_signal_days(db_path: str, wanted: list[str], asset: str, interval_sec: int) -> set[str]:
    p = Path(db_path)
    if not p.exists() or not wanted:
        return set()
    con = sqlite3.connect(str(p))
    try:
        try:
            con.execute("SELECT 1 FROM signals_v2 LIMIT 1").fetchone()
        except Exception:
            return set()
        marks = ",".join(["?"] * len(wanted))
        try:
            rows = con.execute(
                f"SELECT DISTINCT day FROM signals_v2 WHERE asset=? AND interval_sec=? AND day IN ({marks})",
                (str(asset), int(interval_sec), *tuple(wanted)),
            ).fetchall()
        except Exception:
            try:
                rows = con.execute(
                    f"SELECT DISTINCT day FROM signals_v2 WHERE asset=? AND day IN ({marks})",
                    (str(asset), *tuple(wanted)),
                ).fetchall()
            except Exception:
                rows = con.execute(
                    f"SELECT DISTINCT day FROM signals_v2 WHERE day IN ({marks})",
                    tuple(wanted),
                ).fetchall()
        return {str(r[0]) for r in rows if r and r[0] is not None}
    finally:
        con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--db-path", type=str, default="runs/live_signals.sqlite3")
    ap.add_argument("--out-dir", type=str, default="runs")
    args = ap.parse_args()

    cfg, _best = load_cfg()
    tz = ZoneInfo(cfg.get("data", {}).get("timezone", "UTC"))
    asset = cfg.get("data", {}).get("asset", "UNKNOWN")
    interval_sec = int(cfg.get("data", {}).get("interval_sec", 300))
    dataset_path = cfg.get("phase2", {}).get("dataset_path", "data/dataset_phase2.csv")

    wanted = _target_days(tz=tz, days=args.days)
    existing = _existing_signal_days(args.db_path, wanted, asset, interval_sec)

    refreshed: list[str] = []
    for day in wanted:
        if day not in existing:
            continue
        out = write_daily_summary(
            day=day,
            tz=tz,
            asset=asset,
            interval_sec=interval_sec,
            dataset_path=dataset_path,
            db_path=args.db_path,
            out_dir=args.out_dir,
        )
        refreshed.append(out)

    print(json.dumps({
        "asset": asset,
        "interval_sec": interval_sec,
        "days_requested": wanted,
        "days_written": len(refreshed),
        "refreshed": refreshed,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
