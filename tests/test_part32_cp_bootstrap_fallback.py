from __future__ import annotations

import os
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from natbin.domain.gate_meta import MetaPack, compute_scores
from natbin.usecases.observer.runner import _normalize_gate_fail_closed


class _DummyCal:
    def predict_proba(self, X):
        arr = np.asarray(X)
        n = len(arr)
        p = np.full(n, 0.60, dtype=float)
        return np.column_stack([1.0 - p, p])


class _DummyMetaModel:
    def predict_proba(self, X):
        arr = np.asarray(X)
        n = len(arr)
        p = np.full(n, 0.72, dtype=float)
        return np.column_stack([1.0 - p, p])


class _DummyIso:
    def predict(self, x):
        arr = np.asarray(x, dtype=float)
        return np.clip(arr + 0.05, 0.0, 1.0)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            'ts': [1775892300, 1775892600],
            'f_vol48': [0.01, 0.02],
            'f_bb_width20': [0.03, 0.04],
            'f_atr14': [0.02, 0.03],
        }
    )


def test_compute_scores_cp_missing_falls_back_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv('GATE_FAIL_CLOSED', '1')
    monkeypatch.setenv('CP_BOOTSTRAP_FALLBACK', 'auto')

    proba, conf, score, gate_used = compute_scores(
        df=_frame(),
        feat_cols=['f_vol48', 'f_bb_width20', 'f_atr14'],
        tz=ZoneInfo('UTC'),
        cal_model=_DummyCal(),
        iso=None,
        meta_model=MetaPack(model=_DummyMetaModel(), iso=None, cp=None),
        gate_mode='cp',
    )

    assert gate_used == 'cp_fallback_meta'
    assert np.all(score > 0.0)
    assert np.all(conf > 0.0)
    assert np.all(proba > 0.0)



def test_compute_scores_cp_missing_stays_fail_closed_when_fallback_disabled(monkeypatch) -> None:
    monkeypatch.setenv('GATE_FAIL_CLOSED', '1')
    monkeypatch.delenv('CP_BOOTSTRAP_FALLBACK', raising=False)

    _proba, _conf, score, gate_used = compute_scores(
        df=_frame(),
        feat_cols=['f_vol48', 'f_bb_width20', 'f_atr14'],
        tz=ZoneInfo('UTC'),
        cal_model=_DummyCal(),
        iso=None,
        meta_model=MetaPack(model=_DummyMetaModel(), iso=None, cp=None),
        gate_mode='cp',
    )

    assert gate_used == 'cp_fail_closed_missing_cp_meta'
    assert np.allclose(score, 0.0)



def test_compute_scores_cp_missing_uses_meta_iso_name_when_available(monkeypatch) -> None:
    monkeypatch.setenv('GATE_FAIL_CLOSED', '1')
    monkeypatch.setenv('CP_BOOTSTRAP_FALLBACK', 'auto')
    monkeypatch.setenv('META_ISO_ENABLE', '1')
    monkeypatch.setenv('META_ISO_BLEND', '1.0')

    _proba, _conf, score, gate_used = compute_scores(
        df=_frame(),
        feat_cols=['f_vol48', 'f_bb_width20', 'f_atr14'],
        tz=ZoneInfo('UTC'),
        cal_model=_DummyCal(),
        iso=None,
        meta_model=MetaPack(model=_DummyMetaModel(), iso=_DummyIso(), cp=None),
        gate_mode='cp',
    )

    assert gate_used == 'cp_fallback_meta_iso'
    assert np.all(score > 0.0)



def test_normalize_gate_fail_closed_accepts_cp_fallback_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv('CP_BOOTSTRAP_FALLBACK', 'auto')
    score = np.array([0.25, 0.4], dtype=float)
    out, active, detail = _normalize_gate_fail_closed(
        score=score,
        gate_used='cp_fallback_meta',
        gate_mode_requested='cp',
        gate_fail_closed_enabled=True,
    )
    assert active is False
    assert detail == ''
    assert np.allclose(out, score)



def test_normalize_gate_fail_closed_blocks_cp_fallback_when_disabled(monkeypatch) -> None:
    monkeypatch.delenv('CP_BOOTSTRAP_FALLBACK', raising=False)
    score = np.array([0.25, 0.4], dtype=float)
    out, active, detail = _normalize_gate_fail_closed(
        score=score,
        gate_used='cp_fallback_meta',
        gate_mode_requested='cp',
        gate_fail_closed_enabled=True,
    )
    assert active is True
    assert detail == 'cp_fallback_meta'
    assert np.allclose(out, 0.0)
