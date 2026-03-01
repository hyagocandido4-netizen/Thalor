$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param([string]$Path, [string]$Content)
  $dir = Split-Path -Parent $Path
  if ($dir -and -not (Test-Path $dir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

$root = (Get-Location).Path
$py = Join-Path $root ".venv\Scripts\python.exe"
Require-Path $py "Nao encontrei .venv. Rode init.ps1."

Require-Path "config.yaml" "Nao achei config.yaml (rode a partir da raiz do repo)."
Require-Path "src\natbin" "Nao achei src\natbin."

# ---------------------------------------------------------------------
# (A) src/natbin/risk_report.py
# ---------------------------------------------------------------------
Write-Utf8NoBomFile "src\natbin\risk_report.py" @'
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable

import numpy as np
import pandas as pd
import yaml
from zoneinfo import ZoneInfo


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def _chunks(xs: list[int], size: int = 900) -> Iterable[list[int]]:
    for i in range(0, len(xs), size):
        yield xs[i : i + size]


def _load_cfg(path: str = "config.yaml") -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Nao encontrei {path} (rode na raiz do repo).")
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(cfg, dict):
        raise RuntimeError("config.yaml nao parece um dicionario YAML valido.")
    return cfg


def _wilson_ci(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    # Wilson score interval para proporcao binomial.
    # Retorna (low, high). Se n==0 => (nan, nan)
    if n <= 0:
        return (float("nan"), float("nan"))
    z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
    phat = k / n
    denom = 1.0 + (z * z) / n
    center = phat + (z * z) / (2.0 * n)
    rad = z * math.sqrt((phat * (1.0 - phat) + (z * z) / (4.0 * n)) / n)
    low = (center - rad) / denom
    high = (center + rad) / denom
    return (max(0.0, low), min(1.0, high))


def _fmt_pct(x: float) -> str:
    if not math.isfinite(x):
        return "nan"
    return f"{100.0 * x:.2f}%"


def _fmt_f(x: float, nd: int = 4) -> str:
    if not math.isfinite(x):
        return "nan"
    return f"{x:.{nd}f}"


def _dt_local(ts: int, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(int(ts), tz=tz).isoformat(timespec="seconds")


# ------------------------------------------------------------
# Data access
# ------------------------------------------------------------
def _read_trades(signals_db: str) -> pd.DataFrame:
    p = Path(signals_db)
    if not p.exists():
        raise FileNotFoundError(f"Nao encontrei signals_db: {signals_db}")

    con = sqlite3.connect(str(p), timeout=30)
    try:
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signals_v2'"
        ).fetchone()
        if not row:
            raise RuntimeError("Nao achei tabela signals_v2 em runs/live_signals.sqlite3")

        df = pd.read_sql_query(
            "SELECT * FROM signals_v2 WHERE action IN ('CALL','PUT') ORDER BY ts ASC",
            con,
        )
    finally:
        con.close()

    if df.empty:
        return df

    if "ts" not in df.columns or "action" not in df.columns:
        raise RuntimeError("signals_v2 precisa ter colunas ts e action.")

    df["ts"] = pd.to_numeric(df["ts"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["ts"]).copy()
    df["ts"] = df["ts"].astype("int64")
    df["action"] = df["action"].astype(str)
    return df


def _fetch_candles_oc(
    market_db: str, *, interval_sec: int, need: pd.DataFrame
) -> pd.DataFrame:
    # need: DataFrame com colunas [asset, ts] (sem duplicatas).
    # Retorna DataFrame [asset, ts, open, close]
    p = Path(market_db)
    if not p.exists():
        raise FileNotFoundError(f"Nao encontrei market_db: {market_db}")

    if need.empty:
        return pd.DataFrame(columns=["asset", "ts", "open", "close"])

    con = sqlite3.connect(str(p), timeout=30)
    try:
        out: list[pd.DataFrame] = []
        for asset, g in need.groupby("asset"):
            ts_list = sorted({int(x) for x in g["ts"].tolist()})
            if not ts_list:
                continue

            for chunk in _chunks(ts_list, size=900):
                ph = ",".join(["?"] * len(chunk))
                sql = (
                    f"SELECT asset, ts, open, close FROM candles "
                    f"WHERE asset=? AND interval_sec=? AND ts IN ({ph})"
                )
                params = [str(asset), int(interval_sec), *chunk]
                part = pd.read_sql_query(sql, con, params=params)
                out.append(part)

        if not out:
            return pd.DataFrame(columns=["asset", "ts", "open", "close"])

        df = pd.concat(out, ignore_index=True)
        df["ts"] = pd.to_numeric(df["ts"], errors="coerce").astype("Int64")
        df = df.dropna(subset=["ts"]).copy()
        df["ts"] = df["ts"].astype("int64")
        df["asset"] = df["asset"].astype(str)
        df["open"] = pd.to_numeric(df["open"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        return df
    finally:
        con.close()


# ------------------------------------------------------------
# Metrics
# ------------------------------------------------------------
@dataclass(frozen=True)
class WindowSummary:
    label: str
    n_trades: int
    wins: int
    losses: int
    ties: int

    p_hat: float
    p_low: float
    p_high: float

    payout_mean: float
    payout_ref: float
    payout_ref_q: float

    pnl_total: float
    pnl_per_trade: float

    ev_hat: float
    ev_low: float

    kelly_f_star: float
    stake_frac_suggested: float
    stake_suggested: float | None

    start_ts: int | None
    end_ts: int | None
    start_dt_local: str | None
    end_dt_local: str | None


def _compute_summary(
    df: pd.DataFrame,
    *,
    label: str,
    tz: ZoneInfo,
    tie_policy: str,
    payout_ref_q: float,
    min_trades: int,
    bankroll: float | None,
    kelly_frac: float,
    cap_frac: float,
    alpha: float,
) -> WindowSummary:
    if df.empty:
        return WindowSummary(
            label=label,
            n_trades=0,
            wins=0,
            losses=0,
            ties=0,
            p_hat=float("nan"),
            p_low=float("nan"),
            p_high=float("nan"),
            payout_mean=float("nan"),
            payout_ref=float("nan"),
            payout_ref_q=float(payout_ref_q),
            pnl_total=0.0,
            pnl_per_trade=float("nan"),
            ev_hat=float("nan"),
            ev_low=float("nan"),
            kelly_f_star=0.0,
            stake_frac_suggested=0.0,
            stake_suggested=None if bankroll is None else 0.0,
            start_ts=None,
            end_ts=None,
            start_dt_local=None,
            end_dt_local=None,
        )

    wins = int(df["is_win"].sum())
    ties = int(df["is_tie"].sum())
    losses = int((~df["is_win"] & ~df["is_tie"]).sum())

    if tie_policy == "push":
        n_bern = wins + losses
        wins_for_ci = wins
    else:
        # tie conta como loss no Bernoulli
        n_bern = wins + losses + ties
        wins_for_ci = wins

    p_hat = wins_for_ci / n_bern if n_bern > 0 else float("nan")
    p_low, p_high = _wilson_ci(wins_for_ci, n_bern, alpha=alpha)

    payout_used = df["payout_used"].to_numpy(dtype=float)
    payout_mean = float(np.nanmean(payout_used)) if payout_used.size else float("nan")
    payout_ref = (
        float(np.nanquantile(payout_used, payout_ref_q))
        if payout_used.size
        else float("nan")
    )

    # PnL (unidades de 1 stake)
    pnl_total = float(np.nansum(df["pnl"].to_numpy(dtype=float)))
    pnl_per_trade = pnl_total / float(df.shape[0]) if df.shape[0] else float("nan")

    # EV (modelo bernoulli) em unidades de stake, usando payout_ref conservador
    if math.isfinite(payout_ref) and payout_ref > 0 and math.isfinite(p_hat):
        ev_hat = p_hat * payout_ref - (1.0 - p_hat)
    else:
        ev_hat = float("nan")

    if math.isfinite(payout_ref) and payout_ref > 0 and math.isfinite(p_low):
        ev_low = p_low * payout_ref - (1.0 - p_low)
    else:
        ev_low = float("nan")

    # Kelly (para payoff win=+b, loss=-1): f* = p - (1-p)/b
    if math.isfinite(payout_ref) and payout_ref > 0 and math.isfinite(p_low):
        kelly_f_star = p_low - (1.0 - p_low) / payout_ref
        if not math.isfinite(kelly_f_star):
            kelly_f_star = 0.0
    else:
        kelly_f_star = 0.0

    kelly_f_star = max(0.0, float(kelly_f_star))

    stake_frac = 0.0
    if n_bern >= int(min_trades) and math.isfinite(ev_low) and ev_low > 0.0:
        stake_frac = min(float(cap_frac), float(kelly_frac) * kelly_f_star)

    stake = None
    if bankroll is not None:
        stake = float(bankroll) * stake_frac

    start_ts = int(df["ts"].min())
    end_ts = int(df["ts"].max())
    return WindowSummary(
        label=label,
        n_trades=int(df.shape[0]),
        wins=wins,
        losses=losses + (ties if tie_policy != "push" else 0),
        ties=ties if tie_policy == "push" else 0,
        p_hat=float(p_hat),
        p_low=float(p_low),
        p_high=float(p_high),
        payout_mean=float(payout_mean),
        payout_ref=float(payout_ref),
        payout_ref_q=float(payout_ref_q),
        pnl_total=float(pnl_total),
        pnl_per_trade=float(pnl_per_trade),
        ev_hat=float(ev_hat),
        ev_low=float(ev_low),
        kelly_f_star=float(kelly_f_star),
        stake_frac_suggested=float(stake_frac),
        stake_suggested=stake,
        start_ts=start_ts,
        end_ts=end_ts,
        start_dt_local=_dt_local(start_ts, tz),
        end_dt_local=_dt_local(end_ts, tz),
    )


def _pretty_print(summary: WindowSummary, *, payout_break_even: float | None) -> None:
    if summary.n_trades == 0:
        print(f"\n[{summary.label}] sem trades (CALL/PUT) no periodo.")
        return

    q = int(round(100.0 * summary.payout_ref_q))
    print(f"\n[{summary.label}] {summary.start_dt_local} -> {summary.end_dt_local}")
    print(
        f"  trades: {summary.n_trades} | wins: {summary.wins} | losses: {summary.losses} | ties: {summary.ties}"
    )
    print(
        f"  win_rate: {_fmt_pct(summary.p_hat)} | Wilson 95% CI: [{_fmt_pct(summary.p_low)}, {_fmt_pct(summary.p_high)}]"
    )
    print(
        f"  payout_used: mean={_fmt_f(summary.payout_mean, 4)} | p{q}={_fmt_f(summary.payout_ref, 4)}"
    )
    if payout_break_even is not None and math.isfinite(payout_break_even):
        print(f"  break-even p (payout_ref): {_fmt_pct(payout_break_even)}")
    print(
        f"  pnl_total (stake units): {_fmt_f(summary.pnl_total, 4)} | pnl/trade: {_fmt_f(summary.pnl_per_trade, 4)}"
    )
    print(
        f"  EV_hat (payout_ref): {_fmt_f(summary.ev_hat, 4)} | EV_low (p_low): {_fmt_f(summary.ev_low, 4)}"
    )
    print(
        f"  Kelly f* (p_low): {_fmt_f(summary.kelly_f_star, 4)} | stake_frac_suggested: {_fmt_pct(summary.stake_frac_suggested)}"
    )
    if summary.stake_suggested is not None:
        print(f"  stake_suggested (@bankroll): {_fmt_f(summary.stake_suggested, 2)}")


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(
        prog="natbin.risk_report",
        description="Risk report (P3) — win-rate CI (Wilson) + EV_low + stake sizing (fractional Kelly).",
    )
    ap.add_argument("--signals-db", default="runs/live_signals.sqlite3")
    ap.add_argument("--market-db", default="")
    ap.add_argument("--asset", default="")
    ap.add_argument("--interval-sec", type=int, default=0)
    ap.add_argument("--windows", default="30,60,120", help="Ex: 30,60,120")
    ap.add_argument("--tie", choices=["loss", "push"], default="loss")
    ap.add_argument("--outcome", choices=["open_close", "close_close"], default="open_close",
                    help="Modo de avaliacao do trade. open_close = (close(ts+1) vs open(ts+1)) [default]; close_close = (close(ts+1) vs close(ts)).")
    ap.add_argument("--payout-default", type=float, default=float(os.getenv("PAYOUT", "0.8")))
    ap.add_argument("--payout-ref-q", type=float, default=0.10, help="Quantil do payout p/ modo conservador (default=0.10).")
    ap.add_argument("--min-trades", type=int, default=10)
    ap.add_argument("--bankroll", type=float, default=None)
    ap.add_argument("--kelly-frac", type=float, default=0.25)
    ap.add_argument("--cap-frac", type=float, default=0.02)
    ap.add_argument("--alpha", type=float, default=0.05, help="Alpha do CI (default=0.05 => 95%).")
    ap.add_argument("--out-json", default="", help="Opcional: salva um JSON com o resumo.")
    ap.add_argument("--out-trades-csv", default="", help="Opcional: salva CSV com trades + labels (win/loss).")

    args = ap.parse_args()

    cfg = _load_cfg("config.yaml")
    data = cfg.get("data", {}) or {}

    tzname = str(data.get("timezone") or "America/Sao_Paulo")
    tz = ZoneInfo(tzname)

    asset_fallback = str(args.asset or data.get("asset") or "").strip()
    interval_sec = int(args.interval_sec or data.get("interval_sec") or 300)

    market_db = str(args.market_db or data.get("db_path") or "data/market_otc.sqlite3")

    df = _read_trades(args.signals_db)
    if df.empty:
        print("Nenhum trade CALL/PUT encontrado em signals_v2.")
        return

    # Asset efetivo por linha (fallback quando coluna asset estiver vazia)
    if "asset" in df.columns:
        df["asset_eff"] = df["asset"].astype("string").fillna("").astype(str)
        df.loc[df["asset_eff"].str.strip() == "", "asset_eff"] = asset_fallback
    else:
        df["asset_eff"] = asset_fallback

    if not asset_fallback and df["asset_eff"].isna().any():
        raise RuntimeError(
            "Nao consegui inferir asset. Passe --asset ou verifique config.yaml."
        )

    # payout per trade (fallback se vier NaN)
    payout_col = df["payout"] if "payout" in df.columns else None
    if payout_col is not None:
        payout_used = pd.to_numeric(payout_col, errors="coerce")
        payout_used = payout_used.fillna(float(args.payout_default))
    else:
        payout_used = pd.Series([float(args.payout_default)] * df.shape[0])

    df["payout_used"] = payout_used.astype("float64")

    df["ts_next"] = df["ts"] + int(interval_sec)

    need = (
        pd.concat(
            [
                df[["asset_eff", "ts"]].rename(columns={"asset_eff": "asset"}),
                df[["asset_eff", "ts_next"]]
                .rename(columns={"asset_eff": "asset", "ts_next": "ts"}),
            ],
            ignore_index=True,
        )
        .dropna()
        .drop_duplicates()
    )
    need["asset"] = need["asset"].astype(str)
    need["ts"] = need["ts"].astype("int64")

    candles_oc = _fetch_candles_oc(market_db, interval_sec=interval_sec, need=need)

    # Merge candle em ts (para close_now)
    df = df.merge(
        candles_oc.rename(columns={"open": "open_now", "close": "close_now"}),
        left_on=["asset_eff", "ts"],
        right_on=["asset", "ts"],
        how="left",
    )
    df = df.drop(columns=["asset"], errors="ignore")

    # Merge candle em ts_next (para open_next/close_next)
    df = df.merge(
        candles_oc.rename(
            columns={"ts": "ts_next", "open": "open_next", "close": "close_next"}
        ),
        left_on=["asset_eff", "ts_next"],
        right_on=["asset", "ts_next"],
        how="left",
    )
    df = df.drop(columns=["asset"], errors="ignore")

    for c in ["open_now", "close_now", "open_next", "close_next"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # só trades com candle next completo
    df = df.dropna(subset=["open_next", "close_next"]).copy()
    if df.empty:
        print("Nao consegui casar nenhum trade com candle NEXT (open_next/close_next).")
        print("Cheque se market_db / asset / interval_sec batem com o LIVE.")
        return

    # Base de comparacao conforme modo
    if args.outcome == "open_close":
        base = df["open_next"].to_numpy(dtype=float)
    else:
        # close_close: usa close do candle atual (ts)
        df = df.dropna(subset=["close_now"]).copy()
        if df.empty:
            print("Faltou close_now para outcome=close_close (sem candle em ts).")
            return
        base = df["close_now"].to_numpy(dtype=float)

    close_next = df["close_next"].to_numpy(dtype=float)
    is_tie = np.isclose(close_next, base, atol=0.0, rtol=0.0)

    action = df["action"].astype(str).str.upper().to_numpy()
    is_call = action == "CALL"
    is_put = action == "PUT"

    is_win = np.zeros(df.shape[0], dtype=bool)
    is_win[is_call] = close_next[is_call] > base[is_call]
    is_win[is_put] = close_next[is_put] < base[is_put]
    # ties não são win (tratamento vem no pnl / CI conforme --tie)

    df["is_tie"] = is_tie
    df["is_win"] = is_win

    # pnl
    payout_used = df["payout_used"].to_numpy(dtype=float)
    pnl = np.full(df.shape[0], -1.0, dtype=float)  # default: loss
    pnl[is_win] = payout_used[is_win]
    if args.tie == "push":
        pnl[is_tie] = 0.0
    df["pnl"] = pnl

    # windows
    summaries: list[WindowSummary] = []

    end_ts = int(df["ts"].max())
    windows = []
    for part in str(args.windows).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            windows.append(int(part))
        except Exception:
            continue
    if not windows:
        windows = [30, 60, 120]

    # overall
    summaries.append(
        _compute_summary(
            df,
            label="overall",
            tz=tz,
            tie_policy=args.tie,
            payout_ref_q=float(args.payout_ref_q),
            min_trades=int(args.min_trades),
            bankroll=args.bankroll,
            kelly_frac=float(args.kelly_frac),
            cap_frac=float(args.cap_frac),
            alpha=float(args.alpha),
        )
    )

    # rolling
    for wd in windows:
        start = end_ts - int(wd) * 86400
        sub = df[df["ts"] >= start].copy()
        summaries.append(
            _compute_summary(
                sub,
                label=f"rolling_{wd}d",
                tz=tz,
                tie_policy=args.tie,
                payout_ref_q=float(args.payout_ref_q),
                min_trades=int(args.min_trades),
                bankroll=args.bankroll,
                kelly_frac=float(args.kelly_frac),
                cap_frac=float(args.cap_frac),
                alpha=float(args.alpha),
            )
        )

    # header
    print("\n=== RISK REPORT v2 (P3) ===")
    print(f"as_of: {_dt_local(end_ts, tz)} | tz={tzname}")
    print(f"signals_db: {args.signals_db}")
    print(f"market_db:  {market_db}")
    print(f"interval_sec: {interval_sec} | tie_policy: {args.tie} | outcome: {args.outcome}")
    print(
        f"payout_default: {_fmt_f(float(args.payout_default),4)} | payout_ref_q: {float(args.payout_ref_q):.2f}"
    )
    print(
        f"stake_sizing: min_trades={int(args.min_trades)} | kelly_frac={float(args.kelly_frac):.2f} | cap_frac={float(args.cap_frac):.2f}"
    )
    if args.bankroll is not None:
        print(f"bankroll: {float(args.bankroll):.2f}")

    # payout break-even baseado no payout_ref do OVERALL
    payout_ref_overall = summaries[0].payout_ref
    p_be = None
    if math.isfinite(payout_ref_overall) and payout_ref_overall > 0:
        p_be = 1.0 / (1.0 + payout_ref_overall)

    for s in summaries:
        _pretty_print(s, payout_break_even=p_be if s.label == "overall" else None)

    # outputs
    if args.out_trades_csv:
        outp = Path(args.out_trades_csv)
        outp.parent.mkdir(parents=True, exist_ok=True)
        cols = [
            "dt_local",
            "day",
            "ts",
            "asset_eff",
            "action",
            "payout_used",
            "open_now",
            "close_now",
            "open_next",
            "close_next",
            "is_win",
            "is_tie",
            "pnl",
        ]
        keep = [c for c in cols if c in df.columns]
        df[keep].to_csv(outp, index=False)
        print(f"\ntrades_csv: {outp}")

    if args.out_json:
        outp = Path(args.out_json)
        outp.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "as_of_ts": end_ts,
            "as_of_dt_local": _dt_local(end_ts, tz),
            "timezone": tzname,
            "signals_db": str(args.signals_db),
            "market_db": str(market_db),
            "interval_sec": int(interval_sec),
            "tie_policy": str(args.tie),
            "outcome": str(args.outcome),
            "payout_default": float(args.payout_default),
            "payout_ref_q": float(args.payout_ref_q),
            "min_trades": int(args.min_trades),
            "bankroll": None if args.bankroll is None else float(args.bankroll),
            "kelly_frac": float(args.kelly_frac),
            "cap_frac": float(args.cap_frac),
            "windows": [asdict(x) for x in summaries],
        }
        outp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nout_json: {outp}")


if __name__ == "__main__":
    main()
'@

# ---------------------------------------------------------------------
# (B) scripts/tools/risk_report.ps1
# ---------------------------------------------------------------------
Write-Utf8NoBomFile "scripts\tools\risk_report.ps1" @'
param(
  [double]$Bankroll = 1000,
  [string]$Windows = "30,60,120",
  [int]$MinTrades = 10,
  [ValidateSet("loss","push")][string]$Tie = "loss",
  [ValidateSet("open_close","close_close")][string]$Outcome = "open_close",
  [double]$PayoutDefault = 0.8,
  [double]$KellyFrac = 0.25,
  [double]$CapFrac = 0.02,
  [string]$SignalsDb = "runs/live_signals.sqlite3",
  [string]$MarketDb = "",
  [string]$Asset = "",
  [int]$IntervalSec = 0,
  [switch]$Save
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Path {
  param([string]$Path, [string]$Msg)
  if (-not (Test-Path $Path)) { throw $Msg }
}

# Garante rodar no ROOT do repo (Start in do Agendador)
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location $root
try {
  $py = Join-Path $root ".venv\Scripts\python.exe"
  Require-Path $py "Nao encontrei .venv.`nRode scripts/setup/init.ps1 (ou init.ps1) para criar a venv."
  Require-Path "config.yaml" "Nao encontrei config.yaml."
  Require-Path $SignalsDb "Nao encontrei signals db: $SignalsDb"

  $args = @(
    "-m","natbin.risk_report",
    "--signals-db", $SignalsDb,
    "--windows", $Windows,
    "--min-trades", "$MinTrades",
    "--tie", $Tie,
    "--outcome", $Outcome,
    "--payout-default", "$PayoutDefault",
    "--kelly-frac", "$KellyFrac",
    "--cap-frac", "$CapFrac",
    "--bankroll", "$Bankroll"
  )

  if ($MarketDb) { $args += @("--market-db", $MarketDb) }
  if ($Asset)    { $args += @("--asset", $Asset) }
  if ($IntervalSec -gt 0) { $args += @("--interval-sec", "$IntervalSec") }

  if ($Save) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outJson = Join-Path "runs" ("risk_report_{0}.json" -f $stamp)
    $outCsv  = Join-Path "runs" ("risk_trades_{0}.csv" -f $stamp)
    $args += @("--out-json", $outJson, "--out-trades-csv", $outCsv)
  }

  $pretty = (@($py) + $args | ForEach-Object { if ($_ -match "\s") { '"' + $_ + '"' } else { $_ } }) -join " "
  Write-Host ">> $pretty" -ForegroundColor DarkGray
  & $py @args
  if ($LASTEXITCODE -ne 0) { throw "risk_report falhou" }
} finally {
  Pop-Location
}
'@

# ---------------------------------------------------------------------
# (C) docs/risk_report.md
# ---------------------------------------------------------------------
Write-Utf8NoBomFile "docs\risk_report.md" @'
# Risk report v2 (P3)

> ⚠️ Alto risco: este projeto lida com sinais/automação para opções binárias.
> Este relatório NÃO é promessa de ganho — é engenharia + evidência + controle de risco.

## Objetivo

Transformar o LIVE em algo **controlável** via estatística:

1) O bot está acima do break-even nos trades tomados?
2) Quão confiante estamos nisso? (intervalo de confiança)
3) Qual stake faz sentido sem overbet? (Kelly fracionado + cap)

## Fonte de dados (atual)

- `runs/live_signals.sqlite3` — tabela `signals_v2`
  - filtramos apenas trades tomados: `action in ('CALL','PUT')`
- `data/market_otc.sqlite3` — tabela `candles`
  - por padrão (`outcome=open_close`) avaliamos o trade como:
    - **entrada** no `open` do candle `ts_next = ts + interval_sec`
    - **expiração** no `close` do candle `ts_next`
  - win (open_close):
    - CALL: close_next > open_next
    - PUT:  close_next < open_next
  - opção alternativa (`outcome=close_close`) para aproximar entrada por close:
    - CALL: close_next > close_now
    - PUT:  close_next < close_now
  - tie:
    - default: loss
    - opcional: push (`--tie push`)

## Como rodar (PowerShell)

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000
```

Exemplo (modo close_close):

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000 -Outcome close_close
```

Com salvar artefatos:

```powershell
pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000 -Save
# outputs:
#   runs/risk_report_YYYYMMDD_HHMMSS.json
#   runs/risk_trades_YYYYMMDD_HHMMSS.csv
```

## Como rodar (Python)

```powershell
.\.venv\Scripts\python.exe -m natbin.risk_report --signals-db runs/live_signals.sqlite3
```

## Métricas

Para cada janela (overall + rolling 30/60/120 dias):

- `p_hat`: win rate observado
- `Wilson CI`: intervalo conservador para win rate (usamos p_low)
- `payout_ref`: por default usamos **p10** do payout observado (conservador)
- `EV_low`: `p_low * payout_ref - (1 - p_low)`
- `stake_suggested`:
  - Kelly para payoff binário: `f* = p - (1-p)/b`
  - substitui `p` por `p_low`
  - aplica `kelly_frac` (default 0.25)
  - aplica cap duro `cap_frac` (default 0.02)
  - só sugere se `n_trades >= min_trades` e `EV_low > 0`

## Notas

- Se o bot é ultra-seletivo (Top‑K), o n de trades é pequeno: o CI vai ser largo.
- A recomendação de stake é propositalmente conservadora.
'@

# ---------------------------------------------------------------------
# (D) Preflight (syntax)
# ---------------------------------------------------------------------
& $py -m compileall .\src\natbin | Out-Null
if ($LASTEXITCODE -ne 0) { throw "compileall falhou" }

Write-Host "OK. Risk report v2 (P3) instalado." -ForegroundColor Green
Write-Host "Rodar:" -ForegroundColor Yellow
Write-Host "  pwsh -ExecutionPolicy Bypass -File .\scripts\tools\risk_report.ps1 -Bankroll 1000" -ForegroundColor Yellow
