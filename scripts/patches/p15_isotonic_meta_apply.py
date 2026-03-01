from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import py_compile


def repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / ".git").exists():
            return p
    raise SystemExit(
        "Não encontrei .git. Rode dentro do repo (ex: C:\\Users\\hyago\\Documents\\bot)."
    )


def backup_if_exists(p: Path) -> Path | None:
    if not p.exists():
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


GATE_META_PY = r'''# P15: Isotonic calibration on META score (meta_iso)
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


def _truthy(v: str | None) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s not in ("", "0", "false", "no", "off")


# Bump quando você mudar o comportamento do gate (força retrain do cache no observe)
GATE_VERSION = "meta_v2_p15_iso"

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


@dataclass
class MetaPack:
    """Wrapper picklable: mantém o modelo meta e, opcionalmente, um isotonic calibrator do score."""
    model: Any
    iso: IsotonicRegression | None = None

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.model.predict_proba(X)


def _time_feats(ts: np.ndarray, tz: ZoneInfo) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    dt = pd.to_datetime(ts, unit="s", utc=True).tz_convert(tz)
    hour = dt.hour.astype(float) + dt.minute.astype(float) / 60.0
    dow = dt.dayofweek.astype(float)

    hr = 2.0 * np.pi * (hour / 24.0)
    dr = 2.0 * np.pi * (dow / 7.0)
    return np.sin(hr), np.cos(hr), np.sin(dr), np.cos(dr)


def build_meta_X(
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


def _fit_iso_1d(x: np.ndarray, y: np.ndarray, *, min_n: int = 200) -> IsotonicRegression | None:
    try:
        x = np.asarray(x, dtype=float).reshape(-1)
        y = np.asarray(y, dtype=float).reshape(-1)
        if x.size < min_n:
            return None
        if np.unique(y).size < 2:
            return None
        iso = IsotonicRegression(out_of_bounds="clip")
        iso.fit(x, y)
        return iso
    except Exception:
        return None


def _fit_meta_model(X: np.ndarray, y: np.ndarray, meta_model_type: str) -> Any | None:
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

    pipe = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(max_iter=500, solver="lbfgs")),
        ]
    )
    pipe.fit(X, y)
    return pipe


def train_base_cal_iso_meta(
    train_df: pd.DataFrame,
    feat_cols: list[str],
    tz: ZoneInfo,
    meta_model_type: str = "logreg",
) -> tuple[CalibratedClassifierCV, IsotonicRegression | None, Any | None]:
    """
    Treina:
      1) base HGB (sub-treino)
      2) calibração sigmoid (cal)
      3) iso gate (conf -> P(acertar)) em cal_df
      4) meta gate P2.2 (X_meta -> P(acertar))
      5) P15: meta_iso (meta_score_raw -> P(acertar)) em um holdout temporal do meta_df
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

    # --- base + sigmoid calibration ---
    base = HistGradientBoostingClassifier(
        max_depth=3,
        learning_rate=0.05,
        max_iter=300,
        random_state=0,
    )
    base.fit(sub_df[feat_cols], sub_df["y_open_close"])

    cal = CalibratedClassifierCV(base, method="sigmoid", cv="prefit")
    cal.fit(cal_df[feat_cols], cal_df["y_open_close"])

    # --- iso(conf -> P(acertar)) em cima do CAL (cal_df) ---
    p_cal = cal.predict_proba(cal_df[feat_cols])[:, 1]
    pred_cal = (p_cal >= 0.5).astype(int)
    y_cal = cal_df["y_open_close"].to_numpy(dtype=int)
    correct_cal = (pred_cal == y_cal).astype(int)
    conf_cal = np.maximum(p_cal, 1.0 - p_cal)

    iso = _fit_iso_1d(conf_cal, correct_cal, min_n=min_part)

    # --- meta model (X_meta -> P(acertar)) ---
    meta_model: Any | None = None
    meta_iso: IsotonicRegression | None = None

    if len(meta_df) >= min_part and len(np.unique(meta_df["y_open_close"])) >= 2:
        # Split temporal: meta_train (passado) e meta_cal2 (mais recente)
        meta_iso_enable = _truthy(os.getenv("META_ISO_ENABLE", "1"))
        meta_iso_cal_frac = float(os.getenv("META_ISO_CAL_FRAC", "0.25"))
        meta_iso_min = int(os.getenv("META_ISO_MIN", "200"))

        n_m = int(len(meta_df))
        n_cal2 = int(n_m * meta_iso_cal_frac)

        # garante tamanho mínimo do calibrator
        if n_cal2 < meta_iso_min:
            n_cal2 = meta_iso_min

        # precisa sobrar um bloco mínimo para treinar meta
        meta_train_df: pd.DataFrame
        meta_cal2_df: pd.DataFrame | None

        if (n_m - n_cal2) < meta_iso_min:
            # Sem espaço pra split decente: treina meta no tudo e NÃO calibra
            meta_train_df = meta_df
            meta_cal2_df = None
        else:
            meta_train_df = meta_df.iloc[: n_m - n_cal2]
            meta_cal2_df = meta_df.iloc[n_m - n_cal2 :]

        # --- features + labels de "acerto" para meta_train ---
        p_m_tr = cal.predict_proba(meta_train_df[feat_cols])[:, 1].astype(float)
        y_m_tr = meta_train_df["y_open_close"].to_numpy(dtype=int)
        pred_m_tr = (p_m_tr >= 0.5).astype(int)
        correct_m_tr = (pred_m_tr == y_m_tr).astype(int)

        conf_m_tr = np.maximum(p_m_tr, 1.0 - p_m_tr).astype(float)
        iso_score_tr = iso.predict(conf_m_tr.astype(float)).astype(float) if iso is not None else conf_m_tr

        X_tr = build_meta_X(
            ts=meta_train_df["ts"].to_numpy(dtype=int),
            tz=tz,
            proba_up=p_m_tr,
            conf=conf_m_tr,
            vol=meta_train_df["f_vol48"].to_numpy(dtype=float),
            bb=meta_train_df["f_bb_width20"].to_numpy(dtype=float),
            atr=meta_train_df["f_atr14"].to_numpy(dtype=float),
            iso_score=iso_score_tr,
        )

        meta_model = _fit_meta_model(X_tr, correct_m_tr, meta_model_type)

        # --- P15: calibra meta_score_raw com isotonic em meta_cal2 ---
        if meta_iso_enable and meta_model is not None and meta_cal2_df is not None and len(meta_cal2_df) >= meta_iso_min:
            p_m_c2 = cal.predict_proba(meta_cal2_df[feat_cols])[:, 1].astype(float)
            y_m_c2 = meta_cal2_df["y_open_close"].to_numpy(dtype=int)
            pred_m_c2 = (p_m_c2 >= 0.5).astype(int)
            correct_m_c2 = (pred_m_c2 == y_m_c2).astype(int)

            conf_m_c2 = np.maximum(p_m_c2, 1.0 - p_m_c2).astype(float)
            iso_score_c2 = iso.predict(conf_m_c2.astype(float)).astype(float) if iso is not None else conf_m_c2

            X_c2 = build_meta_X(
                ts=meta_cal2_df["ts"].to_numpy(dtype=int),
                tz=tz,
                proba_up=p_m_c2,
                conf=conf_m_c2,
                vol=meta_cal2_df["f_vol48"].to_numpy(dtype=float),
                bb=meta_cal2_df["f_bb_width20"].to_numpy(dtype=float),
                atr=meta_cal2_df["f_atr14"].to_numpy(dtype=float),
                iso_score=iso_score_c2,
            )

            try:
                s_raw = meta_model.predict_proba(X_c2)[:, 1].astype(float)
                meta_iso = _fit_iso_1d(s_raw, correct_m_c2, min_n=meta_iso_min)
            except Exception:
                meta_iso = None

        # encapsula (pickle-friendly)
        meta_model = MetaPack(model=meta_model, iso=meta_iso) if meta_model is not None else None

    return cal, iso, meta_model


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
      - meta/meta_iso: P(acertar) pelo meta-model (e calibrado se meta_iso existir)
      - iso: iso(conf) ~ P(acertar)
      - conf: conf
    """
    gate_mode = (gate_mode or "meta").strip().lower()

    # tolera novos modos (ex: "cp") sem quebrar
    if gate_mode not in ("meta", "iso", "conf", "cp"):
        gate_mode = "meta"

    proba = cal_model.predict_proba(df[feat_cols])[:, 1].astype(float)
    conf = np.maximum(proba, 1.0 - proba).astype(float)
    iso_score = iso.predict(conf.astype(float)).astype(float) if iso is not None else conf

    # META (com P15 meta_iso opcional)
    if gate_mode in ("meta", "cp") and meta_model is not None:
        try:
            pack = meta_model
            model = getattr(pack, "model", pack)
            meta_iso = getattr(pack, "iso", None)

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

            # aplica calibrador isotonic do meta_score se existir
            if meta_iso is not None and _truthy(os.getenv("META_ISO_ENABLE", "1")):
                s = meta_iso.predict(s_raw.astype(float)).astype(float)
                return proba, conf, s, "meta_iso"

            return proba, conf, s_raw, "meta"
        except Exception:
            pass

    # ISO gate
    if gate_mode == "iso" and iso is not None:
        return proba, conf, iso_score, "iso"

    return proba, conf, conf, "conf"
'''


def main() -> None:
    root = repo_root()
    target = root / "src" / "natbin" / "gate_meta.py"
    target.parent.mkdir(parents=True, exist_ok=True)

    bkp = backup_if_exists(target)
    target.write_text(GATE_META_PY, encoding="utf-8")
    py_compile.compile(str(target), doraise=True)

    print(f"[P15] OK wrote {target} (backup={bkp})")
    print("[P15] GATE_VERSION bumped -> observe cache deve retrainar automaticamente.")
    print("[P15] Env knobs (opcionais): META_ISO_ENABLE=1 META_ISO_CAL_FRAC=0.25 META_ISO_MIN=200")


if __name__ == "__main__":
    main()