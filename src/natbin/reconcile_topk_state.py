from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .runtime_repos import ExecutedStateRepository, SignalsRepository


def reconcile(days: int = 7, source_db: str = "runs/live_signals.sqlite3", state_db: str = "runs/live_topk_state.sqlite3") -> dict[str, Any]:
    src_path = Path(source_db)
    st_path = Path(state_db)

    default_interval = 300
    signals = SignalsRepository(src_path, default_interval=default_interval)
    state = ExecutedStateRepository(st_path, default_interval=default_interval)

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
        "repository_layer": True,
    }

    if not src_path.exists():
        return summary

    summary["source_table_present"] = signals.table_present()
    if not summary["source_table_present"]:
        return summary

    days_scanned = signals.distinct_recent_days(int(max(1, days)))
    summary["days_scanned"] = days_scanned
    if not days_scanned:
        return summary

    rows = signals.fetch_trade_rows_for_days(days_scanned)
    summary["signals_trades"] = int(len(rows))
    if not rows:
        summary["state_exists_after"] = st_path.exists()
        return summary

    inserted, existing = state.insert_ignore_trade_rows(rows)
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
