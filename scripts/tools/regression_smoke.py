#!/usr/bin/env python
"""Regression smoke tests (fast, offline, deterministic).

This is NOT a full unit-test suite.
It is a pragmatic safety harness to prevent high-impact regressions in:
  - schema + persistence invariants
  - path scoping (day/asset/interval)
  - daily summary strictness

Run:
  python scripts/tools/regression_smoke.py

Exit code 0 on success, non-zero on failure.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


def _ok(msg: str) -> None:
    print(f"[smoke][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[smoke][FAIL] {msg}")
    raise SystemExit(2)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    root = here.parents[2]
    if not (root / "src" / "natbin").exists():
        _fail(f"repo root not found from {here}")
    return root


def _with_env(**pairs: str | None):
    """Context manager to set/unset env vars temporarily."""

    class _Ctx:
        def __enter__(self):
            self._old = {}
            for k, v in pairs.items():
                self._old[k] = os.environ.get(k)
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = str(v)

        def __exit__(self, exc_type, exc, tb):
            for k, old in self._old.items():
                if old is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = old

    return _Ctx()


def check_signals_schema() -> None:
    from natbin.observe_signal_topk_perday import ensure_signals_v2

    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "signals.sqlite3"
        con = sqlite3.connect(str(db_path))
        try:
            ensure_signals_v2(con)
            info = con.execute("PRAGMA table_info(signals_v2)").fetchall()
            cols = {r[1] for r in info}
            pk_cols = {r[1]: r[5] for r in info}  # pk order index

            required = {
                "day",
                "asset",
                "interval_sec",
                "ts",
                "action",
                "reason",
                "gate_mode",
                "meta_model",
                "proba_up",
                "conf",
                "score",
            }
            missing = sorted([c for c in required if c not in cols])
            if missing:
                _fail(f"signals_v2 missing required columns: {missing}")

            for c in ("day", "asset", "interval_sec", "ts"):
                if int(pk_cols.get(c, 0) or 0) <= 0:
                    _fail(f"signals_v2 PK does not include column: {c}")

        finally:
            con.close()

    _ok("signals_v2 schema ok")


def check_trade_immutability() -> None:
    from natbin.observe_signal_topk_perday import write_sqlite_signal

    with tempfile.TemporaryDirectory() as td:
        db_path = str(Path(td) / "signals.sqlite3")

        base = {
            "day": "2026-02-28",
            "asset": "EURUSD-OTC",
            "interval_sec": 300,
            "ts": 1772316300,
            "dt_local": "2026-02-28 19:05:00",
            "proba_up": 0.55,
            "conf": 0.55,
            "regime_ok": 1,
            "threshold": 0.02,
            "action": "CALL",
            "reason": "topk_emit",
        }
        write_sqlite_signal(dict(base), db_path=db_path)

        # Try to overwrite with HOLD => must NOT overwrite
        hold = dict(base)
        hold["action"] = "HOLD"
        hold["reason"] = "should_not_overwrite"
        write_sqlite_signal(hold, db_path=db_path)

        # Try to overwrite with PUT => must NOT overwrite (first trade row is immutable)
        put = dict(base)
        put["action"] = "PUT"
        put["reason"] = "should_not_overwrite"
        write_sqlite_signal(put, db_path=db_path)

        con = sqlite3.connect(db_path)
        try:
            row = con.execute(
                "SELECT action, reason FROM signals_v2 WHERE day=? AND asset=? AND interval_sec=? AND ts=?",
                (base["day"], base["asset"], base["interval_sec"], base["ts"]),
            ).fetchone()
            if row is None:
                _fail("trade row not found after writes")
            if str(row[0]).upper() != "CALL":
                _fail(f"trade row was overwritten: action={row[0]!r}")
            if str(row[1]) != "topk_emit":
                _fail(f"trade row reason changed unexpectedly: reason={row[1]!r}")
        finally:
            con.close()

    _ok("trade immutability ok")


def check_live_signals_path_resolution() -> None:
    from natbin.observe_signal_topk_perday import _resolve_live_signals_csv_path

    row = {
        "day": "2026-02-28",
        "asset": "EURUSD-OTC",
        "interval_sec": 300,
        "ts": 1772316300,
        "dt_local": "2026-02-28 19:05:00",
        "proba_up": 0.5,
        "conf": 0.5,
        "regime_ok": 1,
        "threshold": 0.02,
        "action": "HOLD",
        "reason": "smoke",
    }

    # If override points to a built-in filename for a DIFFERENT day, it must be ignored.
    with _with_env(LIVE_SIGNALS_PATH="runs/live_signals_v2_20260227_EURUSD-OTC_300s.csv"):
        p = _resolve_live_signals_csv_path(row)
        if "20260228" not in p:
            _fail(f"expected row-derived daily path; got {p!r}")

    # Custom paths should be respected.
    with _with_env(LIVE_SIGNALS_PATH="runs/custom_live_signals.csv"):
        p = _resolve_live_signals_csv_path(row)
        if p.replace("\\", "/") != "runs/custom_live_signals.csv":
            _fail(f"expected override path; got {p!r}")

    _ok("live_signals csv path resolution ok")


def check_daily_summary_strictness() -> None:
    from natbin.summary_paths import daily_summary_candidates

    day = "2026-02-28"
    asset = "EURUSD-OTC"
    interval_sec = 300

    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td)

        # Strict mode (default): when interval is provided, should NOT include legacy fallbacks.
        with _with_env(SUMMARY_LEGACY_FALLBACK=None):
            cands = daily_summary_candidates(day=day, asset=asset, interval_sec=interval_sec, out_dir=out_dir)
            if len(cands) != 1:
                _fail(f"expected strict candidates=1; got {len(cands)} => {cands}")

        # Legacy fallback enabled: interval + asset-only + global.
        with _with_env(SUMMARY_LEGACY_FALLBACK="1"):
            cands = daily_summary_candidates(day=day, asset=asset, interval_sec=interval_sec, out_dir=out_dir)
            if len(cands) < 2:
                _fail(f"expected fallback candidates>=2; got {len(cands)} => {cands}")

    _ok("daily summary strictness ok")


def main() -> None:
    root = _repo_root()

    # Make src importable when running locally.
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    check_signals_schema()
    check_trade_immutability()
    check_live_signals_path_resolution()
    check_daily_summary_strictness()

    print("[smoke] ALL OK")


if __name__ == "__main__":
    main()
