from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from natbin.db import open_db, upsert_candles
from natbin.runtime.broker_dependency import build_dependency_market_context
from natbin.brokers.iqoption import IQOptionAdapter


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _seed_db(db_path: Path) -> None:
    con = open_db(str(db_path))
    try:
        now_ts = int(datetime.now(UTC).timestamp())
        candle_ts = now_ts - 300
        upsert_candles(
            con,
            "EURUSD-OTC",
            300,
            [{
                "from": candle_ts,
                "open": 1.0,
                "max": 1.1,
                "min": 0.9,
                "close": 1.05,
                "volume": 10.0,
            }],
        )
    finally:
        con.close()


def test_build_dependency_market_context_uses_db_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "market.sqlite3"
    _seed_db(db_path)
    payload = build_dependency_market_context(
        asset="EURUSD-OTC",
        interval_sec=300,
        db_path=str(db_path),
        payout_fallback=0.8,
        ctx_path=tmp_path / "market_context.json",
        dependency_reason="forced-test",
    )
    assert payload["dependency_available"] is False
    assert payload["db_rows"] >= 1
    assert payload["last_candle_ts"] is not None
    assert payload["open_source"] in {"db_fresh", "db_stale"}


def test_collect_recent_falls_back_when_dependency_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "market.sqlite3"
    ctx_path = tmp_path / "market_context.json"
    _seed_db(db_path)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["THALOR_FORCE_IQOPTIONAPI_MISSING"] = "1"
    env["THALOR__DATA__DB_PATH"] = str(db_path)
    env["MARKET_CONTEXT_PATH"] = str(ctx_path)

    cp = subprocess.run(
        [sys.executable, "-m", "natbin.collect_recent"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 0, cp.stderr or cp.stdout
    payload = json.loads(cp.stdout.strip().splitlines()[-1])
    assert payload["mode"] == "dependency_fallback"
    assert payload["action"] == "skip_remote_collect"
    assert ctx_path.exists()


def test_refresh_market_context_falls_back_when_dependency_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "market.sqlite3"
    ctx_path = tmp_path / "market_context.json"
    _seed_db(db_path)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["THALOR_FORCE_IQOPTIONAPI_MISSING"] = "1"
    env["THALOR__DATA__DB_PATH"] = str(db_path)
    env["MARKET_CONTEXT_PATH"] = str(ctx_path)

    cp = subprocess.run(
        [sys.executable, "-m", "natbin.refresh_market_context"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert cp.returncode == 0, cp.stderr or cp.stdout
    payload = json.loads(cp.stdout.strip().splitlines()[-1])
    assert payload["dependency_available"] is False
    assert payload["fallback_mode"] == "broker_dependency_closeout"
    assert ctx_path.exists()


def test_iqoption_adapter_healthcheck_reports_dependency_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("THALOR_FORCE_IQOPTIONAPI_MISSING", "1")
    adapter = IQOptionAdapter(
        repo_root=tmp_path,
        account_mode="PRACTICE",
        execution_mode="live",
        broker_config={"email": "demo@example.com", "password": "secret"},
    )
    health = adapter.healthcheck()
    assert health.ready is False
    assert health.healthy is False
    assert health.reason == "iqoption_dependency_missing"
