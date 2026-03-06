#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str) -> str:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    proc = subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "command failed\n"
            f"cmd={proc.args!r}\n"
            f"returncode={proc.returncode}\n"
            f"stdout={proc.stdout}\n"
            f"stderr={proc.stderr}"
        )
    return proc.stdout.strip()


def _parse_json(name: str, text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{name} did not return valid JSON: {exc}\n{text}") from exc


def main() -> int:
    app = _parse_json(
        "runtime_app",
        _run("-m", "natbin.runtime_app", "--repo-root", ".", "--json"),
    )
    if "config" not in app or "health" not in app:
        raise SystemExit(f"runtime_app payload missing expected keys: {sorted(app.keys())}")

    quota = _parse_json(
        "runtime_daemon",
        _run("-m", "natbin.runtime_daemon", "--repo-root", ".", "--quota-json"),
    )
    required_quota_keys = {"asset", "interval_sec", "allowed_now", "allowed_total"}
    missing = sorted(required_quota_keys - quota.keys())
    if missing:
        raise SystemExit(f"runtime_daemon quota payload missing expected keys: {missing}")

    print("repo_entrypoints_smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
