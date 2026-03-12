from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..state.db import open_db
from ..config.env import env_int


def candle_db_snapshot(db_path: str, asset: str, interval_sec: int) -> dict[str, Any]:
    con = open_db(db_path)
    try:
        cur = con.execute(
            "SELECT COUNT(*), MAX(ts) FROM candles WHERE asset=? AND interval_sec=?",
            (asset, int(interval_sec)),
        )
        row = cur.fetchone() or (0, None)
        count = int(row[0] or 0)
        last_ts = int(row[1]) if row[1] is not None else None
    finally:
        con.close()
    return {
        "asset": str(asset),
        "interval_sec": int(interval_sec),
        "db_rows": count,
        "last_candle_ts": last_ts,
    }


def infer_market_open_from_db(db_path: str, asset: str, interval_sec: int) -> tuple[bool, str, int | None]:
    grace_sec = int(env_int("MARKET_OPEN_GRACE_SEC", 90))
    max_age_sec = max(int(interval_sec) * 2 + grace_sec, int(interval_sec) + grace_sec)
    snap = candle_db_snapshot(db_path, asset, interval_sec)
    last_ts = snap.get("last_candle_ts")
    if last_ts is None:
        return False, "db_missing", None
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    age_sec = max(0, now_ts - int(last_ts))
    if age_sec <= max_age_sec:
        return True, "db_fresh", int(last_ts)
    return False, "db_stale", int(last_ts)


def read_cached_json(path: str | Path | None) -> dict[str, Any] | None:
    if path in (None, ""):
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def build_dependency_market_context(
    *,
    asset: str,
    interval_sec: int,
    db_path: str,
    payout_fallback: float = 0.8,
    ctx_path: str | Path | None = None,
    dependency_reason: str | None = None,
) -> dict[str, Any]:
    cached = read_cached_json(ctx_path)
    snap = candle_db_snapshot(db_path, asset, interval_sec)
    market_open, open_source, last_ts = infer_market_open_from_db(db_path, asset, interval_sec)

    payout = float(payout_fallback)
    payout_source = "fallback"
    if isinstance(cached, dict):
        try:
            cached_payout = cached.get("payout")
            if cached_payout not in (None, ""):
                payout = float(cached_payout)
                payout_source = str(cached.get("payout_source") or "cached")
        except Exception:
            pass

    if last_ts is None:
        market_open = False
        open_source = "broker_dependency_missing_db_missing"

    return {
        "asset": str(asset),
        "interval_sec": int(interval_sec),
        "market_open": bool(market_open),
        "open_source": str(open_source),
        "payout": float(payout),
        "payout_source": str(payout_source),
        "last_candle_ts": int(last_ts) if last_ts is not None else None,
        "at_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "dependency_available": False,
        "dependency_reason": str(dependency_reason or "iqoption_dependency_missing"),
        "fallback_mode": "broker_dependency_closeout",
        "db_rows": int(snap.get("db_rows") or 0),
        "cache_used": bool(cached),
    }


def write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
