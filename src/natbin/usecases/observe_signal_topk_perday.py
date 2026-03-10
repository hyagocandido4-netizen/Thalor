from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import pickle
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo

from natbin.domain.gate_meta import GATE_VERSION, META_FEATURES, compute_scores, train_base_cal_iso_meta
from natbin.envutil import env_bool, env_float, env_int
from .runtime.gates.cpreg import maybe_apply_cp_alpha_env
from natbin.runtime_migrations import ensure_executed_state_db as _ensure_executed_state_db
from natbin.runtime_migrations import ensure_signals_v2 as _ensure_signals_v2
from natbin.runtime_repos import RuntimeTradeLedger, SignalsRepository, preserve_existing_trade
from natbin.runtime_observability import (
    append_incident_event,
    build_incident_from_decision,
    write_detailed_decision_snapshot,
    write_latest_decision_snapshot,
)
from natbin.summary_paths import daily_summary_path, sanitize_asset
from natbin.runtime_scope import live_signals_csv_path as scoped_live_signals_csv_path


BASE_FIELDS = [
    "dt_local",
    "day",
    "ts",
    "interval_sec",
    "proba_up",
    "conf",
    "score",
    "gate_mode",
    "regime_ok",
    "thresh_on",
    "threshold",
    "k",
    "rank_in_day",
    "executed_today",
    "budget_left",
    "action",
    "reason",
    "blockers",
    "close",
    "payout",
    "ev",
    "market_context_stale",
    "market_context_fail_closed",
]
META_FIELDS = [
    "asset",
    "model_version",
    "train_rows",
    "train_end_ts",
    "best_source",
    "tune_dir",
    "feat_hash",
    "gate_version",
    "meta_model",
]
ALL_FIELDS = BASE_FIELDS + META_FIELDS



def _env_path(name: str) -> str | None:
    raw = os.getenv(name)
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _resolve_signals_db_path(default: str | Path = 'runs/live_signals.sqlite3') -> Path:
    override = _env_path('THALOR_SIGNALS_DB_PATH') or _env_path('SIGNALS_DB_PATH')
    p = Path(override) if override else Path(default)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def _resolve_state_db_path(default: str | Path = 'runs/live_topk_state.sqlite3') -> Path:
    override = _env_path('THALOR_STATE_DB_PATH') or _env_path('STATE_DB_PATH')
    p = Path(override) if override else Path(default)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def _runtime_ledger() -> RuntimeTradeLedger:
    return RuntimeTradeLedger(
        signals_db=_resolve_signals_db_path('runs/live_signals.sqlite3'),
        state_db=_resolve_state_db_path('runs/live_topk_state.sqlite3'),
        default_interval=env_int("SIGNALS_INTERVAL_SEC", "300"),
    )


def load_cfg() -> tuple[dict[str, Any], dict[str, Any]]:
    """Load observer configuration.

    The runtime control plane uses **config v2** (config/base.yaml + env overrides).
    The legacy observer used to read a hardcoded ``config.yaml`` only.

    Package Q makes the observer **scope-aware** and **config-aware** by:
    - Loading the resolved v2 config via `natbin.config.loader.load_resolved_config`
      using `THALOR_CONFIG_PATH` / `--config` selection.
    - Falling back to legacy ``config.yaml`` only when needed.

    Returns:
        (cfg_dict, best_dict)
    """
    # Prefer modern resolved config (Package M+).
    try:
        from .config.loader import load_resolved_config
        from .config.paths import resolve_config_path, resolve_repo_root

        repo_root = resolve_repo_root(repo_root=None, config_path=None)
        cfg_path = resolve_config_path(repo_root=repo_root, config_path=None)

        # Use explicit scope overrides when provided (portfolio runner sets these).
        asset_env = os.getenv("ASSET") or None
        interval_env = os.getenv("INTERVAL_SEC") or None
        interval_sec_env = int(interval_env) if interval_env and interval_env.strip().isdigit() else None

        rcfg = load_resolved_config(
            config_path=cfg_path,
            repo_root=repo_root,
            asset=asset_env,
            interval_sec=interval_sec_env,
        )

        best: dict[str, Any] = {
            "threshold": float(rcfg.decision.threshold),
            "thresh_on": str(rcfg.decision.thresh_on),
            "gate_mode": str(rcfg.decision.gate_mode),
            "meta_model": str(rcfg.decision.meta_model),
            "tune_dir": str(getattr(rcfg.decision, "tune_dir", "") or ""),
            "bounds": dict(getattr(rcfg.decision, "bounds", {}) or {}),
            # Safe default; can be overridden by TOPK_K env / CLI.
            "k": int(os.getenv("TOPK_K") or 3),
        }

        cfg: dict[str, Any] = {
            "data": {
                "asset": str(rcfg.asset),
                "interval_sec": int(rcfg.interval_sec),
                "timezone": str(rcfg.timezone),
            },
            "phase2": {"dataset_path": str(rcfg.data.dataset_path)},
            "best": best,
        }

        # Backward compatibility: if tune_dir/bounds are not present in v2 config,
        # try to read them from legacy root config.yaml (when available).
        legacy_path = Path(repo_root) / "config.yaml"
        if legacy_path.exists() and (not best.get("tune_dir") or not best.get("bounds")):
            try:
                legacy_cfg = yaml.safe_load(legacy_path.read_text(encoding="utf-8")) or {}
                legacy_best = legacy_cfg.get("best") or {}
                if not best.get("tune_dir") and legacy_best.get("tune_dir"):
                    best["tune_dir"] = str(legacy_best.get("tune_dir"))
                if (not best.get("bounds")) and isinstance(legacy_best.get("bounds"), dict):
                    best["bounds"] = dict(legacy_best.get("bounds") or {})
            except Exception:
                # Never break runtime because of optional legacy fallback.
                pass

        return cfg, best
    except Exception:
        # Fall back to the original legacy behavior.
        pass

    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    best = cfg.get("best") or {}
    return cfg, best



def get_model_version() -> str:
    try:
        import subprocess

        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode("utf-8").strip()
    except Exception:
        return "unknown"


def feat_hash(feat: list[str]) -> str:
    s = ",".join(feat).encode("utf-8")
    return hashlib.sha1(s).hexdigest()[:12]


def sanitize_asset(asset: str) -> str:
    out = []
    for ch in asset:
        if ch.isalnum() or ch in ("-", "_"):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


def cache_paths(asset: str, interval_sec: int | None = None) -> tuple[Path, Path]:
    a = sanitize_asset(asset)
    stem = f"model_cache_{a}" if interval_sec is None else f"model_cache_{a}_{int(interval_sec)}s"
    pkl = Path("runs") / f"{stem}.pkl"
    meta = Path("runs") / f"{stem}.json"
    return pkl, meta


def load_cache(asset: str, interval_sec: int) -> dict[str, Any] | None:
    pkl, meta = cache_paths(asset, interval_sec)
    if (not pkl.exists() or not meta.exists()) and interval_sec is not None:
        pkl, meta = cache_paths(asset, None)
    if not pkl.exists() or not meta.exists():
        return None
    try:
        payload = pickle.loads(pkl.read_bytes())
        m = json.loads(meta.read_text(encoding="utf-8"))
        payload["meta"] = m
        return payload
    except Exception:
        return None


def save_cache(asset: str, interval_sec: int, payload: dict[str, Any]) -> None:
    pkl, meta = cache_paths(asset, interval_sec)
    pkl.parent.mkdir(parents=True, exist_ok=True)

    m = payload.get("meta") or {}
    meta.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    payload2 = dict(payload)
    payload2.pop("meta", None)
    pkl.write_bytes(pickle.dumps(payload2))


def should_retrain(
    meta: dict[str, Any] | None,
    *,
    train_end_ts: int,
    best_source: str,
    fhash: str,
    interval_sec: int,
    meta_model_type: str,
) -> bool:
    if not meta:
        return True

    last_ts = int(meta.get("train_end_ts") or 0)
    last_best = str(meta.get("best_source") or "")
    last_fhash = str(meta.get("feat_hash") or "")
    last_gate = str(meta.get("gate_version") or "")
    last_mm = str(meta.get("meta_model") or "")
    try:
        last_interval = int(meta.get("interval_sec") or 0)
    except Exception:
        last_interval = 0

    if last_interval != int(interval_sec):
        return True
    if last_best != best_source:
        return True
    if last_fhash != fhash:
        return True
    if last_gate != GATE_VERSION:
        return True
    if last_mm != meta_model_type:
        return True

    retrain_every = env_int("RETRAIN_EVERY_CANDLES", "12")
    min_delta = retrain_every * interval_sec
    return (train_end_ts - last_ts) >= min_delta


def ensure_signals_v2(con: sqlite3.Connection) -> None:
    """Compatibility wrapper over the explicit runtime migration module.

    Package B keeps the public helper name stable while moving schema knowledge
    to :mod:`natbin.runtime_migrations`.
    """
    _ensure_signals_v2(con, default_interval=env_int("SIGNALS_INTERVAL_SEC", "300"))


TRADE_ACTIONS = {"CALL", "PUT"}


def _signal_pk(row: dict[str, Any]) -> tuple[str, str, int, int]:
    day = str(row.get("day") or "")
    asset = str(row.get("asset") or "")
    try:
        interval_sec = int(row.get("interval_sec") or 0)
    except Exception:
        interval_sec = 0
    try:
        ts = int(row.get("ts") or 0)
    except Exception:
        ts = 0
    return day, asset, interval_sec, ts


def write_sqlite_signal(row: dict[str, Any], db_path: str = "runs/live_signals.sqlite3") -> None:
    repo = SignalsRepository(db_path=_resolve_signals_db_path(db_path), default_interval=env_int("SIGNALS_INTERVAL_SEC", "300"))
    repo.write_row(row)


def _default_live_signals_csv_path(row: dict[str, Any]) -> str:
    day = str(row.get("day") or "")
    asset = str(row.get("asset") or "UNKNOWN")
    try:
        interval_sec = int(row.get("interval_sec") or env_int("SIGNALS_INTERVAL_SEC", "300"))
    except Exception:
        interval_sec = env_int("SIGNALS_INTERVAL_SEC", "300")
    if day:
        return str(scoped_live_signals_csv_path(day=day, asset=asset, interval_sec=interval_sec, out_dir="runs"))
    asset_tag = sanitize_asset(asset)
    return str(Path("runs") / f"live_signals_v2_{asset_tag}_{int(interval_sec)}s.csv")


def _parse_builtin_live_signals_filename(name: str) -> tuple[str | None, str | None, int | None]:
    """Parse standard live_signals_v2 filenames.

    Returns (day_tag_yyyymmdd_or_None, sanitized_asset_or_None, interval_sec_or_None).
    If the name does not look like a built-in filename, returns (None, None, None).
    """
    m = re.match(r"^live_signals_v2_(\d{8})_(.+)_(\d+)s\.csv$", name)
    if m:
        day_tag, asset_tag, interval_tag = m.groups()
        try:
            return day_tag, asset_tag, int(interval_tag)
        except Exception:
            return day_tag, asset_tag, None
    m = re.match(r"^live_signals_v2_(\d{8})\.csv$", name)
    if m:
        return m.group(1), None, None
    m = re.match(r"^live_signals_v2_(.+)_(\d+)s\.csv$", name)
    if m:
        asset_tag, interval_tag = m.groups()
        try:
            return None, asset_tag, int(interval_tag)
        except Exception:
            return None, asset_tag, None
    return None, None, None


def _resolve_live_signals_csv_path(row: dict[str, Any]) -> str:
    default_path = _default_live_signals_csv_path(row)
    override = os.getenv("LIVE_SIGNALS_PATH", "").strip()
    if not override:
        return default_path

    # For built-in daily/scoped filenames, prefer the row-derived day/asset/interval.
    # This prevents midnight contamination where the scheduler path is already on the
    # new day while the evaluated candle still belongs to the previous day.
    row_day = str(row.get("day") or "").replace("-", "")
    row_asset = sanitize_asset(str(row.get("asset") or "UNKNOWN"))
    try:
        row_interval = int(row.get("interval_sec") or env_int("SIGNALS_INTERVAL_SEC", "300"))
    except Exception:
        row_interval = env_int("SIGNALS_INTERVAL_SEC", "300")

    try:
        name = Path(override).name
        o_day, o_asset, o_interval = _parse_builtin_live_signals_filename(name)
        is_builtin = name.startswith("live_signals_v2_") and name.endswith(".csv")
        if is_builtin:
            if o_day and row_day and o_day != row_day:
                return default_path
            if o_asset and row_asset and o_asset != row_asset:
                return default_path
            if o_interval and int(o_interval) != int(row_interval):
                return default_path
    except Exception:
        return default_path

    return override


def append_csv(row: dict[str, Any]) -> str:
    path = _resolve_live_signals_csv_path(row)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def read_header(pp: Path) -> list[str] | None:
        if not pp.exists():
            return None
        try:
            with pp.open("r", encoding="utf-8", newline="") as f:
                r = csv.reader(f)
                return next(r, None)
        except Exception:
            return None

    def read_rows(pp: Path) -> list[dict[str, Any]]:
        if not pp.exists():
            return []
        try:
            with pp.open("r", encoding="utf-8", newline="") as f:
                r = csv.DictReader(f)
                rows: list[dict[str, Any]] = []
                for rr in r:
                    if not rr:
                        continue
                    rows.append({k: rr.get(k, "") for k in ALL_FIELDS})
                return rows
        except Exception:
            return []

    def normalize_ts(v: Any) -> str:
        try:
            return str(int(float(str(v).strip())))
        except Exception:
            return str(v or "")

    def normalize_interval(v: Any) -> str:
        try:
            return str(int(float(str(v).strip())))
        except Exception:
            return str(v or "")

    header = read_header(p)
    if header and header != ALL_FIELDS:
        p = p.with_name(p.stem + "_meta" + p.suffix)

    incoming = {k: row.get(k, "") for k in ALL_FIELDS}
    day, asset, interval_sec, ts = _signal_pk(row)
    target_key = (day, asset, str(interval_sec), str(ts))
    last_err: Exception | None = None

    for attempt in range(8):
        try:
            rows = read_rows(p)
            idx = None
            existing_action = None
            for i, rr in enumerate(rows):
                rr_key = (
                    str(rr.get("day") or ""),
                    str(rr.get("asset") or ""),
                    normalize_interval(rr.get("interval_sec")),
                    normalize_ts(rr.get("ts")),
                )
                if rr_key == target_key:
                    idx = i
                    existing_action = rr.get("action")
                    break

            if idx is not None and preserve_existing_trade(existing_action, incoming.get("action")):
                return str(p)

            if idx is None:
                rows.append(incoming)
            else:
                rows[idx] = incoming

            tmp = p.with_suffix(p.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=ALL_FIELDS)
                w.writeheader()
                for rr in rows:
                    w.writerow({k: rr.get(k, "") for k in ALL_FIELDS})
            tmp.replace(p)
            return str(p)
        except PermissionError as e:
            last_err = e
            time.sleep(0.25 * (attempt + 1))

    if last_err is not None:
        raise last_err
    raise RuntimeError(f"append_csv failed for {p}")

def ensure_state_db(con: sqlite3.Connection) -> None:
    """Compatibility wrapper over the explicit runtime migration module."""
    _ensure_executed_state_db(con, default_interval=env_int("SIGNALS_INTERVAL_SEC", "300"))


def state_path() -> Path:
    return _resolve_state_db_path(Path('runs') / 'live_topk_state.sqlite3')


def signals_db_path() -> Path:
    return _resolve_signals_db_path(Path('runs') / 'live_signals.sqlite3')


def _fetch_trade_rows_from_signals(asset: str, interval_sec: int, day: str, *, ts: int | None = None) -> list[sqlite3.Row]:
    return _runtime_ledger().signals.fetch_trade_rows(asset, interval_sec, day, ts=ts)


def _heal_state_from_signals(asset: str, interval_sec: int, day: str, *, ts: int | None = None) -> int:
    return _runtime_ledger().heal_state_from_signals(asset, interval_sec, day, ts=ts, log=True)


def _count_state_only(asset: str, interval_sec: int, day: str) -> int:
    return _runtime_ledger().state.count_day(asset, interval_sec, day)


def _last_state_ts_only(asset: str, interval_sec: int, day: str) -> int | None:
    return _runtime_ledger().state.last_ts(asset, interval_sec, day)


def _already_state_only(asset: str, interval_sec: int, day: str, ts: int) -> bool:
    return _runtime_ledger().state.exists(asset, interval_sec, day, int(ts))


def executed_today_count(asset: str, interval_sec: int, day: str) -> int:
    return _runtime_ledger().executed_today_count(asset, interval_sec, day)


def last_executed_ts(asset: str, interval_sec: int, day: str) -> int | None:
    return _runtime_ledger().last_executed_ts(asset, interval_sec, day)


def already_executed(asset: str, interval_sec: int, day: str, ts: int) -> bool:
    return _runtime_ledger().already_executed(asset, interval_sec, day, int(ts))


def mark_executed(asset: str, interval_sec: int, day: str, ts: int, action: str, conf: float, score: float) -> None:
    _runtime_ledger().mark_executed(asset, interval_sec, day, int(ts), action, float(conf), float(score))

def make_regime_mask(df: pd.DataFrame, bounds: dict[str, float]) -> np.ndarray:
    vol = df["f_vol48"].to_numpy(dtype=float)
    bb = df["f_bb_width20"].to_numpy(dtype=float)
    atr = df["f_atr14"].to_numpy(dtype=float)

    m = np.ones(len(df), dtype=bool)
    m &= vol >= float(bounds["vol_lo"])
    m &= vol <= float(bounds["vol_hi"])
    m &= bb >= float(bounds["bb_lo"])
    m &= bb <= float(bounds["bb_hi"])
    m &= atr >= float(bounds["atr_lo"])
    m &= atr <= float(bounds["atr_hi"])
    return m




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
    interval_sec: int,
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

    O arquivo de saída é escopado por asset + interval_sec para evitar
    contaminação entre múltiplos timeframes do mesmo ativo.
    """

    out_base = Path(out_dir)
    out_base.mkdir(parents=True, exist_ok=True)

    out_path = daily_summary_path(day=day, asset=asset, interval_sec=int(interval_sec), out_dir=out_base)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        if str(asset or "").strip():
            try:
                rows = con.execute(
                    "SELECT ts, interval_sec, action, executed_today, score, ev, payout, threshold, k, gate_mode, meta_model, thresh_on "
                    "FROM signals_v2 WHERE day=? AND asset=? AND interval_sec=? ORDER BY ts",
                    (day, str(asset), int(interval_sec)),
                ).fetchall()
            except Exception:
                rows = con.execute(
                    "SELECT ts, action, executed_today, score, ev, payout, threshold, k, gate_mode, meta_model, thresh_on "
                    "FROM signals_v2 WHERE day=? AND asset=? ORDER BY ts",
                    (day, str(asset)),
                ).fetchall()
        else:
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
    by_hour: dict[str, dict[str, Any]] = {h: {"trades": 0, "wins": 0, "ev_sum": 0.0} for h in hours}

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

    label_map: dict[int, float] = {}
    try:
        dlab = pd.read_csv(dataset_path, usecols=["ts", "y_open_close"])
        dlab = dlab.dropna(subset=["ts"])
        dlab["ts"] = dlab["ts"].astype(int)
        for ts, y in zip(dlab["ts"].tolist(), dlab["y_open_close"].tolist()):
            try:
                fy = float(y)
            except Exception:
                continue
            label_map[int(ts)] = fy
    except Exception:
        label_map = {}

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

        try:
            _dt2 = datetime.fromtimestamp(ts, tz=tz)
            _hh2 = f"{_dt2.hour:02d}"
        except Exception:
            _hh2 = "??"
        _ev_val = float(tr.get("ev") or 0.0)
        _bh = by_hour.setdefault(_hh2, {"trades": 0, "wins": 0, "ev_sum": 0.0})
        _bh["trades"] += 1
        _bh["wins"] += int(won)
        _bh["ev_sum"] += _ev_val

        st.setdefault("_ev", []).append(tr.get("ev"))
        st.setdefault("_score", []).append(tr.get("score"))

    for sk, st in slot_stats.items():
        trades_n = int(st.get("trades") or 0)
        wins_n = int(st.get("wins") or 0)
        st["win_rate"] = float(wins_n / trades_n) if trades_n > 0 else None
        st["ev_avg"] = _p10_mean(st.pop("_ev", []))
        st["score_avg"] = _p10_mean(st.pop("_score", []))

    winrate_by_slot = {k: slot_stats[k] for k in sorted(slot_stats.keys(), key=lambda s: int(s))}

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

    for _hh, h in by_hour.items():
        n = int(h.get("trades") or 0)
        w = int(h.get("wins") or 0)
        evs = float(h.get("ev_sum") or 0.0)
        h["losses"] = max(0, n - w)
        h["win_rate"] = (w / n) if n > 0 else None
        h["ev_mean"] = (evs / n) if n > 0 else None
        h.pop("ev_sum", None)

    summary = {
        "day": day,
        "asset": asset,
        "interval_sec": int(interval_sec),
        "timezone": getattr(tz, "key", str(tz)),
        "summary_version": 2,
        "generated_at": datetime.now(tz=tz).isoformat(timespec="seconds"),
        "timezone": str(getattr(tz, "key", str(tz))),
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
        "by_hour": by_hour,
        "winrate_by_slot": winrate_by_slot,
    }

    tmp_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(out_path)
    return str(out_path)


# --- /P10 ---

def main() -> None:
    cfg, best = load_cfg()
    if not best:
        raise RuntimeError("Nao achei bloco 'best' em config.yaml. Rode o tune.")

    tz = ZoneInfo(cfg.get("data", {}).get("timezone", "UTC"))
    asset = cfg.get("data", {}).get("asset", "UNKNOWN")
    interval_sec = int(cfg.get("data", {}).get("interval_sec", 300))



    thr = float(best.get("threshold", 0.60))
    # --- P8c: THRESHOLD env override (fixed order) ---
    _thr_env = os.getenv("THRESHOLD", "").strip()
    if _thr_env:
        try:
            thr = float(_thr_env)
        except Exception:
            pass
    # --- /P8c ---

    bounds = best.get("bounds") or {}
    tune_dir = str(best.get("tune_dir") or "")
    best_source = tune_dir or "unknown"

    # defaults (env tem prioridade)
    k_env = os.getenv("TOPK_K", "").strip()
    try:
        k = int(k_env) if k_env else int(best.get("k", 1))
    except Exception:
        k = 1
    if k < 1:
        k = 1

    thresh_on_env = os.getenv("THRESH_ON", "").strip()
    thresh_on = (thresh_on_env or str(best.get("thresh_on", "score"))).strip().lower()
    if thresh_on not in ("score", "conf", "ev"):
        thresh_on = "score"

    gate_env = os.getenv("GATE_MODE", "").strip()
    gate_mode = (gate_env or str(best.get("gate_mode", "meta"))).strip().lower()
    # Config v2 / legacy compat: some sources used composite labels.
    if gate_mode in ("cp_meta_iso", "cp_meta", "cp-meta-iso"):
        gate_mode = "cp"
    elif gate_mode in ("meta_iso", "meta-iso"):
        gate_mode = "meta"
    if gate_mode not in ("meta", "iso", "conf", "cp"):
        gate_mode = "meta"

    meta_model_env = os.getenv("META_MODEL", "").strip()
    meta_model_type = (meta_model_env or str(best.get("meta_model", "hgb"))).strip().lower()
    if meta_model_type not in ("logreg", "hgb"):
        meta_model_type = "hgb"

    dataset_path = (
        os.getenv("DATASET_PATH")
        or os.getenv("THALOR__DATA__DATASET_PATH")
        or cfg.get("phase2", {}).get("dataset_path")
        or "data/dataset_phase2.csv"
    )
    dataset_path = str(dataset_path)
    if not Path(dataset_path).exists():
        raise FileNotFoundError(f"dataset_not_found:{dataset_path} (run make_dataset before observe)")

    df = pd.read_csv(dataset_path)
    if len(df) == 0:
        raise ValueError("Dataset vazio.")

    feat = [c for c in df.columns if c.startswith("f_")]
    for req in ("ts", "y_open_close", "f_vol48", "f_bb_width20", "f_atr14"):
        if req not in df.columns:
            raise ValueError(f"Dataset sem coluna obrigatoria: {req}")

    # candle atual
    last_ts = int(df["ts"].iloc[-1])
    last_dt = datetime.fromtimestamp(last_ts, tz=tz)
    day = last_dt.strftime("%Y-%m-%d")
    dt_local = last_dt.strftime("%Y-%m-%d %H:%M:%S")

    # treino: tudo menos tail (anti-peek)
    min_train_rows = env_int("MIN_TRAIN_ROWS", "3000")
    tail_holdout = env_int("TRAIN_TAIL_HOLDOUT", "200")
    cut = max(min_train_rows, len(df) - tail_holdout)
    train = df.iloc[:cut].copy()

    train_end_ts = int(train["ts"].iloc[-1])
    train_rows = int(len(train))
    fhash = feat_hash(feat)

    os.environ.setdefault("SIGNALS_INTERVAL_SEC", str(interval_sec))
    cache = load_cache(asset, interval_sec)
    meta = (cache or {}).get("meta") if cache else None

    if should_retrain(
        meta,
        train_end_ts=train_end_ts,
        best_source=best_source,
        fhash=fhash,
        interval_sec=interval_sec,
        meta_model_type=meta_model_type,
    ):
        cal, iso, meta_model = train_base_cal_iso_meta(
            train_df=train, feat_cols=feat, tz=tz, meta_model_type=meta_model_type
        )
        payload = {
            "cal": cal,
            "iso": iso,
            "meta_model": meta_model,
            "meta": {
                "asset": asset,
                "interval_sec": int(interval_sec),
                "created_at": datetime.now(tz=tz).isoformat(timespec="seconds"),
                "train_rows": train_rows,
                "train_end_ts": train_end_ts,
                "best_source": best_source,
                "feat_hash": fhash,
                "gate_version": GATE_VERSION,
                "meta_model": meta_model_type,
                "model_version": get_model_version(),
            },
        }
        save_cache(asset, interval_sec, payload)
        cache = payload
        meta = payload["meta"]

    cal = cache["cal"]
    iso = cache.get("iso", None)
    meta_model = cache.get("meta_model", None)

    # dados do dia (até o último candle)
    dt_all = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz)
    df_day = df.loc[dt_all.dt.strftime("%Y-%m-%d") == day].copy()
    if len(df_day) == 0:
        raise ValueError("Sem dados no dia atual no dataset.")
    # --- P8b: CPREG (alpha schedule + slot-aware) ---
    # Centralized CPREG helper: if enabled, dynamically updates CP_ALPHA (env)
    # based on last_dt (local time) + executed slot for the day.
    if gate_mode == "cp":
        executed_today = executed_today_count(asset, interval_sec, day)
        cp_alpha_applied = maybe_apply_cp_alpha_env(last_dt, executed_today=executed_today)
        if cp_alpha_applied is not None:
            _d["cp_alpha"] = cp_alpha_applied
            _d["cpreg_slot"] = int(executed_today) + 1

    # --- /P8b ---


    gate_mode_requested = gate_mode
    proba, conf, score, gate_used = compute_scores(
        df=df_day,
        feat_cols=feat,
        tz=tz,
        cal_model=cal,
        iso=iso,
        meta_model=meta_model,
        gate_mode=gate_mode_requested,
    )

    gate_fail_closed_enabled = env_bool("GATE_FAIL_CLOSED", True)
    gate_fail_closed_active = False
    gate_fail_detail = ""
    gate_used_s = str(gate_used or "").strip()
    if gate_fail_closed_enabled and gate_mode_requested in ("meta", "cp"):
        legit = False
        if gate_mode_requested == "meta":
            legit = gate_used_s in ("meta", "meta_iso")
        elif gate_mode_requested == "cp":
            # CP gating is considered "legit" only when it actually ran.
            #
            # compute_scores() can return:
            #   - cp_meta / cp_meta_iso               (OK)
            #   - cp_fallback_*                       (soft fallback path)
            #   - cp_fail_closed_missing_cp_*         (hard fail-closed path)
            #
            # We treat both fallback and fail-closed as NOT legit so that
            # gate_fail_closed is surfaced correctly in the decision payload.
            legit = (
                gate_used_s.startswith("cp_")
                and (not gate_used_s.startswith("cp_fallback"))
                and (not gate_used_s.startswith("cp_fail_closed"))
            )
        if not legit:
            gate_fail_closed_active = True
            gate_fail_detail = gate_used_s or "unknown"
            score = np.zeros_like(score, dtype=float)

    mask = make_regime_mask(df_day, bounds) if bounds else np.ones(len(df_day), dtype=bool)
    payout_gate = env_float("PAYOUT", 0.8)
    ev_metric = score * payout_gate - (1.0 - score)
    if thresh_on == "score":
        metric = score
    elif thresh_on == "conf":
        metric = conf
    else:
        metric = ev_metric
    # --- P13c: REGIME_MODE soft/off (mask_gate) ---
    _rm = os.getenv("REGIME_MODE", "hard").strip().lower()
    if _rm not in ("hard","soft","off"):
        _rm = "hard"
    mask_gate = mask if _rm == "hard" else np.ones(len(mask), dtype=bool)
    # --- /P13c ---
    cand = mask_gate & (metric >= thr)

    # --- P26: TOPK ordering must be deterministic ---
    # Numpy's default quicksort is not stable, which can cause non-deterministic
    # tie-breaking when many candles share the same EV/score. Use a stable sort.
    # This matches the behavior of our Python backtests (stable ordering by time).
    payout_rank = env_float("PAYOUT", 0.8)
    rank = score * payout_rank - (1.0 - score)

    order = np.argsort(-rank, kind="mergesort")

    # Optional: restrict TOPK selection to a rolling window (useful for "restart mid-day")
    rolling_min = env_int("TOPK_ROLLING_MINUTES", "0")
    if rolling_min > 0:
        start_ts = int(last_ts) - int(rolling_min) * 60
        win_mask = df_day["ts"].to_numpy(dtype=int) >= start_ts
    else:
        win_mask = np.ones(len(df_day), dtype=bool)

    sel = order[(cand & win_mask)[order]]
    topk = sel[:k]

    now_i = len(df_day) - 1
    in_topk = bool(now_i in set(topk.tolist()))
    rank_in_day = int(np.where(topk == now_i)[0][0] + 1) if in_topk else -1

    executed_today = executed_today_count(asset, interval_sec, day)

    # Optional progressive pacing across the day.
    # Example with k=3: allow 1 trade until 08:00, 2 until 16:00, 3 afterwards.
    pacing_enabled = os.getenv("TOPK_PACING_ENABLE", "0").strip().lower() not in ("0", "false", "f", "no", "n", "off", "")
    pacing_allowed = int(k)
    if pacing_enabled and int(k) > 1:
        dt_now = pd.Timestamp(int(last_ts), unit="s", tz="UTC").tz_convert(tz)
        sec_of_day = int(dt_now.hour) * 3600 + int(dt_now.minute) * 60 + int(dt_now.second)
        frac_day = min(1.0, max(0.0, float(sec_of_day) / 86400.0))
        pacing_allowed = min(int(k), max(1, int(np.floor(float(k) * frac_day)) + 1))

    # If CP gate is active and the candle was rejected, score is hard-masked to 0.0.
    # Without this, we end up reporting "below_ev_threshold" which hides the real cause.
    cp_rejected_now = (
        (not gate_fail_closed_active)
        and str(gate_used or "").startswith("cp_")
        and (not str(gate_used or "").startswith("cp_fallback"))
        and (float(score[now_i]) <= 0.0)
    )

    action = "HOLD"
    reason = "ok"
    blockers: list[str] = []

    market_open = os.getenv("MARKET_OPEN", "1").strip().lower() not in ("0", "false", "f", "no", "n", "off")
    market_context_stale = env_bool("MARKET_CONTEXT_STALE", False)
    market_context_fail_closed = env_bool("MARKET_CONTEXT_FAIL_CLOSED", True)
    market_context_stale_now = bool(market_context_fail_closed and market_context_stale)
    hard_regime_block = (os.getenv("REGIME_MODE","hard").strip().lower() == "hard") and (not bool(mask[now_i]))

    threshold_reason = ""
    if float(metric[now_i]) < thr:
        if thresh_on == "score":
            threshold_reason = "below_score_threshold"
        elif thresh_on == "conf":
            threshold_reason = "below_conf_threshold"
        else:
            threshold_reason = "below_ev_threshold"

    pacing_reason = ""
    if pacing_enabled and executed_today >= pacing_allowed:
        pacing_reason = f"pacing_day_progress({pacing_allowed}/{k})"

    if market_context_stale_now:
        blockers.append("market_context_stale")
    if not market_open:
        blockers.append("market_closed")
    if executed_today >= k:
        blockers.append("max_k_reached")
    if already_executed(asset, interval_sec, day, last_ts):
        blockers.append("already_emitted_for_ts")
    if hard_regime_block:
        blockers.append("regime_block")
    if pacing_reason:
        blockers.append(pacing_reason)
    if gate_fail_closed_active:
        blockers.append("gate_fail_closed")
    if cp_rejected_now:
        blockers.append("cp_reject")
    if threshold_reason:
        blockers.append(threshold_reason)
    if not in_topk:
        blockers.append("not_in_topk_today")

    cooldown_reason = ""
    min_gap_min = env_int("TOPK_MIN_GAP_MINUTES", "0")
    if min_gap_min > 0 and executed_today > 0:
        prev_ts = last_executed_ts(asset, interval_sec, day)
        if prev_ts is not None and (int(last_ts) - int(prev_ts)) < int(min_gap_min) * 60:
            cooldown_reason = f"cooldown_min_gap({min_gap_min}m)"

    if market_context_stale_now:
        reason = "market_context_stale"
    elif not market_open:
        reason = "market_closed"
    elif executed_today >= k:
        reason = "max_k_reached"
    elif already_executed(asset, interval_sec, day, last_ts):
        reason = "already_emitted_for_ts"
    elif pacing_reason:
        reason = pacing_reason
    elif hard_regime_block:
        reason = "regime_block"
    elif gate_fail_closed_active:
        reason = "gate_fail_closed"
    elif cp_rejected_now:
        reason = "cp_reject"
    elif threshold_reason:
        reason = threshold_reason
    elif not in_topk:
        reason = "not_in_topk_today"
    elif cooldown_reason:
        reason = cooldown_reason
        blockers.append(cooldown_reason)

    emitted_now = False
    if reason == "ok":
        action = "CALL" if float(proba[now_i]) >= 0.5 else "PUT"
        reason = "topk_emit"
        emitted_now = True

    executed_after = int(executed_today) + (1 if emitted_now else 0)
    blockers_csv = ";".join(dict.fromkeys([b for b in blockers if b and b != reason]))
    budget_left = max(0, int(k) - int(executed_after))

    payout = env_float("PAYOUT", 0.8)
    ev = float(score[now_i]) * payout - (1.0 - float(score[now_i]))
    row = {
        "dt_local": dt_local,
        "day": day,
        "ts": int(last_ts),
        "interval_sec": int(interval_sec),
        "proba_up": float(proba[now_i]),
        "conf": float(conf[now_i]),
        "score": float(score[now_i]),
        "gate_mode": gate_used,
        "gate_mode_requested": gate_mode_requested,
        "gate_fail_closed": int(bool(gate_fail_closed_active)),
        "gate_fail_detail": gate_fail_detail,
        "regime_ok": int(bool(mask[now_i])),
        "thresh_on": thresh_on,
        "threshold": float(thr),
        "k": int(k),
        "rank_in_day": int(rank_in_day),
        "executed_today": int(executed_after),
        "budget_left": int(budget_left),
        "action": action,
        "reason": reason,
        "blockers": blockers_csv,
        "close": float(df_day["close"].iloc[now_i]) if "close" in df_day.columns else None,
        "payout": float(payout),
        "ev": float(ev),
        "asset": asset,
        "model_version": str(meta.get("model_version") if meta else get_model_version()),
        "train_rows": int(meta.get("train_rows") if meta else train_rows),
        "train_end_ts": int(meta.get("train_end_ts") if meta else train_end_ts),
        "best_source": str(meta.get("best_source") if meta else best_source),
        "tune_dir": tune_dir,
        "feat_hash": fhash,
        "gate_version": GATE_VERSION,
        "meta_model": meta_model_type,
        "market_context_stale": int(1 if market_context_stale_now else 0),
        "market_context_fail_closed": int(1 if market_context_fail_closed else 0),
    }

    write_sqlite_signal(row)
    try:
        out_csv = append_csv(row)
    except Exception as e:
        out_csv = _resolve_live_signals_csv_path(row)
        print(f"[WARN] csv_write failed (non-fatal): {e}")

    if emitted_now:
        _heal_state_from_signals(asset, interval_sec, day, ts=int(last_ts))
        if not _already_state_only(asset, interval_sec, day, int(last_ts)):
            mark_executed(asset, interval_sec, day, last_ts, action, float(conf[now_i]), float(score[now_i]))
        executed_after = executed_today_count(asset, interval_sec, day)
        row["executed_today"] = int(executed_after)
        row["budget_left"] = max(0, int(k) - int(executed_after))

    # --- P10: daily summary call ---
    summary_path = ''
    try:
        summary_path = write_daily_summary(
            day=day,
            tz=tz,
            asset=asset,
            interval_sec=int(interval_sec),
            dataset_path=dataset_path,
            gate_mode=gate_used,
            meta_model=meta_model_type,
            thresh_on=thresh_on,
            threshold=float(thr),
            k=int(k),
            payout=float(payout),
        )
    except Exception as e:
        print(f"[WARN] daily_summary failed: {e}")
    if summary_path:
        print(f"summary_ok: {summary_path}")
    # --- /P10 ---

    latest_snapshot_path = ''
    detailed_snapshot_path = ''
    incident_path = ''
    incident_kind = ''
    try:
        latest_snapshot_path = str(write_latest_decision_snapshot(row))
        detailed = write_detailed_decision_snapshot(row)
        if detailed is not None:
            detailed_snapshot_path = str(detailed)
        incident = build_incident_from_decision(row)
        if incident is not None:
            incident_kind = str(incident.get('incident_type') or '')
            incident_p = append_incident_event(incident)
            incident_path = str(incident_p)
    except Exception as e:
        print(f"[WARN] observability_write failed: {e}")
    else:
        if detailed_snapshot_path or incident_path:
            print(f"[P38] observability_ok latest={latest_snapshot_path or '-'} detailed={detailed_snapshot_path or '-'} incident={incident_kind or '-'}")

    print("=== OBSERVE TOPK-PERDAY (latest) ===")
    print(
        {
            "dt_local": row["dt_local"],
            "day": row["day"],
            "ts": row["ts"],
            "proba_up": row["proba_up"],
            "conf": row["conf"],
            "score": row["score"],
            "gate_mode": row["gate_mode"],
            "meta_model": row.get("meta_model"),
            "regime_ok": row["regime_ok"],
            "thresh_on": row["thresh_on"],
            "threshold": row["threshold"],
            "k": row["k"],
            "rank_in_day": row["rank_in_day"],
            "executed_today": row["executed_today"],
            "action": row["action"],
            "reason": row["reason"],
        }
    )
    print(f"csv_ok: {out_csv}")
    print(f"sqlite_ok: {signals_db_path()} (signals_v2)")


if __name__ == "__main__":
    main()