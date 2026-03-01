"""
P32 - Ensure runtime artifacts are ignored by git.

Fixes selfcheck failure:
  [selfcheck][FAIL] data is NOT ignored by gitignore (it should be)

This patch updates .gitignore (append-only, idempotent) to ignore:
  - data/
  - runs/
  - exports/
  - sqlite DBs and other runtime artifacts

NOTE:
  If you already committed files under data/ or runs/,
  gitignore won't stop git from tracking them.
  In that case do:
    git rm -r --cached data runs
    git commit -m "stop tracking runtime artifacts"
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path


SECTION_HEADER = "# --- natbin runtime artifacts (auto) ---"
SECTION_LINES = [
    SECTION_HEADER,
    "data/",
    "runs/",
    "exports/",
    "*.sqlite3",
    "*.db",
    "*.log",
    "*.bak",
    "*.bak_*",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".venv/",
]


def _backup(path: Path) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_{ts}")
    shutil.copy2(path, bak)
    return bak


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    target = repo / ".gitignore"

    if target.exists():
        raw = target.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        raw = []

    have = set(raw)

    # If the section header exists, we still ensure each line is present somewhere.
    to_add = [line for line in SECTION_LINES if line not in have]

    if not to_add:
        print("[P32] OK: .gitignore already contains required patterns (no changes).")
        return

    bak = _backup(target) if target.exists() else None

    out = list(raw)
    # ensure there's a blank line before our section
    if out and out[-1].strip() != "":
        out.append("")
    if SECTION_HEADER not in have:
        out.append(SECTION_HEADER)
    for line in SECTION_LINES[1:]:
        if line not in have:
            out.append(line)
    out.append("")  # trailing newline

    target.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")

    print(f"[P32] OK patched: {target}")
    if bak:
        print(f"[P32] backup: {bak}")
    print("[P32] Next steps:")
    print("  1) git status (confirm data/ and runs/ are not staged/tracked)")
    print("  2) python scripts/tools/selfcheck_repo.py")
    print("  3) if data/ or runs/ were previously committed: git rm -r --cached data runs && git commit")


if __name__ == "__main__":
    main()
