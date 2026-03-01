from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
import py_compile


def repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / ".git").exists():
            return p
    raise SystemExit("Não encontrei .git. Rode dentro de C:\\Users\\hyago\\Documents\\bot")


def backup_if_exists(p: Path) -> None:
    if not p.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)


AUTO_VOLUME_PY = r"""# P12e: auto volume controller (bootstrap + intraday-aware + stuck->threshold floor 0.00)
from __future__ import annotations

import os
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional


def _truthy(v: str | None) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s not in ("", "0", "false", "no", "off")


def _f(v: Any, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _i(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _load_json(p: Path) -> Optional[dict]:
    try:
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json_atomic(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _today_local() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _collect_summaries(lookback_days: int) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    now = datetime.now()
    for i in range(max(1, lookback_days)):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        p = Path("runs") / f"daily_summary_{day.replace('-','')}.json"
        s = _load_json(p)
        if isinstance(s, dict):
            out.append((day, s))
    return out


def _extract_trades(summary: dict) -> int:
    for k in ("trades_total", "trades", "trades_taken", "actions_trade_total"):
        if k in summary:
            return _i(summary.get(k), 0)
    tb = summary.get("trades_by_hour")
    if isinstance(tb, dict):
        tot = 0
        for hv in tb.values():
            if isinstance(hv, dict) and "total" in hv:
                tot += _i(hv.get("total"), 0)
            elif isinstance(hv, int):
                tot += hv
        return tot
    return 0


def _extract_winrate(summary: dict) -> tuple[int, int, float]:
    if "wins_eval_total" in summary and "trades_eval_total" in summary:
        w = _i(summary.get("wins_eval_total"), 0)
        t = _i(summary.get("trades_eval_total"), 0)
        return w, t, (w / t) if t > 0 else 0.0

    ws = summary.get("winrate_by_slot")
    if isinstance(ws, dict):
        w = 0
        t = 0
        for sv in ws.values():
            if isinstance(sv, dict):
                w += _i(sv.get("wins"), 0)
                t += _i(sv.get("trades"), 0)
        return w, t, (w / t) if t > 0 else 0.0

    return 0, 0, 0.0


def _extract_ev_avg_trades(summary: dict) -> float | None:
    for k in ("ev_avg_trades", "ev_mean_trades", "ev_trades_avg"):
        if k in summary:
            try:
                return float(summary.get(k))
            except Exception:
                return None
    return None


def _break_even_from_payout(payout: float) -> float:
    return 1.0 / (1.0 + payout)


def _extract_break_even(summary: dict, payout: float) -> float:
    be = summary.get("break_even")
    if be is not None:
        try:
            return float(be)
        except Exception:
            pass
    return _break_even_from_payout(payout)


def _aggregate_window(summaries: list[tuple[str, dict]]) -> dict:
    days_used = len(summaries)
    trades_sum = 0
    wins_sum = 0
    trades_eval_sum = 0

    ev_num = 0.0
    ev_den = 0.0

    days: list[str] = []
    for day, s in summaries:
        days.append(day)
        t = _extract_trades(s)
        w, te, _wr = _extract_winrate(s)
        trades_sum += t
        wins_sum += w
        trades_eval_sum += te

        ev = _extract_ev_avg_trades(s)
        if ev is not None and te > 0:
            ev_num += float(ev) * float(te)
            ev_den += float(te)

    tpd = trades_sum / max(1, days_used) if days_used > 0 else 0.0
    wr = (wins_sum / trades_eval_sum) if trades_eval_sum > 0 else 0.0
    ev_w = (ev_num / ev_den) if ev_den > 0 else None

    return {
        "days_used": int(days_used),
        "days": days,
        "trades_sum": int(trades_sum),
        "trades_per_day": float(tpd),
        "wins_sum": int(wins_sum),
        "trades_eval_sum": int(trades_eval_sum),
        "win_rate_eval": float(wr),
        "ev_avg_trades_w": None if ev_w is None else float(ev_w),
    }


@dataclass
class Params:
    threshold: float
    cpreg_alpha_start: float
    cpreg_alpha_end: float
    cpreg_slot2_mult: float
    gate_mode: str


def _current_params() -> Params:
    thr = _f(os.getenv("THRESHOLD"), 0.10)
    a0 = _f(os.getenv("CPREG_ALPHA_START"), _f(os.getenv("CP_ALPHA"), 0.07))
    a1 = _f(os.getenv("CPREG_ALPHA_END"), 0.10)
    m2 = _f(os.getenv("CPREG_SLOT2_MULT"), 0.85)
    gm = (os.getenv("GATE_MODE") or "").strip().lower() or "cp"
    return Params(threshold=thr, cpreg_alpha_start=a0, cpreg_alpha_end=a1, cpreg_slot2_mult=m2, gate_mode=gm)


def compute_next_params(*, window: dict, today: dict | None, be: float) -> dict:
    cur = _current_params()

    payout = _f(os.getenv("PAYOUT"), 0.8)

    target = _f(os.getenv("VOL_TARGET_TRADES_PER_DAY"), 1.2)
    deadband = _f(os.getenv("VOL_DEADBAND"), 0.15)
    wr_margin = _f(os.getenv("VOL_WR_MARGIN"), 0.01)

    lookback_days = _i(os.getenv("VOL_LOOKBACK_DAYS"), 7)
    min_days_used = _i(os.getenv("VOL_MIN_DAYS_USED"), 3)
    min_eval = _i(os.getenv("VOL_MIN_TRADES_EVAL"), 10)

    step_thr = _f(os.getenv("VOL_THR_STEP"), 0.01)
    thr_min = _f(os.getenv("VOL_THR_MIN"), 0.07)
    thr_max = _f(os.getenv("VOL_THR_MAX"), 0.15)

    step_alpha = _f(os.getenv("VOL_ALPHA_STEP"), 0.01)
    a_min = _f(os.getenv("VOL_ALPHA_MIN"), 0.05)
    a_max = _f(os.getenv("VOL_ALPHA_MAX"), 0.12)

    # Bootstrap (no-eval)
    bootstrap = _truthy(os.getenv("VOL_BOOTSTRAP_ENABLE", "1"))
    boot_thr_floor = _f(os.getenv("VOL_BOOTSTRAP_THR_FLOOR"), 0.07)
    boot_alpha_end_ceil = _f(os.getenv("VOL_BOOTSTRAP_ALPHA_END_CEIL"), 0.12)

    # P12e: stuck rule (quando já bateu no alpha_end ceiling e ainda 0 trades, permite descer THR até 0.00)
    stuck_enable = _truthy(os.getenv("VOL_BOOTSTRAP_STUCK_ENABLE", "1"))
    stuck_thr_floor = _f(os.getenv("VOL_BOOTSTRAP_STUCK_THR_FLOOR"), 0.00)  # EV>=0
    stuck_max_trades_today = _i(os.getenv("VOL_BOOTSTRAP_STUCK_MAX_TRADES_TODAY"), 0)

    # Intraday scaling
    intraday = _truthy(os.getenv("VOL_INTRADAY_SCALE", "1"))
    intraday_min_frac = _f(os.getenv("VOL_INTRADAY_MIN_FRAC"), 0.35)

    tpd = float(window.get("trades_per_day") or 0.0)
    te = int(window.get("trades_eval_sum") or 0)
    wr = float(window.get("win_rate_eval") or 0.0)
    ev_w = window.get("ev_avg_trades_w", None)
    try:
        ev_w_f = float(ev_w) if ev_w is not None else None
    except Exception:
        ev_w_f = None

    have_quality = te >= min_eval
    quality_ok = have_quality and (wr >= (be + wr_margin)) and (ev_w_f is None or ev_w_f >= 0.0)
    quality_bad = have_quality and (wr < be)
    ev_bad = (ev_w_f is not None) and (ev_w_f < 0.0)

    low = target * (1.0 - deadband)
    high = target * (1.0 + deadband)

    now = datetime.now()
    frac_day = ((now.hour * 3600) + (now.minute * 60) + now.second) / 86400.0
    frac_day = max(0.0, min(1.0, frac_day))

    trades_today = 0
    if today is not None:
        trades_today = _extract_trades(today)

    volume_low = (tpd < low)
    volume_high = (tpd > high)

    if intraday and (today is not None):
        if frac_day < intraday_min_frac:
            return {
                "recommended": {
                    "threshold": round(cur.threshold, 4),
                    "cpreg_alpha_start": round(cur.cpreg_alpha_start, 4),
                    "cpreg_alpha_end": round(cur.cpreg_alpha_end, 4),
                    "cpreg_slot2_mult": round(cur.cpreg_slot2_mult, 4),
                    "observed_trades_per_day": float(round(tpd, 6)),
                    "observed_trades_today": int(trades_today),
                    "observed_frac_day": float(round(frac_day, 6)),
                    "observed_trades_eval_sum": int(te),
                    "observed_win_rate_eval": float(round(wr, 6)),
                    "break_even": float(round(be, 6)),
                    "observed_ev_avg_trades_w": None if ev_w_f is None else float(round(ev_w_f, 6)),
                    "target_trades_per_day": float(target),
                    "notes": [f"intraday_too_early(frac<{intraday_min_frac})"],
                },
                "decision": "intraday_too_early_keep",
                "guardrails": {
                    "lookback_days": int(lookback_days),
                    "min_days_used": int(min_days_used),
                    "min_trades_eval": int(min_eval),
                    "payout": float(payout),
                },
            }

        expected_so_far = target * frac_day
        low_so_far = expected_so_far * (1.0 - deadband)
        high_so_far = expected_so_far * (1.0 + deadband)
        volume_low = (float(trades_today) < low_so_far)
        volume_high = (float(trades_today) > high_so_far)

    new_thr = cur.threshold
    new_a0 = cur.cpreg_alpha_start
    new_a1 = cur.cpreg_alpha_end
    notes: list[str] = []

    insufficient_days = int(window.get("days_used") or 0) < min_days_used

    if quality_bad or ev_bad:
        new_thr = min(thr_max, new_thr + step_thr)
        new_a1 = max(a_min, new_a1 - step_alpha)
        notes.append("tighten_quality")

    elif volume_low:
        if quality_ok:
            new_thr = max(thr_min, new_thr - step_thr)
            notes.append("relax_threshold_for_volume")

        elif bootstrap and (not have_quality):
            floor = max(thr_min, boot_thr_floor)

            if new_thr > floor:
                new_thr = max(floor, new_thr - step_thr)
                notes.append("bootstrap_relax_threshold_no_eval")

            elif new_a1 < boot_alpha_end_ceil:
                new_a1 = min(boot_alpha_end_ceil, new_a1 + step_alpha)
                notes.append("bootstrap_relax_alpha_end_no_eval")

            else:
                # P12e: stuck logic -> allow threshold to descend toward 0.00 if still 0 trades today
                if stuck_enable and (trades_today <= stuck_max_trades_today) and (new_thr > stuck_thr_floor):
                    new_thr = max(stuck_thr_floor, new_thr - step_thr)
                    notes.append("bootstrap_stuck_relax_threshold_to_ev0")
                else:
                    notes.append("bootstrap_at_limits_keep")
        else:
            notes.append("volume_low_but_quality_unknown_keep")

    elif volume_high:
        new_thr = min(thr_max, new_thr + step_thr)
        notes.append("tighten_threshold_for_volume")

    else:
        notes.append("no_change")

    if new_a0 > new_a1:
        new_a0 = new_a1

    if insufficient_days and (notes == ["no_change"] or notes == ["volume_low_but_quality_unknown_keep"]):
        new_thr = cur.threshold
        new_a0 = cur.cpreg_alpha_start
        new_a1 = cur.cpreg_alpha_end
        notes.append(f"insufficient_days_keep({window.get('days_used')}/{min_days_used})")

    rec = {
        "threshold": round(float(new_thr), 4),
        "cpreg_alpha_start": round(float(new_a0), 4),
        "cpreg_alpha_end": round(float(new_a1), 4),
        "cpreg_slot2_mult": round(float(cur.cpreg_slot2_mult), 4),
        "target_trades_per_day": float(target),
        "observed_trades_per_day": float(round(tpd, 6)),
        "observed_trades_today": int(trades_today),
        "observed_frac_day": float(round(frac_day, 6)),
        "observed_trades_eval_sum": int(te),
        "observed_win_rate_eval": float(round(wr, 6)),
        "break_even": float(round(be, 6)),
        "observed_ev_avg_trades_w": None if ev_w_f is None else float(round(ev_w_f, 6)),
        "notes": notes,
        "p12e": {
            "stuck_enable": bool(stuck_enable),
            "stuck_thr_floor": float(stuck_thr_floor),
            "stuck_max_trades_today": int(stuck_max_trades_today),
            "boot_thr_floor": float(boot_thr_floor),
            "boot_alpha_end_ceil": float(boot_alpha_end_ceil),
        },
    }
    return {
        "recommended": rec,
        "decision": ",".join(notes),
        "guardrails": {
            "lookback_days": int(lookback_days),
            "min_days_used": int(min_days_used),
            "min_trades_eval": int(min_eval),
            "payout": float(payout),
        },
    }


def main() -> None:
    payout = _f(os.getenv("PAYOUT"), 0.8)
    lookback = _i(os.getenv("VOL_LOOKBACK_DAYS"), 7)

    summaries = _collect_summaries(lookback)
    window = _aggregate_window(summaries[:lookback])

    today_day = _today_local()
    today_summary = None
    if summaries and summaries[0][0] == today_day:
        today_summary = summaries[0][1]

    be = _extract_break_even(summaries[0][1], payout) if summaries else _break_even_from_payout(payout)
    res = compute_next_params(window=window, today=today_summary, be=be)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "lookback_days": int(lookback),
        "window": window,
        "based_on_days": [d for d, _ in summaries[:lookback]],
        "recommended": res["recommended"],
        "decision": res["decision"],
        "guardrails": res.get("guardrails", {}),
    }

    out_cur = Path("runs") / "auto_params.json"
    out_hist = Path("runs") / f"auto_params_{datetime.now().strftime('%Y%m%d')}.json"
    _write_json_atomic(out_cur, payload)
    _write_json_atomic(out_hist, payload)

    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
"""


def main() -> None:
    root = repo_root()
    auto_p = root / "src" / "natbin" / "auto_volume.py"
    auto_p.parent.mkdir(parents=True, exist_ok=True)

    backup_if_exists(auto_p)
    auto_p.write_text(AUTO_VOLUME_PY, encoding="utf-8")
    py_compile.compile(str(auto_p), doraise=True)
    print(f"[P12e] OK wrote {auto_p}")


if __name__ == "__main__":
    main()