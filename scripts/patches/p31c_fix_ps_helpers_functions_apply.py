from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


HELPERS = """# [P15e_fix] helpers: safe env parsing for PowerShell (pipeline-friendly; accepts "0,07")
function AsStr {
  param([Parameter(ValueFromPipeline=$true)] $v)
  process { if ($null -eq $v) { "" } else { [string]$v } }
}

function AsInt {
  param([Parameter(ValueFromPipeline=$true)] $v)
  process {
    if ($null -eq $v -or "$v" -eq "") { return 0 }
    $s = [string]$v
    $s = $s.Replace(",", ".")
    try { return [int][double]::Parse($s, [System.Globalization.CultureInfo]::InvariantCulture) } catch { return 0 }
  }
}

function AsFloat {
  param([Parameter(ValueFromPipeline=$true)] $v)
  process {
    if ($null -eq $v -or "$v" -eq "") { return 0.0 }
    $s = [string]$v
    $s = $s.Replace(",", ".")
    try { return [double]::Parse($s, [System.Globalization.CultureInfo]::InvariantCulture) } catch { return 0.0 }
  }
}
# [/P15e_fix]

"""


ORPHAN_PARAM_RE = re.compile(r'^\s*param\(\[Parameter\(ValueFromPipeline=\$true\)\]\s*\$v\)\s*$', re.IGNORECASE)


def patch_observe_loop(repo: Path) -> None:
    target = repo / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not target.exists():
        raise SystemExit(f"[P31c] alvo não encontrado: {target}")

    original = target.read_text(encoding="utf-8", errors="replace")
    lines = original.splitlines(True)

    # Find helper markers
    start = end = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*#\s*\[P15e_fix\]", line, flags=re.IGNORECASE):
            start = i
            break
    if start is not None:
        for j in range(start + 1, len(lines)):
            if re.match(r"^\s*#\s*\[/P15e_fix\]", lines[j], flags=re.IGNORECASE):
                end = j
                break

    helpers_lines = [l + "\n" if not l.endswith("\n") else l for l in HELPERS.splitlines()]

    if start is not None and end is not None:
        # Replace existing block (inclusive markers)
        new_lines = lines[:start] + helpers_lines + lines[end + 1 :]
    else:
        # Insert before P15e anchor if present, otherwise at top after header comments
        insert_at = None
        for i, line in enumerate(lines):
            if re.match(r"^\s*#\s*---\s*P15e:", line, flags=re.IGNORECASE):
                insert_at = i
                break
        if insert_at is None:
            # after leading comments / blank lines (but NEVER before a script-level param() block)
            insert_at = 0
            for i, line in enumerate(lines):
                if line.strip() == "" or line.lstrip().startswith("#"):
                    continue
                # If the first real statement is a script param() block, insert AFTER it.
                if re.match(r"^\s*param\s*\(", line, flags=re.IGNORECASE):
                    # find closing ')' line of the param block
                    close_idx = None
                    for j in range(i + 1, len(lines)):
                        if re.match(r"^\s*\)\s*$", lines[j]):
                            close_idx = j
                            break
                    insert_at = (close_idx + 1) if close_idx is not None else (i + 1)
                else:
                    insert_at = i
                break
        new_lines = lines[:insert_at] + helpers_lines + lines[insert_at:]

    # Remove orphan advanced-function fragments outside helper block:
    # any standalone param(ValueFromPipeline) lines not inside a function.
    # We'll remove that line and, if the following lines look like begin/process/end blocks, remove until a bare '}'.
    cleaned: list[str] = []
    in_helpers = False
    # determine helpers region indices in new_lines
    hs = he = None
    for i, line in enumerate(new_lines):
        if re.match(r"^\s*#\s*\[P15e_fix\]", line, flags=re.IGNORECASE):
            hs = i
        if re.match(r"^\s*#\s*\[/P15e_fix\]", line, flags=re.IGNORECASE):
            he = i
    def is_inside_helpers(idx: int) -> bool:
        return hs is not None and he is not None and hs <= idx <= he

    skip_mode = False
    for idx, line in enumerate(new_lines):
        if is_inside_helpers(idx):
            cleaned.append(line)
            continue

        if skip_mode:
            # stop skipping when we hit a line that looks like a closing brace on its own
            if re.match(r"^\s*}\s*$", line):
                skip_mode = False
            continue

        if ORPHAN_PARAM_RE.match(line):
            skip_mode = True
            continue

        # Also defensively remove orphan "process {" / "begin {" / "end {" that might follow if param line was removed earlier.
        if re.match(r"^\s*(process|begin|end)\s*{\s*$", line, flags=re.IGNORECASE):
            # Only treat as orphan if it's not immediately preceded by a function/filter definition.
            prev = cleaned[-1] if cleaned else ""
            if not re.match(r"^\s*(function|filter)\s+\w+\s*{\s*$", prev, flags=re.IGNORECASE):
                skip_mode = True
                continue

        cleaned.append(line)

    if cleaned == lines:
        print("[P31c] nada a fazer (arquivo já estava ok).")
        return

    backup = target.with_suffix(target.suffix + f".bak_{_ts()}")
    backup.write_text(original, encoding="utf-8")
    target.write_text("".join(cleaned), encoding="utf-8")
    print(f"[P31c] OK patched: {target}")
    print(f"[P31c] backup: {backup}")
    print("[P31c] Próximos passos:")
    print("  - python scripts/tools/selfcheck_repo.py (helpers devem passar)")
    print("  - set env com vírgula e rode observe_loop_auto.ps1 -Once (não pode cair em 'param not recognized')")


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    patch_observe_loop(repo)


if __name__ == "__main__":
    main()
