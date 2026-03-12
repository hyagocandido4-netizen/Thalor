from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .perf import write_text_if_changed
from .scope import decision_latest_path as scoped_latest_decision_path, decision_snapshot_path as scoped_decision_snapshot_path, incident_jsonl_path as scoped_incident_jsonl_path

_SERIOUS_REASONS = {
    "gate_fail_closed",
    "market_context_stale",
    "market_closed",
}


def _safe(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, dict):
        return {str(k): _safe(val) for k, val in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_safe(x) for x in v]
    item = getattr(v, "item", None)
    if callable(item):
        try:
            return _safe(item())
        except Exception:
            pass
    try:
        json.dumps(v)
        return v
    except Exception:
        return str(v)


def _now_utc() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")



def decisions_dir(out_dir: str | Path = "runs") -> Path:
    p = Path(out_dir) / "decisions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def incidents_dir(out_dir: str | Path = "runs") -> Path:
    p = Path(out_dir) / "incidents"
    p.mkdir(parents=True, exist_ok=True)
    return p


def latest_decision_snapshot_path(*, asset: str, interval_sec: int, out_dir: str | Path = "runs") -> Path:
    return scoped_latest_decision_path(asset=asset, interval_sec=interval_sec, out_dir=out_dir)


def decision_snapshot_path(*, day: str, asset: str, interval_sec: int, ts: int, out_dir: str | Path = "runs") -> Path:
    day_tag = str(day).replace("-", "")
    return scoped_decision_snapshot_path(day=day, asset=asset, interval_sec=interval_sec, ts=ts, out_dir=out_dir)


def incident_jsonl_path(*, day: str, asset: str, interval_sec: int, out_dir: str | Path = "runs") -> Path:
    day_tag = str(day).replace("-", "")
    return scoped_incident_jsonl_path(day=day, asset=asset, interval_sec=interval_sec, out_dir=out_dir)


def build_decision_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    row_safe = _safe(dict(row))
    return {
        "kind": "decision",
        "observed_at_utc": _now_utc(),
        "asset": row_safe.get("asset"),
        "interval_sec": row_safe.get("interval_sec"),
        "day": row_safe.get("day"),
        "ts": row_safe.get("ts"),
        "dt_local": row_safe.get("dt_local"),
        "action": row_safe.get("action"),
        "reason": row_safe.get("reason"),
        "blockers": row_safe.get("blockers", ""),
        "executed_today": row_safe.get("executed_today"),
        "budget_left": row_safe.get("budget_left"),
        "gate_mode": row_safe.get("gate_mode"),
        "gate_fail_closed": row_safe.get("gate_fail_closed", 0),
        "gate_fail_detail": row_safe.get("gate_fail_detail", ""),
        "market_context_stale": row_safe.get("market_context_stale", 0),
        "market_context_fail_closed": row_safe.get("market_context_fail_closed", 0),
        "regime_ok": row_safe.get("regime_ok"),
        "threshold": row_safe.get("threshold"),
        "thresh_on": row_safe.get("thresh_on"),
        "k": row_safe.get("k"),
        "rank_in_day": row_safe.get("rank_in_day"),
        "payout": row_safe.get("payout"),
        "ev": row_safe.get("ev"),
        "proba_up": row_safe.get("proba_up"),
        "conf": row_safe.get("conf"),
        "score": row_safe.get("score"),
        "meta_model": row_safe.get("meta_model"),
        "model_version": row_safe.get("model_version"),
        "raw": row_safe,
    }


def write_latest_decision_snapshot(row: dict[str, Any], *, out_dir: str | Path = "runs") -> Path:
    asset = str(row.get("asset") or "UNKNOWN")
    interval_sec = int(row.get("interval_sec") or 0)
    payload = build_decision_snapshot(row)
    p = latest_decision_snapshot_path(asset=asset, interval_sec=interval_sec, out_dir=out_dir)
    write_text_if_changed(p, json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    return p


def should_persist_detailed_decision(row: dict[str, Any]) -> bool:
    action = str(row.get("action") or "HOLD").upper()
    if action in {"CALL", "PUT"}:
        return True
    reason = str(row.get("reason") or "").strip()
    if reason in _SERIOUS_REASONS:
        return True
    try:
        gate_fail_closed = int(_safe(row.get("gate_fail_closed") or 0) or 0)
    except Exception:
        gate_fail_closed = 0
    return gate_fail_closed > 0


def write_detailed_decision_snapshot(row: dict[str, Any], *, out_dir: str | Path = "runs") -> Path | None:
    if not should_persist_detailed_decision(row):
        return None
    asset = str(row.get("asset") or "UNKNOWN")
    interval_sec = int(row.get("interval_sec") or 0)
    day = str(row.get("day") or "")
    ts = int(row.get("ts") or 0)
    if not day or ts <= 0:
        return None
    payload = build_decision_snapshot(row)
    p = decision_snapshot_path(day=day, asset=asset, interval_sec=interval_sec, ts=ts, out_dir=out_dir)
    write_text_if_changed(p, json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    return p


def build_incident_from_decision(row: dict[str, Any]) -> dict[str, Any] | None:
    action = str(row.get("action") or "HOLD").upper()
    reason = str(row.get("reason") or "").strip()
    gate_fail_closed = int(_safe(row.get("gate_fail_closed") or 0) or 0)
    if action in {"CALL", "PUT"}:
        severity = "info"
        incident_type = "trade_emit"
    elif reason in _SERIOUS_REASONS or gate_fail_closed > 0:
        severity = "warning"
        incident_type = reason or "decision_block"
    else:
        return None
    snap = build_decision_snapshot(row)
    return {
        "kind": "incident",
        "incident_type": incident_type,
        "severity": severity,
        "recorded_at_utc": _now_utc(),
        "asset": snap.get("asset"),
        "interval_sec": snap.get("interval_sec"),
        "day": snap.get("day"),
        "ts": snap.get("ts"),
        "action": snap.get("action"),
        "reason": snap.get("reason"),
        "blockers": snap.get("blockers"),
        "snapshot": snap,
    }


def append_incident_event(payload: dict[str, Any], *, out_dir: str | Path = "runs") -> Path:
    asset = str(payload.get("asset") or "UNKNOWN")
    interval_sec = int(payload.get("interval_sec") or 0)
    day = str(payload.get("day") or "")
    if not day:
        raise ValueError("incident payload missing day")
    p = incident_jsonl_path(day=day, asset=asset, interval_sec=interval_sec, out_dir=out_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_safe(payload), ensure_ascii=False) + "\n")
    return p
