"""P33 - Fix misleading gate_used label when META_ISO is enabled under CP gating.

Problem
-------
In `src/natbin/gate_meta.py`, `compute_scores()` may return `gate_used == "meta_iso"`
when `gate_mode == "cp"` if META_ISO is enabled. That is confusing and can make logs,
backtests, daily summaries, and SQLite rows look like you're running `meta_iso` gating
when you are actually running CP gating.

Fix
---
Keep the meta-isotonic score path, but only label it as `meta_iso` when the requested
`gate_mode` is actually `meta`. When the requested `gate_mode` is `cp`, keep the
label `cp`.

This patch is designed to be:
- minimal (single-line return change)
- safe (backs up the file, compile-checks after writing)
- idempotent (won't re-apply if already fixed)

Usage
-----
python scripts/patches/p33_fix_gate_used_label_apply.py

"""

from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
import py_compile


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def patch_gate_meta(repo: Path) -> bool:
    target = repo / "src" / "natbin" / "gate_meta.py"
    if not target.exists():
        raise FileNotFoundError(f"gate_meta.py not found at {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")

    already = '("meta_iso" if gate_mode == "meta" else gate_mode)' in txt
    if already:
        print("[P33] gate_meta.py already patched (label fix present).")
        return False

    # We only want to touch the specific return inside the META_ISO block.
    # Expected legacy line:
    #   return proba, conf, score, "meta_iso"
    # or single quotes.
    pat = r"return\s+proba\s*,\s*conf\s*,\s*score\s*,\s*(['\"])meta_iso\1"

    if not re.search(pat, txt):
        raise RuntimeError(
            "[P33] Could not find legacy return '..., "
            "meta_iso'. The file may have changed, or it is already fixed."
        )

    repl = 'return proba, conf, score, ("meta_iso" if gate_mode == "meta" else gate_mode)'
    new_txt, n = re.subn(pat, repl, txt, count=1)
    if n != 1:
        raise RuntimeError(f"[P33] Unexpected substitutions: {n}")

    # Optional: bump GATE_VERSION if present (keep it simple & safe).
    m = re.search(r"^GATE_VERSION\s*=\s*(['\"])([^'\"]+)\1\s*$", new_txt, flags=re.M)
    if m:
        old = m.group(2)
        if "p33" not in old:
            bumped = old + "+p33label"
            new_txt = re.sub(
                r"^GATE_VERSION\s*=\s*(['\"])([^'\"]+)\1\s*$",
                f'GATE_VERSION = "{bumped}"',
                new_txt,
                flags=re.M,
                count=1,
            )

    backup = target.with_name(target.name + f".bak_{_ts()}")
    backup.write_text(txt, encoding="utf-8")
    target.write_text(new_txt, encoding="utf-8")

    # Compile-check just this file (fast).
    py_compile.compile(str(target), doraise=True)

    print(f"[P33] OK patched: {target}")
    print(f"[P33] backup: {backup}")
    print("[P33] expected behavior:")
    print("  - META_ISO_ENABLE=1 + gate_mode=cp -> gate_used should stay 'cp' (not 'meta_iso')")
    print("  - META_ISO_ENABLE=1 + gate_mode=meta -> gate_used remains 'meta_iso' (as before)")
    return True


def main() -> None:
    repo = Path(__file__).resolve().parents[2]  # scripts/patches/ -> repo root
    try:
        patch_gate_meta(repo)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
