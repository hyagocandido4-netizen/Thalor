$ErrorActionPreference="Stop"
Set-StrictMode -Version Latest

$path = ".\src\natbin\dataset2.py"
if(-not (Test-Path $path)){ throw "Nao achei $path" }

@'
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


EPS = 1e-12


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
    df = df.copy()
    df["ts"] = (df["ts"] // step) * step
    df = df.drop_duplicates(subset=["ts"], keep="last").sort_values("ts").reset_index(drop=True)
    return df


def _add_sessions(df: pd.DataFrame, step: int) -> pd.DataFrame:
    df = df.copy()
    gap = df["ts"].diff().fillna(step).astype("int64")

    tol = 2
    gap_ok = (gap >= (step - tol)) & (gap <= (step + tol))
    gap_too_big = gap > (step * 3)

    new_sess = ((~gap_ok) | gap_too_big).astype("int64")
    new_sess.iloc[0] = 0

    df["gap_sec"] = gap
    df["gap_too_big"] = gap_too_big.astype("int64")
    df["session_id"] = new_sess.cumsum().astype("int64")
    return df


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = gain.rolling(period, min_periods=max(5, period // 2)).mean()
    avg_loss = loss.rolling(period, min_periods=max(5, period // 2)).mean()
    avg_loss = avg_loss.replace(0.0, EPS)

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _build_features_one_session(g: pd.DataFrame) -> pd.DataFrame:
    g = g.copy()

    g["f_ret1"] = np.log(g["close"] / g["close"].shift(1))
    g["f_ret3"] = np.log(g["close"] / g["close"].shift(3))
    g["f_ret6"] = np.log(g["close"] / g["close"].shift(6))
    g["f_ret12"] = np.log(g["close"] / g["close"].shift(12))

    close_safe = g["close"].replace(0.0, np.nan)
    g["f_range"] = (g["high"] - g["low"]) / close_safe
    g["f_body"] = (g["close"] - g["open"]) / close_safe

    oc_max = np.maximum(g["open"], g["close"])
    oc_min = np.minimum(g["open"], g["close"])
    g["f_wick_up"] = (g["high"] - oc_max) / close_safe
    g["f_wick_dn"] = (oc_min - g["low"]) / close_safe

    g["f_vol12"] = g["f_ret1"].rolling(12, min_periods=6).std()
    g["f_vol48"] = g["f_ret1"].rolling(48, min_periods=24).std()
    g["f_mom12"] = g["f_ret1"].rolling(12, min_periods=6).mean()

    prev_close = g["close"].shift(1)
    tr = np.maximum(
        g["high"] - g["low"],
        np.maximum((g["high"] - prev_close).abs(), (g["low"] - prev_close).abs()),
    )
    atr = tr.rolling(14, min_periods=7).mean()
    g["f_atr14"] = atr / close_safe

    g["f_rsi14"] = _rsi(g["close"], period=14)

    m20 = g["close"].rolling(20, min_periods=10).mean()
    s20 = g["close"].rolling(20, min_periods=10).std()
    m50 = g["close"].rolling(50, min_periods=25).mean()

    g["f_sma20"] = (g["close"] / m20) - 1.0
    g["f_sma50"] = (g["close"] / m50) - 1.0
    g["f_z20"] = (g["close"] - m20) / s20
    g["f_bb_width20"] = (4.0 * s20) / close_safe

    ema12 = g["close"].ewm(span=12, adjust=False).mean()
    ema26 = g["close"].ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    g["f_macd"] = macd
    g["f_macdsig"] = signal
    g["f_macdhist"] = macd - signal

    if g["volume"].notna().any():
        vmean = g["volume"].rolling(20, min_periods=10).mean()
        denom = vmean.replace(0.0, np.nan)
        g["f_volratio20"] = g["volume"] / denom

    return g


def _cleanup_features(df: pd.DataFrame, feature_cols: list[str]) -> list[str]:
    df[feature_cols] = df[feature_cols].replace([np.inf, -np.inf], np.nan)
    nan_ratio = df[feature_cols].isna().mean()
    keep = [c for c in feature_cols if (nan_ratio.get(c, 1.0) <= 0.95) and df[c].notna().any()]
    return keep


def build_dataset(db_path: str, asset: str, interval_sec: int, out_csv: str) -> DatasetBuildResult:
    step = int(interval_sec)

    df = _load_candles(db_path, asset, step)
    df = _snap_ts(df, step)
    df = _add_sessions(df, step)

    entry_open = df["open"].shift(-1)
    expiry_close = df["close"].shift(-1)

    same_sess_next = df["session_id"].shift(-1) == df["session_id"]
    gap_next = df["ts"].shift(-1) - df["ts"]
    tol = 2
    gap_next_ok = (gap_next >= (step - tol)) & (gap_next <= (step + tol))

    y = (expiry_close > entry_open).astype("float64")
    y[~same_sess_next] = np.nan
    y[~gap_next_ok] = np.nan
    df["y_open_close"] = y

    df = df.groupby("session_id", group_keys=False).apply(_build_features_one_session)

    feature_cols = [c for c in df.columns if c.startswith("f_")]
    feature_cols = _cleanup_features(df, feature_cols)

    keep_cols = ["ts", "open", "high", "low", "close", "volume", "session_id", "y_open_close"] + feature_cols
    out = df[keep_cols].copy()
    out = out.dropna(subset=["y_open_close"] + feature_cols).reset_index(drop=True)

    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_csv, index=False)

    return DatasetBuildResult(path=out_csv, n_rows=int(out.shape[0]), feature_cols=feature_cols)
'@ | Set-Content -Encoding UTF8 $path

Write-Host "OK: dataset2.py atualizado (P0)" -ForegroundColor Green