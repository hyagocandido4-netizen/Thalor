from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path


MARKER = "# --- P15c: meta_iso defaults (blend=0.75, k=3, safe floors) ---"


def repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / ".git").exists():
            return p
    raise SystemExit("Não encontrei .git. Rode este script dentro do repo (ex: C:\\Users\\hyago\\Documents\\bot).")


def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


def detect_newline(txt: str) -> str:
    # Preserve CRLF if file already uses it
    return "\r\n" if "\r\n" in txt else "\n"


def insertion_index(lines: list[str]) -> int:
    # Prefer to insert AFTER a `param( ... )` block if present at top.
    param_re = re.compile(r"^\s*param\s*\(\s*$", re.IGNORECASE)
    close_re = re.compile(r"^\s*\)\s*$")

    for i, line in enumerate(lines[:120]):  # only inspect the header
        if param_re.match(line):
            # Find the closing ')'
            for j in range(i + 1, min(len(lines), i + 200)):
                if close_re.match(lines[j]):
                    return j + 1
            # If we didn't find, fall back to insert right after the `param(` line
            return i + 1

    # Otherwise, insert after initial comments/blank lines
    for i, line in enumerate(lines):
        s = line.strip()
        if s == "" or s.startswith("#"):
            continue
        return i
    return len(lines)


def main() -> None:
    root = repo_root()
    target = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not target.exists():
        raise SystemExit(f"[P15c] Não achei: {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")
    if MARKER in txt:
        print(f"[P15c] Já aplicado: {target}")
        return

    nl = detect_newline(txt)
    lines = txt.splitlines()

    idx = insertion_index(lines)

    block = [
        MARKER,
        '$env:META_ISO_ENABLE = "1"',
        '$env:META_ISO_BLEND  = "0.75"',
        '$env:TOPK_K          = "3"',
        "",
        "# Auto-volume guardrails (derivados do sweep P14 com blend=0.75, k=3):",
        "# - thr=0.01 perde dinheiro; thr>=0.02 é piso pnl>=0.",
        '$env:VOL_SAFE_THR_MIN = "0.02"',
        '$env:VOL_THR_MIN      = "0.02"',
        '$env:VOL_THR_MAX      = "0.12"',
        '$env:VOL_TARGET_TRADES_PER_DAY = "1.0"',
        "",
        "# Opcional: mantenha CP bem comportado. (Pode comentar se preferir auto.)",
        '$env:CP_ALPHA = "0.07"',
        '$env:CPREG_ALPHA_START = "0.07"',
        '$env:CPREG_ALPHA_END   = "0.12"',
        "# --- /P15c ---",
        "",
    ]

    new_lines = lines[:idx] + block + lines[idx:]
    new_txt = nl.join(new_lines) + nl

    bkp = backup(target)
    target.write_text(new_txt, encoding="utf-8")

    print(f"[P15c] OK patched: {target}")
    print(f"[P15c] Backup: {bkp}")
    print("[P15c] Dica: rode `pwsh -ExecutionPolicy Bypass -File .\\scripts\\scheduler\\observe_loop_auto.ps1 -Once` e confirme no log:")
    print("       META_ISO_BLEND=0.75, TOPK_K=3 e que THRESHOLD não cai abaixo de 0.02.")


if __name__ == "__main__":
    main()
