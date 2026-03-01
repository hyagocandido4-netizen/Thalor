$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

Require-Path ".\.venv\Scripts\python.exe" "Nao achei .venv\Scripts\python.exe"
Require-Path "src\natbin\collect_recent.py" "Nao achei src\natbin\collect_recent.py"

@'
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

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
    # candle fechado se "agora" já passou do fim do candle
    return now_ts >= (f + interval_sec)


def main():
    s = load_settings()
    asset = s.data.asset
    interval_sec = int(s.data.interval_sec)
    db_path = s.data.db_path
    max_batch = int(s.data.max_batch)

    lookback = int(os.getenv("LOOKBACK_CANDLES", "2000"))
    sleep_s = float(os.getenv("IQ_SLEEP_S", "0.15"))

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
        candles = client.get_candles(asset, interval_sec, n, cursor_end)
        if not candles:
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

    con.close()
    print(f"collect_recent(closed-only): seen~{total_seen} upserted~{total_upsert} lookback={lookback}")


if __name__ == "__main__":
    main()
'@ | Set-Content -Encoding UTF8 "src\natbin\collect_recent.py"

Write-Host "OK. collect_recent agora grava SOMENTE candles fechados." -ForegroundColor Green
Write-Host "Teste: pwsh -ExecutionPolicy Bypass -File .\observe_loop.ps1 -Once" -ForegroundColor Yellow