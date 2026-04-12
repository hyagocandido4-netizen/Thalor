from __future__ import annotations

import sqlite3
from pathlib import Path

from natbin.state.migrations import ensure_signals_v2
from natbin.state.repos import SignalsRepository


def _legacy_create(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            """
            CREATE TABLE signals_v2 (
              dt_local TEXT NOT NULL,
              day TEXT NOT NULL,
              asset TEXT NOT NULL,
              interval_sec INTEGER NOT NULL,
              ts INTEGER NOT NULL,
              proba_up REAL NOT NULL,
              conf REAL NOT NULL,
              score REAL,
              gate_mode TEXT,
              gate_mode_requested TEXT,
              gate_fail_closed INTEGER,
              gate_fail_detail TEXT,
              regime_ok INTEGER NOT NULL,
              thresh_on TEXT,
              threshold REAL NOT NULL,
              k INTEGER,
              rank_in_day INTEGER,
              executed_today INTEGER,
              budget_left INTEGER,
              action TEXT NOT NULL,
              reason TEXT NOT NULL,
              blockers TEXT,
              close REAL,
              payout REAL,
              ev REAL,
              model_version TEXT,
              train_rows INTEGER,
              train_end_ts INTEGER,
              best_source TEXT,
              tune_dir TEXT,
              feat_hash TEXT,
              gate_version TEXT,
              meta_model TEXT,
              market_context_stale INTEGER,
              market_context_fail_closed INTEGER,
              PRIMARY KEY(day, asset, interval_sec, ts)
            )
            """
        )
        con.commit()
    finally:
        con.close()


def test_ensure_signals_v2_adds_cp_bootstrap_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "live_signals.sqlite3"
    _legacy_create(db_path)

    con = sqlite3.connect(str(db_path))
    try:
        ensure_signals_v2(con, default_interval=300)
        cols = {r[1] for r in con.execute("PRAGMA table_info(signals_v2)").fetchall()}
    finally:
        con.close()

    assert "cp_bootstrap_fallback" in cols
    assert "cp_bootstrap_fallback_active" in cols
    assert "cp_available" in cols


def test_signals_repository_write_row_accepts_cp_bootstrap_fields_on_legacy_db(tmp_path: Path) -> None:
    db_path = tmp_path / "live_signals.sqlite3"
    _legacy_create(db_path)

    repo = SignalsRepository(db_path=db_path, default_interval=300)
    row = {
        "dt_local": "2026-04-12 17:00:00",
        "day": "2026-04-12",
        "asset": "EURUSD-OTC",
        "interval_sec": 300,
        "ts": 1776000000,
        "proba_up": 0.51,
        "conf": 0.52,
        "score": 0.0,
        "gate_mode": "cp_fallback_meta",
        "gate_mode_requested": "cp",
        "gate_fail_closed": 0,
        "gate_fail_detail": None,
        "regime_ok": 1,
        "thresh_on": "score",
        "threshold": 0.5,
        "k": 1,
        "rank_in_day": 1,
        "executed_today": 0,
        "budget_left": 1,
        "action": "HOLD",
        "reason": "bootstrap",
        "blockers": "",
        "close": 1.0,
        "payout": 0.8,
        "ev": 0.0,
        "model_version": "m",
        "train_rows": 10,
        "train_end_ts": 1775990000,
        "best_source": "meta",
        "tune_dir": "",
        "feat_hash": "abc",
        "gate_version": "g",
        "meta_model": "none",
        "market_context_stale": 0,
        "market_context_fail_closed": 0,
        "cp_bootstrap_fallback": "meta",
        "cp_bootstrap_fallback_active": 1,
        "cp_available": 0,
    }

    result = repo.write_row(row)
    assert result["written"] is True

    con = sqlite3.connect(str(db_path))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(signals_v2)").fetchall()}
        out = con.execute(
            "SELECT cp_bootstrap_fallback, cp_bootstrap_fallback_active, cp_available FROM signals_v2 WHERE day=? AND asset=? AND interval_sec=? AND ts=?",
            ("2026-04-12", "EURUSD-OTC", 300, 1776000000),
        ).fetchone()
    finally:
        con.close()

    assert {"cp_bootstrap_fallback", "cp_bootstrap_fallback_active", "cp_available"}.issubset(cols)
    assert out == ("meta", 1, 0)
