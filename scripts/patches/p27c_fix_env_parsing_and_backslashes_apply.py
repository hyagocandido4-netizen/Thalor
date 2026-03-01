#!/usr/bin/env python3
"""
P27c - Hotfix for accidental escape/backslash regressions introduced by P27/P27b.

Fixes:
  1) env_*("NAME") calls accidentally written as env_*(\"NAME\" ...) (SyntaxError).
  2) Broken string literals like replace("\", "/") -> replace("\\", "/") (SyntaxError).
  3) Adds missing AsInt helper to scripts/scheduler/observe_loop_auto.ps1 (selfcheck expectation).
  4) Runs py_compile over src/natbin and scripts/tools to ensure syntax is clean.

How to run (Windows PowerShell):
  .\.venv\Scripts\python.exe .\scripts\patches\p27c_fix_env_parsing_and_backslashes_apply.py
"""
from __future__ import annotations

import datetime as _dt
import py_compile
import re
import shutil
from pathlib import Path


TS = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")

ENV_FN = r"(?:env_(?:float|int|bool|str))"


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + f".bak_{TS}")
    shutil.copy2(path, bak)
    return bak


def fix_env_escapes(text: str) -> str:
    """Remove stray backslashes before quotes in env_* calls (only)."""
    # Case A: env_float(\"NAME\"
    text = re.sub(rf"\b({ENV_FN})\(\s*\\\"([^\"\\]+?)\\\"", r'\1("\2"', text)
    # Case B: env_float(\"NAME"
    text = re.sub(rf"\b({ENV_FN})\(\s*\\\"([^\"\\]+?)\"", r'\1("\2"', text)
    # Case C: env_float("NAME\"
    text = re.sub(rf"\b({ENV_FN})\(\s*\"([^\"\\]+?)\\\"", r'\1("\2"', text)
    return text


def fix_broken_backslash_literals(text: str) -> str:
    """Fix syntax-error pattern: "\\", -> "\\\\", (and same for single quotes)."""
    text = text.replace('"\\",', '"\\\\",')
    text = text.replace("'\\',", "'\\\\',")
    return text


def patch_python_tree(repo: Path) -> list[tuple[Path, Path]]:
    targets: list[Path] = []
    for base in [repo / "src" / "natbin", repo / "scripts" / "tools"]:
        if base.exists():
            targets.extend(sorted(base.rglob("*.py")))

    changed: list[tuple[Path, Path]] = []
    for p in targets:
        raw = p.read_text(encoding="utf-8", errors="replace")
        txt = raw
        txt = fix_env_escapes(txt)
        txt = fix_broken_backslash_literals(txt)

        if txt != raw:
            bak = _backup(p)
            p.write_text(txt, encoding="utf-8", newline="\n")
            changed.append((p, bak))

    # Compile key python modules to ensure repo isn't left broken.
    for p in targets:
        py_compile.compile(str(p), doraise=True)

    return changed


def patch_observe_loop_auto_ps1(repo: Path) -> tuple[bool, Path | None]:
    ps1 = repo / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps1.exists():
        return (False, None)

    txt = ps1.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?im)^\s*function\s+AsInt\b", txt):
        return (False, ps1)

    insert_block = r'''
function AsInt([object]$v) {
  if ($null -eq $v) { return 0 }
  try {
    $s = "$v"
    $s = $s.Replace(",", ".")
    return [int]([double]$s)
  } catch {
    return 0
  }
}
'''.strip("\n") + "\n\n"

    # Prefer inserting right after AsStr if present
    lines = txt.splitlines(keepends=True)
    idx = None
    for i, line in enumerate(lines):
        if re.match(r"(?im)^\s*function\s+AsStr\b", line):
            idx = i
            break

    if idx is None:
        new_txt = insert_block + txt
    else:
        lines.insert(idx + 1, insert_block)
        new_txt = "".join(lines)

    bak = _backup(ps1)
    ps1.write_text(new_txt, encoding="utf-8", newline="\n")
    return (True, bak)


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    if not (repo / "src" / "natbin").exists():
        print(f"[P27c][ERR] Repo root not detected at: {repo}")
        print("          Place this file under scripts/patches/ and run again.")
        return 2

    ps1_changed, ps1_bak = patch_observe_loop_auto_ps1(repo)
    py_changed = patch_python_tree(repo)

    if ps1_changed:
        print(f"[P27c] OK patched observe_loop_auto.ps1 (backup={ps1_bak})")
    else:
        print("[P27c] observe_loop_auto.ps1: no change (AsInt already present or file missing)")

    if py_changed:
        print(f"[P27c] OK fixed python files: {len(py_changed)}")
        for p, bak in py_changed[:30]:
            print(f"  - {p} (backup={bak})")
        if len(py_changed) > 30:
            print(f"  ... +{len(py_changed)-30} more")
    else:
        print("[P27c] Python files: no changes needed")

    print("[P27c] OK: py_compile passed for src/natbin and scripts/tools.")
    print("[P27c] Próximos testes sugeridos:")
    print("  1) .\\.venv\\Scripts\\python.exe .\\scripts\\tools\\selfcheck_repo.py")
    print("  2) $env:CP_ALPHA='0,07' ; pwsh -ExecutionPolicy Bypass -File .\\scripts\\scheduler\\observe_loop_auto.ps1 -Once")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
