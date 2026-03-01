from __future__ import annotations

import shutil
import py_compile
from datetime import datetime
from pathlib import Path

PS_MARKER = "# --- P16: auto META_ISO_BLEND from daily_summary ---"
PY_MARKER = "# --- P16: auto META_ISO_BLEND ---"


def repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / ".git").exists():
            return p
    raise SystemExit(
        "Não encontrei .git. Rode este script dentro do repo (ex: C:\\Users\\hyago\\Documents\\bot)."
    )


def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


def detect_newline(txt: str) -> str:
    return "\r\n" if "\r\n" in txt else "\n"


def write_auto_isoblend_py(target: Path) -> None:
    if target.exists():
        txt = target.read_text(encoding="utf-8", errors="replace")
        if PY_MARKER in txt:
            print(f"[P16] Já existe: {target}")
            return

    target.parent.mkdir(parents=True, exist_ok=True)
    code = """# --- P16: auto META_ISO_BLEND ---
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
        return {}


def compute_meta_iso_blend(now: datetime | None = None) -> Dict[str, Any]:
    now = now or datetime.now()

    runs_dir = Path(os.getenv("RUNS_DIR", "runs")).resolve()
    lookback_days = _as_int(os.getenv("ISO_BLEND_LOOKBACK_DAYS", "7"), 7)
    min_trades_eval = _as_int(os.getenv("ISO_BLEND_MIN_TRADES_EVAL", "30"), 30)

    # Prefer win-rate; only relax blend if we're already comfortably above breakeven
    margin = _as_float(os.getenv("ISO_BLEND_MARGIN", "0.01"), 0.01)

    min_blend = _as_float(os.getenv("ISO_BLEND_MIN", "0.75"), 0.75)
    max_blend = _as_float(os.getenv("ISO_BLEND_MAX", "1.00"), 1.00)
    step = _as_float(os.getenv("ISO_BLEND_STEP", "0.05"), 0.05)

    # Where to snap during bootstrap / no-eval: keep max blend (more conservative)
    bootstrap_blend = _as_float(os.getenv("ISO_BLEND_BOOTSTRAP", str(max_blend)), max_blend)

    payout = _as_float(os.getenv("PAYOUT", "0.8"), 0.8)
    break_even = 1.0 / (1.0 + payout) if payout > 0 else 0.5

    # Target trades/day can be inherited from auto_volume if present
    target_tpd = _as_float(os.getenv("ISO_BLEND_TARGET_TPD", os.getenv("VOL_TARGET_TRADES_PER_DAY", "1.0")), 1.0)

    # Optional: only relax blend when THRESHOLD is already near its floor.
    thr = _as_float(os.getenv("THRESHOLD", "nan"), math.nan)
    thr_floor = _as_float(os.getenv("VOL_SAFE_THR_MIN", os.getenv("VOL_THR_MIN", "0.02")), 0.02)
    at_thr_floor = (not math.isnan(thr)) and (thr <= thr_floor + 1e-12)

    # Aggregate summaries
    days_used = 0
    trades_total = 0
    wins_total = 0
    today_key = now.strftime("%Y%m%d")
    trades_today = 0

    for i in range(max(1, lookback_days)):
        day_key = (now - timedelta(days=i)).strftime("%Y%m%d")
        p = runs_dir / f"daily_summary_{day_key}.json"
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

    # Current blend
    cur_blend = _as_float(os.getenv("META_ISO_BLEND", str(bootstrap_blend)), bootstrap_blend)
    cur_blend = max(min(cur_blend, max_blend), min_blend)

    decision = "keep"
    new_blend = cur_blend

    if trades_total < min_trades_eval or days_used < 2:
        # Not enough data: stay conservative (bootstrap_blend; default=max_blend)
        new_blend = max(min(bootstrap_blend, max_blend), min_blend)
        decision = "bootstrap_set_blend"
        # If the day is half-gone and still zero trades, relax *one step*,
        # but only if threshold is already at/near its floor.
        if trades_today == 0 and frac_day >= 0.50 and at_thr_floor:
            new_blend = max(min_blend, new_blend - step)
            decision = "bootstrap_no_trades_relax_one_step_at_thr_floor"
    else:
        # Enough eval data: steer blend by WR first, then by volume.
        if wr < break_even:
            new_blend = max_blend
            decision = "wr_below_breakeven_force_max"
        elif wr < break_even + margin:
            # Barely above breakeven: keep conservative
            new_blend = max(cur_blend, 0.95)
            new_blend = min(new_blend, max_blend)
            decision = "wr_thin_keep_conservative"
        else:
            # WR is good, can trade volume for more signals if needed
            if tpd < target_tpd:
                # Relax gradually; prefer doing it after threshold hit floor.
                if at_thr_floor:
                    new_blend = max(min_blend, cur_blend - step)
                    decision = "tpd_low_relax_blend"
                else:
                    new_blend = cur_blend
                    decision = "tpd_low_wait_threshold"
            elif tpd > target_tpd * 1.50:
                new_blend = min(max_blend, cur_blend + step)
                decision = "tpd_high_tighten_blend"
            else:
                new_blend = cur_blend
                decision = "keep"

    out = {
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
    }
    return out


def main() -> None:
    out = compute_meta_iso_blend()
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
"""
    target.write_text(code, encoding="utf-8")
    py_compile.compile(str(target), doraise=True)
    print(f"[P16] OK wrote {target}")


def patch_observe_loop_ps1(target: Path) -> None:
    if not target.exists():
        raise SystemExit(f"[P16] Não achei: {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")
    if PS_MARKER in txt:
        print(f"[P16] Já aplicado no wrapper: {target}")
        return

    nl = detect_newline(txt)
    lines = txt.splitlines()

    insert_at = None
    for i, line in enumerate(lines):
        if "[P12]" in line and "applied" in line and ("Write-Host" in line or "write-host" in line.lower()):
            insert_at = i + 1
            break
    if insert_at is None:
        for i, line in enumerate(lines):
            if "observe_signal_topk_perday" in line:
                insert_at = i
                break
    if insert_at is None:
        insert_at = len(lines)

    block = [
        "# --- P16: auto META_ISO_BLEND from daily_summary ---",
        "try {",
        '  if ($env:META_ISO_ENABLE -eq "1") {',
        '    $py = ".\\\\.venv\\\\Scripts\\\\python.exe"',
        "    if (Test-Path $py) {",
        "      $p16 = & $py -m natbin.auto_isoblend",
        "      if ($LASTEXITCODE -eq 0 -and $p16) {",
        "        $o = $p16 | ConvertFrom-Json",
        "        if ($o.meta_iso_blend -ne $null) {",
        "          $env:META_ISO_BLEND = [string]$o.meta_iso_blend",
        '          Write-Host ("[P16] decision={0} META_ISO_BLEND={1} (tpd={2} wr={3} days={4} today={5})" -f $o.decision, $env:META_ISO_BLEND, $o.tpd, $o.wr, $o.days_used, $o.trades_today)',
        "        }",
        "      }",
        "    } else {",
        '      Write-Host "[P16] WARN: .venv python não encontrado (pulei auto_isoblend)"',
        "    }",
        "  }",
        "} catch {",
        '  Write-Host ("[P16] WARN: auto_isoblend falhou: {0}" -f $_.Exception.Message)',
        "}",
        "# --- /P16 ---",
        "",
    ]

    new_lines = lines[:insert_at] + block + lines[insert_at:]
    new_txt = nl.join(new_lines) + nl

    bkp = backup(target)
    target.write_text(new_txt, encoding="utf-8")
    print(f"[P16] OK patched: {target}")
    print(f"[P16] Backup: {bkp}")


def main() -> None:
    root = repo_root()
    py_target = root / "src" / "natbin" / "auto_isoblend.py"
    ps_target = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"

    write_auto_isoblend_py(py_target)
    patch_observe_loop_ps1(ps_target)

    print()
    print("[P16] Teste:")
    print(r"  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once")
    print("  (procure por uma linha [P16] no log e verifique META_ISO_BLEND.)")


if __name__ == "__main__":
    main()