from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class PatchResult:
    changed: bool
    path: Path
    backup: Path


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(12):
        if (cur / "src" / "natbin" / "observe_signal_topk_perday.py").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise SystemExit("[P10] Nao achei repo root (precisa ter src/natbin/observe_signal_topk_perday.py)")


def patch_file(path: Path) -> PatchResult:
    text = path.read_text(encoding="utf-8")
    orig = text

    # 1) Inject helper functions before def main()
    if "# --- P10: DAILY SUMMARY" not in text:
        m = re.search(r"^def\s+main\s*\(\s*\)\s*(?:->\s*None\s*)?:\s*$", text, flags=re.M)
        if not m:
            raise SystemExit("[P10] Nao achei 'def main()' para injetar o bloco P10")

        insert_at = m.start()

        block = '''

# --- P10: DAILY SUMMARY (runs/daily_summary_YYYYMMDD.json) ---


def _p10_mean(xs: list[float | None]) -> float | None:
    import math

    vals: list[float] = []
    for x in xs:
        if x is None:
            continue
        try:
            fx = float(x)
        except Exception:
            continue
        if math.isnan(fx) or math.isinf(fx):
            continue
        vals.append(fx)
    if not vals:
        return None
    return float(sum(vals) / len(vals))



def write_daily_summary(
    *,
    day: str,
    tz: ZoneInfo,
    asset: str,
    dataset_path: str,
    db_path: str = "runs/live_signals.sqlite3",
    out_dir: str = "runs",
    gate_mode: str | None = None,
    meta_model: str | None = None,
    thresh_on: str | None = None,
    threshold: float | None = None,
    k: int | None = None,
    payout: float | None = None,
) -> str:
    """Gera um resumo diário em JSON a partir do signals_v2.

    Escreve: runs/daily_summary_YYYYMMDD.json (YYYYMMDD sem hífens).

    Métricas:
      - counts por hora (observações e trades)
      - EV médio (trades e geral)
      - win-rate por slot (1o trade do dia, 2o trade do dia, ...)

    Observações:
      - win-rate é calculado via dataset_path (ts -> y_open_close).
      - se y_open_close estiver ausente/NaN para algum ts, aquele trade não entra no win-rate.
    """

    out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)

    # nome do arquivo conforme pedido: daily_summary_YYYYMMDD.json
    ymd = day.replace("-", "")
    out_path = out_base / f"daily_summary_{ymd}.json"
    tmp_path = out_base / f"daily_summary_{ymd}.tmp"

    # --- Lê sinais do sqlite ---
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT ts, action, executed_today, score, ev, payout, threshold, k, gate_mode, meta_model, thresh_on "
            "FROM signals_v2 WHERE day=? ORDER BY ts",
            (day,),
        ).fetchall()
    finally:
        con.close()

    hours = [f"{h:02d}" for h in range(24)]
    obs_by_hour: dict[str, int] = {h: 0 for h in hours}
    trades_by_hour: dict[str, dict[str, int]] = {h: {"total": 0, "CALL": 0, "PUT": 0} for h in hours}

    trades: list[dict[str, Any]] = []
    ev_all: list[float | None] = []
    ev_trades: list[float | None] = []

    last_row: dict[str, Any] | None = None

    for r in rows:
        d = dict(r)
        last_row = d
        ts = int(d.get("ts") or 0)
        h = datetime.fromtimestamp(ts, tz=tz).strftime("%H") if ts else "00"
        if h not in obs_by_hour:
            obs_by_hour[h] = 0
            trades_by_hour[h] = {"total": 0, "CALL": 0, "PUT": 0}
        obs_by_hour[h] += 1

        ev_all.append(d.get("ev"))

        action = str(d.get("action") or "").upper()
        if action in ("CALL", "PUT"):
            trades.append(d)
            ev_trades.append(d.get("ev"))
            trades_by_hour[h]["total"] += 1
            trades_by_hour[h][action] += 1

    # --- Lê labels do dataset ---
    label_map: dict[int, float] = {}
    try:
        dlab = pd.read_csv(dataset_path, usecols=["ts", "y_open_close"])
        dlab = dlab.dropna(subset=["ts"])  # ts sempre deve existir
        dlab["ts"] = dlab["ts"].astype(int)
        for ts, y in zip(dlab["ts"].tolist(), dlab["y_open_close"].tolist()):
            try:
                fy = float(y)
            except Exception:
                continue
            label_map[int(ts)] = fy
    except Exception:
        label_map = {}

    # --- Win-rate por slot ---
    slot_stats: dict[str, dict[str, Any]] = {}
    total_eval = 0
    total_wins = 0

    for tr in trades:
        ts = int(tr.get("ts") or 0)
        y = label_map.get(ts, None)
        if y is None:
            continue
        try:
            fy = float(y)
        except Exception:
            continue
        if np.isnan(fy):
            continue
        lbl = 1 if fy >= 0.5 else 0
        action = str(tr.get("action") or "").upper()
        pred = 1 if action == "CALL" else 0
        won = 1 if pred == lbl else 0

        slot = int(tr.get("executed_today") or 0)
        if slot < 1:
            slot = 1
        sk = str(slot)
        st = slot_stats.setdefault(
            sk,
            {
                "slot": slot,
                "trades": 0,
                "wins": 0,
                "win_rate": None,
                "ev_avg": None,
                "score_avg": None,
            },
        )
        st["trades"] += 1
        st["wins"] += won
        total_eval += 1
        total_wins += won

        st.setdefault("_ev", []).append(tr.get("ev"))
        st.setdefault("_score", []).append(tr.get("score"))

    for sk, st in slot_stats.items():
        trades_n = int(st.get("trades") or 0)
        wins_n = int(st.get("wins") or 0)
        st["win_rate"] = float(wins_n / trades_n) if trades_n > 0 else None
        st["ev_avg"] = _p10_mean(st.pop("_ev", []))
        st["score_avg"] = _p10_mean(st.pop("_score", []))

    winrate_by_slot = {k: slot_stats[k] for k in sorted(slot_stats.keys(), key=lambda s: int(s))}

    # metadata default (se não vier do caller, tenta do último row)
    if last_row:
        gate_mode = gate_mode or str(last_row.get("gate_mode") or "")
        meta_model = meta_model or str(last_row.get("meta_model") or "")
        thresh_on = thresh_on or str(last_row.get("thresh_on") or "")
        try:
            threshold = float(threshold if threshold is not None else last_row.get("threshold"))
        except Exception:
            threshold = None
        try:
            k = int(k if k is not None else last_row.get("k"))
        except Exception:
            k = None
        try:
            payout = float(payout if payout is not None else last_row.get("payout"))
        except Exception:
            payout = None

    break_even = None
    if payout is not None:
        try:
            break_even = float(1.0 / (1.0 + float(payout)))
        except Exception:
            break_even = None

    summary = {
        "day": day,
        "asset": asset,
        "generated_at": datetime.now(tz=tz).isoformat(timespec="seconds"),
        "db_path": db_path,
        "dataset_path": dataset_path,
        "k": k,
        "gate_mode": gate_mode,
        "meta_model": meta_model,
        "thresh_on": thresh_on,
        "threshold": threshold,
        "payout": payout,
        "break_even": break_even,
        "rows_total": int(len(rows)),
        "trades_total": int(len(trades)),
        "trades_eval_total": int(total_eval),
        "wins_eval_total": int(total_wins),
        "win_rate_eval_total": float(total_wins / total_eval) if total_eval > 0 else None,
        "ev_avg_all": _p10_mean(ev_all),
        "ev_avg_trades": _p10_mean(ev_trades),
        "observations_by_hour": obs_by_hour,
        "trades_by_hour": trades_by_hour,
        "winrate_by_slot": winrate_by_slot,
    }

    tmp_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(out_path)
    return str(out_path)


# --- /P10 ---

'''

        text = text[:insert_at] + block + text[insert_at:]

    # 2) Ensure main() calls write_daily_summary()
    if "# --- P10: daily summary call ---" not in text:
        pat = r"^(\s*)write_sqlite_signal\(row\)\s*$"
        m2 = re.search(pat, text, flags=re.M)
        if not m2:
            raise SystemExit("[P10] Nao achei 'write_sqlite_signal(row)' para injetar chamada")
        indent = m2.group(1)

        call_lines = [
            "",
            f"{indent}# --- P10: daily summary call ---",
            f"{indent}summary_path = ''",
            f"{indent}try:",
            f"{indent}    summary_path = write_daily_summary(",
            f"{indent}        day=day,",
            f"{indent}        tz=tz,",
            f"{indent}        asset=asset,",
            f"{indent}        dataset_path=dataset_path,",
            f"{indent}        gate_mode=gate_used,",
            f"{indent}        meta_model=meta_model_type,",
            f"{indent}        thresh_on=thresh_on,",
            f"{indent}        threshold=float(thr),",
            f"{indent}        k=int(k),",
            f"{indent}        payout=float(payout),",
            f"{indent}    )",
            f"{indent}except Exception as e:",
            f"{indent}    print(f\"[WARN] daily_summary failed: {{e}}\")",
            f"{indent}if summary_path:",
            f"{indent}    print(f\"summary_ok: {{summary_path}}\")",
            f"{indent}# --- /P10 ---",
        ]
        call_block = "\n".join(call_lines)

        insert_pos = m2.end()
        text = text[:insert_pos] + call_block + text[insert_pos:]

    changed = text != orig

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak_{ts}")
    shutil.copy2(path, backup)

    path.write_text(text, encoding="utf-8")

    # basic syntax check on target file
    import py_compile

    py_compile.compile(str(path), doraise=True)

    return PatchResult(changed=changed, path=path, backup=backup)



def main() -> None:
    repo = find_repo_root(Path.cwd())
    target = repo / "src" / "natbin" / "observe_signal_topk_perday.py"
    print(f"[P10] Repo: {repo}")
    print(f"[P10] File: {target}")
    res = patch_file(target)
    print(f"[P10] Backup: {res.backup}")
    print("[P10] OK - daily_summary habilitado.")


if __name__ == "__main__":
    main()
