"""CI smoke test for Package R execution layer.

This is intentionally lightweight:
- Creates a temporary repo_root with a signals DB.
- Runs process_latest_signal() in two modes:
  1) execution disabled -> must persist an explicit intent_blocked event with reason=execution_disabled
  2) live + fake broker -> must create a submit attempt

The goal is to catch regressions in execution wiring without requiring
external broker credentials.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def _repo_root() -> Path:
    # scripts/ci/smoke_execution_layer.py -> repo root
    return Path(__file__).resolve().parents[2]


def _write_cfg(path: Path, *, enabled: bool, mode: str) -> None:
    # Minimal v2 config; everything else uses model defaults.
    cfg = f"""version: '2.0'
assets:
  - asset: 'EURUSD-OTC'
    interval_sec: 300
    timezone: 'UTC'
execution:
  enabled: {str(enabled).lower()}
  mode: '{mode}'
  provider: 'fake'
  account_mode: 'PRACTICE'
  stake:
    amount: 1.0
    currency: 'USD'
"""
    path.write_text(cfg, encoding="utf-8")


def _ensure_signals_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        from natbin.state.migrations import ensure_signals_v2

        ensure_signals_v2(conn)
        conn.commit()


def _insert_signal(db_path: Path, *, ts: int, action: str) -> None:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    day = dt[:10]

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO signals_v2 (
              dt_local, day, asset, interval_sec, ts,
              proba_up, conf, regime_ok, threshold,
              action, reason
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                dt,
                day,
                "EURUSD-OTC",
                300,
                int(ts),
                0.55,
                0.55,
                1,
                0.50,
                action,
                "smoke",
            ),
        )
        conn.commit()


def _count_rows(db_path: Path, table: str, where_sql: str = "", params: tuple = ()) -> int:
    with sqlite3.connect(db_path) as conn:
        q = f"SELECT COUNT(*) FROM {table}"
        if where_sql:
            q += f" WHERE {where_sql}"
        return int(conn.execute(q, params).fetchone()[0])


def main() -> int:
    repo_root = _repo_root()
    sys.path.insert(0, str(repo_root / "src"))

    from natbin.runtime.execution import execution_repo_path, process_latest_signal, signals_repo_db_path

    # NOTE: On Windows, sqlite files can stay locked for a short time (WinError 32)
    # (delayed handle release / AV scanning). This is a smoke test, so temp cleanup
    # is best-effort and must not fail the test.
    try:
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    except TypeError:
        tmp = tempfile.TemporaryDirectory()

    try:
        tmp_root = Path(tmp.name)

        sig_db = signals_repo_db_path(tmp_root)
        _ensure_signals_db(sig_db)

        # --- Case 1: execution disabled should persist a blocked entry
        cfg_disabled = tmp_root / "config_disabled.yaml"
        _write_cfg(cfg_disabled, enabled=False, mode="disabled")

        ts0 = int(time.time())
        _insert_signal(sig_db, ts=ts0, action="CALL")

        payload_disabled = process_latest_signal(repo_root=tmp_root, config_path=cfg_disabled)
        assert payload_disabled.get("enabled") is False, payload_disabled
        assert payload_disabled.get("blocked_reason") == "execution_disabled", payload_disabled

        ex_db = execution_repo_path(tmp_root)
        assert ex_db.exists(), f"execution db not created at {ex_db}"

        intents = _count_rows(ex_db, "order_intents")
        blocked = _count_rows(
            ex_db,
            "order_events",
            where_sql="event_type = ? AND payload_json LIKE ?",
            params=("intent_blocked", "%execution_disabled%"),
        )
        assert intents >= 1, f"expected >=1 intents, got {intents}"
        assert blocked >= 1, f"expected >=1 blocked events with execution_disabled, got {blocked}"

        # --- Case 2: live mode + fake broker should submit
        cfg_live = tmp_root / "config_live.yaml"
        _write_cfg(cfg_live, enabled=True, mode="live")

        ts1 = ts0 + 1
        _insert_signal(sig_db, ts=ts1, action="CALL")

        payload_live = process_latest_signal(repo_root=tmp_root, config_path=cfg_live)
        assert payload_live.get("enabled") is True, payload_live
        assert payload_live.get("submit_attempt") is not None, payload_live

        attempts = _count_rows(ex_db, "order_submit_attempts")
        assert attempts >= 1, f"expected >=1 submit attempts, got {attempts}"
    finally:
        # Help release sqlite handles before cleanup (Windows).
        try:
            import gc as _gc
            _gc.collect()
        except Exception:
            pass
        try:
            tmp.cleanup()
        except Exception as e:
            print(f"smoke_execution_layer: warn: temp cleanup failed: {e}", file=sys.stderr)
    print("smoke_execution_layer: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
