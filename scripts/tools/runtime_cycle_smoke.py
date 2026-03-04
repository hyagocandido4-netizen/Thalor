#!/usr/bin/env python
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from natbin.runtime_cycle import (  # noqa: E402
    NONZERO_EXIT,
    OK,
    TIMEOUT,
    build_auto_cycle_plan,
    classify_outcome_kind,
)


def ok(msg: str) -> None:
    print(f"[smoke][OK] {msg}")


def fail(msg: str) -> None:
    print(f"[smoke][FAIL] {msg}")
    raise SystemExit(2)


def main() -> None:
    plan = build_auto_cycle_plan(ROOT, topk=3, lookback_candles=2000)
    names = [s.name for s in plan]
    expected = [
        "collect_recent",
        "make_dataset",
        "refresh_daily_summary",
        "refresh_market_context",
        "auto_volume",
        "auto_isoblend",
        "auto_hourthr",
        "observe_loop_once",
    ]
    if names != expected:
        fail(f"unexpected step plan: {names}")
    ok("runtime cycle plan shape ok")

    obs = plan[-1]
    joined = " ".join(obs.argv)
    for token in ("-Once", "-SkipCollect", "-SkipDataset", "-TopK 3"):
        if token not in joined:
            fail(f"observe_loop_once missing token {token}: {joined}")
    ok("observe loop step args ok")

    if classify_outcome_kind(returncode=0) != OK:
        fail("returncode=0 should classify as ok")
    if classify_outcome_kind(returncode=9) != NONZERO_EXIT:
        fail("non-zero returncode should classify as nonzero_exit")
    if classify_outcome_kind(returncode=None, timed_out=True) != TIMEOUT:
        fail("timeout classification broken")
    ok("outcome classifier ok")

    env = dict(**__import__('os').environ)
    env['PYTHONPATH'] = str(SRC) + ((env.get('PYTHONPATH') and (__import__('os').pathsep + env['PYTHONPATH'])) or '')
    cp = subprocess.run(
        [sys.executable, "-m", "natbin.runtime_cycle", "--repo-root", str(ROOT), "--topk", "3", "--lookback-candles", "2000", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    if cp.returncode != 0:
        fail(f"runtime_cycle --json failed: {cp.stderr or cp.stdout}")
    try:
        payload = json.loads(cp.stdout)
    except Exception as e:
        fail(f"runtime_cycle --json not valid JSON: {e}")
    if payload.get("mode") != "auto_cycle":
        fail(f"unexpected cycle mode: {payload.get('mode')!r}")
    if len(payload.get("steps") or []) != len(expected):
        fail("json plan step count mismatch")
    ok("runtime cycle cli json ok")

    print("[smoke] ALL OK")


if __name__ == "__main__":
    main()
