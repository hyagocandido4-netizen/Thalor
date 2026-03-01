from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from natbin.settings import load_settings
from natbin.iq_client import IQClient, IQConfig
from .envutil import env_bool, env_float, env_int, env_str


def default_market_context_path(asset: str, interval_sec: int) -> Path:
    tag = ''.join(ch if (ch.isalnum() or ch in '-_') else '_' for ch in str(asset or 'UNKNOWN')).strip('_') or 'UNKNOWN'
    return Path('runs') / f"market_context_{tag}_{int(interval_sec)}s.json"


def infer_market_open_from_db(db_path: str, asset: str, interval_sec: int) -> tuple[bool, str, int | None]:
    """
    Infer market-open status from the freshness of recently collected closed candles.

    Why this exists:
    - iqoptionapi.get_all_open_time() can spawn noisy background threads and crash with
      KeyError('underlying') on some digital/OTC paths.
    - The scheduler already runs collect_recent immediately before refreshing market context,
      so the local DB is a better and cheaper source of truth for "is this feed alive now?".
    """
    grace_sec = int(env_int("MARKET_OPEN_GRACE_SEC", 90))
    max_age_sec = max(int(interval_sec) * 2 + grace_sec, int(interval_sec) + grace_sec)

    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(
            "SELECT MAX(ts) FROM candles WHERE asset=? AND interval_sec=?",
            (asset, int(interval_sec)),
        )
        row = cur.fetchone()
        last_ts = int(row[0]) if row and row[0] is not None else None
    finally:
        con.close()

    if last_ts is None:
        return True, "db_missing", None

    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    age_sec = max(0, now_ts - int(last_ts))
    if age_sec <= max_age_sec:
        return True, "db_fresh", int(last_ts)
    return False, "db_stale", int(last_ts)


def main() -> None:
    s = load_settings()
    asset = s.data.asset
    interval_sec = int(s.data.interval_sec)
    db_path = s.data.db_path
    out_env = os.getenv("MARKET_CONTEXT_PATH", "").strip()
    out_path = Path(out_env) if out_env else default_market_context_path(asset=asset, interval_sec=interval_sec)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payout_fallback = env_float("PAYOUT_FALLBACK", env_float("PAYOUT", 0.8))

    client = IQClient(IQConfig(
        email=s.iq.email,
        password=s.iq.password,
        balance_mode=s.iq.balance_mode,
    ))
    client.connect()

    ctx = client.get_market_context(asset=asset, interval_sec=interval_sec, payout_fallback=payout_fallback)

    if env_bool("IQ_MARKET_OPEN_FROM_DB", True):
        market_open, open_source, last_candle_ts = infer_market_open_from_db(db_path=db_path, asset=asset, interval_sec=interval_sec)
    else:
        market_open = bool(ctx.get("market_open", True))
        open_source = str(ctx.get("open_source", "fallback"))
        last_candle_ts = None

    payload = {
        "asset": asset,
        "interval_sec": interval_sec,
        "market_open": bool(market_open),
        "open_source": str(open_source),
        "payout": float(ctx.get("payout", payout_fallback)),
        "payout_source": str(ctx.get("payout_source", "fallback")),
        "last_candle_ts": int(last_candle_ts) if last_candle_ts is not None else None,
        "at_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    }

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
