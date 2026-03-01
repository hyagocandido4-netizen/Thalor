#!/usr/bin/env python
"""Repo self-checks (fast, no network).

Run:
  python scripts/tools/selfcheck_repo.py

This script is intended to be CI-friendly (Windows runner) and also usable locally.
It verifies:
  - gate_meta API surface exists
  - observe_signal_topk_perday can be imported
  - observe_loop_auto.ps1 helper functions exist
  - Git ignore rules for secrets & heavy artifacts are in place

Exit code 0 on success, non-zero on failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _ok(msg: str) -> None:
    print(f"[selfcheck][OK] {msg}")


def _fail(msg: str) -> None:
    print(f"[selfcheck][FAIL] {msg}")
    raise SystemExit(2)


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    root = here.parents[2]
    if not (root / "src" / "natbin").exists():
        _fail(f"repo root not found from {here}")
    return root


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _git_available(cwd: Path) -> bool:
    try:
        cp = _run_git(["--version"], cwd)
        return cp.returncode == 0
    except FileNotFoundError:
        return False


def _git_check_ignored(path: str, should_be_ignored: bool, cwd: Path) -> None:
    # `git check-ignore -q` returns:
    #   0 => ignored
    #   1 => not ignored
    cp = _run_git(["check-ignore", "-q", path], cwd)
    ignored = cp.returncode == 0
    if ignored != should_be_ignored:
        if should_be_ignored:
            _fail(f"{path} is NOT ignored by gitignore (it should be)")
        else:
            _fail(f"{path} IS ignored by gitignore (it should NOT be)")


def main() -> None:
    root = _repo_root()

    # 1) gate_meta API surface
    try:
        from natbin import gate_meta  # noqa: F401
        from natbin.gate_meta import (  # noqa: F401
            GATE_VERSION,
            META_FEATURES,
            compute_scores,
            train_base_cal_iso_meta,
        )
    except Exception as e:  # pragma: no cover
        _fail(f"gate_meta API broken: {e}")
    _ok("gate_meta API ok")

    # 2) observe import
    try:
        from natbin import observe_signal_topk_perday  # noqa: F401
    except Exception as e:  # pragma: no cover
        _fail(f"observe_signal_topk_perday import failed: {e}")
    _ok("observe_signal_topk_perday import ok")

    # 3) observe_loop_auto.ps1 helpers
    ps1 = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps1.exists():
        _fail("scripts/scheduler/observe_loop_auto.ps1 not found")
    txt = ps1.read_text(encoding="utf-8", errors="replace")
    for fn in ("AsStr", "AsInt", "AsFloat"):
        if (f"function {fn}" not in txt) and (f"function\t{fn}" not in txt):
            _fail(f"observe_loop_auto.ps1 missing helper function: {fn}")
    _ok("observe_loop_auto.ps1 helpers ok")

    # 4) Secret & artifact hygiene (gitignore)
    if not _git_available(root):
        print("[selfcheck][WARN] git not available; skipping gitignore checks")
    else:
        _git_check_ignored(".env", True, root)
        _git_check_ignored(".env.example", False, root)
        _git_check_ignored("runs", True, root)
        _git_check_ignored("data", True, root)
        _ok("gitignore hygiene ok")


    # envutil import completeness (ensures env_* used are imported)
    try:
        _check_envutil_imports(root)
        _ok("envutil imports ok")
    except SystemExit:
        raise
    except Exception as e:
        _fail(f"envutil imports check failed: {e}")

    # pt-BR decimal comma safety (auto_volume)
    try:
        from natbin import auto_volume as _av
        v = _av._f("0,07", 0.0)
        if abs(v - 0.07) > 1e-9:
            _fail(f"auto_volume._f does not parse comma decimals: got {v}")
        _ok("auto_volume locale float parse ok")
    except SystemExit:
        raise
    except Exception as e:
        _fail(f"auto_volume locale parse check failed: {e}")

    print("[selfcheck] ALL OK")



# --- envutil import check (auto) ---

def _check_envutil_imports(repo_root: Path) -> None:
    import re

    env_call_re = re.compile(r"\b(env_(?:float|int|bool|str))\s*\(")
    rel_line = re.compile(r"^\s*from\s+\.envutil\s+import\s+(.+?)\s*$", re.M)
    abs_line = re.compile(r"^\s*from\s+natbin\.envutil\s+import\s+(.+?)\s*$", re.M)

    def parse_list(s: str) -> set[str]:
        s = s.split("#", 1)[0].strip().strip("() ")
        parts = [p.strip() for p in s.split(",") if p.strip()]
        out = set()
        for p in parts:
            out.add(p.split()[0])
        return out

    src = repo_root / "src" / "natbin"
    if not src.exists():
        return

    offenders = []
    for py in src.rglob("*.py"):
        if py.name == "envutil.py":
            continue
        try:
            t = py.read_text(encoding="utf-8")
        except Exception:
            continue

        used = set(env_call_re.findall(t))
        if not used:
            continue

        imported: set[str] = set()
        for m in rel_line.finditer(t):
            imported |= parse_list(m.group(1))
        for m in abs_line.finditer(t):
            imported |= parse_list(m.group(1))

        missing = used - imported
        if missing:
            offenders.append((py, ", ".join(sorted(used)), ", ".join(sorted(imported)), ", ".join(sorted(missing))))

    if offenders:
        lines = ["[selfcheck][FAIL] envutil imports incomplete:"]
        for py, used, imported, missing in offenders[:25]:
            lines.append(f"  - {py}\n      used={used}\n      imported={imported}\n      missing={missing}")
        raise SystemExit("\n".join(lines))


if __name__ == "__main__":
    # Ensure src/ is on sys.path when running from repo root
    # (CI sets PYTHONPATH in workflow, but locally this helps.)
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    main()