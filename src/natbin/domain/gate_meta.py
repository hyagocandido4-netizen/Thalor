# -*- coding: utf-8 -*-
"""
gate_meta.py

Model stack used by observe/backtests:
- base direction model (HistGradientBoostingClassifier)
- sigmoid calibration (CalibratedClassifierCV) -> proba_up
- optional isotonic calibration over confidence -> iso_score ~= P(correct | conf)
- meta-model that predicts P(correct) using time-of-day + a few regime features
- optional isotonic calibration over meta score (P15) + optional blend (META_ISO_BLEND)
- OPTIONAL conformal "CP" gate: selective prediction that only accepts trades when
  conformal prediction set is {correct}. This makes CPREG (dynamic CP_ALPHA) actually work.

Env knobs (existing + new):
- SUB_FRAC (default 0.70): fraction of train_df used to fit base model
- CAL_FRAC (default 0.15): fraction used to fit CalibratedClassifierCV (sigmoid) and iso(conf)
  meta_df is the remainder (1 - SUB_FRAC - CAL_FRAC)

- META_ISO_ENABLE (default 1): enable isotonic calibration of meta score if available
- META_ISO_BLEND  (default 1.0): 0=raw meta score, 1=isotonic meta score, mix in between
- META_ISO_MIN_N   (default 400): min rows for meta_iso calibration
- META_ISO_CAL2_FRAC (default 0.20): fraction of meta_df reserved as calibration tail

Conformal / CP gate:
- CP_ENABLE (default 1): fit CP calibrator when possible
- CP_MIN_N  (default 400): min rows to fit CP calibrator
- CP_BINS   (default 3): number of score bins (0/1 disables binning)
- CP_ALPHA  (runtime, default 0.05): significance level used by accept_mask()
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, Tuple

import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo

from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from ..config.env import env_float, env_int




# -----------------------------------------------------------------------------
# Public API / cache invalidation
#
# IMPORTANT:
# - `observe_signal_topk_perday.py` and tuning scripts import these symbols.
# - `GATE_VERSION` is persisted in model_cache.json; bump it when gate behavior
#   changes to force a retrain and avoid mixing incompatible cache artifacts.
# -----------------------------------------------------------------------------

# Bump this string whenever the gating behavior or feature construction changes.
GATE_VERSION = "meta_v2_p20_cp"

# Column order for the meta-model features produced by `build_meta_X`.
# Keep this list in sync with build_meta_X().
META_FEATURES = [
    "dow_sin",
    "dow_cos",
    "min_sin",
    "min_cos",
    "proba_up",
    "conf",
    "vol",
    "bb",
    "atr",
    "iso_score",
]
def _truthy(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _gate_fail_closed_enabled() -> bool:
    return _truthy(os.getenv("GATE_FAIL_CLOSED", "1"))


def _clamp01(x: np.ndarray) -> np.ndarray:
    return np.clip(x, 1e-6, 1.0 - 1e-6)


def _fit_iso_1d(x: np.ndarray, y: np.ndarray, min_n: int = 400) -> Optional[IsotonicRegression]:
    if x is None or y is None:
        return None
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=int)
    if len(x) < max(20, int(min_n)):
        return None
    # must have both classes
    if np.unique(y).size < 2:
        return None
    try:
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(x.astype(float), y.astype(int))
        return iso
    except Exception:
        return None


def build_meta_X(
    ts: np.ndarray,
    tz: ZoneInfo,
    proba_up: np.ndarray,
    conf: np.ndarray,
    vol: np.ndarray,
    bb: np.ndarray,
    atr: np.ndarray,
    iso_score: np.ndarray,
) -> np.ndarray:
    """
    Meta features: time of day/week (cyclic) + proba/conf + a few regime indicators.

    IMPORTANT: Keep it simple and stable. This runs in live loop.
    """
    ts = np.asarray(ts, dtype=int)
    proba_up = np.asarray(proba_up, dtype=float)
    conf = np.asarray(conf, dtype=float)
    vol = np.asarray(vol, dtype=float)
    bb = np.asarray(bb, dtype=float)
    atr = np.asarray(atr, dtype=float)
    iso_score = np.asarray(iso_score, dtype=float)

    # Convert epoch -> local datetime components
    # We do it in Python loop; arrays are small in live (often 1 row).
    dows = np.empty(len(ts), dtype=float)
    mins = np.empty(len(ts), dtype=float)
    for i, t in enumerate(ts):
        dt = pd.Timestamp(t, unit="s", tz="UTC").tz_convert(tz)
        dows[i] = float(dt.dayofweek)  # 0..6
        mins[i] = float(dt.hour * 60 + dt.minute)  # 0..1439

    # cyc encodings
    dow_rad = (2.0 * np.pi * dows) / 7.0
    min_rad = (2.0 * np.pi * mins) / 1440.0

    dow_sin = np.sin(dow_rad)
    dow_cos = np.cos(dow_rad)
    min_sin = np.sin(min_rad)
    min_cos = np.cos(min_rad)

    X = np.column_stack(
        [
            dow_sin,
            dow_cos,
            min_sin,
            min_cos,
            proba_up,
            conf,
            vol,
            bb,
            atr,
            iso_score,
        ]
    ).astype(float)

    return X


# -------------------------
# Conformal / CP gate
# -------------------------
@dataclass
class ConformalCP:
    """
    Simple inductive conformal predictor for binary label:
      y=1 means "prediction was correct" (we want to trade)
      y=0 means "prediction was wrong" (we want to avoid)

    We use nonconformity:
      A(y=1) = 1 - s
      A(y=0) = s
    where s is predicted P(correct).

    Conformal set contains label y if p_y > alpha.
    We accept trade if set == {1}  => (p1 > alpha) & (p0 <= alpha)
    """
    a0: np.ndarray  # nonconformity samples for label 0
    a1: np.ndarray  # nonconformity samples for label 1
    bins: int = 0
    edges: Optional[np.ndarray] = None
    a0_bins: Optional[list[np.ndarray]] = None
    a1_bins: Optional[list[np.ndarray]] = None
    min_bin_n: int = 25

    def _pval(self, a_ref: np.ndarray, a: np.ndarray) -> np.ndarray:
        # p = (#{ref >= a} + 1) / (n + 1)
        # vectorized via broadcasting; but keep memory reasonable for small arrays.
        a_ref = np.asarray(a_ref, dtype=float)
        a = np.asarray(a, dtype=float)
        if a_ref.size == 0:
            return np.ones_like(a, dtype=float)
        # Count ref >= a
        cnt = (a_ref[None, :] >= a[:, None]).sum(axis=1).astype(float)
        return (cnt + 1.0) / (float(a_ref.size) + 1.0)

    def _select_bin(self, s: np.ndarray) -> np.ndarray:
        if self.edges is None or self.bins <= 1:
            return np.zeros(len(s), dtype=int)
        # edges length = bins+1
        # bin = searchsorted(edges, s, right)-1
        b = np.searchsorted(self.edges, s, side="right") - 1
        b = np.clip(b, 0, len(self.edges) - 2)
        return b.astype(int)

    def p_values(self, s: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        s = _clamp01(np.asarray(s, dtype=float))
        a_y1 = 1.0 - s
        a_y0 = s

        if self.edges is None or self.bins <= 1 or not self.a0_bins or not self.a1_bins:
            p0 = self._pval(self.a0, a_y0)
            p1 = self._pval(self.a1, a_y1)
            return p0, p1

        b = self._select_bin(s)
        p0 = np.empty(len(s), dtype=float)
        p1 = np.empty(len(s), dtype=float)

        for i in range(len(s)):
            bi = int(b[i])
            a0_ref = self.a0_bins[bi] if bi < len(self.a0_bins) else self.a0
            a1_ref = self.a1_bins[bi] if bi < len(self.a1_bins) else self.a1

            # fallback to global if bin too small
            if a0_ref.size < self.min_bin_n:
                a0_ref = self.a0
            if a1_ref.size < self.min_bin_n:
                a1_ref = self.a1

            p0[i] = self._pval(a0_ref, np.array([a_y0[i]], dtype=float))[0]
            p1[i] = self._pval(a1_ref, np.array([a_y1[i]], dtype=float))[0]

        return p0, p1

    def accept_mask(self, s: np.ndarray, alpha: float) -> np.ndarray:
        try:
            a = float(alpha)
        except Exception:
            a = 0.05
        a = max(0.0, min(1.0, a))
        p0, p1 = self.p_values(s)
        return (p1 > a) & (p0 <= a)


def fit_conformal_cp(score: np.ndarray, correct: np.ndarray, bins: int = 0, min_bin_n: int = 25) -> Optional[ConformalCP]:
    """
    Fit CP on a calibration set.

    score: predicted P(correct)
    correct: 0/1 true correctness label (1 if base prediction matched y)
    """
    s = _clamp01(np.asarray(score, dtype=float))
    y = np.asarray(correct, dtype=int)

    if len(s) < 50 or np.unique(y).size < 2:
        return None

    a = np.where(y == 1, 1.0 - s, s).astype(float)
    a0 = np.sort(a[y == 0])
    a1 = np.sort(a[y == 1])
    if a0.size < 10 or a1.size < 10:
        return None

    cp = ConformalCP(a0=a0, a1=a1, bins=int(bins or 0), min_bin_n=int(min_bin_n))

    if cp.bins and cp.bins > 1:
        try:
            edges = np.quantile(s, np.linspace(0.0, 1.0, cp.bins + 1)).astype(float)
            edges = np.unique(edges)
            # Need at least 3 unique edges for 2+ bins
            if edges.size >= 3:
                # If unique edges shrink bins, adjust cp.bins accordingly
                cp.edges = edges
                cp.bins = int(edges.size - 1)
                cp.a0_bins = []
                cp.a1_bins = []
                for bi in range(cp.bins):
                    lo = edges[bi]
                    hi = edges[bi + 1]
                    if bi == cp.bins - 1:
                        mask = (s >= lo) & (s <= hi)
                    else:
                        mask = (s >= lo) & (s < hi)
                    a_b = a[mask]
                    y_b = y[mask]
                    cp.a0_bins.append(np.sort(a_b[y_b == 0]))
                    cp.a1_bins.append(np.sort(a_b[y_b == 1]))
            else:
                cp.edges = None
                cp.a0_bins = None
                cp.a1_bins = None
                cp.bins = 0
        except Exception:
            cp.edges = None
            cp.a0_bins = None
            cp.a1_bins = None
            cp.bins = 0

    return cp


# -------------------------
# Model pack
# -------------------------
@dataclass
class MetaPack:
    model: Any
    iso: Optional[IsotonicRegression] = None  # P15 meta score isotonic
    cp: Optional[ConformalCP] = None          # P20 conformal gate


def train_base_cal_iso_meta(
    train_df: pd.DataFrame,
    feat_cols: list[str],
    tz: ZoneInfo,
    meta_model_type: Optional[str] = None,
    *,
    base_model: Optional[str] = None,
    meta_model: Optional[str] = None,
    base_model_type: Optional[str] = None,
    **_compat_kwargs: Any,
) -> tuple[CalibratedClassifierCV, Optional[IsotonicRegression], Optional[MetaPack]]:
    """
    Train stack on a rolling window.

    Returns:
      cal: calibrated classifier (sigmoid) over base
      iso: isotonic regression mapping conf -> P(correct)
      meta_pack: MetaPack(model, meta_iso, cp) or None
    """

    # Compat: older callers pass meta_model_type/base_model_type (and we ignore unknown kwargs).
    if meta_model is None:
        meta_model = meta_model_type
    if base_model is None:
        base_model = base_model_type
    if meta_model is None:
        meta_model = "hgb"
    if base_model is None:
        base_model = "hgb"

    meta_model = str(meta_model).strip().lower()
    base_model = str(base_model).strip().lower()
    if meta_model in ("lr", "logistic", "logisticregression"):
        meta_model = "logreg"

    if train_df is None or len(train_df) < 500:
        raise ValueError("train_df muito pequeno para treinar")

    # Only labeled rows
    df = train_df.copy()
    if "y_open_close" not in df.columns:
        raise ValueError("train_df precisa ter coluna y_open_close")
    df = df[df["y_open_close"].notna()].copy()
    if len(df) < 500:
        raise ValueError("train_df com poucos labels válidos (y_open_close NaN)")

    sub_frac = env_float("SUB_FRAC", "0.70")
    cal_frac = env_float("CAL_FRAC", "0.15")
    sub_frac = max(0.10, min(0.90, sub_frac))
    cal_frac = max(0.05, min(0.50, cal_frac))
    if sub_frac + cal_frac >= 0.95:
        cal_frac = max(0.05, 0.94 - sub_frac)

    n = len(df)
    n_sub = max(200, int(n * sub_frac))
    n_cal = max(200, int(n * cal_frac))
    if n_sub + n_cal >= n - 50:
        # ensure some meta_df
        n_cal = max(50, n - n_sub - 50)

    sub_df = df.iloc[:n_sub].copy()
    cal_df = df.iloc[n_sub : n_sub + n_cal].copy()
    meta_df = df.iloc[n_sub + n_cal :].copy()

    y_sub = sub_df["y_open_close"].to_numpy(dtype=int)
    y_cal = cal_df["y_open_close"].to_numpy(dtype=int)

    # base
    if base_model.lower() != "hgb":
        raise ValueError("base_model suportado: hgb")
    base = HistGradientBoostingClassifier(random_state=42)
    base.fit(sub_df[feat_cols], y_sub)

    # calibrated sigmoid
    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(cal_df[feat_cols], y_cal)

    # iso(conf) -> P(correct)
    p_cal = cal.predict_proba(cal_df[feat_cols])[:, 1].astype(float)
    conf_cal = np.maximum(p_cal, 1.0 - p_cal).astype(float)
    pred_cal = (p_cal >= 0.5).astype(int)
    correct_cal = (pred_cal == y_cal).astype(int)

    iso: Optional[IsotonicRegression] = None
    try:
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(conf_cal.astype(float), correct_cal.astype(int))
    except Exception:
        iso = None

    # meta model
    meta_pack: Optional[MetaPack] = None
    if meta_model.lower() not in ("hgb", "logreg"):
        raise ValueError("meta_model suportado: hgb|logreg")

    meta_iso_enable = _truthy(os.getenv("META_ISO_ENABLE", "1"))
    meta_iso_min = env_int("META_ISO_MIN_N", "400")
    meta_cal2_frac = env_float("META_ISO_CAL2_FRAC", "0.20")
    meta_cal2_frac = max(0.05, min(0.50, meta_cal2_frac))

    cp_enable = _truthy(os.getenv("CP_ENABLE", "1"))
    cp_min_n = env_int("CP_MIN_N", "400")
    cp_bins = env_int("CP_BINS", "3")
    cp_bins = max(0, min(10, cp_bins))

    if len(meta_df) >= 200:
        # split meta_df into train + cal2 tail
        n_cal2 = max(meta_iso_min, int(len(meta_df) * meta_cal2_frac))
        n_cal2 = min(len(meta_df), n_cal2)
        if n_cal2 >= len(meta_df):
            meta_train_df = meta_df.copy()
            meta_cal2_df = None
        else:
            meta_train_df = meta_df.iloc[:-n_cal2].copy()
            meta_cal2_df = meta_df.iloc[-n_cal2:].copy()

        # train meta model to predict correctness
        p_m = cal.predict_proba(meta_train_df[feat_cols])[:, 1].astype(float)
        y_m = meta_train_df["y_open_close"].to_numpy(dtype=int)
        pred_m = (p_m >= 0.5).astype(int)
        correct_m = (pred_m == y_m).astype(int)

        conf_m = np.maximum(p_m, 1.0 - p_m).astype(float)
        iso_score_m = iso.predict(conf_m.astype(float)).astype(float) if iso is not None else conf_m

        X_m = build_meta_X(
            ts=meta_train_df["ts"].to_numpy(dtype=int),
            tz=tz,
            proba_up=p_m,
            conf=conf_m,
            vol=meta_train_df["f_vol48"].to_numpy(dtype=float),
            bb=meta_train_df["f_bb_width20"].to_numpy(dtype=float),
            atr=meta_train_df["f_atr14"].to_numpy(dtype=float),
            iso_score=iso_score_m,
        )

        # Meta-model choices:
        # - hgb: non-linear, usually stronger
        # - logreg: fast, stable baseline (scaled)
        if meta_model == "hgb":
            mm: Any = HistGradientBoostingClassifier(random_state=42)
        else:
            mm = Pipeline(
                steps=[
                    ("scaler", StandardScaler()),
                    ("lr", LogisticRegression(max_iter=2000)),
                ]
            )
        mm.fit(X_m, correct_m)

        meta_iso: Optional[IsotonicRegression] = None
        cp_obj: Optional[ConformalCP] = None

        # If we have a calibration tail, we can use it for meta_iso and/or CP.
        iso_cal_df = meta_cal2_df
        cp_df = None

        if meta_cal2_df is not None and len(meta_cal2_df) > 0:
            # Prefer splitting tail so meta_iso and CP don't use the exact same rows (when possible)
            if meta_iso_enable and cp_enable and len(meta_cal2_df) >= (meta_iso_min + cp_min_n):
                cp_df = meta_cal2_df.tail(cp_min_n).copy()
                iso_cal_df = meta_cal2_df.iloc[: len(meta_cal2_df) - len(cp_df)].copy()
            else:
                # fallback: share tail (still better than nothing)
                if cp_enable and len(meta_cal2_df) >= cp_min_n:
                    cp_df = meta_cal2_df.copy()

        # Fit meta_iso on iso_cal_df if possible
        if meta_iso_enable and iso_cal_df is not None and len(iso_cal_df) >= meta_iso_min:
            p_c2 = cal.predict_proba(iso_cal_df[feat_cols])[:, 1].astype(float)
            y_c2 = iso_cal_df["y_open_close"].to_numpy(dtype=int)
            pred_c2 = (p_c2 >= 0.5).astype(int)
            correct_c2 = (pred_c2 == y_c2).astype(int)

            conf_c2 = np.maximum(p_c2, 1.0 - p_c2).astype(float)
            iso_score_c2 = iso.predict(conf_c2.astype(float)).astype(float) if iso is not None else conf_c2

            X_c2 = build_meta_X(
                ts=iso_cal_df["ts"].to_numpy(dtype=int),
                tz=tz,
                proba_up=p_c2,
                conf=conf_c2,
                vol=iso_cal_df["f_vol48"].to_numpy(dtype=float),
                bb=iso_cal_df["f_bb_width20"].to_numpy(dtype=float),
                atr=iso_cal_df["f_atr14"].to_numpy(dtype=float),
                iso_score=iso_score_c2,
            )

            s_raw = mm.predict_proba(X_c2)[:, 1].astype(float)
            meta_iso = _fit_iso_1d(s_raw, correct_c2, min_n=meta_iso_min)

        # Fit CP on cp_df if possible
        if cp_enable and cp_df is not None and len(cp_df) >= cp_min_n:
            p_cp = cal.predict_proba(cp_df[feat_cols])[:, 1].astype(float)
            y_cp = cp_df["y_open_close"].to_numpy(dtype=int)
            pred_cp = (p_cp >= 0.5).astype(int)
            correct_cp = (pred_cp == y_cp).astype(int)

            conf_cp = np.maximum(p_cp, 1.0 - p_cp).astype(float)
            iso_score_cp = iso.predict(conf_cp.astype(float)).astype(float) if iso is not None else conf_cp

            X_cp = build_meta_X(
                ts=cp_df["ts"].to_numpy(dtype=int),
                tz=tz,
                proba_up=p_cp,
                conf=conf_cp,
                vol=cp_df["f_vol48"].to_numpy(dtype=float),
                bb=cp_df["f_bb_width20"].to_numpy(dtype=float),
                atr=cp_df["f_atr14"].to_numpy(dtype=float),
                iso_score=iso_score_cp,
            )

            s_raw_cp = mm.predict_proba(X_cp)[:, 1].astype(float)

            # Apply meta_iso + blend the same way compute_scores will do
            s_cp = s_raw_cp
            if meta_iso is not None and _truthy(os.getenv("META_ISO_ENABLE", "1")):
                s_iso_cp = meta_iso.predict(s_raw_cp.astype(float)).astype(float)
                try:
                    w = env_float("META_ISO_BLEND", 1.0)
                except Exception:
                    w = 1.0
                w = max(0.0, min(1.0, float(w)))
                s_cp = (w * s_iso_cp) + ((1.0 - w) * s_raw_cp)

            cp_obj = fit_conformal_cp(s_cp, correct_cp, bins=cp_bins)

        meta_pack = MetaPack(model=mm, iso=meta_iso, cp=cp_obj)

    return cal, iso, meta_pack


def compute_scores(
    df: pd.DataFrame,
    feat_cols: list[str],
    tz: ZoneInfo,
    cal_model: CalibratedClassifierCV,
    iso: Optional[IsotonicRegression],
    meta_model: Optional[Any],
    gate_mode: str = "meta",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    """
    Returns: proba_up, conf, score, gate_used

    score:
      - meta/meta_iso: P(correct) from meta-model (optionally isotonic-calibrated, P15)
      - cp: same meta score, masked by conformal accept_mask(CP_ALPHA)
      - iso: iso(conf) ~= P(correct)
      - conf: conf
    """
    gate_mode = (gate_mode or "meta").strip().lower()
    if gate_mode not in ("meta", "iso", "conf", "cp"):
        gate_mode = "meta"

    proba = cal_model.predict_proba(df[feat_cols])[:, 1].astype(float)
    conf = np.maximum(proba, 1.0 - proba).astype(float)
    iso_score = iso.predict(conf.astype(float)).astype(float) if iso is not None else conf

    # META + optional CP
    if gate_mode in ("meta", "cp") and meta_model is not None:
        try:
            pack = meta_model
            model = getattr(pack, "model", pack)
            meta_iso = getattr(pack, "iso", None)
            cp_obj = getattr(pack, "cp", None)

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

            s_raw = model.predict_proba(X)[:, 1].astype(float)
            s = s_raw
            used = "meta"

            if meta_iso is not None and _truthy(os.getenv("META_ISO_ENABLE", "1")):
                s_iso = meta_iso.predict(s_raw.astype(float)).astype(float)
                try:
                    w = env_float("META_ISO_BLEND", 1.0)
                except Exception:
                    w = 1.0
                w = max(0.0, min(1.0, float(w)))
                s = (w * s_iso) + ((1.0 - w) * s_raw)
                used = "meta_iso"

            if gate_mode == "cp":
                if cp_obj is None:
                    if _gate_fail_closed_enabled():
                        return proba, conf, np.zeros_like(conf, dtype=float), f"cp_fail_closed_missing_cp_{used}"
                    return proba, conf, s, f"cp_fallback_{used}"
                try:
                    alpha = env_float("CP_ALPHA", "0.05")
                except Exception:
                    alpha = 0.05
                alpha = max(0.0, min(1.0, alpha))
                mask = cp_obj.accept_mask(s, alpha=alpha)
                s2 = s.copy()
                s2[~mask] = 0.0
                return proba, conf, s2, f"cp_{used}"

            return proba, conf, s, used
        except Exception:
            if gate_mode in ("meta", "cp") and _gate_fail_closed_enabled():
                return proba, conf, np.zeros_like(conf, dtype=float), f"{gate_mode}_fail_closed_exception"
            # fall through
            pass

    if gate_mode in ("meta", "cp") and _gate_fail_closed_enabled():
        return proba, conf, np.zeros_like(conf, dtype=float), f"{gate_mode}_fail_closed_missing_meta"

    # ISO gate
    if gate_mode == "iso" and iso is not None:
        return proba, conf, iso_score, "iso"

    return proba, conf, conf, "conf"
