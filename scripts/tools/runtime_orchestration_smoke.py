#!/usr/bin/env python
from __future__ import annotations

import csv
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def _ok(msg: str) -> None:
    print(f"[orchestration-smoke][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[orchestration-smoke][FAIL] {msg}")
    raise SystemExit(2)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from natbin.refresh_daily_summary import refresh_daily_summaries
    from natbin.runtime_repos import SignalsRepository
    from natbin.summary_paths import daily_summary_path

    tz = ZoneInfo("UTC")
    now = datetime.now(tz=tz)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        runs = td_path / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        db_path = runs / "live_signals.sqlite3"
        dataset_path = td_path / "dataset.csv"

        with dataset_path.open("w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(["ts", "y_open_close"])
            wr.writerow([int(now.timestamp()), ""])
            wr.writerow([int((now - timedelta(days=1)).timestamp()), 1])

        repo = SignalsRepository(db_path, default_interval=300)
        repo.write_row({
            "day": yesterday,
            "asset": "EURUSD-OTC",
            "interval_sec": 300,
            "ts": int((now - timedelta(days=1)).timestamp()),
            "dt_local": (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "proba_up": 0.55,
            "conf": 0.55,
            "score": 0.55,
            "regime_ok": 1,
            "threshold": 0.02,
            "action": "CALL",
            "reason": "topk_emit",
        })

        cfg = {
            "data": {
                "timezone": "UTC",
                "asset": "EURUSD-OTC",
                "interval_sec": 300,
            },
            "phase2": {
                "dataset_path": str(dataset_path),
            },
        }

        result = refresh_daily_summaries(
            cfg=cfg,
            wanted=[today, yesterday],
            db_path=str(db_path),
            out_dir=str(runs),
            force_today_stub=True,
        )

        refreshed = {Path(p).name for p in result.get("refreshed", [])}
        today_name = daily_summary_path(day=today, asset="EURUSD-OTC", interval_sec=300, out_dir=runs).name
        yesterday_name = daily_summary_path(day=yesterday, asset="EURUSD-OTC", interval_sec=300, out_dir=runs).name

        if today_name not in refreshed:
            _fail(f"today summary not refreshed: expected {today_name}, got {sorted(refreshed)}")
        if yesterday_name not in refreshed:
            _fail(f"yesterday summary not refreshed: expected {yesterday_name}, got {sorted(refreshed)}")
        if today not in set(result.get("forced_stub_days", [])):
            _fail(f"today not marked as forced_stub_days: {result}")

        _ok("refresh_daily_summary writes current-day stub summary before autos")

        con = sqlite3.connect(db_path)
        try:
            cnt = con.execute("SELECT COUNT(*) FROM signals_v2 WHERE day=?", (today,)).fetchone()[0]
        finally:
            con.close()
        if cnt != 0:
            _fail(f"today should remain signal-free in smoke fixture, got {cnt} rows")
        _ok("forced current-day summary does not require signal rows")

    print("[orchestration-smoke] ALL OK")


if __name__ == "__main__":
    main()
