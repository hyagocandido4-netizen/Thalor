#!/usr/bin/env python3
"""
P16b - Fix ParserError in observe_loop_auto.ps1 caused by P16 injection inside a multi-line "-f" Write-Host.

Symptoms:
  ParserError ... You must provide a value expression following the '-f' operator.

Root cause:
  The previous P16 patch inserted the block right after the first line of a multi-line
  Write-Host (... "-f `"), splitting the "-f" and its arguments.

What this patch does:
  1) Ensures src/natbin/auto_isoblend.py exists (idempotent).
  2) Removes any existing P16 block from scripts/scheduler/observe_loop_auto.ps1.
  3) Re-inserts a SAFE P16 block (no "-f") after the complete "[P12] applied" statement.

Run:
  .\\.venv\\Scripts\\python.exe .\\scripts\\patches\\p16b_fix_observe_loop_auto_apply.py

Test:
  pwsh -ExecutionPolicy Bypass -File .\\scripts\\scheduler\\observe_loop_auto.ps1 -Once
"""
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
import py_compile

P16_START = "# --- P16: auto META_ISO_BLEND from daily_summary ---"
P16_END = "# --- /P16 ---"
PY_MARKER = "# --- P16: auto META_ISO_BLEND ---"


def _repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / ".git").exists():
            return p
    # fallback: structure-based
    for p in [cwd] + list(cwd.parents):
        if (p / "src" / "natbin").exists() and (p / "scripts").exists():
            return p
    raise SystemExit("Não encontrei o root do repo (.git). Rode dentro do repo.")


def _backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _ensure_auto_isoblend(py_path: Path) -> None:
    if py_path.exists():
        txt = py_path.read_text(encoding="utf-8", errors="replace")
        if PY_MARKER in txt:
            print(f"[P16b] auto_isoblend já existe: {py_path}")
            return

    py_path.parent.mkdir(parents=True, exist_ok=True)
    code = f"""{PY_MARKER}
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict


def _as_float(x: Any, default: float) -> float:
    try:
        if x is None:
            return default
        s = str(x).strip().replace(",", ".")
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


def _as_int(x: Any, default: int) -> int:
    try:
        if x is None:
            return default
        s = str(x).strip()
        if s == "":
            return default
        return int(float(s))
    except Exception:
        return default


def _load_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {{}}


def compute_meta_iso_blend(now: datetime | None = None) -> Dict[str, Any]:
    now = now or datetime.now()

    runs_dir = Path(os.getenv("RUNS_DIR", "runs")).resolve()
    lookback_days = _as_int(os.getenv("ISO_BLEND_LOOKBACK_DAYS", "7"), 7)
    min_trades_eval = _as_int(os.getenv("ISO_BLEND_MIN_TRADES_EVAL", "30"), 30)

    margin = _as_float(os.getenv("ISO_BLEND_MARGIN", "0.01"), 0.01)

    min_blend = _as_float(os.getenv("ISO_BLEND_MIN", "0.75"), 0.75)
    max_blend = _as_float(os.getenv("ISO_BLEND_MAX", "1.00"), 1.00)
    step = _as_float(os.getenv("ISO_BLEND_STEP", "0.05"), 0.05)

    bootstrap_blend = _as_float(os.getenv("ISO_BLEND_BOOTSTRAP", str(max_blend)), max_blend)

    payout = _as_float(os.getenv("PAYOUT", "0.8"), 0.8)
    break_even = 1.0 / (1.0 + payout) if payout > 0 else 0.5

    target_tpd = _as_float(os.getenv("ISO_BLEND_TARGET_TPD", os.getenv("VOL_TARGET_TRADES_PER_DAY", "1.0")), 1.0)

    thr = _as_float(os.getenv("THRESHOLD", "nan"), math.nan)
    thr_floor = _as_float(os.getenv("VOL_SAFE_THR_MIN", os.getenv("VOL_THR_MIN", "0.02")), 0.02)
    at_thr_floor = (not math.isnan(thr)) and (thr <= thr_floor + 1e-12)

    days_used = 0
    trades_total = 0
    wins_total = 0
    today_key = now.strftime("%Y%m%d")
    trades_today = 0

    for i in range(max(1, lookback_days)):
        day_key = (now - timedelta(days=i)).strftime("%Y%m%d")
        p = runs_dir / f"daily_summary_{{day_key}}.json"
        if not p.exists():
            continue
        s = _load_json(p)
        t = _as_int(s.get("trades_total"), 0)
        w = _as_int(s.get("trades_won"), 0)
        trades_total += t
        wins_total += w
        days_used += 1
        if day_key == today_key:
            trades_today = t

    tpd = (trades_total / days_used) if days_used > 0 else 0.0
    wr = (wins_total / trades_total) if trades_total > 0 else 0.0

    sec = now.hour * 3600 + now.minute * 60 + now.second
    frac_day = sec / 86400.0

    cur_blend = _as_float(os.getenv("META_ISO_BLEND", str(bootstrap_blend)), bootstrap_blend)
    cur_blend = max(min(cur_blend, max_blend), min_blend)

    decision = "keep"
    new_blend = cur_blend

    if trades_total < min_trades_eval or days_used < 2:
        new_blend = max(min(bootstrap_blend, max_blend), min_blend)
        decision = "bootstrap_set_blend"
        if trades_today == 0 and frac_day >= 0.50 and at_thr_floor:
            new_blend = max(min_blend, new_blend - step)
            decision = "bootstrap_no_trades_relax_one_step_at_thr_floor"
    else:
        if wr < break_even:
            new_blend = max_blend
            decision = "wr_below_breakeven_force_max"
        elif wr < break_even + margin:
            new_blend = min(max_blend, max(cur_blend, 0.95))
            decision = "wr_thin_keep_conservative"
        else:
            if tpd < target_tpd:
                if at_thr_floor:
                    new_blend = max(min_blend, cur_blend - step)
                    decision = "tpd_low_relax_blend"
                else:
                    decision = "tpd_low_wait_threshold"
            elif tpd > target_tpd * 1.50:
                new_blend = min(max_blend, cur_blend + step)
                decision = "tpd_high_tighten_blend"
            else:
                decision = "keep"

    return {{
        "decision": decision,
        "meta_iso_blend": round(float(new_blend), 4),
        "cur_blend": round(float(cur_blend), 4),
        "min_blend": round(float(min_blend), 4),
        "max_blend": round(float(max_blend), 4),
        "step": round(float(step), 4),
        "break_even": round(float(break_even), 6),
        "margin": round(float(margin), 4),
        "target_tpd": round(float(target_tpd), 4),
        "tpd": round(float(tpd), 4),
        "wr": round(float(wr), 4),
        "days_used": int(days_used),
        "trades_total": int(trades_total),
        "wins_total": int(wins_total),
        "trades_today": int(trades_today),
        "frac_day": round(float(frac_day), 6),
        "thr": None if math.isnan(thr) else float(thr),
        "thr_floor": float(thr_floor),
        "at_thr_floor": bool(at_thr_floor),
        "runs_dir": str(runs_dir),
    }}


def main() -> None:
    out = compute_meta_iso_blend()
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
"""
    py_path.write_text(code, encoding="utf-8")
    py_compile.compile(str(py_path), doraise=True)
    print(f"[P16b] OK wrote {py_path}")


def _remove_p16_block(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    removed = False
    while i < len(lines):
        if lines[i].strip() == P16_START:
            removed = True
            # skip until end marker
            i += 1
            while i < len(lines) and lines[i].strip() != P16_END:
                i += 1
            if i < len(lines) and lines[i].strip() == P16_END:
                i += 1
            # also skip one trailing blank line if present
            while i < len(lines) and lines[i].strip() == "":
                # keep at most one blank line
                i += 1
            continue
        out.append(lines[i])
        i += 1
    if removed:
        print("[P16b] Removed previous P16 block (was in a bad spot).")
    return out


def _find_insert_after_p12_applied(lines: list[str]) -> int | None:
    # Find the line that contains "[P12] applied"
    for i, ln in enumerate(lines):
        if "[P12]" in ln and "applied" in ln:
            # Insert after the full statement (handle multi-line -f with backticks)
            # Scan forward a few lines until we see a line that doesn't end with backtick and likely closes the call.
            j = i
            for j in range(i, min(i + 20, len(lines))):
                s = lines[j].rstrip()
                cont = s.endswith("`")
                if cont:
                    continue
                # Heuristic: end of Write-Host (...) statement usually contains ')'
                if ")" in s:
                    return j + 1
                # If it's single-line, insert right after it
                if j == i:
                    return i + 1
            # fallback
            return i + 1
    return None


def _find_insert_before_observe_call(lines: list[str]) -> int:
    # Safer fallback: insert before the call to observe_loop.ps1 if detectable
    rx = re.compile(r"(?i)^\s*(?:&\s*)?.*observe_loop\.ps1\b")
    for i, ln in enumerate(lines):
        if rx.search(ln) and ".bak" not in ln.lower():
            return i
    # Another fallback: before any "OBSERVE LOOP" echo (rare)
    for i, ln in enumerate(lines):
        if "OBSERVE LOOP" in ln:
            return i
    return len(lines)


def patch_observe_loop_auto(ps1_path: Path) -> None:
    txt = ps1_path.read_text(encoding="utf-8", errors="replace")
    nl = _detect_newline(txt)
    lines = txt.splitlines()

    lines = _remove_p16_block(lines)

    if any(l.strip() == P16_START for l in lines):
        # should not happen due to removal, but keep safe
        lines = _remove_p16_block(lines)

    insert_at = _find_insert_after_p12_applied(lines)
    if insert_at is None:
        insert_at = _find_insert_before_observe_call(lines)

    p16_block = [
        P16_START,
        "try {",
        '  if ($env:META_ISO_ENABLE -eq "1") {',
        '    $py = ".\\\\.venv\\\\Scripts\\\\python.exe"',
        "    if (Test-Path $py) {",
        "      $p16 = & $py -m natbin.auto_isoblend",
        "      if ($LASTEXITCODE -eq 0 -and $p16) {",
        "        $o = $p16 | ConvertFrom-Json",
        "        if ($null -ne $o.meta_iso_blend) {",
        "          $env:META_ISO_BLEND = [string]$o.meta_iso_blend",
        '          Write-Host "[P16] decision=$($o.decision) META_ISO_BLEND=$($env:META_ISO_BLEND) (tpd=$($o.tpd) wr=$($o.wr) days=$($o.days_used) today=$($o.trades_today))"',
        "        }",
        "      }",
        "    } else {",
        '      Write-Host "[P16] WARN: .venv python não encontrado (pulei auto_isoblend)"',
        "    }",
        "  }",
        "} catch {",
        '  Write-Host "[P16] WARN: auto_isoblend falhou: $($_.Exception.Message)"',
        "}",
        P16_END,
        "",
    ]

    new_lines = lines[:insert_at] + p16_block + lines[insert_at:]
    new_txt = nl.join(new_lines) + nl

    bkp = _backup(ps1_path)
    ps1_path.write_text(new_txt, encoding="utf-8")
    print(f"[P16b] OK patched: {ps1_path}")
    print(f"[P16b] Backup: {bkp.name}")


def main() -> None:
    root = _repo_root()
    py = root / "src" / "natbin" / "auto_isoblend.py"
    ps1 = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps1.exists():
        raise SystemExit(f"[P16b] Não achei: {ps1}")

    _ensure_auto_isoblend(py)
    patch_observe_loop_auto(ps1)

    print("\n[P16b] Teste agora:")
    print(r"  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once")
    print("  (procure por uma linha [P16] no log e confirme que não há ParserError.)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[P16b] ERRO: {e}")
        sys.exit(1)
