#!/usr/bin/env python3
"""
P35c: Fix circular import in natbin.envutil and ensure any module that uses env_* helpers
imports the names it uses.

Why:
- selfcheck reported: "cannot import name 'env_bool' from partially initialized module natbin.envutil"
  which is a classic circular-import symptom.
- observe_loop crashed with NameError: env_int not defined in collect_recent.py after envutil unification.

What it does:
1) Overwrites src/natbin/envutil.py with a standalone, dependency-free implementation (stdlib only).
   (Backs up the previous file.)
2) Scans src/natbin/*.py for usage of env_float/env_int/env_bool/env_str and ensures the file
   imports those symbols (patches existing envutil imports or inserts a safe try/except import block).
3) Runs py_compile on patched files.

Safe-by-design:
- envutil.py will NOT import any project module (prevents cycles).
- Import insertion respects module docstring and __future__ imports (AST-based).
"""

from __future__ import annotations

import ast
import datetime as _dt
import os
import re
import sys
import py_compile
from pathlib import Path
from typing import Iterable, Set


NEEDED_FUNCS = ("env_float", "env_int", "env_bool", "env_str")


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for p in [cur] + list(cur.parents):
        if (p / ".git").exists():
            return p
        if (p / "pyproject.toml").exists() and (p / "src").exists():
            return p
    # fallback: assume scripts/patches/...
    return start.resolve().parents[2]


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def backup_file(path: Path) -> Path:
    b = path.with_suffix(path.suffix + f".bak_{_ts()}")
    b.write_text(path.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    return b


def envutil_text() -> str:
    return 