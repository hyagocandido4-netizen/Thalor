# p5_meta_low_gate_v2.ps1
# Upgrade: meta gate calibration + uncertainty (meta_low) to improve win rate robustness
# - Adds gate_mode=meta_low (Wilson lower bound per score bin) + meta score calibration
# - Bumps GATE_VERSION to force retrain (cache invalidation)
# - Adds a few stable meta-features (rsi/z20/macdhist)
# Apply from repo root:
#   pwsh -ExecutionPolicy Bypass -File .\scripts\patches\p5_meta_low_gate_v2.ps1

$ErrorActionPreference = "Stop"

function Write-Utf8NoBomFile([string]$Path, [string]$Content) {
  $dir = Split-Path -Parent $Path
  if ($dir -and !(Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Patch-Replace([string]$Path, [string]$Find, [string]$Replace) {
  if (!(Test-Path $Path)) { throw "Arquivo não encontrado: $Path" }
  $t = Get-Content -Raw -Encoding UTF8 $Path
  if ($t.IndexOf($Find, [System.StringComparison]::Ordinal) -lt 0) {
    throw ("Padrão não encontrado em {0}: {1}" -f $Path, $Find)
  }
  $t2 = $t.Replace($Find, $Replace)
  Write-Utf8NoBomFile -Path $Path -Content $t2
}


if (!(Test-Path "config.yaml")) {
  throw "Rode este patch no diretório raiz do repo (onde existe config.yaml)."
}

# 1) Rewrite gate_meta.py (formatted + new features)
$gateMeta = @'
from __future__ import annotations

import os
from statistics import NormalDist
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

# Bump quando você mudar as meta-features (força retrain do cache no observe)
GATE_VERSION = "meta_v2"

# Apenas para auditoria (colunas em ordem no X_meta)
META_FEATURES = [
    "conf",
    "proba_up",
    "signed_margin",
    "abs_margin",
    "vol",
    "bb",
    "atr",
    "rsi",
    "z20",
    "macdhist",
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


def _safe_arr(df: pd.DataFrame, col: str, like: np.ndarray) -> np.ndarray:
    if col in df.columns:
        return df[col].to_numpy(dtype=float)
    return np.zeros_like(like, dtype=float)


def build_meta_X(
    *,
    ts: np.ndarray,
    tz: ZoneInfo,
    proba_up: np.ndarray,
    conf: np.ndarray,
    vol: np.ndarray,
    bb: np.ndarray,
    atr: np.ndarray,
    rsi: np.ndarray | None = None,
    z20: np.ndarray | None = None,
    macdhist: np.ndarray | None = None,
    iso_score: np.ndarray | None = None,
) -> np.ndarray:
    """
    X_meta -> prever P(acertar) (previsibilidade), não direção.

    Notas:
    - iso_score é um "prior" 1D (conf -> P(acertar)) e ajuda bastante a estabilizar.
    - rsi/z20/macdhist são features leves e geralmente estáveis para o meta gate.
    """
    if iso_score is None:
        iso_score = conf
    if rsi is None:
        rsi = np.zeros_like(conf, dtype=float)
    if z20 is None:
        z20 = np.zeros_like(conf, dtype=float)
    if macdhist is None:
        macdhist = np.zeros_like(conf, dtype=float)

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
            rsi,
            z20,
            macdhist,
            hsin,
            hcos,
            dsin,
            dcos,
            iso_score,
        ]
    )
    return np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)


def wilson_lower_bound(k: int, n: int, conf: float = 0.90) -> float:
    """
    Wilson lower bound para proporção binária (k sucessos em n).
    conf=0.90 => limite inferior com ~90% de confiança (2-sided Wilson).
    """
    if n <= 0:
        return 0.0
    if k < 0:
        k = 0
    if k > n:
        k = n

    p = k / n
    # z do intervalo 2-sided: (1+conf)/2
    z = float(NormalDist().inv_cdf((1.0 + float(conf)) / 2.0))
    z2 = z * z

    denom = 1.0 + (z2 / n)
    center = p + (z2 / (2.0 * n))
    adj = z * np.sqrt((p * (1.0 - p) + (z2 / (4.0 * n))) / n)
    low = (center - adj) / denom
    return float(max(0.0, min(1.0, low)))


def _fit_iso_1d(x: np.ndarray, y: np.ndarray) -> IsotonicRegression | None:
    try:
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(x.astype(float), y.astype(float))
        return iso
    except Exception:
        return None


def _fit_meta_model(X: np.ndarray, y: np.ndarray, meta_model_type: str) -> Any | None:
    # min samples e classes
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


class _BinnedWilsonLow:
    """
    Calibrador "conservador": mapeia p_raw -> p_low (Wilson lower bound) por bins.
    Isso funciona como um "uncertainty gating" local, simples e auditável.

    Observação:
    - não é um conformal prediction completo;
    - mas produz um score mais conservador e tende a aumentar win rate/robustez.
    """

    def __init__(self, cuts: np.ndarray, p_low: np.ndarray):
        self.cuts = np.asarray(cuts, dtype=float)  # shape (B-1,)
        self.p_low = np.asarray(p_low, dtype=float)  # shape (B,)

    @classmethod
    def fit(
        cls,
        p_raw: np.ndarray,
        y: np.ndarray,
        *,
        conf: float = 0.90,
        max_bins: int = 20,
        min_bin: int = 50,
    ) -> "_BinnedWilsonLow" | None:
        p_raw = np.asarray(p_raw, dtype=float)
        y = np.asarray(y, dtype=int)

        n = int(len(p_raw))
        if n < max(3 * min_bin, 150):
            return None

        # número de bins baseado no tamanho da calibração
        B = min(int(max_bins), int(n // min_bin))
        if B < 3:
            return None

        order = np.argsort(p_raw)
        p_s = p_raw[order]
        y_s = y[order]

        # cortes para bins contíguos (quantis por contagem)
        idx = np.linspace(0, n, B + 1, dtype=int)

        cuts: list[float] = []
        lows: list[float] = []
        for b in range(B):
            a = int(idx[b])
            c = int(idx[b + 1])
            if c <= a:
                continue

            pb = p_s[a:c]
            yb = y_s[a:c]

            nb = int(len(pb))
            kb = int(np.sum(yb))
            low = wilson_lower_bound(kb, nb, conf=conf)
            lows.append(low)

            if b < (B - 1):
                cuts.append(float(pb[-1]))

        if len(lows) < 3:
            return None

        cuts_arr = np.asarray(cuts, dtype=float)
        lows_arr = np.asarray(lows, dtype=float)

        # força monotonicidade (não pode piorar com score maior)
        lows_arr = np.maximum.accumulate(lows_arr)

        # valida shapes
        if len(cuts_arr) != (len(lows_arr) - 1):
            return None

        return cls(cuts_arr, lows_arr)

    def predict(self, p_raw: np.ndarray) -> np.ndarray:
        p_raw = np.asarray(p_raw, dtype=float)
        idx = np.searchsorted(self.cuts, p_raw, side="right")
        idx = np.clip(idx, 0, len(self.p_low) - 1)
        out = self.p_low[idx]
        return np.clip(out, 0.0, 1.0)


class MetaGate:
    """
    Wrapper para meta_model com:
      - calibração 1D (isotonic) do p_raw -> p_mean (opcional)
      - mapeamento conservador p_raw -> p_low por bins (opcional)
    """

    def __init__(
        self,
        model: Any,
        *,
        iso_mean: IsotonicRegression | None = None,
        low_map: _BinnedWilsonLow | None = None,
    ):
        self.model = model
        self.iso_mean = iso_mean
        self.low_map = low_map

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        p = self.model.predict_proba(X)[:, 1].astype(float)
        if self.iso_mean is not None:
            p = self.iso_mean.predict(p).astype(float)
        p = np.clip(p, 0.0, 1.0)
        return np.column_stack([1.0 - p, p])

    def predict_low(self, X: np.ndarray) -> np.ndarray:
        p_raw = self.model.predict_proba(X)[:, 1].astype(float)
        if self.low_map is not None:
            return self.low_map.predict(p_raw).astype(float)
        if self.iso_mean is not None:
            return np.clip(self.iso_mean.predict(p_raw).astype(float), 0.0, 1.0)
        return np.clip(p_raw, 0.0, 1.0)


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
      4) meta gate (X_meta -> P(acertar))

    Upgrade P5:
      - calibra saída do meta gate e opcionalmente produz score "meta_low"
        (Wilson lower bound por bins) para gating mais robusto.
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

    # 1) base model
    base = HistGradientBoostingClassifier(
        max_depth=3,
        learning_rate=0.05,
        max_iter=300,
        random_state=0,
    )
    base.fit(sub_df[feat_cols], sub_df["y_open_close"])

    # 2) calibrar base (sigmoid)
    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(cal_df[feat_cols], cal_df["y_open_close"])

    # 3) ISO gate em cima do CAL
    p_cal = cal.predict_proba(cal_df[feat_cols])[:, 1]
    pred_cal = (p_cal >= 0.5).astype(int)
    y_cal = cal_df["y_open_close"].to_numpy(dtype=int)
    correct_cal = (pred_cal == y_cal).astype(int)
    conf_cal = np.maximum(p_cal, 1.0 - p_cal)

    iso = _fit_iso_1d(conf_cal, correct_cal)

    # 4) META gate em cima do META
    if len(meta_df) < min_part:
        return cal, iso, None

    def _meta_xy(df_part: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        p = cal.predict_proba(df_part[feat_cols])[:, 1].astype(float)
        pred = (p >= 0.5).astype(int)
        y = df_part["y_open_close"].to_numpy(dtype=int)
        correct = (pred == y).astype(int)
        conf = np.maximum(p, 1.0 - p).astype(float)
        iso_s = iso.predict(conf.astype(float)).astype(float) if iso is not None else conf

        X = build_meta_X(
            ts=df_part["ts"].to_numpy(dtype=int),
            tz=tz,
            proba_up=p,
            conf=conf,
            vol=df_part["f_vol48"].to_numpy(dtype=float),
            bb=df_part["f_bb_width20"].to_numpy(dtype=float),
            atr=df_part["f_atr14"].to_numpy(dtype=float),
            rsi=_safe_arr(df_part, "f_rsi14", conf),
            z20=_safe_arr(df_part, "f_z20", conf),
            macdhist=_safe_arr(df_part, "f_macdhist", conf),
            iso_score=iso_s,
        )
        return X, correct

    # split meta em train/cal (time split) se tiver tamanho
    meta_cal_frac = float(os.getenv("META_CAL_FRAC", "0.25"))
    min_meta_cal = int(os.getenv("MIN_META_CAL", str(min_part)))

    m = int(len(meta_df))
    do_meta_cal = m >= (min_part + min_meta_cal)

    if do_meta_cal:
        cal_size = max(min_meta_cal, int(m * meta_cal_frac))
        cal_size = min(cal_size, m - min_part)

        meta_train_df = meta_df.iloc[: m - cal_size]
        meta_cal_df = meta_df.iloc[m - cal_size :]

        X_tr, y_tr = _meta_xy(meta_train_df)
        meta_model = _fit_meta_model(X_tr, y_tr, meta_model_type)
        if meta_model is None:
            return cal, iso, None

        X_c, y_c = _meta_xy(meta_cal_df)
        p_raw_c = meta_model.predict_proba(X_c)[:, 1].astype(float)

        # mean calibration (isotonic)
        iso_mean = _fit_iso_1d(p_raw_c, y_c)

        # conservative low mapping (bins + Wilson)
        low_conf = float(os.getenv("META_LOW_CONF", "0.90"))
        low_bins = int(os.getenv("META_LOW_MAX_BINS", "20"))
        low_minbin = int(os.getenv("META_LOW_MIN_BIN", "50"))
        low_map = _BinnedWilsonLow.fit(
            p_raw_c,
            y_c,
            conf=low_conf,
            max_bins=low_bins,
            min_bin=low_minbin,
        )

        meta_gate: Any = MetaGate(meta_model, iso_mean=iso_mean, low_map=low_map)
        return cal, iso, meta_gate

    # fallback: sem calibração extra (treina no meta inteiro)
    X_m, y_m = _meta_xy(meta_df)
    meta_model = _fit_meta_model(X_m, y_m, meta_model_type)
    if meta_model is None:
        return cal, iso, None

    meta_gate = MetaGate(meta_model, iso_mean=None, low_map=None)
    return cal, iso, meta_gate


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
    Retorna:
      - proba_up
      - conf
      - score
      - gate_used

    score = P(acertar) (modo iso/meta/meta_low) ou conf (fallback).
    """
    gate_mode = (gate_mode or "meta").strip().lower()
    if gate_mode not in ("meta", "meta_low", "iso", "conf"):
        gate_mode = "meta"

    proba = cal_model.predict_proba(df[feat_cols])[:, 1].astype(float)
    conf = np.maximum(proba, 1.0 - proba).astype(float)

    iso_score = iso.predict(conf.astype(float)).astype(float) if iso is not None else conf

    if gate_mode in ("meta", "meta_low") and meta_model is not None:
        X = build_meta_X(
            ts=df["ts"].to_numpy(dtype=int),
            tz=tz,
            proba_up=proba,
            conf=conf,
            vol=df["f_vol48"].to_numpy(dtype=float),
            bb=df["f_bb_width20"].to_numpy(dtype=float),
            atr=df["f_atr14"].to_numpy(dtype=float),
            rsi=_safe_arr(df, "f_rsi14", conf),
            z20=_safe_arr(df, "f_z20", conf),
            macdhist=_safe_arr(df, "f_macdhist", conf),
            iso_score=iso_score,
        )
        try:
            if gate_mode == "meta_low" and hasattr(meta_model, "predict_low"):
                s = meta_model.predict_low(X).astype(float)
                return proba, conf, s, "meta_low"

            s = meta_model.predict_proba(X)[:, 1].astype(float)
            return proba, conf, s, "meta"
        except Exception:
            pass

    if gate_mode == "iso" and iso is not None:
        return proba, conf, iso_score, "iso"

    return proba, conf, conf, "conf"
'@

Write-Utf8NoBomFile -Path "src/natbin/gate_meta.py" -Content $gateMeta
Write-Host "ok: src/natbin/gate_meta.py (GATE_VERSION=meta_v2, meta_low added)"

# 2) Allow gate_mode=meta_low in observe + tuner + paper
Patch-Replace `
  -Path "src/natbin/observe_signal_topk_perday.py" `
  -Find 'if gate_mode not in ("meta", "iso", "conf"):' `
  -Replace 'if gate_mode not in ("meta", "meta_low", "iso", "conf"):' 
Write-Host "ok: observe_signal_topk_perday gate_mode whitelist updated"

Patch-Replace `
  -Path "src/natbin/tune_multiwindow_topk.py" `
  -Find 'choices=["meta", "iso", "conf"]' `
  -Replace 'choices=["meta", "meta_low", "iso", "conf"]'
Write-Host "ok: tune_multiwindow_topk gate-mode choices updated"

Patch-Replace `
  -Path "src/natbin/paper_pnl_backtest.py" `
  -Find 'choices=["meta", "iso", "conf"]' `
  -Replace 'choices=["meta", "meta_low", "iso", "conf"]'
Write-Host "ok: paper_pnl_backtest gate-mode choices updated"

# 3) Quick smoke: compileall
Write-Host "Running python -m compileall src/natbin ..."
& .\.venv\Scripts\python.exe -m compileall -q src/natbin
Write-Host "compileall ok"

Write-Host "DONE: P5 meta_low gate added. Next: re-tune with --gate-mode meta_low and validate paper."
