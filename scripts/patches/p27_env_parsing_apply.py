#!/usr/bin/env python
"""P27 - Robust env parsing (comma-friendly) + small targeted refactors.

Problem:
  On Windows/pt-BR it's common to copy/paste numbers with comma decimals.
  Many modules do float(os.getenv(...)) which will crash on "0,07".

Fix:
  - Adds src/natbin/envutil.py with env_float/env_int/env_bool/env_str
  - Updates a few high-impact call sites (gate_meta, observe_signal_topk_perday,
    collect_recent) to use env_float.

Safe to re-run: idempotent with backups.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
from pathlib import Path


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + f".bak_{_ts()}")
    shutil.copy2(path, bak)
    return bak


def _repo_root_from_here() -> Path:
    here = Path(__file__).resolve()
    root = here.parents[2]
    if not (root / "src" / "natbin").exists():
        raise SystemExit(f"[P27] ERROR: repo root not found from {here}")
    return root


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


ENVUTIL = r'''"""Small helpers for reading environment variables safely.

Why this exists:
- PowerShell/Windows users frequently use comma decimals ("0,07").
- float("0,07") raises ValueError.

These helpers are intentionally dependency-free (stdlib only).
"""

from __future__ import annotations

import os
from typing import Optional


def env_str(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    return v if v != "" else default


def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    if v == "":
        return default
    v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip()
    if v == "":
        return default
    # int("10.0") would raise; keep strict
    try:
        return int(v)
    except ValueError:
        # accept comma/dot floats that are actually ints
        try:
            return int(float(v.replace(",", ".")))
        except Exception:
            return default


def env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    v = v.strip().lower()
    if v in ("1", "true", "t", "yes", "y", "on"):
        return True
    if v in ("0", "false", "f", "no", "n", "off"):
        return False
    return default
'''


def _ensure_import(text: str, import_line: str) -> str:
    if import_line in text:
        return text
    lines = text.splitlines(True)
    # Insert after the last import line at top-of-file.
    insert_at = 0
    for i, ln in enumerate(lines[:80]):
        if ln.startswith("import ") or ln.startswith("from "):
            insert_at = i + 1
    lines.insert(insert_at, import_line + "\n")
    return "".join(lines)


def _replace_all(text: str, replacements: list[tuple[str, str]]) -> tuple[str, int]:
    changed = 0
    for pat, repl in replacements:
        new, n = re.subn(pat, repl, text, flags=re.MULTILINE)
        if n:
            changed += n
            text = new
    return text, changed


def patch_gate_meta(path: Path) -> str:
    original = path.read_text(encoding="utf-8")
    txt = original

    txt = _ensure_import(txt, "from .envutil import env_float")

    # Replace only the known META_ISO_BLEND parsing.
    repls = [
        (r"float\(os\.getenv\(\"META_ISO_BLEND\",\s*\"([0-9.]+)\"\)\)", r"env_float(\"META_ISO_BLEND\", \1)"),
    ]
    txt2, n = _replace_all(txt, repls)

    if n == 0 and txt2 != txt:
        n = 1

    if txt2 != original:
        _backup(path)
        path.write_text(txt2, encoding="utf-8")
        return f"OK patched {path.name} (env_float)"
    return f"SKIP {path.name} (no changes)"


def patch_observe(path: Path) -> str:
    original = path.read_text(encoding="utf-8")
    txt = original

    txt = _ensure_import(txt, "from .envutil import env_float")

    repls = [
        (r"float\(os\.getenv\(\"PAYOUT\",\s*\"([0-9.]+)\"\)\)", r"env_float(\"PAYOUT\", \1)"),
        (r"float\(os\.getenv\(\"CP_ALPHA\",\s*\"([0-9.]+)\"\)\)", r"env_float(\"CP_ALPHA\", \1)"),
        (r"float\(os\.getenv\(\"CPREG_ALPHA_START\",\s*\"([0-9.]+)\"\)\)", r"env_float(\"CPREG_ALPHA_START\", \1)"),
        (r"float\(os\.getenv\(\"CPREG_ALPHA_END\",\s*\"([0-9.]+)\"\)\)", r"env_float(\"CPREG_ALPHA_END\", \1)"),
        (r"float\(os\.getenv\(\"META_ISO_BLEND\",\s*\"([0-9.]+)\"\)\)", r"env_float(\"META_ISO_BLEND\", \1)"),
    ]
    txt2, _n = _replace_all(txt, repls)

    if txt2 != original:
        _backup(path)
        path.write_text(txt2, encoding="utf-8")
        return f"OK patched {path.name} (env_float)"
    return f"SKIP {path.name} (no changes)"


def patch_collect_recent(path: Path) -> str:
    original = path.read_text(encoding="utf-8")
    txt = original

    txt = _ensure_import(txt, "from .envutil import env_float")

    repls = [
        (r"float\(os\.getenv\(\"IQ_SLEEP_S\",\s*\"([0-9.]+)\"\)\)", r"env_float(\"IQ_SLEEP_S\", \1)"),
    ]
    txt2, _n = _replace_all(txt, repls)

    if txt2 != original:
        _backup(path)
        path.write_text(txt2, encoding="utf-8")
        return f"OK patched {path.name} (env_float)"
    return f"SKIP {path.name} (no changes)"


def main() -> None:
    root = _repo_root_from_here()
    envutil_path = root / "src" / "natbin" / "envutil.py"
    if envutil_path.exists():
        _backup(envutil_path)
    _write(envutil_path, ENVUTIL)

    results: list[str] = []

    gate_meta = root / "src" / "natbin" / "gate_meta.py"
    if gate_meta.exists():
        results.append(patch_gate_meta(gate_meta))

    observe = root / "src" / "natbin" / "observe_signal_topk_perday.py"
    if observe.exists():
        results.append(patch_observe(observe))

    collect_recent = root / "src" / "natbin" / "collect_recent.py"
    if collect_recent.exists():
        results.append(patch_collect_recent(collect_recent))

    print(f"[P27] OK wrote {envutil_path}")
    for r in results:
        print(f"[P27] {r}")

    print("[P27] Smoke-tests sugeridos:")
    print("  - python scripts/tools/selfcheck_repo.py")
    print("  - (opcional) set env com vírgula: $env:CP_ALPHA='0,07' e rode observe -Once (não deve crashar)")


if __name__ == "__main__":
    main()
