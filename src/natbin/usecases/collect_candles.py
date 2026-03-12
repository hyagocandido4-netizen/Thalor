import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..config.settings import load_settings
from ..adapters.iq_client import IQClient, IQConfig
from ..state.db import open_db, upsert_candles, count_candles


def to_epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def norm_ts(x: int) -> int:
    x = int(x)
    if x > 1_000_000_000_000:  # ms -> s
        x //= 1000
    return x


def is_closed(candle: dict, now_ts: int, interval_sec: int) -> bool:
    """Best-effort filter for in-progress candles.

    IQ Option sometimes returns the current in-progress candle in get_candles().
    Persisting it is dangerous (partial OHLC) unless DB upsert updates it later.
    We still prefer to only store closed candles.
    """
    f = candle.get("from") or candle.get("time") or 0
    f = norm_ts(int(f))
    return now_ts >= (f + interval_sec)


def main():
    s = load_settings()
    tz = ZoneInfo(s.data.timezone)

    asset = s.data.asset
    interval_sec = int(s.data.interval_sec)
    db_path = s.data.db_path
    max_batch = int(s.data.max_batch)

    end_dt = datetime.now(tz=tz)
    start_dt = end_dt.replace(year=end_dt.year - 1)

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    # We evaluate "closed" based on an UTC now_ts to avoid timezone confusion.
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())

    con = open_db(db_path)

    client = IQClient(IQConfig(
        email=s.iq.email,
        password=s.iq.password,
        balance_mode=s.iq.balance_mode,
    ))
    client.connect()

    print(f"Coletando {asset} {interval_sec}s | {start_dt.isoformat()} -> {end_dt.isoformat()}")
    cursor_end = end_ts
    loops = 0

    while True:
        loops += 1
        try:
            candles = client.get_candles(asset, interval_sec, max_batch, cursor_end)
        except Exception as e:
            raise RuntimeError(
                f"collect_candles failed: asset={asset} interval={interval_sec} count={max_batch} end={cursor_end} loops={loops} err={type(e).__name__}: {e}"
            ) from e
        if not candles:
            print(f"[WARN] Sem candles retornados em loops={loops} end={cursor_end}. Encerrando.")
            break

        # Prefer closed-only candles (avoid persisting partial in-progress candle)
        closed = [c for c in candles if is_closed(c, now_ts, interval_sec)]
        upserted = upsert_candles(con, asset, interval_sec, closed)

        ts_list = [
            norm_ts(int(c.get("from") or c.get("time") or 0))
            for c in candles
        ]
        ts_list = [t for t in ts_list if t > 0]
        if not ts_list:
            print("[WARN] batch sem timestamps válidos; encerrando.")
            break
        min_ts = min(ts_list)
        cursor_end = int(min_ts - interval_sec)

        total = count_candles(con, asset, interval_sec)
        print(f"[{loops}] batch={len(candles)} upserted={upserted} total_db={total} cursor_end={datetime.fromtimestamp(cursor_end, tz=tz).isoformat()}")

        if cursor_end <= start_ts:
            print("Alcancei o inicio da janela. Fim.")
            break

        time.sleep(0.25)

    con.close()


if __name__ == "__main__":
    main()