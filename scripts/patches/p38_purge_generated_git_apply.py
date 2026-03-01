"""P38 - Purge generated artifacts from git + rewrite .gitignore coherently.

What it does:
  1) Writes a clean .gitignore (backs up existing).
  2) If the git working tree is clean, removes (git rm --cached) any *tracked*
     files under generated dirs (data/, runs/, exports/, backups/, configs/variants/)
     and also root config_*.yml/yaml + .env* (except .env.example).

Safety:
  - Never deletes your local files (uses --cached).
  - Refuses to run git rm if you have uncommitted changes.

Usage (Windows):
  .\.venv\Scripts\python.exe .\scripts\patches\p38_purge_generated_git_apply.py

Then:
  git status
  git commit -m "chore: purge generated artifacts"
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List


GITIGNORE_TEXT = """# ================================
# Python
# ================================
__pycache__/
*.py[cod]
*$py.class

# Virtualenvs
.venv/
venv/
ENV/

# Build / packaging
build/
dist/
*.egg-info/

# Test / type / lint caches
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# ================================
# OS / editors
# ================================
.DS_Store
Thumbs.db
.vscode/
.idea/

# ================================
# Secrets / local config
# ================================
.env
.env.*
!.env.example

# Local config variants (do not version)
configs/variants/
config_*.yml
config_*.yaml

# ================================
# Runtime artifacts (generated)
# ================================
# IMPORTANT: these are output directories and should never be committed.
data/
runs/
exports/
backups/

# Common artifacts
*.log
*.csv
*.parquet
*.feather
*.sqlite3
*.db
*.joblib
*.pkl
*.pickle
*.npy
*.npz

# SQLite sidecars
*.sqlite3-wal
*.sqlite3-shm

# Patch/backup suffixes
*.bak_*
*.orig
*.rej
"""


def _run(cmd: List[str], cwd: Path | None = None, capture: bool = True) -> str:
    p = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture,
    )
    if p.returncode != 0:
        if capture:
            raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
        raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(cmd)}")
    return (p.stdout or "").strip()


def _git_root() -> Path:
    root = _run(["git", "rev-parse", "--show-toplevel"])  # raises if not a git repo
    return Path(root).resolve()


def _git_clean(root: Path) -> bool:
    return _run(["git", "status", "--porcelain"], cwd=root) == ""


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(path.name + f".bak_{ts}")
    backup.write_bytes(path.read_bytes())
    return backup


def _write_gitignore(root: Path) -> None:
    p = root / ".gitignore"
    bak = _backup(p)
    p.write_text(GITIGNORE_TEXT, encoding="utf-8", newline="\n")
    if bak:
        print(f"[P38] OK wrote {p} (backup={bak})")
    else:
        print(f"[P38] OK wrote {p}")


def _list_tracked(root: Path) -> List[str]:
    out = _run(["git", "ls-files"], cwd=root)
    return [ln for ln in out.splitlines() if ln.strip()]


def _filter_tracked_for_purge(tracked: Iterable[str]) -> List[str]:
    prefixes = (
        "data/",
        "runs/",
        "exports/",
        "backups/",
        "configs/variants/",
    )

    rx_root_config = re.compile(r"^config_.*\.(ya?ml)$", re.IGNORECASE)

    to_rm: set[str] = set()
    for f in tracked:
        f = f.strip()
        if not f:
            continue

        if any(f.startswith(pref) for pref in prefixes):
            to_rm.add(f)
            continue

        if rx_root_config.match(f):
            to_rm.add(f)
            continue

        if f == ".env":
            to_rm.add(f)
            continue

        if f.startswith(".env.") and f != ".env.example":
            to_rm.add(f)
            continue

    return sorted(to_rm)


def _git_rm_cached(root: Path, paths: List[str]) -> None:
    if not paths:
        return

    # Batch to avoid command-length limits (especially on Windows)
    BATCH = 120
    print(f"[P38] removing tracked generated files (count={len(paths)}) ...")
    for i in range(0, len(paths), BATCH):
        chunk = paths[i : i + BATCH]
        # Use '--' to stop option parsing
        subprocess.run(["git", "rm", "--cached", "--ignore-unmatch", "--"] + chunk, cwd=str(root))


def main() -> int:
    try:
        root = _git_root()
    except Exception as e:
        print(f"[P38][FAIL] Not a git repo (or git not installed): {e}")
        return 2

    print(f"[P38] repo={root}")

    _write_gitignore(root)

    tracked = _list_tracked(root)
    purge = _filter_tracked_for_purge(tracked)

    if not purge:
        print("[P38] No tracked generated artifacts found. Nothing to untrack.")
        print("[P38] DONE.")
        return 0

    if not _git_clean(root):
        print("[P38][WARN] Working tree is NOT clean. I will NOT run 'git rm --cached' automatically.")
        print("            Commit/stash first, then run: ")
        print("            git rm -r --cached --ignore-unmatch data runs exports backups configs/variants")
        print("            git rm --cached --ignore-unmatch config_*.yaml config_*.yml .env .env.*")
        print("            (but keep .env.example)")
        print("            git status")
        print("            git commit -m \"chore: purge generated artifacts\"")
        print("[P38] DONE (partial).")
        return 0

    _git_rm_cached(root, purge)

    print("[P38] OK. Next:")
    print("  - git status")
    print("  - git commit -m \"chore: purge generated artifacts\"")
    print("  - (optional) python scripts/tools/selfcheck_repo.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
