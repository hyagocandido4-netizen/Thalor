"""
P31b - Hard fix for observe_loop_auto.ps1 PowerShell helper crashes.

Fixes:
  - "The term 'param' is not recognized..." at runtime
  - selfcheck helper expectations (AsStr/AsInt/AsFloat)

Approach:
  1) Replace the entire helper block between markers:
       # [P15e_fix]
       ...
       # [/P15e_fix]
     with a known-good, simple implementation based on `filter` (pipeline-friendly)
     that also accepts comma decimals (pt-BR env values like "0,07").

  2) Outside that marker block, remove any orphan advanced-function fragments of:
       param([Parameter(ValueFromPipeline=$true)] $v)
     and any immediate process/begin/end block that follows.

Why filter?
  - It supports pipeline usage: ($env:CP_ALPHA | AsFloat)
  - No need for "process { }" blocks
  - Greatly reduces the chance of leaving broken param/process fragments at script scope.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Optional, Tuple


MARK_START = "# [P15e_fix]"
MARK_END = "# [/P15e_fix]"

PARAM_RE = re.compile(r"^\s*param\(\[Parameter\(ValueFromPipeline=\$true\)\]\s*\$v\)\s*$", re.IGNORECASE)
BLOCK_START_RE = re.compile(r"^\s*(process|begin|end)\s*\{", re.IGNORECASE)

REPLACEMENT_BLOCK = f"""{MARK_START}
# Helper converters used by observe_loop_auto.ps1.
# - tolerate null/empty
# - accept comma decimals (ex: "0,07") by normalizing to dot
# - pipeline-friendly: ($env:CP_ALPHA | AsFloat)

filter AsStr {{
  param([Parameter(ValueFromPipeline=$true)] $v)
  if ($null -eq $v) {{ return "" }}
  return ([string]$v).Trim()
}}

filter AsInt {{
  param([Parameter(ValueFromPipeline=$true)] $v)
  $s = ([string]$v)
  if ([string]::IsNullOrWhiteSpace($s)) {{ return 0 }}
  $s = $s.Trim().Replace(',', '.')
  $n = 0
  if ([int]::TryParse($s, [ref]$n)) {{ return $n }}

  # fallback: allow "1.0" etc
  $d = 0.0
  if ([double]::TryParse($s, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$d)) {{
    return [int][Math]::Round($d)
  }}
  return 0
}}

filter AsFloat {{
  param([Parameter(ValueFromPipeline=$true)] $v)
  $s = ([string]$v)
  if ([string]::IsNullOrWhiteSpace($s)) {{ return 0.0 }}
  $s = $s.Trim().Replace(',', '.')
  $d = 0.0
  if ([double]::TryParse($s, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$d)) {{
    return $d
  }}
  return 0.0
}}
{MARK_END}
"""


def _backup(path: Path) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_{ts}")
    shutil.copy2(path, bak)
    return bak


def _find_marker_block(lines: list[str]) -> Tuple[Optional[int], Optional[int]]:
    start = None
    end = None
    for i, line in enumerate(lines):
        if start is None and MARK_START in line:
            start = i
        if MARK_END in line:
            end = i
            # if multiple blocks exist, we still replace the first; later blocks will be removed
            if start is not None:
                break
    if start is None or end is None or end < start:
        return None, None
    return start, end


def _remove_ps_block(lines: list[str], start_i: int) -> int:
    brace = 0
    i = start_i
    while i < len(lines):
        brace += lines[i].count("{")
        brace -= lines[i].count("}")
        i += 1
        if brace <= 0 and i > start_i:
            break
    del lines[start_i:i]
    return start_i


def main() -> None:
    repo = Path(__file__).resolve().parents[2]  # scripts/patches -> repo root
    target = repo / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not target.exists():
        raise SystemExit(f"[P31b] target not found: {target}")

    raw = target.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines(True)

    start, end = _find_marker_block(lines)

    changed = False
    if start is not None and end is not None:
        # Replace the block (inclusive)
        before = lines[:start]
        after = lines[end + 1 :]
        repl_lines = (REPLACEMENT_BLOCK.strip("\n") + "\n").splitlines(True)
        lines = before + repl_lines + after
        changed = True
        # Remove any *additional* marker blocks (rare but possible)
        # (optional) We won't overcomplicate: if there is another MARK_START later, we remove it crudely.
        # We'll do a second pass to remove duplicate blocks by keeping only the first occurrence.
        seen_first = False
        out: list[str] = []
        i = 0
        while i < len(lines):
            if MARK_START in lines[i]:
                if not seen_first:
                    seen_first = True
                    out.append(lines[i]); i += 1
                    continue
                # remove duplicate block until MARK_END
                i += 1
                while i < len(lines) and MARK_END not in lines[i]:
                    i += 1
                if i < len(lines) and MARK_END in lines[i]:
                    i += 1
                changed = True
                continue
            out.append(lines[i])
            i += 1
        lines = out
    else:
        # If markers are missing, insert the helper block near the top (after initial comments/shebang)
        insert_at = 0
        while insert_at < len(lines) and (lines[insert_at].strip().startswith("#") or lines[insert_at].strip() == ""):
            insert_at += 1
        repl_lines = (REPLACEMENT_BLOCK.strip("\n") + "\n\n").splitlines(True)
        lines = lines[:insert_at] + repl_lines + lines[insert_at:]
        changed = True
        print("[P31b] WARN: marker block not found; inserted helper block near top of file.")

    # Now remove orphan param/process blocks outside the marker block
    # Recompute marker range after replacement/insertion
    start2, end2 = _find_marker_block(lines)

    def protected(i: int) -> bool:
        return start2 is not None and end2 is not None and start2 <= i <= end2

    removed_orphans = 0
    i = 0
    while i < len(lines):
        if protected(i):
            i += 1
            continue
        if PARAM_RE.match(lines[i]):
            removed_orphans += 1
            changed = True
            del lines[i]
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            if i < len(lines) and BLOCK_START_RE.match(lines[i]):
                i = _remove_ps_block(lines, i)
            continue
        i += 1

    if not changed:
        print("[P31b] OK: nothing changed (already clean).")
        return

    bak = _backup(target)
    target.write_text("".join(lines), encoding="utf-8")
    print(f"[P31b] OK patched: {target}")
    print(f"[P31b] backup: {bak}")
    print(f"[P31b] orphan param blocks removed: {removed_orphans}")
    print("[P31b] Next steps:")
    print("  1) python scripts/tools/selfcheck_repo.py")
    print("  2) $env:CP_ALPHA='0,07' ; pwsh -ExecutionPolicy Bypass -File scripts/scheduler/observe_loop_auto.ps1 -Once")


if __name__ == "__main__":
    main()
