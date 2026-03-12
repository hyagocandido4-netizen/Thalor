from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.db import open_db, upsert_candles  # noqa: E402
from natbin.brokers.iqoption import IQOptionAdapter  # noqa: E402


def ok(msg: str) -> None:
    print(f"[h7][OK] {msg}")


def fail(msg: str) -> None:
    print(f"[h7][FAIL] {msg}")
    raise SystemExit(2)


def seed_db(db_path: Path) -> None:
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


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        db_path = tdp / "market.sqlite3"
        ctx_path = tdp / "market_context.json"
        seed_db(db_path)

        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["THALOR_FORCE_IQOPTIONAPI_MISSING"] = "1"
        env["THALOR__DATA__DB_PATH"] = str(db_path)
        env["MARKET_CONTEXT_PATH"] = str(ctx_path)

        cp_collect = subprocess.run(
            [sys.executable, "-m", "natbin.collect_recent"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
        )
        if cp_collect.returncode != 0:
            fail(f"collect_recent fallback failed: {cp_collect.stderr or cp_collect.stdout}")
        try:
            collect_payload = json.loads(cp_collect.stdout.strip().splitlines()[-1])
        except Exception as exc:
            fail(f"collect_recent fallback output is not valid JSON: {exc}")
        if collect_payload.get("mode") != "dependency_fallback":
            fail(f"unexpected collect_recent mode: {collect_payload!r}")
        if collect_payload.get("action") != "skip_remote_collect":
            fail(f"unexpected collect_recent action: {collect_payload!r}")
        ok("collect_recent falls back without iqoptionapi")

        cp_ctx = subprocess.run(
            [sys.executable, "-m", "natbin.refresh_market_context"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
        )
        if cp_ctx.returncode != 0:
            fail(f"refresh_market_context fallback failed: {cp_ctx.stderr or cp_ctx.stdout}")
        try:
            ctx_payload = json.loads(cp_ctx.stdout.strip().splitlines()[-1])
        except Exception as exc:
            fail(f"refresh_market_context output is not valid JSON: {exc}")
        if ctx_payload.get("dependency_available") is not False:
            fail(f"unexpected refresh_market_context dependency flag: {ctx_payload!r}")
        if ctx_payload.get("fallback_mode") != "broker_dependency_closeout":
            fail(f"unexpected refresh_market_context mode: {ctx_payload!r}")
        if not ctx_path.exists():
            fail("market_context file was not written")
        ok("refresh_market_context falls back without iqoptionapi")

        adapter = IQOptionAdapter(
            repo_root=tdp,
            account_mode="PRACTICE",
            execution_mode="live",
            broker_config={"email": "demo@example.com", "password": "secret"},
        )
        health = adapter.healthcheck()
        if health.reason != "iqoption_dependency_missing":
            fail(f"unexpected adapter health reason: {health.reason!r}")
        ok("IQOptionAdapter reports explicit dependency-missing health")

    print("[h7] ALL OK")


if __name__ == "__main__":
    main()
