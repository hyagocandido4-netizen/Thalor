from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

import yaml


RUNS_DIR = Path("runs")
DATE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("daily_summary", re.compile(r"^daily_summary_(\d{8})(?:_[A-Za-z0-9_-]+)?(?:_\d+s)?\.json$")),
    ("live_signals_csv", re.compile(r"^live_signals_v2_(\d{8})(?:_[A-Za-z0-9_-]+)?(?:_\d+s)?\.csv$")),
]


def repo_timezone() -> ZoneInfo:
    cfg_path = Path("config.yaml")
    if cfg_path.exists():
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            tz_name = str((cfg.get("data") or {}).get("timezone") or "UTC")
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return ZoneInfo("UTC")


def keep_cutoff_day(days: int) -> str:
    days = max(1, int(days))
    today = datetime.now(repo_timezone()).date()
    cutoff = today - timedelta(days=days - 1)
    return cutoff.isoformat()


def parse_file_day(name: str) -> str | None:
    for _, pat in DATE_PATTERNS:
        m = pat.match(name)
        if m:
            raw = m.group(1)
            try:
                dt = datetime.strptime(raw, "%Y%m%d")
                return dt.strftime("%Y-%m-%d")
            except Exception:
                return None
    return None


def prune_daily_files(cutoff_day: str) -> dict[str, int]:
    deleted: dict[str, int] = {key: 0 for key, _ in DATE_PATTERNS}
    if not RUNS_DIR.exists():
        return deleted
    for path in RUNS_DIR.iterdir():
        if not path.is_file():
            continue
        for key, pat in DATE_PATTERNS:
            if not pat.match(path.name):
                continue
            day = parse_file_day(path.name)
            if day and day < cutoff_day:
                try:
                    path.unlink()
                    deleted[key] += 1
                except FileNotFoundError:
                    pass
                except Exception:
                    # best-effort cleanup; keep moving
                    pass
            break
    return deleted


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def prune_db_rows(db_path: Path, table: str, cutoff_day: str) -> int:
    if not db_path.exists():
        return 0
    deleted = 0
    con = sqlite3.connect(str(db_path))
    try:
        if not table_exists(con, table):
            return 0
        cur = con.execute(f"DELETE FROM {table} WHERE day < ?", (cutoff_day,))
        deleted = int(cur.rowcount or 0)
        con.commit()
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    finally:
        con.close()
    return deleted


def build_summary(days: int) -> dict:
    cutoff_day = keep_cutoff_day(days)
    deleted_files = prune_daily_files(cutoff_day)
    pruned_rows = {
        "executed": prune_db_rows(RUNS_DIR / "live_topk_state.sqlite3", "executed", cutoff_day),
        "signals_v2": prune_db_rows(RUNS_DIR / "live_signals.sqlite3", "signals_v2", cutoff_day),
    }
    return {
        "retention_days": int(days),
        "cutoff_day": cutoff_day,
        "deleted_files": deleted_files,
        "pruned_rows": pruned_rows,
        "files_deleted_total": int(sum(deleted_files.values())),
        "rows_deleted_total": int(sum(pruned_rows.values())),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Prune old runtime artifacts under runs/")
    ap.add_argument("--days", type=int, default=30, help="keep the last N local days including today (default: 30)")
    args = ap.parse_args()
    summary = build_summary(max(1, int(args.days)))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
