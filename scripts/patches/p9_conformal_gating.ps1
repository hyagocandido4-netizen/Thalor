# P9: Conformal uncertainty gating (CP) + wiring in CLI/observe/tune
# - Adds gate_mode="cp" (conformal) on top of meta score (P(correct))
# - Uses Mondrian bins on score for more local calibration (fallback to global)
# - Bumps GATE_VERSION -> meta_v2 (forces cache retrain)
#
# Safe to run multiple times.
#
# After patch:
#   - paper: --gate-mode cp
#   - tune:  --gate-mode cp
#   - live:  set env GATE_MODE=cp (or config best.gate_mode=cp)
#   - control via env:
#       CP_ALPHA   (default 0.10)
#       CP_FRAC    (default 0.25)  # slice of meta_df reserved for CP calibration
#       CP_MIN     (default 200)   # minimum rows for CP calibration slice
#       CP_BINS    (default 10)    # Mondrian bins on score quantiles
#       CP_MIN_BIN (default 30)    # min calib samples per bin; else use global
#
#requires -Version 7.0
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Utf8NoBomFile {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Content
  )
  $dir = Split-Path -Parent $Path
  if ($dir -and !(Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $normalized = $Content.Replace("`r`n", "`n").Replace("`r", "`n")
  Set-Content -Path $Path -Value $normalized -Encoding utf8NoBOM
  Write-Host "Wrote: $Path"
}

function Replace-InFile {
  param(
    [Parameter(Mandatory=$true)][string]$Path,
    [Parameter(Mandatory=$true)][string]$Old,
    [Parameter(Mandatory=$true)][string]$New
  )
  if (!(Test-Path $Path)) {
    Write-Host "Skip (missing): $Path"
    return
  }
  $txt = Get-Content -Raw -Encoding UTF8 $Path
  if ($txt -notlike "*$Old*") {
    # try regex-escape fallback? keep simple
    Write-Host "Pattern not found in $Path (skipped): $Old"
    return
  }
  $txt2 = $txt.Replace($Old, $New)
  Set-Content -Path $Path -Value $txt2 -Encoding utf8NoBOM
  Write-Host "Patched: $Path"
}

# --- src/natbin/gate_meta.py (rewrite with CP support) ---
$gateMeta = @'
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Tuple

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# Bump quando você mudar as meta-features / CP (força retrain do cache no observe)
GATE_VERSION = "meta_v2"

META_FEATURES = [
    "conf",
    "proba_up",
    "signed_margin",
    "abs_margin",
    "vol",
    "bb",
    "atr",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "iso_score",
]


def _time_feats(ts: np.ndarray, tz: ZoneInfo) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    dt = pd.to_datetime(ts, unit="s", utc=True).tz_convert(tz)
    hour = dt.hour.astype(float) + dt.minute.astype(float) / 60.0
    dow = dt.dayofweek.astype(float)
    hr = 2.0 * np.pi * (hour / 24.0)
    dr = 2.0 * np.pi * (dow / 7.0)
    return np.sin(hr), np.cos(hr), np.sin(dr), np.cos(dr)


def build_meta_X(
    *,
    ts: np.ndarray,
    tz: ZoneInfo,
    proba_up: np.ndarray,
    conf: np.ndarray,
    vol: np.ndarray,
    bb: np.ndarray,
    atr: np.ndarray,
    iso_score: np.ndarray | None,
) -> np.ndarray:
    if iso_score is None:
        iso_score = conf

    signed = proba_up - 0.5
    abs_m = np.abs(signed)
    hsin, hcos, dsin, dcos = _time_feats(ts, tz)

    X = np.column_stack(
        [
            conf,
            proba_up,
            signed,
            abs_m,
            vol,
            bb,
            atr,
            hsin,
            hcos,
            dsin,
            dcos,
            iso_score,
        ]
    )
    return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)


def _fit_iso(conf: np.ndarray, correct: np.ndarray) -> IsotonicRegression | None:
    try:
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(conf.astype(float), correct.astype(float))
        return iso
    except Exception:
        return None


def _fit_meta_model(X: np.ndarray, y: np.ndarray, meta_model_type: str) -> Any | None:
    # meta precisa de tamanho mínimo, senão vira ruído
    if len(X) < 200:
        return None
    if len(np.unique(y)) < 2:
        return None

    meta_model_type = (meta_model_type or "logreg").strip().lower()
    if meta_model_type not in ("logreg", "hgb"):
        meta_model_type = "logreg"

    if meta_model_type == "hgb":
        meta = HistGradientBoostingClassifier(
            max_depth=2,
            learning_rate=0.05,
            max_iter=250,
            random_state=0,
        )
        meta.fit(X, y)
        return meta

    # logreg default
    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(max_iter=500, solver="lbfgs")),
        ]
    )
    pipe.fit(X, y)
    return pipe


@dataclass
class ConformalCP:
    """
    Mondrian (bin-wise) conformal calibration for the correctness score s = P(correct).

    Nonconformity:
      a(x, y=1) = 1 - s
      a(x, y=0) = s

    Calibration scores are computed with the TRUE label (correct/incorrect).
    For gating, we accept only if the conformal prediction set is {1} at level alpha.
    """

    scores_global: np.ndarray  # sorted asc, shape (n,)
    edges: np.ndarray | None = None  # shape (n_bins+1,)
    scores_by_bin: list[np.ndarray] | None = None  # list of sorted arrays per bin
    min_bin: int = 30

    @staticmethod
    def _pvals(scores_sorted: np.ndarray, a: np.ndarray) -> np.ndarray:
        n = int(len(scores_sorted))
        if n <= 0:
            return np.zeros_like(a, dtype=float)
        idx = np.searchsorted(scores_sorted, a, side="left")
        ge = n - idx
        return (ge + 1.0) / (n + 1.0)

    def p_values(self, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        s = np.clip(s.astype(float), 0.0, 1.0)
        a1 = 1.0 - s
        a0 = s

        p1 = self._pvals(self.scores_global, a1)
        p0 = self._pvals(self.scores_global, a0)

        if self.edges is None or self.scores_by_bin is None:
            return p1, p0

        # Assign bin by score quantile edges
        bin_idx = np.searchsorted(self.edges, s, side="right") - 1
        bin_idx = np.clip(bin_idx, 0, len(self.scores_by_bin) - 1)

        for b, sc in enumerate(self.scores_by_bin):
            if sc is None or len(sc) < int(self.min_bin):
                continue
            mask = bin_idx == b
            if not np.any(mask):
                continue
            p1[mask] = self._pvals(sc, a1[mask])
            p0[mask] = self._pvals(sc, a0[mask])

        return p1, p0

    def accept_mask(self, s: np.ndarray, alpha: float) -> np.ndarray:
        alpha = float(alpha)
        if alpha < 0.0:
            alpha = 0.0
        if alpha > 1.0:
            alpha = 1.0
        p1, p0 = self.p_values(s)
        # prediction set == {1}
        return (p1 > alpha) & (p0 <= alpha)


def fit_conformal_cp(
    *,
    s: np.ndarray,
    correct: np.ndarray,
    bins: int = 10,
    min_bin: int = 30,
) -> ConformalCP | None:
    s = np.clip(np.asarray(s, dtype=float), 0.0, 1.0)
    correct = np.asarray(correct, dtype=int)

    # nonconformity score for true label
    a = np.where(correct == 1, 1.0 - s, s).astype(float)
    a = np.clip(a, 0.0, 1.0)

    if len(a) < max(50, int(min_bin) * 2):
        return None

    scores_global = np.sort(a)

    bins = int(bins)
    min_bin = int(min_bin)

    if bins < 2:
        return ConformalCP(scores_global=scores_global, edges=None, scores_by_bin=None, min_bin=min_bin)

    try:
        qs = np.linspace(0.0, 1.0, bins + 1)
        edges = np.quantile(s, qs)
        edges[0] = 0.0
        edges[-1] = 1.0
        edges = np.unique(edges)

        if len(edges) < 3:
            return ConformalCP(scores_global=scores_global, edges=None, scores_by_bin=None, min_bin=min_bin)

        n_bins_eff = int(len(edges) - 1)
        bin_idx = np.searchsorted(edges, s, side="right") - 1
        bin_idx = np.clip(bin_idx, 0, n_bins_eff - 1)

        scores_by_bin: list[np.ndarray] = []
        for b in range(n_bins_eff):
            mask = bin_idx == b
            if not np.any(mask):
                scores_by_bin.append(np.array([], dtype=float))
            else:
                scores_by_bin.append(np.sort(a[mask]))

        return ConformalCP(scores_global=scores_global, edges=edges, scores_by_bin=scores_by_bin, min_bin=min_bin)
    except Exception:
        return ConformalCP(scores_global=scores_global, edges=None, scores_by_bin=None, min_bin=min_bin)


def _unpack_meta(meta_model: Any | None) -> tuple[Any | None, ConformalCP | None]:
    if isinstance(meta_model, dict):
        return meta_model.get("model", None), meta_model.get("cp", None)
    return meta_model, None


def train_base_cal_iso_meta(
    train_df: pd.DataFrame,
    feat_cols: list[str],
    tz: ZoneInfo,
    meta_model_type: str = "logreg",
) -> tuple[CalibratedClassifierCV, IsotonicRegression | None, Any | None]:
    """
    Treina:
      1) base HGB (em sub-treino)
      2) calibração sigmoid (em cal)
      3) iso gate (conf -> P(acertar))
      4) meta gate P2.2 (X_meta -> P(acertar))
      5) P9: conformal calibrator (CP) sobre score de meta (abstain/gating)
    """
    n = int(len(train_df))
    if n < 500:
        raise ValueError(f"Treino insuficiente (n={n}).")

    sub_frac = float(os.getenv("SUB_FRAC", "0.70"))
    cal_frac = float(os.getenv("CAL_FRAC", "0.15"))
    min_part = int(os.getenv("MIN_PART", "200"))

    n_sub = int(n * sub_frac)
    n_cal = int(n * cal_frac)
    n_meta = n - n_sub - n_cal

    # fallback: 80/20 se ficar pequeno demais
    if n_sub < min_part or n_cal < min_part:
        n_sub = int(n * 0.80)
        n_cal = n - n_sub
        n_meta = 0

    sub_df = train_df.iloc[:n_sub]
    cal_df = train_df.iloc[n_sub : n_sub + n_cal]

    # meta: se não tiver meta suficiente, usa cal+meta juntos
    meta_start = (n_sub + n_cal) if n_meta >= min_part else n_sub
    meta_df = train_df.iloc[meta_start:]

    base = HistGradientBoostingClassifier(
        max_depth=3,
        learning_rate=0.05,
        max_iter=300,
        random_state=0,
    )
    base.fit(sub_df[feat_cols], sub_df["y_open_close"])

    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(cal_df[feat_cols], cal_df["y_open_close"])

    # ISO gate em cima do CAL
    p_cal = cal.predict_proba(cal_df[feat_cols])[:, 1]
    pred_cal = (p_cal >= 0.5).astype(int)
    y_cal = cal_df["y_open_close"].to_numpy(dtype=int)
    correct_cal = (pred_cal == y_cal).astype(int)
    conf_cal = np.maximum(p_cal, 1.0 - p_cal)
    iso = _fit_iso(conf_cal, correct_cal)

    # META + CP (precisam de meta_df suficiente)
    meta_pack: Any | None = None
    if len(meta_df) >= min_part:
        # split meta_df -> meta_train_df + cp_df (tail)
        cp_frac = float(os.getenv("CP_FRAC", "0.25"))
        cp_min = int(os.getenv("CP_MIN", "200"))
        cp_bins = int(os.getenv("CP_BINS", "10"))
        cp_min_bin = int(os.getenv("CP_MIN_BIN", "30"))

        meta_train_df = meta_df
        cp_df = meta_df.iloc[:0]

        if len(meta_df) >= (min_part * 2):
            n_meta_df = int(len(meta_df))
            n_cp = max(int(n_meta_df * cp_frac), cp_min)
            if (n_meta_df - n_cp) < min_part:
                n_cp = max(0, n_meta_df - min_part)

            if n_cp >= min_part and (n_meta_df - n_cp) >= min_part:
                meta_train_df = meta_df.iloc[: n_meta_df - n_cp]
                cp_df = meta_df.iloc[n_meta_df - n_cp :]

        # train meta_model em meta_train_df
        p_m = cal.predict_proba(meta_train_df[feat_cols])[:, 1]
        pred_m = (p_m >= 0.5).astype(int)
        y_m = meta_train_df["y_open_close"].to_numpy(dtype=int)
        correct_m = (pred_m == y_m).astype(int)
        conf_m = np.maximum(p_m, 1.0 - p_m)

        iso_m = iso.predict(conf_m.astype(float)) if iso is not None else conf_m
        X_meta = build_meta_X(
            ts=meta_train_df["ts"].to_numpy(dtype=int),
            tz=tz,
            proba_up=p_m.astype(float),
            conf=conf_m.astype(float),
            vol=meta_train_df["f_vol48"].to_numpy(dtype=float),
            bb=meta_train_df["f_bb_width20"].to_numpy(dtype=float),
            atr=meta_train_df["f_atr14"].to_numpy(dtype=float),
            iso_score=iso_m.astype(float),
        )
        meta_model = _fit_meta_model(X_meta, correct_m, meta_model_type)

        # CP calibrator (se cp_df tem tamanho)
        cp: ConformalCP | None = None
        if meta_model is not None and len(cp_df) >= min_part:
            try:
                p_cp = cal.predict_proba(cp_df[feat_cols])[:, 1]
                pred_cp = (p_cp >= 0.5).astype(int)
                y_cp = cp_df["y_open_close"].to_numpy(dtype=int)
                correct_cp = (pred_cp == y_cp).astype(int)
                conf_cp = np.maximum(p_cp, 1.0 - p_cp)
                iso_cp = iso.predict(conf_cp.astype(float)) if iso is not None else conf_cp

                X_cp = build_meta_X(
                    ts=cp_df["ts"].to_numpy(dtype=int),
                    tz=tz,
                    proba_up=p_cp.astype(float),
                    conf=conf_cp.astype(float),
                    vol=cp_df["f_vol48"].to_numpy(dtype=float),
                    bb=cp_df["f_bb_width20"].to_numpy(dtype=float),
                    atr=cp_df["f_atr14"].to_numpy(dtype=float),
                    iso_score=iso_cp.astype(float),
                )
                s_cp = meta_model.predict_proba(X_cp)[:, 1].astype(float)
                cp = fit_conformal_cp(s=s_cp, correct=correct_cp, bins=cp_bins, min_bin=cp_min_bin)
            except Exception:
                cp = None

        meta_pack = {"model": meta_model, "cp": cp}

    return cal, iso, meta_pack


def compute_scores(
    df: pd.DataFrame,
    feat_cols: list[str],
    tz: ZoneInfo,
    cal_model: CalibratedClassifierCV,
    iso: IsotonicRegression | None,
    meta_model: Any | None,
    gate_mode: str = "meta",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    """
    Retorna: proba_up, conf, score, gate_used

    score:
      - gate_mode=meta: P(acertar) via meta-model
      - gate_mode=iso : P(acertar) via isotonic(conf)
      - gate_mode=conf: conf (fallback)
      - gate_mode=cp  : P(acertar) via meta-model, mas zera score quando CP rejeita (HOLD)
    """
    gate_mode = (gate_mode or "meta").strip().lower()
    if gate_mode not in ("meta", "iso", "conf", "cp"):
        gate_mode = "meta"

    proba = cal_model.predict_proba(df[feat_cols])[:, 1].astype(float)
    conf = np.maximum(proba, 1.0 - proba).astype(float)

    iso_score = iso.predict(conf.astype(float)).astype(float) if iso is not None else conf

    meta_obj, cp = _unpack_meta(meta_model)

    # META score (se disponível)
    meta_score: np.ndarray | None = None
    if meta_obj is not None:
        X = build_meta_X(
            ts=df["ts"].to_numpy(dtype=int),
            tz=tz,
            proba_up=proba,
            conf=conf,
            vol=df["f_vol48"].to_numpy(dtype=float),
            bb=df["f_bb_width20"].to_numpy(dtype=float),
            atr=df["f_atr14"].to_numpy(dtype=float),
            iso_score=iso_score,
        )
        try:
            meta_score = meta_obj.predict_proba(X)[:, 1].astype(float)
        except Exception:
            meta_score = None

    # P9: conformal gating on meta_score
    if gate_mode == "cp" and (cp is not None) and (meta_score is not None):
        alpha = float(os.getenv("CP_ALPHA", "0.10"))
        ok = cp.accept_mask(meta_score, alpha=alpha)
        out = meta_score.copy()
        out[~ok] = 0.0
        return proba, conf, out, "cp"

    if gate_mode == "meta" and meta_score is not None:
        return proba, conf, meta_score, "meta"

    if gate_mode == "iso" and iso is not None:
        return proba, conf, iso_score, "iso"

    return proba, conf, conf, "conf"
'@

Write-Utf8NoBomFile -Path "src/natbin/gate_meta.py" -Content $gateMeta

# --- Wire cp into CLIs/validation (minimal edits) ---
# paper_pnl_backtest.py (P6 version)
Replace-InFile -Path "src/natbin/paper_pnl_backtest.py" -Old 'choices=["meta", "iso", "conf"]' -New 'choices=["meta", "iso", "conf", "cp"]'
Replace-InFile -Path "src/natbin/paper_pnl_backtest.py" -Old 'choices=["meta","iso","conf"]' -New 'choices=["meta","iso","conf","cp"]'

# tune_multiwindow_topk.py
Replace-InFile -Path "src/natbin/tune_multiwindow_topk.py" -Old 'choices=["meta", "iso", "conf"]' -New 'choices=["meta", "iso", "conf", "cp"]'
Replace-InFile -Path "src/natbin/tune_multiwindow_topk.py" -Old 'choices=["meta","iso","conf"]' -New 'choices=["meta","iso","conf","cp"]'

# observe_signal_topk_perday.py
Replace-InFile -Path "src/natbin/observe_signal_topk_perday.py" -Old '("meta", "iso", "conf")' -New '("meta", "iso", "conf", "cp")'
Replace-InFile -Path "src/natbin/observe_signal_topk_perday.py" -Old '("meta","iso","conf")' -New '("meta","iso","conf","cp")'

# Optional: quick syntax check
$py = Join-Path -Path "." -ChildPath ".venv\Scripts\python.exe"
if (Test-Path $py) {
  & $py -m compileall -q "src
atbin" | Out-Null
  Write-Host "compileall: OK"
} else {
  Write-Host "Note: .venv not found; skipped compileall."
}

Write-Host "P9 applied. Suggested next tests:"
Write-Host "  python -m natbin.paper_pnl_backtest --config .\configs\wr_meta_hgb_3x20.yaml --k 1 --holdout-days 60 --payout 0.8 --gate-mode cp --meta-model hgb --thresh-on ev --retrain-every-days 20 --threshold 0.10"
Write-Host "  (try CP_ALPHA=0.05 / 0.10 / 0.20 to trade off winrate vs coverage)"
