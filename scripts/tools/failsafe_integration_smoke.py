from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.runtime_failsafe import RuntimeFailsafe, CircuitBreakerPolicy
from natbin.runtime_control_repo import RuntimeControlRepository
from natbin.runtime_precheck import run_precheck


def main() -> int:
    now = datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "runtime_control.sqlite3"
        repo = RuntimeControlRepository(db)
        fs = RuntimeFailsafe(
            kill_switch_file=Path(td) / "KILL_SWITCH",
            kill_switch_env_var="THALOR_KILL_SWITCH",
            global_fail_closed=True,
            market_context_fail_closed=True,
            policy=CircuitBreakerPolicy(failures_to_open=2, cooldown_minutes=15, half_open_trials=1),
        )

        asset = "EURUSD-OTC"
        interval_sec = 300
        fresh = {"market_open": True, "payout": 0.85, "age_sec": 0, "stale": False}

        d0 = run_precheck(
            fs,
            asset=asset,
            interval_sec=interval_sec,
            control_repo=repo,
            market_context=fresh,
            now_utc=now,
        )
        assert not d0.blocked and d0.snapshot and d0.snapshot.ready_to_trade
        print("[smoke][OK] precheck open ok")

        snap = repo.load_breaker(asset, interval_sec)
        snap = fs.record_failure(snap, "collect_recent_timeout", now)
        repo.save_breaker(snap)
        snap = fs.record_failure(snap, "collect_recent_timeout", now)
        repo.save_breaker(snap)

        d1 = run_precheck(
            fs,
            asset=asset,
            interval_sec=interval_sec,
            control_repo=repo,
            market_context=fresh,
            now_utc=now,
        )
        assert d1.blocked and d1.reason and "circuit" in d1.reason
        print("[smoke][OK] circuit breaker blocks precheck ok")

        repo2 = RuntimeControlRepository(Path(td) / "runtime_control_2.sqlite3")
        fs2 = RuntimeFailsafe(
            kill_switch_file=Path(td) / "KILL_SWITCH_2",
            kill_switch_env_var="THALOR_KILL_SWITCH",
            global_fail_closed=True,
            market_context_fail_closed=True,
            policy=CircuitBreakerPolicy(failures_to_open=5, cooldown_minutes=15, half_open_trials=1),
        )
        stale = {"market_open": True, "payout": 0.85, "age_sec": 600, "stale": True}

        d2 = run_precheck(
            fs2,
            asset=asset,
            interval_sec=interval_sec,
            control_repo=repo2,
            market_context=stale,
            now_utc=now,
        )
        assert d2.blocked and d2.reason and "market_context_stale" in d2.reason
        print("[smoke][OK] stale market context blocks precheck ok")

    print("[smoke] ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
