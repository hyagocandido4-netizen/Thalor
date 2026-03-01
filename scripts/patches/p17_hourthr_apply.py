#!/usr/bin/env python3
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
import py_compile


PY_MARKER = "# --- P17: auto hour threshold ---"
PS_START = "# --- P17: hour-aware threshold multiplier ---"
PS_END = "# --- /P17 ---"


def repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / ".git").exists():
            return p
    # fallback
    for p in [cwd] + list(cwd.parents):
        if (p / "src" / "natbin").exists() and (p / "scripts").exists():
            return p
    raise SystemExit("Não encontrei o root do repo (.git). Rode dentro do repo.")


def backup(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


def detect_newline(txt: str) -> str:
    return "\r\n" if "\r\n" in txt else "\n"


def write_auto_hourthr(py_path: Path) -> None:
    if py_path.exists():
        txt = py_path.read_text(encoding="utf-8", errors="replace")
        if PY_MARKER in txt:
            print(f"[P17] Já existe: {py_path}")
            return

    py_path.parent.mkdir(parents=True, exist_ok=True)

    code = f"""{PY_MARKER}
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Tuple


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
        return int(float(s.replace(",", ".")))
    except Exception:
        return default


def _load_json(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {{}}


def _get_dict(obj: Dict[str, Any], *names: str) -> Dict[str, Any] | None:
    for n in names:
        v = obj.get(n)
        if isinstance(v, dict):
            return v
    return None


def _get_hour_value(container: Any, hour: int) -> Any:
    # supports dict with keys "13"/"13:00"/"13h"/"13:00:00" or list[24]
    if isinstance(container, list) or isinstance(container, tuple):
        if 0 <= hour < len(container):
            return container[hour]
        return None
    if not isinstance(container, dict):
        return None
    keys = [
        f"{{hour:02d}}".format(hour=hour),
        str(hour),
        f"{{hour:02d}}:00".format(hour=hour),
        f"{hour}:00",
        f"{{hour:02d}}:00:00".format(hour=hour),
        f"{hour}h",
    ]
    for k in keys:
        if k in container:
            return container[k]
    return None


def _extract_hour_stats(summary: Dict[str, Any], hour: int) -> Tuple[int, int, float]:
    # returns (count, wins, ev_mean_or_nan)
    by_hour = _get_dict(summary, "by_hour", "hours", "hourly", "per_hour")
    entry = _get_hour_value(by_hour, hour) if by_hour else None

    count = 0
    wins = 0
    ev_mean = math.nan

    if isinstance(entry, dict):
        count = _as_int(entry.get("count") or entry.get("taken") or entry.get("trades") or entry.get("n") or entry.get("total"), 0)
        wins = _as_int(entry.get("won") or entry.get("wins") or entry.get("w"), 0)

        wr = _as_float(entry.get("wr") or entry.get("hit") or entry.get("win_rate"), math.nan)
        if wins == 0 and count > 0 and not math.isnan(wr):
            wins = int(round(wr * count))

        ev_mean = _as_float(
            entry.get("ev_mean") or entry.get("ev_avg") or entry.get("mean_ev") or entry.get("ev"),
            math.nan,
        )
        if math.isnan(ev_mean):
            ev_total = entry.get("ev_total") or entry.get("ev_sum")
            if ev_total is not None and count > 0:
                ev_mean = _as_float(ev_total, math.nan) / max(1, count)

    elif entry is not None:
        # sometimes by_hour[hour] == count
        count = _as_int(entry, 0)

    # fallback maps (if entry didn't carry wins/ev)
    if count > 0 and wins == 0:
        wins_map = _get_dict(summary, "wins_by_hour", "hour_wins", "wins_hour", "by_hour_wins")
        v = _get_hour_value(wins_map, hour) if wins_map else None
        if v is not None:
            wins = _as_int(v, 0)

    if count > 0 and math.isnan(ev_mean):
        ev_map = _get_dict(summary, "ev_mean_by_hour", "hour_ev_mean", "ev_by_hour_mean", "by_hour_ev_mean")
        v = _get_hour_value(ev_map, hour) if ev_map else None
        if v is not None:
            ev_mean = _as_float(v, math.nan)

    return count, wins, ev_mean


def compute_hour_threshold(now: datetime | None = None) -> Dict[str, Any]:
    now = now or datetime.now()

    if os.getenv("P17_ENABLE", "1") == "0":
        return {{"decision": "disabled"}}

    runs_dir = Path(os.getenv("RUNS_DIR", "runs")).resolve()
    lookback_days = _as_int(os.getenv("P17_LOOKBACK_DAYS", "14"), 14)
    min_trades_hour = _as_int(os.getenv("P17_MIN_TRADES_HOUR", "8"), 8)

    payout = _as_float(os.getenv("PAYOUT", "0.8"), 0.8)
    break_even = 1.0 / (1.0 + payout) if payout > 0 else 0.5
    margin = _as_float(os.getenv("P17_WR_MARGIN", "0.01"), 0.01)

    good_ev = _as_float(os.getenv("P17_GOOD_EV", "0.02"), 0.02)
    bad_ev = _as_float(os.getenv("P17_BAD_EV", "-0.02"), -0.02)

    mult_good = _as_float(os.getenv("P17_MULT_GOOD", "0.95"), 0.95)
    mult_bad = _as_float(os.getenv("P17_MULT_BAD", "1.05"), 1.05)
    mult_min = _as_float(os.getenv("P17_MULT_MIN", "0.90"), 0.90)
    mult_max = _as_float(os.getenv("P17_MULT_MAX", "1.10"), 1.10)

    thr_floor = _as_float(os.getenv("VOL_SAFE_THR_MIN", os.getenv("VOL_THR_MIN", "0.02")), 0.02)
    thr_ceil = _as_float(os.getenv("P17_THR_CEIL", "0.20"), 0.20)

    thr_in_raw = os.getenv("THRESHOLD", "")
    thr_in = _as_float(thr_in_raw, math.nan)

    hour = now.hour

    days_used = 0
    h_trades = 0
    h_wins = 0
    ev_weight_sum = 0.0
    ev_weight_n = 0

    for i in range(max(1, lookback_days)):
        day_key = (now - timedelta(days=i)).strftime("%Y%m%d")
        p = runs_dir / f"daily_summary_{{day_key}}.json"
        if not p.exists():
            continue
        s = _load_json(p)
        c, w, evm = _extract_hour_stats(s, hour)
        if c <= 0:
            days_used += 1  # file exists, but hour had no trades; still counts as observed day
            continue
        h_trades += c
        h_wins += w
        if not math.isnan(evm):
            ev_weight_sum += float(evm) * c
            ev_weight_n += c
        days_used += 1

    wr_hour = (h_wins / h_trades) if h_trades > 0 else 0.0

    if ev_weight_n > 0:
        ev_hour = ev_weight_sum / ev_weight_n
    elif h_trades > 0:
        # estimate EV from WR + payout
        ev_hour = wr_hour * (1.0 + payout) - 1.0
    else:
        ev_hour = 0.0

    decision = "keep"
    hour_mult = 1.0

    if h_trades < min_trades_hour:
        decision = "insufficient_hour_trades_keep"
        hour_mult = 1.0
    else:
        if (ev_hour <= bad_ev) or (wr_hour < break_even):
            decision = "bad_hour_tighten"
            hour_mult = mult_bad
        elif (ev_hour >= good_ev) and (wr_hour >= break_even + margin):
            decision = "good_hour_relax"
            hour_mult = mult_good
        else:
            decision = "neutral_hour_keep"
            hour_mult = 1.0

    hour_mult = max(mult_min, min(mult_max, float(hour_mult)))

    thr_out = thr_in
    thr_out_str = thr_in_raw

    if not math.isnan(thr_in):
        thr_out = thr_in * hour_mult
        thr_out = max(thr_floor, min(thr_ceil, thr_out))
        thr_out_str = f"{{thr_out:.2f}}"

    out = {{
        "decision": decision,
        "hour": int(hour),
        "hour_trades": int(h_trades),
        "hour_wins": int(h_wins),
        "hour_wr": round(float(wr_hour), 4),
        "hour_ev_mean": round(float(ev_hour), 6),
        "hour_mult": round(float(hour_mult), 4),
        "threshold_in": thr_in_raw,
        "threshold_out": thr_out_str,
        "thr_floor": float(thr_floor),
        "thr_ceil": float(thr_ceil),
        "lookback_days": int(lookback_days),
        "days_used": int(days_used),
        "min_trades_hour": int(min_trades_hour),
        "break_even": round(float(break_even), 6),
        "margin": round(float(margin), 4),
        "runs_dir": str(runs_dir),
    }}
    return out


def main() -> None:
    out = compute_hour_threshold()
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
"""
    py_path.write_text(code, encoding="utf-8")
    py_compile.compile(str(py_path), doraise=True)
    print(f"[P17] OK wrote {py_path}")


def remove_existing_p17(lines: list[str]) -> list[str]:
    out = []
    i = 0
    removed = False
    while i < len(lines):
        if lines[i].strip() == PS_START:
            removed = True
            i += 1
            while i < len(lines) and lines[i].strip() != PS_END:
                i += 1
            if i < len(lines) and lines[i].strip() == PS_END:
                i += 1
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        out.append(lines[i])
        i += 1
    if removed:
        print("[P17] Removed previous P17 block (idempotent reapply).")
    return out


def patch_wrapper(ps1: Path) -> None:
    txt = ps1.read_text(encoding="utf-8", errors="replace")
    nl = detect_newline(txt)
    lines = txt.splitlines()

    lines = remove_existing_p17(lines)

    # insert after P16 end marker if present
    insert_at = None
    for i, ln in enumerate(lines):
        if ln.strip() == "# --- /P16 ---":
            insert_at = i + 1
            break

    if insert_at is None:
        # fallback: before observe call
        rx = re.compile(r"(?i)observe_loop\.ps1\b")
        for i, ln in enumerate(lines):
            if rx.search(ln):
                insert_at = i
                break
    if insert_at is None:
        insert_at = len(lines)

    block = [
        PS_START,
        "try {",
        '  if ($env:P17_ENABLE -ne "0") {',
        '    $py = ".\\\\.venv\\\\Scripts\\\\python.exe"',
        "    if (Test-Path $py) {",
        "      $p17 = & $py -m natbin.auto_hourthr",
        "      if ($LASTEXITCODE -eq 0 -and $p17) {",
        "        $o = $p17 | ConvertFrom-Json",
        "        if ($null -ne $o.threshold_out -and $o.threshold_out -ne \"\") {",
        "          $thr_before = $env:THRESHOLD",
        "          $env:THRESHOLD = [string]$o.threshold_out",
        '          Write-Host "[P17] decision=$($o.decision) hour=$($o.hour) mult=$($o.hour_mult) THRESHOLD=$thr_before->$($env:THRESHOLD) (h_trades=$($o.hour_trades) h_wr=$($o.hour_wr) h_ev=$($o.hour_ev_mean))"',
        "        }",
        "      }",
        "    } else {",
        '      Write-Host "[P17] WARN: .venv python não encontrado (pulei auto_hourthr)"',
        "    }",
        "  }",
        "} catch {",
        '  Write-Host "[P17] WARN: auto_hourthr falhou: $($_.Exception.Message)"',
        "}",
        PS_END,
        "",
    ]

    new_lines = lines[:insert_at] + block + lines[insert_at:]
    new_txt = nl.join(new_lines) + nl

    bkp = backup(ps1)
    ps1.write_text(new_txt, encoding="utf-8")
    print(f"[P17] OK patched: {ps1}")
    print(f"[P17] Backup: {bkp.name}")


def main() -> None:
    root = repo_root()
    py_path = root / "src" / "natbin" / "auto_hourthr.py"
    ps1 = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"

    if not ps1.exists():
        raise SystemExit(f"[P17] Não achei wrapper: {ps1}")

    write_auto_hourthr(py_path)
    patch_wrapper(ps1)

    print("\n[P17] Teste:")
    print(r"  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once")
    print("  Procure por uma linha [P17] no log.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[P17] ERRO: {e}")
        sys.exit(1)