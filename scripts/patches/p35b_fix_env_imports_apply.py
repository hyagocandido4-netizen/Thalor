#!/usr/bin/env python3
"""
P35b - Fix envutil import drift (missing env_int/env_float/env_bool/env_str).

You hit:
  NameError: name 'env_int' is not defined (collect_recent.py)

Root cause:
- Some modules were updated to call env_* helpers (env_int/env_float/...) but the import
  line from envutil was missing OR incomplete (e.g. only env_float imported).

This patch:
1) Scans src/natbin/**/*.py
2) Detects env_* calls (env_float/env_int/env_bool/env_str)
3) Ensures the file imports *all* used env_* names from envutil:
   - If it already has a 'from .envutil import ...' (or 'from natbin.envutil import ...') line,
     it expands that import to include missing names.
   - Otherwise, it inserts a safe try/except import block near the top of the file.

Bonus:
- Extends scripts/tools/selfcheck_repo.py (if present) to fail when a file uses env_*
  but doesn't import all required names from envutil (static check -> catches this class of bug).

Safety:
- Writes .bak_YYYYMMDD_HHMMSS backups
- Runs py_compile on all touched files
"""

from __future__ import annotations

import re
import time
import py_compile
from pathlib import Path


# Detect direct calls like env_int(...)
ENV_CALL_RE = re.compile(r"\b(env_(?:float|int|bool|str))\s*\(")

# Match *single-line* imports (works for this repo; avoids AST dependency)
REL_IMPORT_RE = re.compile(r"^(\s*from\s+\.envutil\s+import\s+)(.+?)(\s*(?:#.*)?)$", re.M)
ABS_IMPORT_RE = re.compile(r"^(\s*from\s+natbin\.envutil\s+import\s+)(.+?)(\s*(?:#.*)?)$", re.M)


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + f".bak_{_timestamp()}")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return bak


def _parse_import_list(s: str) -> list[str]:
    # remove inline comment
    s = s.split("#", 1)[0].strip()
    s = s.strip("() ")
    parts = [p.strip() for p in s.split(",") if p.strip()]
    out: list[str] = []
    for p in parts:
        # strip aliases: env_float as ef
        out.append(p.split()[0])
    return out


def _render_import_list(names: list[str]) -> str:
    # stable + unique
    seen = set()
    out: list[str] = []
    for n in sorted(names):
        if n not in seen:
            seen.add(n)
            out.append(n)
    return ", ".join(out)


def _find_insert_line(lines: list[str]) -> int:
    """Insert after shebang/encoding/docstring/future imports."""
    i = 0
    if lines and lines[0].startswith("#!"):
        i += 1
    if i < len(lines) and re.match(r"^#.*coding[:=]\s*[-\w.]+", lines[i]):
        i += 1

    # docstring heuristic
    if i < len(lines):
        s = lines[i].lstrip()
        if s.startswith('"""') or s.startswith("'''"):
            quote = s[:3]
            # ends on same line?
            if s.count(quote) >= 2 and len(s.strip()) > 6:
                i += 1
            else:
                i += 1
                while i < len(lines):
                    if quote in lines[i]:
                        i += 1
                        break
                    i += 1

    # future imports
    while i < len(lines):
        if lines[i].startswith("from __future__ import"):
            i += 1
            continue
        if lines[i].strip() == "":
            j = i
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and lines[j].startswith("from __future__ import"):
                i = j
                continue
        break

    return i


def _ensure_envutil_imports(py_path: Path) -> bool:
    txt = py_path.read_text(encoding="utf-8")
    needed = sorted(set(ENV_CALL_RE.findall(txt)))
    if not needed:
        return False

    touched = False

    def _expand(m: re.Match) -> str:
        nonlocal touched
        prefix, imports, suffix = m.group(1), m.group(2), m.group(3) or ""
        current = _parse_import_list(imports)
        merged = sorted(set(current).union(needed))
        if set(merged) == set(current):
            return m.group(0)
        touched = True
        return f"{prefix}{_render_import_list(merged)}{suffix}"

    new_txt = REL_IMPORT_RE.sub(_expand, txt)
    new_txt = ABS_IMPORT_RE.sub(_expand, new_txt)

    if touched:
        _backup(py_path)
        py_path.write_text(new_txt, encoding="utf-8")
        return True

    # No single-line envutil import found -> insert standard block
    # (Even if the file contains 'envutil' elsewhere, inserting this block is safe and fixes NameError.)
    if not (REL_IMPORT_RE.search(txt) or ABS_IMPORT_RE.search(txt)):
        lines = txt.splitlines(keepends=True)
        insert_at = _find_insert_line(lines)
        names = ", ".join(needed)
        block = (
            "\n"
            "# ---- env helpers (comma-safe) ----\n"
            "try:\n"
            f"    from .envutil import {names}\n"
            "except Exception:  # pragma: no cover\n"
            f"    from natbin.envutil import {names}\n"
            "\n"
        )
        _backup(py_path)
        lines.insert(insert_at, block)
        py_path.write_text("".join(lines), encoding="utf-8")
        return True

    return False


def _patch_selfcheck(repo_root: Path) -> bool:
    p = repo_root / "scripts" / "tools" / "selfcheck_repo.py"
    if not p.exists():
        return False

    txt = p.read_text(encoding="utf-8")
    if "envutil import check" in txt:
        return False

    marker = "\n# --- envutil import check (auto) ---\n"
    snippet = r'''
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
'''
    insert_pos = txt.rfind("if __name__")
    if insert_pos == -1:
        new_txt = txt + marker + snippet + "\n"
    else:
        new_txt = txt[:insert_pos] + marker + snippet + "\n\n" + txt[insert_pos:]

    # Best-effort hook: inject after repo_root assignment
    if "_check_envutil_imports(" not in new_txt:
        new_txt = re.sub(
            r"(repo_root\s*=\s*Path\([^\n]+\)\s*\n)",
            r"\1    _check_envutil_imports(repo_root)\n",
            new_txt,
            count=1,
        )

    _backup(p)
    p.write_text(new_txt, encoding="utf-8")
    return True


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    src = repo / "src" / "natbin"
    if not src.exists():
        raise SystemExit(f"[P35b] src not found at {src}")

    touched = []
    for py in src.rglob("*.py"):
        try:
            if _ensure_envutil_imports(py):
                touched.append(py)
        except Exception as e:
            raise SystemExit(f"[P35b] ERROR patching {py}: {e}") from e

    selfcheck_changed = _patch_selfcheck(repo)

    # Compile touched files (and collect_recent specifically)
    to_compile = set(touched)
    cr = src / "collect_recent.py"
    if cr.exists():
        to_compile.add(cr)

    for p in sorted(to_compile):
        py_compile.compile(str(p), doraise=True)

    print(f"[P35b] OK. files updated: {len(touched)}")
    if touched:
        for p in touched[:25]:
            print(f"  - {p}")
        if len(touched) > 25:
            print(f"  ... (+{len(touched)-25} more)")
    print(f"[P35b] selfcheck updated: {selfcheck_changed}")
    print("[P35b] Próximos passos:")
    print("  1) python scripts/tools/selfcheck_repo.py")
    print("  2) pwsh -ExecutionPolicy Bypass -File scripts/scheduler/observe_loop_auto.ps1 -Once")
    print("  3) (opcional) teste vírgula: $env:CP_ALPHA='0,07' e rode o observe -Once (não deve crashar)")


if __name__ == "__main__":
    main()
