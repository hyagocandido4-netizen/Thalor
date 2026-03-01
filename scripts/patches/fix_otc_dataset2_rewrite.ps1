$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param([string]$Path, [string]$Content)
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

Require-Path "src\natbin\dataset2.py" "Nao achei src\natbin\dataset2.py"
Require-Path ".venv\Scripts\python.exe" "Nao achei .venv\Scripts\python.exe"

Write-Utf8NoBomFile "src\natbin\dataset2.py" @'
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DatasetBuildResult:
    path: str
    n_rows: int
    feature_cols: list[str]


def _load_candles(db_path: str, asset: str, interval_sec: int) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT ts, open, high, low, close, volume
            FROM candles
            WHERE asset = ? AND interval_sec = ?
            ORDER BY ts ASC
            """,
            con,
            params=(asset, interval_sec),
        )
    finally:
        con.close()

    if df.empty:
        raise RuntimeError("Nenhum candle encontrado no SQLite para esse asset/interval.")

    df["ts"] = df["ts"].astype("int64")
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype("float64")
    df["volume"] = pd.to_numeric(df.get("volume"), errors="coerce")
    return df


def _snap_ts(df: pd.DataFrame, step: int) -> pd.DataFrame:
    # alinha ts para múltiplos do step (reduz jitter e gaps falsos)
    df = df.copy()
    df["ts"] = (df["ts"] // step) * step
    # remove duplicatas (podem aparecer após o snap)
    df = df.drop_duplicates(subset=["ts"], keep="last").sort_values("ts").reset_index(drop=True)
    return df


def _add_sessions(df: pd.DataFrame, step: int) -> pd.DataFrame:
    # sessão nova só quando gap for realmente grande/anômalo
    gap = df["ts"].diff().fillna(step).astype("int64")

    # tolerância: gap “ok” é exatamente step (já snapado), mas mantém tolerância mínima
    tol = 2
    ok = (gap >= (step - tol)) & (gap <= (step + tol))

    new_sess = (~ok).astype("int64")
    new_sess.iloc[0] = 0
    df["session_id"] = new_sess.cumsum().astype("int64")
    return df


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(period, min_periods=max(5, period // 2)).mean()
    avg_loss = loss.rolling(period, min_periods=max(5, period // 2)).mean()
    rs = avg_gain / (avg_loss.replace(0, np.nan))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _build_features_one_session(g: pd.DataFrame) -> pd.DataFrame:
    g = g.copy()

    # returns
    g["f_ret1"] = np.log(g["close"] / g["close"].shift(1))
    g["f_ret3"] = np.log(g["close"] / g["close"].shift(3))
    g["f_ret6"] = np.log(g["close"] / g["close"].shift(6))
    g["f_ret12"] = np.log(g["close"] / g["close"].shift(12))

    # candle shape
    g["f_range"] = (g["high"] - g["low"]) / g["close"]
    g["f_body"] = (g["close"] - g["open"]) / g["close"]
    oc_max = np.maximum(g["open"], g["close"])
    oc_min = np.minimum(g["open"], g["close"])
    g["f_wick_up"] = (g["high"] - oc_max) / g["close"]
    g["f_wick_dn"] = (oc_min - g["low"]) / g["close"]

    # volatility (min_periods tolerante)
    g["f_vol12"] = g["f_ret1"].rolling(12, min_periods=6).std()
    g["f_vol48"] = g["f_ret1"].rolling(48, min_periods=24).std()
    g["f_mom12"] = g["f_ret1"].rolling(12, min_periods=6).mean()

    # ATR (min_periods tolerante)
    prev_close = g["close"].shift(1)
    tr = np.maximum(
        g["high"] - g["low"],
        np.maximum((g["high"] - prev_close).abs(), (g["low"] - prev_close).abs()),
    )
    g["f_atr14"] = tr.rolling(14, min_periods=7).mean() / g["close"]

    # RSI
    g["f_rsi14"] = _rsi(g["close"], period=14)

    # SMA/BB (tolerante)
    m20 = g["close"].rolling(20, min_periods=10).mean()
    s20 = g["close"].rolling(20, min_periods=10).std()
    m50 = g["close"].rolling(50, min_periods=25).mean()

    g["f_sma20"] = (g["close"] / m20) - 1.0
    g["f_sma50"] = (g["close"] / m50) - 1.0
    g["f_z20"] = (g["close"] - m20) / s20
    g["f_bb_width20"] = (4.0 * s20) / g["close"]

    # MACD
    ema12 = g["close"].ewm(span=12, adjust=False).mean()
    ema26 = g["close"].ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    g["f_macd"] = macd
    g["f_macdsig"] = signal
    g["f_macdhist"] = macd - signal

    # volume ratio (se existir)
    if g["volume"].notna().any():
        vmean = g["volume"].rolling(20, min_periods=10).mean()
        g["f_volratio20"] = g["volume"] / vmean

    return g


def build_dataset(db_path: str, asset: str, interval_sec: int, out_csv: str) -> DatasetBuildResult:
    step = int(interval_sec)

    df = _load_candles(db_path, asset, step)
    df = _snap_ts(df, step)
    df = _add_sessions(df, step)

    # label delay-aware: open(t+1) -> close(t+1) (mesma sessão)
    entry_open = df["open"].shift(-1)
    expiry_close = df["close"].shift(-1)
    same_sess_next = (df["session_id"].shift(-1) == df["session_id"])

    y = (expiry_close > entry_open).astype("float64")
    y[~same_sess_next] = np.nan
    df["y_open_close"] = y

    # features por sessão
    df = df.groupby("session_id", group_keys=False).apply(_build_features_one_session)

    # remove features 100% NaN (isso é o que estava zerando o dataset em OTC)
    feature_cols = [c for c in df.columns if c.startswith("f_")]
    feature_cols = [c for c in feature_cols if df[c].notna().any()]

    keep_cols = ["ts", "open", "high", "low", "close", "volume", "session_id", "y_open_close"] + feature_cols
    out = df[keep_cols].copy()
    out = out.dropna(subset=["y_open_close"] + feature_cols).reset_index(drop=True)

    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)

    return DatasetBuildResult(path=out_csv, n_rows=int(out.shape[0]), feature_cols=feature_cols)
'@

Write-Host "dataset2.py reescrito (OTC-safe)." -ForegroundColor Green

$py = ".\.venv\Scripts\python.exe"
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK. Agora rode: .\.venv\Scripts\python.exe -m natbin.make_dataset" -ForegroundColor Yellow