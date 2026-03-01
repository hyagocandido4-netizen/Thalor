from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from natbin.settings import load_settings
from natbin.iq_client import IQClient, IQConfig
from natbin.db import open_db, upsert_candles


def norm_ts(x: int) -> int:
    x = int(x)
    if x > 1_000_000_000_000:  # ms -> s
        x //= 1000
    return x


def is_closed(candle: dict, now_ts: int, interval_sec: int) -> bool:
    f = candle.get("from") or candle.get("time") or 0
    f = norm_ts(int(f))
    return now_ts >= (f + interval_sec)


def dt(ts: int, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz=tz).isoformat(timespec="seconds")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--asset", type=str, default=None)
    ap.add_argument("--interval", type=int, default=None)
    ap.add_argument("--sleep_ms", type=int, default=200)
    ap.add_argument("--max_batch", type=int, default=None)
    args = ap.parse_args()

    s = load_settings()
    tz = ZoneInfo(s.data.timezone)

    asset = args.asset or s.data.asset
    interval_sec = int(args.interval or s.data.interval_sec)
    max_batch = int(args.max_batch or s.data.max_batch)
    db_path = s.data.db_path

    end_dt = datetime.now(tz=tz)
    start_dt = end_dt - timedelta(days=int(args.days))
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    con = open_db(db_path)
    client = IQClient(IQConfig(
        email=s.iq.email,
        password=s.iq.password,
        balance_mode=s.iq.balance_mode,
    ))
    client.connect()

    now_ts = int(datetime.now(tz=timezone.utc).timestamp())

    cursor_end = end_ts
    loops = 0
    total_rows_seen = 0

    print(f"[backfill] asset={asset} interval={interval_sec}s days={args.days}")
    print(f"[backfill] window: {start_dt.isoformat(timespec='seconds')} -> {end_dt.isoformat(timespec='seconds')}")

    while cursor_end > start_ts:
        loops += 1
        try:
            candles = client.get_candles(asset, interval_sec, max_batch, cursor_end)
        except Exception as e:
            raise RuntimeError(
                f"backfill_candles failed: asset={asset} interval={interval_sec} count={max_batch} end={cursor_end} loops={loops} rows_seen={total_rows_seen} err={type(e).__name__}: {e}"
            ) from e
        if not candles:
            print(f"[backfill][WARN] sem retorno em loops={loops} end={cursor_end}; parando.")
            break

        total_rows_seen += len(candles)
        # Prefer closed-only candles (avoid persisting partial in-progress candle)
        closed = [c for c in candles if is_closed(c, now_ts, interval_sec)]
        upsert_candles(con, asset, interval_sec, closed)

        ts_list = [norm_ts(int(c.get("from") or c.get("time") or 0)) for c in candles]
        ts_list = [t for t in ts_list if t > 0]
        if not ts_list:
            print("[backfill] batch sem timestamps válidos; encerrando.")
            break
        min_ts = min(ts_list)
        cursor_end = int(min_ts - interval_sec)

        if loops % 20 == 0:
            print(f"[backfill] loops={loops} last_cursor={dt(cursor_end, tz)} rows_seen~{total_rows_seen}")

        time.sleep(max(0.0, float(args.sleep_ms) / 1000.0))

    con.close()
    print(f"[backfill] done. loops={loops} rows_seen~{total_rows_seen} last_cursor={dt(cursor_end, tz)}")


if __name__ == "__main__":
    main()