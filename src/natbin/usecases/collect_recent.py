from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ..config.settings import load_settings
from ..adapters.iq_client import IQClient, IQConfig, IQDependencyUnavailable, iqoption_dependency_status
from ..state.db import open_db, upsert_candles
from ..config.env import env_float, env_int
from ..runtime.scope import market_context_path as scoped_market_context_path
from natbin.runtime.broker_dependency import build_dependency_market_context, candle_db_snapshot, write_json


def norm_ts(x: int) -> int:
    x = int(x)
    if x > 1_000_000_000_000:  # ms -> s
        x //= 1000
    return x


def is_closed(candle: dict, now_ts: int, interval_sec: int) -> bool:
    f = candle.get("from") or candle.get("time") or 0
    f = norm_ts(int(f))
    return now_ts >= (f + interval_sec)


def default_market_context_path(asset: str, interval_sec: int) -> Path:
    return scoped_market_context_path(asset=asset, interval_sec=interval_sec, out_dir='runs')


def maybe_write_market_context(client: IQClient, *, asset: str, interval_sec: int, db_path: str) -> None:
    ctx_env = os.getenv("MARKET_CONTEXT_PATH", "").strip()
    ctx_path = Path(ctx_env) if ctx_env else default_market_context_path(asset=asset, interval_sec=interval_sec)
    ctx_path.parent.mkdir(parents=True, exist_ok=True)

    payout_fallback = env_float("PAYOUT_FALLBACK", env_float("PAYOUT", 0.8))

    try:
        ctx = client.get_market_context(asset=asset, interval_sec=interval_sec, payout_fallback=payout_fallback)
        from natbin.runtime.broker_dependency import infer_market_open_from_db

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
            "dependency_available": True,
            "dependency_reason": None,
        }
        write_json(ctx_path, payload)
    except Exception as e:
        print(f"[P30] WARN: market_context cache write failed: {type(e).__name__}: {e}")


def _dependency_fallback(*, asset: str, interval_sec: int, db_path: str, dependency_reason: str) -> int:
    ctx_env = os.getenv("MARKET_CONTEXT_PATH", "").strip()
    ctx_path = Path(ctx_env) if ctx_env else default_market_context_path(asset=asset, interval_sec=interval_sec)
    payload = build_dependency_market_context(
        asset=asset,
        interval_sec=interval_sec,
        db_path=db_path,
        payout_fallback=env_float("PAYOUT_FALLBACK", env_float("PAYOUT", 0.8)),
        ctx_path=ctx_path,
        dependency_reason=dependency_reason,
    )
    write_json(ctx_path, payload)
    snap = candle_db_snapshot(db_path, asset, interval_sec)
    report = {
        "ok": bool((snap.get("db_rows") or 0) > 0),
        "mode": "dependency_fallback",
        "asset": asset,
        "interval_sec": int(interval_sec),
        "db_rows": int(snap.get("db_rows") or 0),
        "last_candle_ts": snap.get("last_candle_ts"),
        "dependency_reason": str(dependency_reason),
        "action": "skip_remote_collect" if (snap.get("db_rows") or 0) > 0 else "fail_no_local_data",
        "market_context_path": str(ctx_path),
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 2


def main():
    s = load_settings()
    asset = s.data.asset
    interval_sec = int(s.data.interval_sec)
    db_path = s.data.db_path
    max_batch = int(s.data.max_batch)

    lookback = env_int("LOOKBACK_CANDLES", "2000")
    sleep_s = env_float("IQ_SLEEP_S", 0.15)

    dep = iqoption_dependency_status()
    if not bool(dep.get("available", True)):
        raise SystemExit(_dependency_fallback(asset=asset, interval_sec=interval_sec, db_path=db_path, dependency_reason=str(dep.get("reason"))))

    now_ts = int(datetime.now(tz=timezone.utc).timestamp())

    con = open_db(db_path)
    try:
        try:
            client = IQClient(IQConfig(
                email=s.iq.email,
                password=s.iq.password,
                balance_mode=s.iq.balance_mode,
            ))
            client.connect()
        except IQDependencyUnavailable as exc:
            raise SystemExit(_dependency_fallback(asset=asset, interval_sec=interval_sec, db_path=db_path, dependency_reason=str(exc)))

        remaining = lookback
        cursor_end = now_ts
        total_seen = 0
        total_upsert = 0

        while remaining > 0:
            n = min(max_batch, remaining)
            try:
                candles = client.get_candles(asset, interval_sec, n, cursor_end)
            except IQDependencyUnavailable as exc:
                raise SystemExit(_dependency_fallback(asset=asset, interval_sec=interval_sec, db_path=db_path, dependency_reason=str(exc)))
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

            closed = [c for c in candles if is_closed(c, now_ts, interval_sec)]
            if closed:
                upsert_candles(con, asset, interval_sec, closed)
                total_upsert += len(closed)

            min_ts = min(norm_ts(int(c.get("from", 0) or 0)) for c in candles)
            cursor_end = min_ts - interval_sec
            remaining -= n

            time.sleep(sleep_s)

        try:
            maybe_write_market_context(client, asset=asset, interval_sec=interval_sec, db_path=db_path)
        finally:
            pass
        print(f"collect_recent(closed-only): seen~{total_seen} upserted~{total_upsert} lookback={lookback}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
