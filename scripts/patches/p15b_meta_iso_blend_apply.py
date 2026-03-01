#!/usr/bin/env python
"""
P15b - Meta Isotonic BLEND (volume vs calibration)

This patch adds an optional environment variable META_ISO_BLEND to blend between:
  - s_iso  : isotonic-calibrated meta score (more conservative, better calibration)
  - s_raw  : raw meta model score (more aggressive, more volume)

Blend:
  score = w*s_iso + (1-w)*s_raw
where w = float(META_ISO_BLEND) in [0,1]. Default w=1.0 (current behavior).

How to use:
  # keep current behavior (fully isotonic)
  $env:META_ISO_BLEND="1.0"

  # more volume / less conservative
  $env:META_ISO_BLEND="0.5"

  # fully raw (disable iso effect without retraining)
  $env:META_ISO_BLEND="0.0"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
import py_compile
from datetime import datetime


def die(msg: str, code: int = 1) -> None:
    print(f"[P15b] ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def patch_gate_meta(repo_root: Path) -> Path:
    target = repo_root / "src" / "natbin" / "gate_meta.py"
    if not target.exists():
        die(f"Arquivo não encontrado: {target}")

    txt = target.read_text(encoding="utf-8")

    # If already patched, do nothing.
    if "META_ISO_BLEND" in txt:
        print(f"[P15b] gate_meta.py já contém META_ISO_BLEND (skip): {target}")
        return target

    # Find the exact line that computes s from meta_iso
    # We keep indentation by capturing leading whitespace.
    pat = re.compile(
        r'^(?P<indent>\s*)s\s*=\s*meta_iso\.predict\(\s*s_raw\.astype\(float\)\s*\)\.astype\(float\)\s*$',
        re.MULTILINE,
    )

    m = pat.search(txt)
    if not m:
        die(
            "Não encontrei a linha 's = meta_iso.predict(s_raw.astype(float)).astype(float)' em gate_meta.py "
            "(talvez o arquivo mudou)."
        )

    indent = m.group("indent")
    repl = (
        f"{indent}s_iso = meta_iso.predict(s_raw.astype(float)).astype(float)\n"
        f"{indent}# P15b: optional blend between calibrated and raw score (0=raw, 1=isotonic)\n"
        f"{indent}try:\n"
        f"{indent}    w = float(os.getenv(\"META_ISO_BLEND\", \"1.0\"))\n"
        f"{indent}except Exception:\n"
        f"{indent}    w = 1.0\n"
        f"{indent}w = max(0.0, min(1.0, float(w)))\n"
        f"{indent}s = (w * s_iso) + ((1.0 - w) * s_raw)\n"
    )

    txt2 = pat.sub(repl, txt, count=1)

    # Backup
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = target.with_suffix(f".py.bak_{ts}")
    bak.write_text(txt, encoding="utf-8")

    target.write_text(txt2, encoding="utf-8")

    # Compile check
    py_compile.compile(str(target), doraise=True)

    print(f"[P15b] OK patched: {target} (backup={bak})")
    return target


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]  # scripts/patches/<this_file>
    patch_gate_meta(repo_root)


if __name__ == "__main__":
    main()
