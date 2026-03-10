from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from natbin.settings import load_settings
from natbin.iq_client import IQClient, IQConfig
from natbin.db import open_db, upsert_candles
from natbin.envutil import env_float, env_int, env_str
from natbin.runtime_scope import market_context_path as scoped_market_context_path


def norm_ts(x: int) -> int:
    x = int(x)
    if x > 1_000_000_000_000:  # ms -> s
        x //= 1000
    return x


def is_closed(candle: dict, now_ts: int, interval_sec: int) -> bool:
    f = candle.get("from") or candle.get("time") or 0
    f = norm_ts(int(f))
    # candle fechado se "agora" já passou do fim do candle
    return now_ts >= (f + interval_sec)




def default_market_context_path(asset: str, interval_sec: int) -> Path:
    return scoped_market_context_path(asset=asset, interval_sec=interval_sec, out_dir='runs')


def infer_market_open_from_db(db_path: str, asset: str, interval_sec: int) -> tuple[bool, str, int | None]:
    grace_sec = int(env_int("MARKET_OPEN_GRACE_SEC", 90))
    max_age_sec = max(int(interval_sec) * 2 + grace_sec, int(interval_sec) + grace_sec)

    con = open_db(db_path)
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


def maybe_write_market_context(client: IQClient, *, asset: str, interval_sec: int, db_path: str) -> None:
    """
    Refresh the scoped market_context sidecar using the SAME IQ session already opened for collect_recent.

    Rationale:
    - observe_loop_auto.ps1 runs collect_recent immediately before loading market context.
    - Doing a second login/connect only to fetch payout/open state is wasteful and increases API noise.
    - We can reuse the existing session for payout and infer open/closed from DB freshness.
    """
    ctx_env = os.getenv("MARKET_CONTEXT_PATH", "").strip()
    ctx_path = Path(ctx_env) if ctx_env else default_market_context_path(asset=asset, interval_sec=interval_sec)
    ctx_path.parent.mkdir(parents=True, exist_ok=True)

    payout_fallback = env_float("PAYOUT_FALLBACK", env_float("PAYOUT", 0.8))

    try:
        ctx = client.get_market_context(asset=asset, interval_sec=interval_sec, payout_fallback=payout_fallback)
        market_open, open_source, last_candle_ts = infer_market_open_from_db(
            db_path=db_path, asset=asset, interval_sec=interval_sec
        )
        payload = {
            "asset": asset,
            "interval_sec": int(interval_sec),
            "market_open": bool(market_open),
            "open_source": str(open_source),
            "payout": float(ctx.get("payout", payout_fallback)),
            "payout_source": str(ctx.get("payout_source", "fallback")),
            "last_candle_ts": int(last_candle_ts) if last_candle_ts is not None else None,
            "at_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        }
        ctx_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[P30] WARN: market_context cache write failed: {type(e).__name__}: {e}")


def main():
    s = load_settings()
    asset = s.data.asset
    interval_sec = int(s.data.interval_sec)
    db_path = s.data.db_path
    max_batch = int(s.data.max_batch)

    lookback = env_int("LOOKBACK_CANDLES", "2000")
    sleep_s = env_float("IQ_SLEEP_S", 0.15)

    now_ts = int(datetime.now(tz=timezone.utc).timestamp())

    con = open_db(db_path)
    client = IQClient(IQConfig(
        email=s.iq.email,
        password=s.iq.password,
        balance_mode=s.iq.balance_mode,
    ))
    client.connect()

    remaining = lookback
    cursor_end = now_ts
    total_seen = 0
    total_upsert = 0

    while remaining > 0:
        n = min(max_batch, remaining)
        try:
            candles = client.get_candles(asset, interval_sec, n, cursor_end)
        except Exception as e:
            raise RuntimeError(
                f"collect_recent failed: asset={asset} interval={interval_sec} count={n} end={cursor_end} seen={total_seen} upserted={total_upsert} err={type(e).__name__}: {e}"
            ) from e
        if not candles:
            if total_seen == 0:
                raise RuntimeError(
                    f"collect_recent returned no candles on first batch: asset={asset} interval={interval_sec} count={n} end={cursor_end}"
                )
            print(f"[WARN] collect_recent: empty batch after seen={total_seen}; stopping early at end={cursor_end}")
            break

        total_seen += len(candles)

        # filtra candles fechados (remove o candle em formação)
        closed = [c for c in candles if is_closed(c, now_ts, interval_sec)]
        if closed:
            upsert_candles(con, asset, interval_sec, closed)
            total_upsert += len(closed)

        # anda o cursor pra trás (pelo menor "from" recebido)
        min_ts = min(norm_ts(int(c.get("from", 0) or 0)) for c in candles)
        cursor_end = min_ts - interval_sec
        remaining -= n

        time.sleep(sleep_s)

    try:
        maybe_write_market_context(client, asset=asset, interval_sec=interval_sec, db_path=db_path)
    finally:
        con.close()
    print(f"collect_recent(closed-only): seen~{total_seen} upserted~{total_upsert} lookback={lookback}")


if __name__ == "__main__":
    main()
