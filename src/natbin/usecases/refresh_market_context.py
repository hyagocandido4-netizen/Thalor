from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from ..config.settings import load_settings
from ..adapters.iq_client import IQClient, IQConfig, IQDependencyUnavailable, iqoption_dependency_status
from ..config.env import env_float
from ..runtime.scope import market_context_path as scoped_market_context_path
from natbin.runtime.broker_dependency import build_dependency_market_context, infer_market_open_from_db, write_json


def default_market_context_path(asset: str, interval_sec: int) -> Path:
    return scoped_market_context_path(asset=asset, interval_sec=interval_sec, out_dir='runs')


def main() -> None:
    s = load_settings()
    asset = s.data.asset
    interval_sec = int(s.data.interval_sec)
    db_path = s.data.db_path
    out_env = os.getenv("MARKET_CONTEXT_PATH", "").strip()
    out_path = Path(out_env) if out_env else default_market_context_path(asset=asset, interval_sec=interval_sec)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payout_fallback = env_float("PAYOUT_FALLBACK", env_float("PAYOUT", 0.8))

    dep = iqoption_dependency_status()
    if not bool(dep.get("available", True)):
        payload = build_dependency_market_context(
            asset=asset,
            interval_sec=interval_sec,
            db_path=db_path,
            payout_fallback=payout_fallback,
            ctx_path=out_path,
            dependency_reason=str(dep.get("reason")),
        )
        write_json(out_path, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return

    try:
        client = IQClient(IQConfig(
            email=s.iq.email,
            password=s.iq.password,
            balance_mode=s.iq.balance_mode,
        ))
        client.connect()
        ctx = client.get_market_context(asset=asset, interval_sec=interval_sec, payout_fallback=payout_fallback)
    except IQDependencyUnavailable as exc:
        payload = build_dependency_market_context(
            asset=asset,
            interval_sec=interval_sec,
            db_path=db_path,
            payout_fallback=payout_fallback,
            ctx_path=out_path,
            dependency_reason=str(exc),
        )
        write_json(out_path, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return

    market_open, open_source, last_candle_ts = infer_market_open_from_db(db_path=db_path, asset=asset, interval_sec=interval_sec)

    payload = {
        "asset": asset,
        "interval_sec": interval_sec,
        "market_open": bool(market_open),
        "open_source": str(open_source),
        "payout": float(ctx.get("payout", payout_fallback)),
        "payout_source": str(ctx.get("payout_source", "fallback")),
        "last_candle_ts": int(last_candle_ts) if last_candle_ts is not None else None,
        "at_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "dependency_available": True,
        "dependency_reason": None,
    }

    write_json(out_path, payload)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
