param(
  [int]$BackfillDays = 120,
  [int]$LookbackCandles = 8000,
  [int]$SleepMs = 200
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

$root = (Get-Location).Path
$py = Join-Path $root ".venv\Scripts\python.exe"

Require-Path $py "Nao encontrei .venv. Rode init.ps1."
Require-Path "config.yaml" "Nao encontrei config.yaml."
Require-Path "src\natbin\db.py" "Nao achei src\natbin\db.py"
Require-Path "src\natbin\make_dataset.py" "Nao achei src\natbin\make_dataset.py"
Require-Path "src\natbin\backfill_candles.py" "Nao achei src\natbin\backfill_candles.py"
Require-Path "src\natbin\collect_recent.py" "Nao achei src\natbin\collect_recent.py"

Write-Host "== FIX OTC dataset empty (timestamp ms->s) ==" -ForegroundColor Cyan

# 1) Reescreve db.py com normalização de timestamp
@'
import sqlite3
from pathlib import Path
from typing import Iterable, Dict, Any, Tuple


DDL = """
CREATE TABLE IF NOT EXISTS candles (
  asset TEXT NOT NULL,
  interval_sec INTEGER NOT NULL,
  ts INTEGER NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume REAL,
  PRIMARY KEY (asset, interval_sec, ts)
);
CREATE INDEX IF NOT EXISTS idx_candles_asset_ts ON candles(asset, ts);
"""


def open_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    for stmt in DDL.strip().split(";"):
        s = stmt.strip()
        if s:
            con.execute(s + ";")
    con.commit()
    return con


def _normalize_ts(ts: int) -> int:
    # Se vier em milissegundos (13 dígitos), converte para segundos
    if ts > 1_000_000_000_000:
        ts = ts // 1000
    return int(ts)


def _row_from_candle(asset: str, interval_sec: int, c: Dict[str, Any]) -> Tuple:
    raw_ts = int(c.get("from") or c.get("time") or 0)
    ts = _normalize_ts(raw_ts)

    o = float(c["open"])
    cl = float(c["close"])
    lo = float(c.get("min", c.get("low")))
    hi = float(c.get("max", c.get("high")))
    vol = c.get("volume")
    vol = float(vol) if vol is not None else None

    if ts <= 0:
        raise ValueError(f"Candle sem timestamp valido: {c}")
    return (asset, interval_sec, ts, o, hi, lo, cl, vol)


def upsert_candles(con: sqlite3.Connection, asset: str, interval_sec: int, candles: Iterable[Dict[str, Any]]) -> int:
    rows = []
    for c in candles:
        try:
            rows.append(_row_from_candle(asset, interval_sec, c))
        except Exception:
            continue

    if not rows:
        return 0

    con.executemany(
        """
        INSERT OR IGNORE INTO candles(asset, interval_sec, ts, open, high, low, close, volume)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    con.commit()
    return len(rows)


def count_candles(con: sqlite3.Connection, asset: str, interval_sec: int) -> int:
    cur = con.execute("SELECT COUNT(*) FROM candles WHERE asset=? AND interval_sec=?", (asset, interval_sec))
    return int(cur.fetchone()[0])
'@ | Set-Content -Encoding UTF8 "src\natbin\db.py"

Write-Host "db.py atualizado (ms->s)." -ForegroundColor Green

# 2) Backup e recria o DB OTC (porque ele foi populado com ts errado)
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$otcDb = "data\market_otc.sqlite3"
if (Test-Path $otcDb) {
  Move-Item -Force $otcDb ("data\market_otc_backup_$stamp.sqlite3")
  Write-Host "DB OTC antigo movido para backup: data\market_otc_backup_$stamp.sqlite3" -ForegroundColor Yellow
}

# 3) Compileall para garantir que tudo está OK
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou (algum .py invalido)" }

# 4) Backfill + recent + dataset
Write-Host "`nBackfill OTC ($BackfillDays dias)..." -ForegroundColor Cyan
& $py -m natbin.backfill_candles --days $BackfillDays --sleep_ms $SleepMs
if ($LASTEXITCODE -ne 0) { throw "backfill_candles falhou" }

Write-Host "`nSeed recent (lookback=$LookbackCandles)..." -ForegroundColor Cyan
$env:LOOKBACK_CANDLES = "$LookbackCandles"
& $py -m natbin.collect_recent
if ($LASTEXITCODE -ne 0) { throw "collect_recent falhou" }

Write-Host "`nRebuild dataset..." -ForegroundColor Cyan
& $py -m natbin.make_dataset
if ($LASTEXITCODE -ne 0) { throw "make_dataset falhou" }

Write-Host "`nOK. Agora o dataset não deve mais sair com rows=0." -ForegroundColor Green