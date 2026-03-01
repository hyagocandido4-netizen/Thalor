#!/usr/bin/env python3
# P36b — Env parsing hardening (locale-safe) + fix indentation bug inside try blocks

from __future__ import annotations

import datetime as _dt
import re
import shutil
from pathlib import Path
import py_compile


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


def _is_simple_default(expr: str) -> bool:
    expr = expr.strip()
    if not expr:
        return False
    # do NOT touch strings / calls / tuples
    if any(ch in expr for ch in ("'", '"', "(", ")", ",")):
        return False
    # allow identifiers / dotted attrs / numbers
    if re.match(r"^[A-Za-z_][A-Za-z0-9_\.]*$", expr):
        return True
    if _NUM_RE.match(expr.replace(",", ".")):
        return True
    return False


def _fix_try_envutil_indent(text: str) -> str:
    """
    Repair a known broken pattern:
        try:
        from .envutil import ...
    by indenting the import line into the try block.
    """
    lines = text.splitlines(True)
    for i in range(len(lines) - 1):
        m = re.match(r"^(\s*)try:\s*$", lines[i])
        if not m:
            continue
        indent = m.group(1)

        # find next non-empty
        j = i + 1
        while j < len(lines) and lines[j].strip() == "":
            j += 1
        if j >= len(lines):
            continue

        # if next statement is envutil import at SAME indent level -> broken
        if re.match(rf"^{re.escape(indent)}from\s+\.envutil\s+import\s+", lines[j]):
            lines[j] = indent + "    " + lines[j].lstrip()
    return "".join(lines)


def _ensure_envutil_import(text: str, needed: set[str]) -> str:
    """Ensure a top-level `from .envutil import ...` contains needed names.
       IMPORTANT: preserve indentation if line is inside try/except import shim."""
    if not needed:
        return text

    lines = text.splitlines(True)

    # 1) Extend an existing envutil import line (preserving indentation)
    for i, line in enumerate(lines[:300]):
        m = re.match(r"(?P<indent>\s*)from\s+\.envutil\s+import\s+(?P<rest>.*)", line)
        if not m:
            continue

        indent = m.group("indent")
        rhs = m.group("rest").rstrip("\n")

        # preserve trailing comment
        comment = ""
        if "#" in rhs:
            rhs, comment = rhs.split("#", 1)
            comment = "#" + comment

        existing = [n.strip() for n in rhs.split(",") if n.strip()]
        merged = sorted(set(existing) | set(needed))
        new_line = indent + "from .envutil import " + ", ".join(merged)
        if comment:
            new_line += "  " + comment.strip()
        lines[i] = new_line.rstrip() + "\n"
        return "".join(lines)

    # 2) Otherwise insert a new import after docstring/imports
    j = 0

    # shebang / encoding
    if j < len(lines) and lines[j].startswith("#!"):
        j += 1
    if j < len(lines) and re.match(r"#.*coding[:=]", lines[j]):
        j += 1

    # skip leading blanks/comments
    while j < len(lines) and (lines[j].strip() == "" or lines[j].lstrip().startswith("#")):
        j += 1

    # module docstring (triple quotes)
    if j < len(lines):
        s = lines[j].lstrip()
        if s.startswith('"""') or s.startswith("'''"):
            delim = '"""' if s.startswith('"""') else "'''"
            if s.count(delim) >= 2:
                j += 1
            else:
                j += 1
                while j < len(lines):
                    if delim in lines[j]:
                        j += 1
                        break
                    j += 1

    # move through import block
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


# patterns
_RE_FLOAT_STR = re.compile(
    r"""float\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*(?P<dq>['"])(?P<def>[^'"]*)(?P=dq)\s*\)\s*\)"""
)
_RE_INT_STR = re.compile(
    r"""int\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*(?P<dq>['"])(?P<def>[^'"]*)(?P=dq)\s*\)\s*\)"""
)
_RE_FLOAT_STR_EXPR = re.compile(
    r"""float\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*str\((?P<expr>[^()]+)\)\s*\)\s*\)"""
)
_RE_INT_STR_EXPR = re.compile(
    r"""int\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*str\((?P<expr>[^()]+)\)\s*\)\s*\)"""
)
_RE_FLOAT_SIMPLE = re.compile(
    r"""float\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*(?P<def>[^)]+?)\s*\)\s*\)"""
)
_RE_INT_SIMPLE = re.compile(
    r"""int\(\s*os\.getenv\(\s*(?P<q>['"])(?P<var>[^'"]+)(?P=q)\s*,\s*(?P<def>[^)]+?)\s*\)\s*\)"""
)


def patch_repo(repo: Path) -> list[Path]:
    src_dir = repo / "src" / "natbin"
    if not src_dir.exists():
        raise SystemExit(f"[P36b] src dir not found: {src_dir}")

    ts = _timestamp()
    changed: list[Path] = []

    for p in sorted(src_dir.rglob("*.py")):
        if p.name == "envutil.py":
            continue

        orig = p.read_text(encoding="utf-8", errors="replace")
        text = orig

        # Repair known broken try/envutil indentation if present
        text = _fix_try_envutil_indent(text)

        # Replacements
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

        text = _RE_FLOAT_STR.sub(repl_float_str, text)
        text = _RE_INT_STR.sub(repl_int_str, text)
        text = _RE_FLOAT_STR_EXPR.sub(repl_float_str_expr, text)
        text = _RE_INT_STR_EXPR.sub(repl_int_str_expr, text)
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
                raise SystemExit(f"[P36b][FAIL] compile error after patching {p}: {e}")

            changed.append(p)

    return changed


def main() -> None:
    repo = _repo_root_from_here()
    changed = patch_repo(repo)

    print(f"[P36b] repo={repo}")
    if not changed:
        print("[P36b] No changes needed (already clean).")
    else:
        print(f"[P36b] Patched files: {len(changed)}")
        for p in changed:
            print(f"  - {p.relative_to(repo)}")

    print("[P36b] DONE. Smoke tests:")
    print(r"  1) .\.venv\Scripts\python.exe .\scripts\tools\selfcheck_repo.py")
    print(r"  2) $env:CP_ALPHA='0,07' ; pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once")


if __name__ == "__main__":
    main()