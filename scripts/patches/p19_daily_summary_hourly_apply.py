#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P19 — Daily summary: add `by_hour` with outcomes (trades/wins/win_rate/ev_mean)

Why:
- auto_hourthr.py (P17) needs hourly performance to adjust threshold per-hour.
- Current daily_summary writes only counts by hour, without wins/EV.
  => P17 becomes a no-op.

This patch updates:
- src/natbin/observe_signal_topk_perday.py :: write_daily_summary()

It will:
- Create by_hour buckets: {"HH": {"trades":..,"wins":..,"losses":..,"win_rate":..,"ev_mean":..}}
- Only uses evaluated trades (where y label exists in dataset)
- Keeps existing keys for backward compatibility.

Run (repo root):
  .\\.venv\\Scripts\\python.exe .\\scripts\\patches\\p19_daily_summary_hourly_apply.py
"""
from __future__ import annotations

import py_compile
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class PatchResult:
    path: Path
    changed: bool
    backup_path: Optional[Path]
    note: str = ""


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def find_repo_root() -> Path:
    cwd = Path.cwd().resolve()
    if (cwd / "src" / "natbin").exists():
        return cwd
    here = Path(__file__).resolve()
    for p in [here.parent] + list(here.parents):
        if (p / "src" / "natbin").exists():
            return p
    raise SystemExit("P19: não encontrei src/natbin. Rode a partir do root do repo.")


def backup_file(path: Path) -> Path:
    b = path.with_suffix(path.suffix + f".bak_{stamp()}")
    shutil.copy2(path, b)
    return b


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_callput_version(text: str) -> tuple[str, bool, str]:
    """
    Patch version where actions are CALL/PUT and daily summary computes wins by looking up labels in dataset_csv.
    """
    if '"by_hour": by_hour' in text:
        return text, False, "already has by_hour"

    if "def write_daily_summary" not in text:
        return text, False, "write_daily_summary not found"

    changed = False
    notes = []

    # 1) init by_hour right after trades_by_hour init (hours buckets exist)
    # Find line containing: trades_by_hour: dict[str, dict[str, int]] = {h: {"total": ...} for h in hours}
    pat_init = re.compile(
        r"^(?P<indent>\s*)trades_by_hour\s*:\s*dict\[str,\s*dict\[str,\s*int\]\]\s*=\s*\{h:\s*\{\"total\":\s*0,\s*\"CALL\":\s*0,\s*\"PUT\":\s*0\}\s*for\s*h\s*in\s*hours\}\s*$",
        re.MULTILINE,
    )
    m = pat_init.search(text)
    if not m:
        return text, False, "CALL/PUT trades_by_hour init not found (unexpected file layout)"
    ind = m.group("indent")
    init_line = f'{ind}by_hour: dict[str, dict[str, Any]] = {{h: {{"trades": 0, "wins": 0, "ev_sum": 0.0}} for h in hours}}  # P19\n'
    text = text[:m.end()] + "\n" + init_line + text[m.end():]
    changed = True
    notes.append("init by_hour")

    # 2) accumulate by_hour inside evaluated-trade loop
    # Anchor: total_wins += won
    pat_acc = re.compile(r"^(?P<indent>\s*)total_wins\s*\+=\s*won\s*$", re.MULTILINE)
    m2 = pat_acc.search(text)
    if not m2:
        return text, changed, "; ".join(notes) + "; WARN: total_wins += won not found"
    ind2 = m2.group("indent")
    # ind2 is inside loop at the same level as total_eval, total_wins lines.
    block = (
        f"{ind2}# P19: by_hour outcomes (evaluated trades only)\n"
        f"{ind2}try:\n"
        f"{ind2}    _dt2 = datetime.fromtimestamp(ts, tz=ZoneInfo(tz))\n"
        f"{ind2}    _hh2 = f\"{{_dt2.hour:02d}}\"\n"
        f"{ind2}except Exception:\n"
        f"{ind2}    _hh2 = \"??\"\n"
        f"{ind2}_ev_val = float(tr.get(\"ev\") or 0.0)\n"
        f"{ind2}_bh = by_hour.setdefault(_hh2, {{\"trades\": 0, \"wins\": 0, \"ev_sum\": 0.0}})\n"
        f"{ind2}_bh[\"trades\"] += 1\n"
        f"{ind2}_bh[\"wins\"] += int(won)\n"
        f"{ind2}_bh[\"ev_sum\"] += _ev_val\n"
    )
    text = text[:m2.end()] + "\n" + block + text[m2.end():]
    changed = True
    notes.append("accumulate by_hour")

    # 3) finalize by_hour before summary dict
    pat_summary = re.compile(r"^(?P<indent>\s*)summary\s*=\s*\{\s*$", re.MULTILINE)
    m3 = pat_summary.search(text)
    if not m3:
        return text, changed, "; ".join(notes) + "; WARN: summary = { not found"
    ind3 = m3.group("indent")
    finalize = (
        f"{ind3}# P19: finalize by_hour stats\n"
        f"{ind3}for _hh, h in by_hour.items():\n"
        f"{ind3}    n = int(h.get(\"trades\") or 0)\n"
        f"{ind3}    w = int(h.get(\"wins\") or 0)\n"
        f"{ind3}    evs = float(h.get(\"ev_sum\") or 0.0)\n"
        f"{ind3}    h[\"losses\"] = max(0, n - w)\n"
        f"{ind3}    h[\"win_rate\"] = (w / n) if n > 0 else None\n"
        f"{ind3}    h[\"ev_mean\"] = (evs / n) if n > 0 else None\n"
        f"{ind3}    h.pop(\"ev_sum\", None)\n\n"
    )
    text = text[:m3.start()] + finalize + text[m3.start():]
    changed = True
    notes.append("finalize by_hour")

    # 4) add by_hour to summary dict after trades_by_hour
    pat_key = re.compile(r'^(?P<indent>\s*)"trades_by_hour"\s*:\s*trades_by_hour\s*,\s*$', re.MULTILINE)
    m4 = pat_key.search(text)
    if not m4:
        return text, changed, "; ".join(notes) + "; WARN: trades_by_hour key in summary not found"
    ind4 = m4.group("indent")
    text = text[:m4.end()] + "\n" + f'{ind4}"by_hour": by_hour,\n' + text[m4.end():]
    changed = True
    notes.append("add by_hour key")

    return text, changed, "; ".join(notes)


def main() -> None:
    root = find_repo_root()
    path = root / "src" / "natbin" / "observe_signal_topk_perday.py"
    if not path.exists():
        raise SystemExit(f"P19: não achei {path}")

    orig = read_text(path)
    new, changed, note = patch_callput_version(orig)
    if not changed:
        print(f"[P19] skip: {note}")
        return

    b = backup_file(path)
    write_text(path, new)

    try:
        py_compile.compile(str(path), doraise=True)
    except Exception as e:
        print("[P19] ERROR: py_compile falhou:", e)
        print(f"[P19] Backup salvo em: {b}")
        raise SystemExit(2)

    print(f"[P19] OK {path}")
    print(f"[P19] backup: {b}")
    print(f"[P19] note: {note}")
    print("[P19] Teste sugerido:")
    print("  - rode observe para gerar daily_summary_YYYYMMDD.json")
    print("  - verifique a chave by_hour com trades/wins/win_rate/ev_mean por hora")


if __name__ == "__main__":
    main()
