#!/usr/bin/env python
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.release_hygiene import build_release_report, create_release_bundle


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> int:
    report = build_release_report(ROOT)
    _assert(report.ok, f"release report not ok: {report.missing_required_files}")
    _assert("README.md" in report.sample_entries or report.included_files > 0, "empty release report")

    with tempfile.TemporaryDirectory(prefix="thalor_release_smoke_") as td:
        out = Path(td) / "repo_clean.zip"
        bundle = create_release_bundle(ROOT, out_path=out)
        _assert(bundle.ok, f"bundle creation failed: {bundle.missing_required_files}")
        _assert(out.exists(), "bundle zip not created")

        with zipfile.ZipFile(out, "r") as zf:
            names = set(zf.namelist())
        _assert("README.md" in names, "README.md missing in bundle")
        _assert(".env.example" in names, ".env.example missing in bundle")
        _assert("scripts/tools/release_bundle.py" in names, "release_bundle.py missing in bundle")
        _assert(".env" not in names, ".env leaked into bundle")
        _assert(not any(name.startswith(".git/") for name in names), ".git leaked into bundle")
        _assert(not any(name.startswith(".venv/") for name in names), ".venv leaked into bundle")
        _assert(not any(name.startswith("runs/") for name in names), "runs leaked into bundle")
        _assert(not any(name.startswith("data/") for name in names), "data leaked into bundle")
        _assert(not any("natbin.egg-info/" in name for name in names), "egg-info leaked into bundle")
        _assert(not any(name.startswith("secrets/") for name in names), "secrets leaked into bundle")
        _assert("config/broker_secrets.yaml" not in names, "config secret bundle leaked into bundle")

        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "tools" / "release_bundle.py"),
            "--repo-root",
            str(ROOT),
            "--dry-run",
            "--json",
        ]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True)
        payload = json.loads(proc.stdout)
        _assert(payload["ok"] is True, "CLI dry-run payload not ok")
        _assert(payload["included_files"] == report.included_files, "CLI report mismatch")

    print("release_hygiene_smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
