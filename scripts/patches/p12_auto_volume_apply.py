from __future__ import annotations

import os
import json
import shutil
from datetime import datetime
from pathlib import Path
import py_compile


def repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / ".git").exists():
            return p
    raise SystemExit("Não encontrei .git. Rode dentro do repo (C:\\Users\\hyago\\Documents\\bot).")


def backup_if_exists(p: Path) -> None:
    if not p.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)


AUTO_VOLUME_PY = r'''# P12: auto volume controller (uses runs/daily_summary_YYYYMMDD.json)
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


def _yesterday_local() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _summary_path(day: str) -> Path:
    return Path("runs") / f"daily_summary_{day.replace('-','')}.json"


def _find_best_summary_day(min_obs: int) -> tuple[str, Optional[dict]]:
    """
    Preferimos o summary de hoje se já tem observações suficientes,
    senão usamos o de ontem.
    """
    day_today = _today_local()
    s_today = _load_json(_summary_path(day_today))
    obs_today = _i((s_today or {}).get("rows_total") or (s_today or {}).get("observations_total") or 0, 0)
    if s_today and obs_today >= min_obs:
        return day_today, s_today

    day_y = _yesterday_local()
    s_y = _load_json(_summary_path(day_y))
    return day_y, s_y


def _extract_trades(summary: dict) -> int:
    # tenta campos comuns
    for k in ("trades_total", "trades", "trades_taken", "actions_trade_total"):
        if k in summary:
            return _i(summary.get(k), 0)
    # tenta trades_by_hour
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
    wins = None
    trades_eval = None

    # campos diretos
    if "wins_eval_total" in summary and "trades_eval_total" in summary:
        wins = _i(summary.get("wins_eval_total"), 0)
        trades_eval = _i(summary.get("trades_eval_total"), 0)
    elif "wins_total" in summary and "trades_total" in summary:
        wins = _i(summary.get("wins_total"), 0)
        trades_eval = _i(summary.get("trades_total"), 0)

    if trades_eval is not None and trades_eval > 0:
        return wins or 0, trades_eval, (wins or 0) / max(1, trades_eval)

    # tenta winrate_by_slot agregando
    ws = summary.get("winrate_by_slot")
    if isinstance(ws, dict):
        w = 0
        t = 0
        for sv in ws.values():
            if isinstance(sv, dict):
                w += _i(sv.get("wins"), 0)
                t += _i(sv.get("trades"), 0)
        if t > 0:
            return w, t, w / t

    # fallback
    return 0, 0, 0.0


def _extract_ev_avg_trades(summary: dict) -> float | None:
    for k in ("ev_avg_trades", "ev_mean_trades", "ev_trades_avg"):
        if k in summary:
            try:
                return float(summary.get(k))
            except Exception:
                return None
    return None


def _extract_break_even(summary: dict, payout: float) -> float:
    be = summary.get("break_even")
    if be is not None:
        try:
            return float(be)
        except Exception:
            pass
    # break-even for binary payout (win gains payout, lose -1): p = 1/(1+payout)
    return 1.0 / (1.0 + payout)


@dataclass
class Params:
    threshold: float
    cpreg_alpha_start: float
    cpreg_alpha_end: float
    cpreg_slot2_mult: float
    gate_mode: str


def _current_params() -> Params:
    # defaults alinhados com teus papers
    thr = _f(os.getenv("THRESHOLD"), 0.10)
    a0 = _f(os.getenv("CPREG_ALPHA_START"), _f(os.getenv("CP_ALPHA"), 0.07))
    a1 = _f(os.getenv("CPREG_ALPHA_END"), 0.10)
    m2 = _f(os.getenv("CPREG_SLOT2_MULT"), 0.85)
    gm = (os.getenv("GATE_MODE") or "").strip().lower() or "cp"
    return Params(threshold=thr, cpreg_alpha_start=a0, cpreg_alpha_end=a1, cpreg_slot2_mult=m2, gate_mode=gm)


def compute_next_params(summary: dict | None) -> dict:
    # tunáveis por env
    payout = _f(os.getenv("PAYOUT"), 0.8)
    target = _f(os.getenv("VOL_TARGET_TRADES_PER_DAY"), 2.0)  # <= k normalmente
    deadband = _f(os.getenv("VOL_DEADBAND"), 0.15)            # 15%
    wr_margin = _f(os.getenv("VOL_WR_MARGIN"), 0.01)          # acima do break-even
    step_alpha = _f(os.getenv("VOL_ALPHA_STEP"), 0.01)
    a_min = _f(os.getenv("VOL_ALPHA_MIN"), 0.05)
    a_max = _f(os.getenv("VOL_ALPHA_MAX"), 0.12)
    step_thr = _f(os.getenv("VOL_THR_STEP"), 0.01)
    thr_min = _f(os.getenv("VOL_THR_MIN"), 0.07)
    thr_max = _f(os.getenv("VOL_THR_MAX"), 0.15)

    cur = _current_params()

    if not summary:
        # sem summary: não mexe
        return {
            "recommended": {
                "threshold": cur.threshold,
                "cpreg_alpha_start": cur.cpreg_alpha_start,
                "cpreg_alpha_end": cur.cpreg_alpha_end,
                "cpreg_slot2_mult": cur.cpreg_slot2_mult,
            },
            "decision": "no_summary_keep_defaults",
        }

    trades = _extract_trades(summary)
    wins, trades_eval, wr = _extract_winrate(summary)
    ev_avg = _extract_ev_avg_trades(summary)
    be = _extract_break_even(summary, payout)

    low = target * (1.0 - deadband)
    high = target * (1.0 + deadband)

    # regras “sérias” (guard-rails)
    wr_ok = (trades_eval >= 10) and (wr >= (be + wr_margin))
    wr_bad = (trades_eval >= 10) and (wr < be)
    ev_bad = (ev_avg is not None) and (ev_avg < 0)

    new_thr = cur.threshold
    new_a0 = cur.cpreg_alpha_start
    new_a1 = cur.cpreg_alpha_end
    note = []

    # 1) Se qualidade ruim, aperta (menos trades)
    if wr_bad or ev_bad:
        new_a1 = max(a_min, new_a1 - step_alpha)
        new_thr = min(thr_max, new_thr + step_thr)
        note.append("tighten_due_to_quality")

    # 2) Se trades abaixo do alvo e qualidade ok, relaxa primeiro via alpha_end
    elif trades < low and wr_ok:
        if new_a1 < a_max:
            new_a1 = min(a_max, new_a1 + step_alpha)
            note.append("relax_alpha_end_for_more_trades")
        else:
            new_thr = max(thr_min, new_thr - step_thr)
            note.append("relax_threshold_for_more_trades")

    # 3) Se trades acima do alvo, aperta um pouco (protege EV)
    elif trades > high:
        new_a1 = max(a_min, new_a1 - step_alpha)
        note.append("tighten_alpha_end_for_less_trades")

    # mantém consistência: start <= end
    if new_a0 > new_a1:
        new_a0 = new_a1

    rec = {
        "threshold": round(float(new_thr), 4),
        "cpreg_alpha_start": round(float(new_a0), 4),
        "cpreg_alpha_end": round(float(new_a1), 4),
        "cpreg_slot2_mult": round(float(cur.cpreg_slot2_mult), 4),
        "target_trades_per_day": float(target),
        "observed_trades": int(trades),
        "observed_wins_eval": int(wins),
        "observed_trades_eval": int(trades_eval),
        "observed_win_rate_eval": float(round(wr, 6)),
        "break_even": float(round(be, 6)),
        "observed_ev_avg_trades": None if ev_avg is None else float(round(ev_avg, 6)),
        "notes": note or ["no_change"],
    }
    return {"recommended": rec, "decision": ",".join(note) if note else "no_change"}


def main() -> None:
    # silencioso no stdout (só JSON), logs no stderr
    min_obs = _i(os.getenv("VOL_MIN_OBS_TODAY"), 60)
    day_used, summary = _find_best_summary_day(min_obs)
    res = compute_next_params(summary)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "based_on_day": day_used,
        "summary_path": str(_summary_path(day_used)),
        "recommended": res["recommended"],
        "decision": res["decision"],
    }

    # escreve arquivos em runs/
    out_cur = Path("runs") / "auto_params.json"
    out_hist = Path("runs") / f"auto_params_{datetime.now().strftime('%Y%m%d')}.json"
    _write_json_atomic(out_cur, payload)
    _write_json_atomic(out_hist, payload)

    # stdout: JSON puro para wrappers
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
'''

OBSERVE_LOOP_AUTO_PS1 = r'''param(
  [switch]$Once,
  [int]$TopK = 0
)

# Wrapper do P12:
# 1) roda auto_volume e lê JSON
# 2) seta env vars (THRESHOLD / CPREG_ALPHA_START/END etc)
# 3) chama observe_loop.ps1 original

$ErrorActionPreference = "Stop"

Write-Host "[P12] auto volume: computing params..." -ForegroundColor Cyan

# garante que python da venv existe
$py = ".\.venv\Scripts\python.exe"
if (!(Test-Path $py)) { throw "Python venv não encontrado em $py" }

# roda e captura JSON (stdout puro)
$json = & $py -m natbin.auto_volume
if (!$json) { throw "auto_volume não retornou JSON" }

$obj = $json | ConvertFrom-Json

$rec = $obj.recommended

# aplica env vars (válidas só para esta sessão)
if ($rec.threshold -ne $null) { $env:THRESHOLD = [string]$rec.threshold }
if ($rec.cpreg_alpha_start -ne $null) { $env:CPREG_ALPHA_START = [string]$rec.cpreg_alpha_start }
if ($rec.cpreg_alpha_end -ne $null) { $env:CPREG_ALPHA_END = [string]$rec.cpreg_alpha_end }
if ($rec.cpreg_slot2_mult -ne $null) { $env:CPREG_SLOT2_MULT = [string]$rec.cpreg_slot2_mult }

# garante CPREG ligado (se você estiver usando gate_mode=cp)
$env:CPREG_ENABLE = "1"
# fallback razoável:
if (!$env:CP_ALPHA) { $env:CP_ALPHA = $env:CPREG_ALPHA_START }

Write-Host ("[P12] applied: THRESHOLD={0} CPREG_ALPHA_START={1} CPREG_ALPHA_END={2} SLOT2_MULT={3}" -f `
  $env:THRESHOLD, $env:CPREG_ALPHA_START, $env:CPREG_ALPHA_END, $env:CPREG_SLOT2_MULT) -ForegroundColor Green

# chama o observe_loop original
$loop = ".\scripts\scheduler\observe_loop.ps1"
if (!(Test-Path $loop)) { throw "observe_loop.ps1 não encontrado em $loop" }

if ($TopK -gt 0) {
  & pwsh -ExecutionPolicy Bypass -File $loop -Once:$Once -TopK $TopK
} else {
  & pwsh -ExecutionPolicy Bypass -File $loop -Once:$Once
}
exit $LASTEXITCODE
'''


def main() -> None:
    root = repo_root()

    # 1) cria o natbin/auto_volume.py
    auto_p = root / "src" / "natbin" / "auto_volume.py"
    auto_p.parent.mkdir(parents=True, exist_ok=True)
    backup_if_exists(auto_p)
    auto_p.write_text(AUTO_VOLUME_PY, encoding="utf-8")
    py_compile.compile(str(auto_p), doraise=True)
    print(f"[P12] OK wrote {auto_p}")

    # 2) cria wrapper observe_loop_auto.ps1
    wrap_p = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    wrap_p.parent.mkdir(parents=True, exist_ok=True)
    backup_if_exists(wrap_p)
    wrap_p.write_text(OBSERVE_LOOP_AUTO_PS1, encoding="utf-8")
    print(f"[P12] OK wrote {wrap_p}")

    print("[P12] Done.")


if __name__ == "__main__":
    main()