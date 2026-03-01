#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P35 - Unify env parsing (Python): replace float/int(os.getenv/ os.environ.get) with envutil helpers.

Why:
- Your repo runs on Windows/pt-BR locale where users naturally type env floats with comma:
    $env:CP_ALPHA="0,07"
  Any float(os.getenv(...)) will crash. This already happened.
- Env parsing logic is duplicated across files with slightly different behavior.
- We standardize on src/natbin/envutil.py and ensure core files use env_float/env_int.

What this patch does:
1) Ensures src/natbin/envutil.py exists with robust parsing helpers:
   - to_float, to_int, to_bool, to_str
   - env_float, env_int, env_bool, env_str
   (comma decimals supported, blanks handled, safe defaults)
2) Patches src/natbin/**/*.py (safe text-level replace):
   - float(os.getenv("X", ...))  -> env_float("X", ...)
   - float(os.environ.get("X", ...)) -> env_float("X", ...)
   - int(os.getenv("X", ...))   -> env_int("X", ...)
   - int(os.environ.get("X", ...)) -> env_int("X", ...)
   Only for the "two-argument" getenv/get form to preserve semantics.
3) Adds a small import shim when needed (keeps __future__ imports valid):
   try: from .envutil import env_float, env_int, env_bool, env_str
   except: from envutil import env_float, env_int, env_bool, env_str
4) Runs py_compile on changed files and aborts if anything is broken.

Usage:
  Save as: scripts/patches/p35_envutil_unify_apply.py
  Run:     .\.venv\Scripts\python.exe .\scripts\patches\p35_envutil_unify_apply.py

Then smoke-test:
  .\.venv\Scripts\python.exe .\scripts\tools\selfcheck_repo.py
  $env:CP_ALPHA="0,07"
  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
from pathlib import Path
import py_compile


IMPORT_SHIM = """\
try:
    from .envutil import env_float, env_int, env_bool, env_str
except Exception:  # pragma: no cover
    from envutil import env_float, env_int, env_bool, env_str
"""


ENVUTIL_CANON = """# -*- coding: utf-8 -*-
'''
natbin.envutil

Single source of truth for parsing environment variables.

Design goals:
- Accept pt-BR comma decimals ("0,07") without crashing.
- Treat "", " " as missing.
- Keep behavior stable: if env is missing/invalid -> return default.
- Work both when running as module (-m natbin.xxx) and when running a file directly
  from inside src/natbin (so helpers are importable).
'''
from __future__ import annotations

import os
from typing import Any, Optional


_TRUE = {"1", "true", "t", "yes", "y", "on"}
_FALSE = {"0", "false", "f", "no", "n", "off"}


def to_str(v: Any, default: Optional[str] = None) -> Optional[str]:
    if v is None:
        return default
    s = str(v)
    if s.strip() == "":
        return default
    return s


def to_float(v: Any, default: Any = 0.0) -> float:
    '''
    Parse float from:
      - float/int -> float(v)
      - string with "." or "," decimal separator -> float
      - blank/None/invalid -> float(default) (default may be str/float/int)
    '''
    if v is None:
        return to_float(default, 0.0) if default is not None else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s == "":
        return to_float(default, 0.0) if default is not None else 0.0
    s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return to_float(default, 0.0) if default is not None else 0.0


def to_int(v: Any, default: Any = 0) -> int:
    '''
    Parse int from:
      - int -> int(v)
      - float -> int(v)
      - string (supports comma decimal) -> int(float(...))
      - blank/None/invalid -> int(default) (default may be str/int/float)
    '''
    if v is None:
        return to_int(default, 0) if default is not None else 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return int(v)
    if isinstance(v, float):
        return int(v)
    s = str(v).strip()
    if s == "":
        return to_int(default, 0) if default is not None else 0
    s = s.replace(",", ".")
    try:
        return int(float(s))
    except Exception:
        return to_int(default, 0) if default is not None else 0


def to_bool(v: Any, default: bool = False) -> bool:
    '''
    Parse bool from common env representations:
      - "1", "true", "yes", "on" -> True
      - "0", "false", "no", "off" -> False
      - blank/None/invalid -> default
    '''
    if v is None:
        return bool(default)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    s = str(v).strip().lower()
    if s == "":
        return bool(default)
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return bool(default)


def env_str(key: str, default: Optional[str] = None) -> Optional[str]:
    return to_str(os.getenv(key), default)


def env_float(key: str, default: Any = 0.0) -> float:
    return to_float(os.getenv(key), default)


def env_int(key: str, default: Any = 0) -> int:
    return to_int(os.getenv(key), default)


def env_bool(key: str, default: bool = False) -> bool:
    return to_bool(os.getenv(key), default)
"""


# default can be literal or simple identifier/attribute (no parentheses).
_DEFAULT = r"""((['"][^'"]*['"])|([-+]?\d+(\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*))"""

_FLOAT_PATTERNS = [
    (re.compile(
        rf"""float\(\s*os\.getenv\(\s*(?P<key>['"][^'"]+['"])\s*,\s*(?P<default>{_DEFAULT})\s*\)\s*\)"""
    ), r"env_float(\g<key>, \g<default>)"),
    (re.compile(
        rf"""float\(\s*os\.environ\.get\(\s*(?P<key>['"][^'"]+['"])\s*,\s*(?P<default>{_DEFAULT})\s*\)\s*\)"""
    ), r"env_float(\g<key>, \g<default>)"),
]

_INT_PATTERNS = [
    (re.compile(
        rf"""int\(\s*os\.getenv\(\s*(?P<key>['"][^'"]+['"])\s*,\s*(?P<default>{_DEFAULT})\s*\)\s*\)"""
    ), r"env_int(\g<key>, \g<default>)"),
    (re.compile(
        rf"""int\(\s*os\.environ\.get\(\s*(?P<key>['"][^'"]+['"])\s*,\s*(?P<default>{_DEFAULT})\s*\)\s*\)"""
    ), r"env_int(\g<key>, \g<default>)"),
]


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def find_repo_root(start: Path) -> Path:
    p = start
    for _ in range(12):
        if (p / "src" / "natbin").is_dir():
            return p
        p = p.parent
    raise SystemExit("Could not locate repo root (expected to find src/natbin). Run from inside the repo.")


def backup(path: Path) -> Path:
    b = path.with_suffix(path.suffix + f".bak_{_ts()}")
    shutil.copy2(path, b)
    return b


def ensure_envutil(root: Path) -> bool:
    envutil = root / "src" / "natbin" / "envutil.py"
    changed = False
    if envutil.exists():
        txt = envutil.read_text(encoding="utf-8", errors="replace")
        required = ["def env_float", "def env_int", "def env_bool", "def to_float", "def to_int"]
        if not all(r in txt for r in required):
            b = backup(envutil)
            envutil.write_text(ENVUTIL_CANON, encoding="utf-8")
            print(f"[P35] OK rewrote {envutil} (backup={b})")
            changed = True
    else:
        envutil.write_text(ENVUTIL_CANON, encoding="utf-8")
        print(f"[P35] OK wrote {envutil}")
        changed = True

    py_compile.compile(str(envutil), doraise=True)
    return changed


def ensure_import_shim(py_text: str) -> str:
    # already present?
    if ("from .envutil import" in py_text) or ("from envutil import" in py_text):
        return py_text

    lines = py_text.splitlines()

    i = 0
    # shebang/encoding/comments/blank
    while i < len(lines):
        s = lines[i].strip()
        if s == "" or s.startswith("#") or lines[i].startswith("#!") or ("coding" in lines[i] and lines[i].lstrip().startswith("#")):
            i += 1
            continue
        break

    # module docstring handling (so we don't break __future__ imports)
    if i < len(lines):
        l = lines[i].lstrip()
        if l.startswith('"""') or l.startswith("'''"):
            quote = '"""' if l.startswith('"""') else "'''"
            # docstring ends on same line?
            if l.count(quote) >= 2:
                i += 1
            else:
                i += 1
                while i < len(lines):
                    if quote in lines[i]:
                        i += 1
                        break
                    i += 1

    # now consume __future__ imports (must stay at top)
    insert_at = i
    while insert_at < len(lines) and lines[insert_at].startswith("from __future__ import"):
        insert_at += 1

    # if there is an initial import block, place after it
    j = insert_at
    while j < len(lines) and (lines[j].startswith("import ") or lines[j].startswith("from ")):
        j += 1
    if j > insert_at:
        insert_at = j

    new_lines = lines[:insert_at] + [IMPORT_SHIM.rstrip()] + lines[insert_at:]
    return "\n".join(new_lines) + ("\n" if py_text.endswith("\n") else "")


def patch_file(path: Path) -> bool:
    txt = path.read_text(encoding="utf-8", errors="replace")
    orig = txt

    for rx, repl in _FLOAT_PATTERNS:
        txt = rx.sub(repl, txt)
    for rx, repl in _INT_PATTERNS:
        txt = rx.sub(repl, txt)

    if txt == orig:
        return False

    if ("env_float(" in txt) or ("env_int(" in txt):
        txt = ensure_import_shim(txt)

    b = backup(path)
    path.write_text(txt, encoding="utf-8")
    py_compile.compile(str(path), doraise=True)
    print(f"[P35] OK {path} (backup={b})")
    return True


def main() -> None:
    here = Path(__file__).resolve()
    root = find_repo_root(here)

    ensure_envutil(root)

    natbin_dir = root / "src" / "natbin"
    patched = 0
    for p in sorted(natbin_dir.rglob("*.py")):
        if p.name == "envutil.py":
            continue
        if patch_file(p):
            patched += 1

    print(f"[P35] patched_files={patched}")
    if patched == 0:
        print("[P35] NOTE: no changes were necessary (already normalized).")
    print("[P35] Suggested smoke-tests:")
    print("  1) .\\.venv\\Scripts\\python.exe .\\scripts\\tools\\selfcheck_repo.py")
    print("  2) $env:CP_ALPHA='0,07' ; pwsh -ExecutionPolicy Bypass -File .\\scripts\\scheduler\\observe_loop_auto.ps1 -Once")


if __name__ == "__main__":
    main()
