#!/usr/bin/env python
"""P26 - Secret hygiene + sanitized export tooling + CI selfcheck integration.

What this patch does:
  1) Ensures scripts/tools/export_repo_sanitized.ps1 exists (creates if missing).
     - Creates a zip without .env, .venv, data/, runs/, backups/, exports/, temp*, *.bak_*, *.sqlite3, etc.
  2) Ensures scripts/tools/selfcheck_repo.py exists (creates/overwrites).
     - Verifies key ignore rules (.env ignored, .env.example NOT ignored, runs/ and data/ ignored).
     - Keeps existing checks we rely on (gate_meta API + observe import + PS helpers).
  3) Patches .github/workflows/ci.yml to run selfcheck_repo.py.

Safe to re-run: idempotent, creates timestamped backups for edited files.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
from pathlib import Path


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + f".bak_{_ts()}")
    shutil.copy2(path, bak)
    return bak


def _repo_root_from_here() -> Path:
    # scripts/patches/*.py -> repo root is 2 parents up
    here = Path(__file__).resolve()
    root = here.parents[2]
    if not (root / "src" / "natbin").exists():
        raise SystemExit(f"[P26] ERROR: repo root not found from {here}. Expected src/natbin under {root}")
    return root


EXPORT_PS1 = r'''# P26 - Export sanitized repo snapshot (no secrets / no heavy artifacts)
# Usage:
#   pwsh -ExecutionPolicy Bypass -File .\scripts\tools\export_repo_sanitized.ps1
#   pwsh -ExecutionPolicy Bypass -File .\scripts\tools\export_repo_sanitized.ps1 -Out exports\repo_sanitized.zip
#
# This creates a zip good for sharing/debugging. It EXCLUDES:
#   - .env / .env.* (secrets)
#   - .git/
#   - .venv/ (heavy)
#   - data/, runs/, backups/, exports/, temp_snapshot/, __pycache__/
#   - *.bak_* backups
#   - *.sqlite3 + sqlite wal/shm
#
# IMPORTANT: if you already shared a zip containing .env with credentials,
# rotate the credentials immediately.

param(
  [string]$Out = ""
)

$ErrorActionPreference = "Stop"

function New-DirIfMissing([string]$p) {
  if (-not (Test-Path -LiteralPath $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\.." )).Path

$ts = Get-Date -Format "yyyyMMdd_HHmmss"
if ($Out -eq "") {
  New-DirIfMissing (Join-Path $root "exports")
  $Out = Join-Path $root ("exports\\repo_sanitized_" + $ts + ".zip")
} else {
  # allow relative paths
  if (-not [System.IO.Path]::IsPathRooted($Out)) {
    $Out = Join-Path $root $Out
  }
  New-DirIfMissing ([System.IO.Path]::GetDirectoryName($Out))
}

# Build file list
$files = Get-ChildItem -Path $root -Recurse -File -Force | Where-Object {
  $full = $_.FullName
  $rel  = $full.Substring($root.Length).TrimStart("\\")

  # directories to exclude
  if ($rel -match "^\\.git\\\\") { return $false }
  if ($rel -match "^\\.venv\\\\") { return $false }
  if ($rel -match "^venv\\\\") { return $false }
  if ($rel -match "^data\\\\") { return $false }
  if ($rel -match "^runs\\\\") { return $false }
  if ($rel -match "^backups\\\\") { return $false }
  if ($rel -match "^exports\\\\") { return $false }
  if ($rel -match "^temp_") { return $false }
  if ($rel -match "__pycache__") { return $false }

  # sensitive files
  if ($rel -ieq ".env") { return $false }
  if ($rel -like ".env.*") { return $false }

  # generated backups
  if ($rel -match "\\.bak_") { return $false }
  if ($rel -match "\\.bak\\.") { return $false }

  # sqlite artifacts
  if ($rel -match "\\.sqlite3$") { return $false }
  if ($rel -match "\\.sqlite3-wal$") { return $false }
  if ($rel -match "\\.sqlite3-shm$") { return $false }

  return $true
}

if (Test-Path -LiteralPath $Out) {
  Remove-Item -LiteralPath $Out -Force
}

# Compress-Archive accepts an array of paths
$paths = $files | ForEach-Object { $_.FullName }
Compress-Archive -Path $paths -DestinationPath $Out -CompressionLevel Optimal
Write-Host "[export] OK: $Out"
'''


SELF_CHECK_PY = r'''#!/usr/bin/env python
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

    print("[selfcheck] ALL OK")


if __name__ == "__main__":
    # Ensure src/ is on sys.path when running from repo root
    # (CI sets PYTHONPATH in workflow, but locally this helps.)
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    main()
'''


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def patch_ci(ci_path: Path) -> tuple[bool, str]:
    """Inject a CI step to run selfcheck_repo.py.

    We look for the compileall step and insert right after it.
    If already present, do nothing.
    """

    if not ci_path.exists():
        return False, "ci.yml not found (skipped)"

    original = ci_path.read_text(encoding="utf-8")
    if "scripts/tools/selfcheck_repo.py" in original:
        return False, "ci.yml already runs selfcheck_repo.py (skip)"

    lines = original.splitlines(True)

    # Find a place to insert: after the compileall step if present.
    insert_at = None
    for i, ln in enumerate(lines):
        if re.search(r"python\s+-m\s+compileall", ln):
            # insert after the step block; heuristically after next blank line or next '- name:'
            insert_at = i + 1

    # Fallback: after 'Set up Python'
    if insert_at is None:
        for i, ln in enumerate(lines):
            if "uses: actions/setup-python" in ln:
                insert_at = i + 1
                break

    step = (
        "\n"
        "      - name: Repo selfcheck\n"
        "        run: python scripts/tools/selfcheck_repo.py\n"
    )

    if insert_at is None:
        # append at end of file
        new = original.rstrip() + step + "\n"
    else:
        # Insert before the next '- name:' that starts a new step at same indent.
        # We'll insert right after the compileall command line; safe enough.
        new_lines = lines[: insert_at] + [step] + lines[insert_at:]
        new = "".join(new_lines)

    _backup(ci_path)
    ci_path.write_text(new, encoding="utf-8")
    return True, f"patched {ci_path}"


def main() -> None:
    root = _repo_root_from_here()

    # 1) export script
    export_path = root / "scripts" / "tools" / "export_repo_sanitized.ps1"
    if export_path.exists():
        _backup(export_path)
    _write_text(export_path, EXPORT_PS1)

    # 2) selfcheck script
    selfcheck_path = root / "scripts" / "tools" / "selfcheck_repo.py"
    if selfcheck_path.exists():
        _backup(selfcheck_path)
    _write_text(selfcheck_path, SELF_CHECK_PY)

    # 3) CI workflow patch
    ci_path = root / ".github" / "workflows" / "ci.yml"
    changed, note = patch_ci(ci_path)

    print(f"[P26] OK wrote {export_path}")
    print(f"[P26] OK wrote {selfcheck_path}")
    if changed:
        print(f"[P26] OK {note} (backup created)")
    else:
        print(f"[P26] NOTE {note}")

    print("[P26] Testes sugeridos:")
    print("  1) python scripts/tools/selfcheck_repo.py")
    print("  2) pwsh -ExecutionPolicy Bypass -File scripts/tools/export_repo_sanitized.ps1")
    print("  3) (CI) push -> actions should run selfcheck step")


if __name__ == "__main__":
    main()
