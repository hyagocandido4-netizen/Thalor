#!/usr/bin/env python3
"""
P37 — .gitignore hygiene (prevent repo rot)

What this fixes
---------------
Even if your code is clean, the repo becomes unmaintainable when you keep committing
(or even *tracking*) generated artifacts:

- data/ (datasets), runs/ (signals, summaries), exports/ (sanitized zips), backups/
- .venv/, __pycache__/, *.bak_TIMESTAMP created by our patch scripts
- local patch scripts (optional): scripts/patches/*.py, *.ps1

This patch ONLY edits .gitignore by appending a clearly delimited block.

How to run
----------
1) Save as: scripts/patches/p37_gitignore_hygiene_apply.py
2) Run:
   .\.venv\Scripts\python.exe .\scripts\patches\p37_gitignore_hygiene_apply.py

Important
---------
.gitignore does NOT untrack files already committed.
If any of these folders/files are already tracked, you must run:
   git rm -r --cached data runs exports backups
then commit.
"""

from __future__ import annotations

from pathlib import Path


BLOCK_HEADER = "# --- NATBOT HYGIENE (P37) ---"
BLOCK_LINES = [
    BLOCK_HEADER,
    "",
    "# Python",
    ".venv/",
    "__pycache__/",
    "*.pyc",
    "",
    "# Local env / secrets",
    ".env",
    ".env.*",
    "",
    "# Generated artifacts (MUST NOT be committed)",
    "data/",
    "runs/",
    "exports/",
    "backups/",
    "",
    "# Model/data artifacts",
    "*.csv",
    "*.parquet",
    "*.sqlite3",
    "*.joblib",
    "*.pkl",
    "*.pickle",
    "*.npz",
    "*.npy",
    "",
    "# Patch backups created by scripts/patches/*.py",
    "*.bak_*",
    "**/*.bak_*",
    "",
    "# OPTIONAL: ignore local patch scripts (keeps `git status` clean)",
    "# If you WANT to version patches, comment these 2 lines.",
    "scripts/patches/*.py",
    "scripts/patches/*.ps1",
    "scripts/patches/*.bak_*",
    "",
]


def _repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[2]


def main() -> None:
    repo = _repo_root_from_here()
    gi = repo / ".gitignore"

    old = ""
    if gi.exists():
        old = gi.read_text(encoding="utf-8", errors="replace")

    if BLOCK_HEADER in old:
        print(f"[P37] .gitignore already has P37 block (skip): {gi}")
        return

    new = old.rstrip() + ("\n\n" if old.strip() else "") + "\n".join(BLOCK_LINES) + "\n"
    gi.write_text(new, encoding="utf-8")

    print(f"[P37] OK patched: {gi}")
    print("[P37] Next steps:")
    print("  - git status (should be cleaner)")
    print("  - If any generated dirs were tracked before: git rm -r --cached data runs exports backups")


if __name__ == "__main__":
    main()