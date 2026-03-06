from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.runtime_failsafe import RuntimeFailsafe, CircuitBreakerSnapshot
from natbin.runtime_control_repo import RuntimeControlRepository


def main() -> int:
    now = datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc)

    with TemporaryDirectory() as td:
        kill_file = Path(td) / "KILL_SWITCH"
        fs = RuntimeFailsafe(kill_switch_file=kill_file)

        snap = CircuitBreakerSnapshot(asset="EURUSD-OTC", interval_sec=300)
        pre = fs.precheck(
            now_utc=now,
            breaker=snap,
            market_open=True,
            market_context_stale=False,
            quota_hard_block=False,
            quota_reason=None,
            env={},
        )
        assert pre.ready_to_trade is True
        print("[smoke][OK] precheck open ok")

        kill_file.write_text("1", encoding="utf-8")
        pre2 = fs.precheck(
            now_utc=now,
            breaker=snap,
            market_open=True,
            market_context_stale=False,
            quota_hard_block=False,
            quota_reason=None,
            env={},
        )
        assert pre2.ready_to_trade is False and pre2.kill_switch_active is True
        print("[smoke][OK] kill switch file blocks ok")
        kill_file.unlink()

        snap = fs.record_failure(snap, "broker_timeout", now)
        snap = fs.record_failure(snap, "broker_timeout", now)
        snap = fs.record_failure(snap, "broker_timeout", now)
        assert snap.state == "open"
        print("[smoke][OK] breaker opens after failures ok")

        repo = RuntimeControlRepository(Path(td) / "runtime_control.sqlite3")
        repo.save_breaker(snap)
        loaded = repo.load_breaker("EURUSD-OTC", 300)
        assert loaded.state == "open" and loaded.failures == 3
        print("[smoke][OK] control repo breaker roundtrip ok")

        pre3 = fs.precheck(
            now_utc=now,
            breaker=loaded,
            market_open=True,
            market_context_stale=True,
            quota_hard_block=False,
            quota_reason=None,
            env={},
        )
        assert pre3.ready_to_trade is False
        print("[smoke][OK] stale market context fail-closed ok")

    print("[smoke] ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
