#!/usr/bin/env python
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def _ok(msg: str) -> None:
    print(f"[repos-smoke][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[repos-smoke][FAIL] {msg}")
    raise SystemExit(2)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from natbin.runtime_repos import RuntimeTradeLedger, SignalsRepository

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        sig_db = td_path / "signals.sqlite3"
        st_db = td_path / "state.sqlite3"

        repo = SignalsRepository(sig_db, default_interval=300)
        base = {
            "day": "2026-03-01",
            "asset": "EURUSD-OTC",
            "interval_sec": 300,
            "ts": 1772316300,
            "dt_local": "2026-03-01 00:05:00",
            "proba_up": 0.55,
            "conf": 0.55,
            "regime_ok": 1,
            "threshold": 0.02,
            "action": "CALL",
            "reason": "topk_emit",
        }
        repo.write_row(dict(base))
        hold = dict(base)
        hold["action"] = "HOLD"
        hold["reason"] = "should_not_overwrite"
        result = repo.write_row(hold)
        if not result.get("preserved_trade"):
            _fail("signals repository did not preserve first emitted trade")
        _ok("signals repository preserves emitted trade rows")

        ledger = RuntimeTradeLedger(signals_db=sig_db, state_db=st_db, default_interval=300)
        count = ledger.executed_today_count("EURUSD-OTC", 300, "2026-03-01")
        if count != 1:
            _fail(f"ledger count expected 1 got {count}")
        if not ledger.already_executed("EURUSD-OTC", 300, "2026-03-01", 1772316300):
            _fail("ledger already_executed should use durable signal rows")
        last_ts = ledger.last_executed_ts("EURUSD-OTC", 300, "2026-03-01")
        if int(last_ts or 0) != 1772316300:
            _fail(f"ledger last_executed_ts mismatch: {last_ts}")
        _ok("ledger prefers durable signals and heals state")

        ledger.mark_executed("EURUSD-OTC", 300, "2026-03-01", 1772316600, "PUT", 0.61, 0.61)
        count_state_only = ledger.state.count_day("EURUSD-OTC", 300, "2026-03-01")
        if count_state_only < 2:
            _fail(f"state repository upsert missing expected row count>=2 got {count_state_only}")
        _ok("state repository upsert ok")

    print("[repos-smoke] ALL OK")


if __name__ == "__main__":
    main()
