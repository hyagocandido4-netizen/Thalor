#!/usr/bin/env python3
"""
P36 — Env parsing hardening (locale-safe)

Goal
-----
Replace unsafe patterns that crash with pt-BR decimals (e.g. "0,07"):
    float(os.getenv("X", "0.8"))  -> env_float("X", 0.8)
    int(os.getenv("Y", "2000"))  -> env_int("Y", 2000)
    float(os.getenv("Z", str(var))) -> env_float("Z", var)

This patch:
  - edits ONLY files under src/natbin (excluding envutil.py)
  - auto-inserts / updates: `from .envutil import env_float, env_int` as needed
  - creates .bak_TIMESTAMP backups
  - py_compile checks every changed file (fails fast + restores on error)

How to run
----------
1) Save as: scripts/patches/p36_env_getenv_cleanup_apply.py
2) Run:
   .\.venv\Scripts\python.exe .\scripts\patches\p36_env_getenv_cleanup_apply.py

Afterwards (smoke):
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


# ---------- helpers

def _repo_root_from_here() -> Path:
    # scripts/patches/<this_file>.py  -> repo root = parents[2]
    return Path(__file__).resolve().parents[2]


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


_NUM_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def _parse_float_literal(s: str) -> float | None:
    s = s.strip()
    if not s:
        return None
    # Accept both '.' and ',' in defaults (we normalize)
    s2 = s.replace(",", ".")
    if not _NUM_RE.match(s2):
        return None
    try:
        return float(s2)
    except Exception:
        return None


def _parse_int_literal(s: str) -> int | None:
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


def _ensure_envutil_import(text: str, needed: set[str]) -> str:
    """Ensure a top-level `from .envutil import ...` contains needed names."""
    if not needed:
        return text

    lines = text.splitlines(True)

    # 1) If an envutil import exists, extend it.
    for i, line in enumerate(lines[:250]):
        m = re.match(r"\s*from\s+\.envutil\s+import\s+(.*)", line)
        if not m:
            continue

        rhs = m.group(1).rstrip("\n")
        # Preserve any trailing comment.
        comment = ""
        if "#" in rhs:
            rhs, comment = rhs.split("#", 1)
            comment = "#" + comment

        existing = [n.strip() for n in rhs.split(",") if n.strip()]
        merged = sorted(set(existing) | set(needed))
        new_line = "from .envutil import " + ", ".join(merged)
        if comment:
            new_line += "  " + comment.strip()
        lines[i] = new_line.rstrip() + "\n"
        return "".join(lines)

    # 2) Otherwise, insert a new import after initial docstring/import block.
    j = 0

    # shebang / encoding
    if j < len(lines) and lines[j].startswith("#!"):
        j += 1
    if j < len(lines) and re.match(r"#.*coding[:=]", lines[j]):
        j += 1

    # skip leading blanks/comments
    while j < len(lines) and (lines[j].strip() == "" or lines[j].lstrip().startswith("#")):
        j += 1

    # module docstring (triple quotes only)
    triple_s = "'" * 3
    if j < len(lines):
        s = lines[j].lstrip()
        if s.startswith('"""') or s.startswith(triple_s):
            delim = '"""' if s.startswith('"""') else triple_s
            # close on same line?
            if s.count(delim) >= 2 and s.find(delim, len(delim)) != -1:
                j += 1
            else:
                j += 1
                while j < len(lines):
                    if delim in lines[j]:
                        j += 1
                        break
                    j += 1

    # walk import block
    k = j
    while k < len(lines):
        s = lines[k].lstrip()
        if s.startswith("import ") or s.startswith("from "):
            k += 1
            continue
        if s.strip() == "" or s.startswith("#"):
            k += 1
            continue
        break

    insert_at = k
    import_line = "from .envutil import " + ", ".join(sorted(needed)) + "\n"
    lines.insert(insert_at, import_line)
    return "".join(lines)


# ---------- patch logic

_RE_FLOAT_STR = re.compile(
    r"""float\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*(?P<dq>['"])(?P<def>[^'"]*)(?P=dq)\s*\)\s*\)"""
)
_RE_INT_STR = re.compile(
    r"""int\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*(?P<dq>['"])(?P<def>[^'"]*)(?P=dq)\s*\)\s*\)"""
)

# Only patch str(<simple>) where <simple> has no parentheses.
# This avoids breaking cases like str(func(x)).
_RE_FLOAT_STR_EXPR = re.compile(
    r"""float\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*str\((?P<expr>[^()]+)\)\s*\)\s*\)"""
)
_RE_INT_STR_EXPR = re.compile(
    r"""int\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*str\((?P<expr>[^()]+)\)\s*\)\s*\)"""
)

# Optional: float(os.getenv("X", 0.8)) / float(os.getenv("X", SOME_VAR))
# We only patch if default is a simple token (no quotes, parens, commas).
_RE_FLOAT_SIMPLE = re.compile(
    r"""float\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*(?P<def>[^)]+?)\s*\)\s*\)"""
)
_RE_INT_SIMPLE = re.compile(
    r"""int\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*(?P<def>[^)]+?)\s*\)\s*\)"""
)


def _is_simple_default(expr: str) -> bool:
    expr = expr.strip()
    if not expr:
        return False
    if any(ch in expr for ch in ("'", '"', "(", ")", ",")):
        return False
    # allow identifiers / numbers / dotted attrs
    if re.match(r"^[A-Za-z_][A-Za-z0-9_\.]*$", expr):
        return True
    if _NUM_RE.match(expr.replace(",", ".")):
        return True
    return False


def patch_repo(repo: Path) -> list[Path]:
    src_dir = repo / "src" / "natbin"
    if not src_dir.exists():
        raise SystemExit(f"[P36] src dir not found: {src_dir}")

    ts = _timestamp()
    changed: list[Path] = []

    for p in sorted(src_dir.rglob("*.py")):
        if p.name == "envutil.py":
            continue

        text = p.read_text(encoding="utf-8", errors="replace")
        orig = text

        def repl_float_str(m: re.Match) -> str:
            var = m.group("var")
            d = m.group("def")
            num = _parse_float_literal(d)
            if num is not None:
                return f'env_float("{var}", {num})'
            return f'env_float("{var}", "{d}")'

        def repl_int_str(m: re.Match) -> str:
            var = m.group("var")
            d = m.group("def")
            num = _parse_int_literal(d)
            if num is not None:
                return f'env_int("{var}", {num})'
            return f'env_int("{var}", "{d}")'

        def repl_float_str_expr(m: re.Match) -> str:
            var = m.group("var")
            expr = m.group("expr").strip()
            return f'env_float("{var}", {expr})'

        def repl_int_str_expr(m: re.Match) -> str:
            var = m.group("var")
            expr = m.group("expr").strip()
            return f'env_int("{var}", {expr})'

        def repl_float_simple(m: re.Match) -> str:
            var = m.group("var")
            d = m.group("def").strip()
            if not _is_simple_default(d):
                return m.group(0)
            return f'env_float("{var}", {d})'

        def repl_int_simple(m: re.Match) -> str:
            var = m.group("var")
            d = m.group("def").strip()
            if not _is_simple_default(d):
                return m.group(0)
            return f'env_int("{var}", {d})'

        # Apply safe patterns first (string defaults / str(simple))
        text = _RE_FLOAT_STR.sub(repl_float_str, text)
        text = _RE_INT_STR.sub(repl_int_str, text)
        text = _RE_FLOAT_STR_EXPR.sub(repl_float_str_expr, text)
        text = _RE_INT_STR_EXPR.sub(repl_int_str_expr, text)

        # Then optional simple-default patterns
        text = _RE_FLOAT_SIMPLE.sub(repl_float_simple, text)
        text = _RE_INT_SIMPLE.sub(repl_int_simple, text)

        needed: set[str] = set()
        if "env_float(" in text:
            needed.add("env_float")
        if "env_int(" in text:
            needed.add("env_int")
        if needed:
            text = _ensure_envutil_import(text, needed)

        if text != orig:
            bak = p.with_suffix(p.suffix + f".bak_{ts}")
            shutil.copy2(p, bak)
            p.write_text(text, encoding="utf-8")

            try:
                py_compile.compile(str(p), doraise=True)
            except Exception as e:
                shutil.copy2(bak, p)
                raise SystemExit(f"[P36][FAIL] compile error after patching {p}: {e}")

            changed.append(p)

    return changed


def main() -> None:
    repo = _repo_root_from_here()
    changed = patch_repo(repo)

    print(f"[P36] repo={repo}")
    if not changed:
        print("[P36] No changes needed (already clean).")
    else:
        print(f"[P36] Patched files: {len(changed)}")
        for p in changed:
            print(f"  - {p.relative_to(repo)}")

    print("[P36] DONE. Smoke tests:")
    print(r"  1) .\.venv\Scripts\python.exe .\scripts\tools\selfcheck_repo.py")
    print(r"  2) $env:CP_ALPHA='0,07' ; pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once")


if __name__ == "__main__":
    main()