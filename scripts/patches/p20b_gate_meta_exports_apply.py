#!/usr/bin/env python3
"""
P20b - Restore gate_meta public exports required by observe/tuning.

Fixes ImportError: cannot import name 'GATE_VERSION' (and META_FEATURES).

Idempotent: if the symbols already exist, does nothing.
Creates a timestamped .bak_YYYYMMDD_HHMMSS backup.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
import py_compile


INSERT_BLOCK = """

# -----------------------------------------------------------------------------
# Public API / cache invalidation
#
# IMPORTANT:
# - `observe_signal_topk_perday.py` and tuning scripts import these symbols.
# - `GATE_VERSION` is persisted in model_cache.json; bump it when gate behavior
#   changes to force a retrain and avoid mixing incompatible cache artifacts.
# -----------------------------------------------------------------------------

# Bump this string whenever the gating behavior or feature construction changes.
GATE_VERSION = "meta_v2_p20_cp"

# Column order for the meta-model features produced by `build_meta_X`.
# Keep this list in sync with build_meta_X().
META_FEATURES = [
    "dow_sin",
    "dow_cos",
    "min_sin",
    "min_cos",
    "proba_up",
    "conf",
    "vol",
    "bb",
    "atr",
    "iso_score",
]
"""


def find_repo_root(start: Path) -> Path:
    # Walk up from start until we find src/natbin/gate_meta.py
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / "src" / "natbin" / "gate_meta.py").exists():
            return p

    # Fallback: current working directory
    cwd = Path.cwd().resolve()
    for p in [cwd, *cwd.parents]:
        if (p / "src" / "natbin" / "gate_meta.py").exists():
            return p

    raise SystemExit(
        "Repo root not found (expected src/natbin/gate_meta.py). "
        "Run from inside the repo."
    )


def main() -> None:
    root = find_repo_root(Path(__file__).parent)
    target = root / "src" / "natbin" / "gate_meta.py"
    txt = target.read_text(encoding="utf-8", errors="replace")

    missing_gate_version = not re.search(r"^GATE_VERSION\s*=", txt, flags=re.M)
    missing_meta_features = not re.search(r"^META_FEATURES\s*=", txt, flags=re.M)

    if not missing_gate_version and not missing_meta_features:
        print("[P20b] gate_meta.py already exports GATE_VERSION and META_FEATURES. Nothing to do.")
        return

    # Insert after the imports block. Heuristic: after the last top-level import/from line.
    lines = txt.splitlines(True)
    insert_at = 0
    for i, line in enumerate(lines):
        if re.match(r"^(import\s+|from\s+)", line):
            insert_at = i + 1
            continue
        # allow blank lines and comments immediately after imports
        if insert_at > 0 and (line.strip() == "" or line.lstrip().startswith("#")):
            insert_at = i + 1
            continue
        if insert_at > 0:
            break

    patched = "".join(lines[:insert_at]) + INSERT_BLOCK + "".join(lines[insert_at:])

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = target.with_suffix(target.suffix + f".bak_{ts}")
    backup.write_text(txt, encoding="utf-8")
    target.write_text(patched, encoding="utf-8")

    print(f"[P20b] OK patched: {target}")
    print(f"[P20b] backup: {backup}")

    # Smoke compile
    py_compile.compile(str(target), doraise=True)

    obs = root / "src" / "natbin" / "observe_signal_topk_perday.py"
    if obs.exists():
        py_compile.compile(str(obs), doraise=True)
        print("[P20b] OK compiled gate_meta.py and observe_signal_topk_perday.py")
    else:
        print("[P20b] OK compiled gate_meta.py")


if __name__ == "__main__":
    main()