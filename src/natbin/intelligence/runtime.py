
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..portfolio.models import CandidateDecision, PortfolioScope
from ..portfolio.paths import ScopeRuntimePaths
from .anti_overfit import build_anti_overfit_report
from .coverage import coverage_bias
from .drift import assess_drift, load_recent_signal_rows, update_drift_state
from .learned_gate import feature_row_from_signal, predict_probability
from .paths import drift_state_path, latest_eval_path, pack_path, retrain_trigger_path
from .slot_profile import slot_stats_for_ts


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def load_intelligence_pack(
    *,
    repo_root: str | Path,
    scope_tag: str,
    artifact_dir: str | Path,
) -> dict[str, Any] | None:
    p = pack_path(repo_root=repo_root, scope_tag=scope_tag, artifact_dir=artifact_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def enrich_candidate(
    *,
    repo_root: str | Path,
    scope: PortfolioScope,
    candidate: CandidateDecision,
    runtime_paths: ScopeRuntimePaths | None,
    cfg: Any,
) -> CandidateDecision:
    int_cfg = getattr(cfg, 'intelligence', None)
    if not bool(getattr(int_cfg, 'enabled', False)):
        return candidate

    artifact_dir = getattr(int_cfg, 'artifact_dir', Path('runs/intelligence'))
    pack = load_intelligence_pack(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)
    eval_payload: dict[str, Any] = {
        'kind': 'intelligence_eval',
        'schema_version': 'm5-intelligence-eval-v1',
        'evaluated_at_utc': _now_utc(),
        'scope_tag': scope.scope_tag,
        'asset': scope.asset,
        'interval_sec': int(scope.interval_sec),
        'pack_available': bool(pack is not None),
    }

    if pack is None:
        eval_payload['status'] = 'pack_missing'
        _write_json(latest_eval_path(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir), eval_payload)
        return candidate

    raw = dict(candidate.raw or {})
    ts = candidate.ts if candidate.ts is not None else _safe_int(raw.get('ts'), 0) or None
    action = str(candidate.action or raw.get('action') or 'HOLD').upper()
    executed_today = _safe_int(raw.get('executed_today'), 0)
    payout = _safe_float(raw.get('payout'), 0.80)

    base_rank = candidate.ev
    if base_rank is None:
        base_rank = candidate.score if candidate.score is not None else candidate.conf
    base_rank_f = _safe_float(base_rank, 0.0)
    base_quality = candidate.score if candidate.score is not None else candidate.conf
    if base_quality is None and candidate.ev is not None:
        base_quality = 0.5 + float(candidate.ev)
    base_quality_f = max(0.0, min(1.0, _safe_float(base_quality, 0.5)))

    slot_profile = pack.get('slot_profile')
    slot = slot_stats_for_ts(slot_profile, ts, timezone_name=scope.timezone)

    target_tpd = getattr(int_cfg, 'coverage_target_trades_per_day', None)
    if target_tpd is None:
        target_tpd = getattr(getattr(cfg, 'quota', None), 'target_trades_per_day', 1.0)
    cov = coverage_bias(
        pack.get('coverage_profile'),
        ts=ts,
        timezone_name=scope.timezone,
        executed_today=executed_today,
        target_trades_per_day=float(target_tpd or 1.0),
        tolerance=float(getattr(int_cfg, 'coverage_tolerance', 0.5)),
        bias_weight=float(getattr(int_cfg, 'coverage_bias_weight', 0.04)),
    )

    feature_payload = feature_row_from_signal(
        {
            'ts': ts,
            'action': action,
            'proba_up': raw.get('proba_up'),
            'conf': candidate.conf,
            'score': candidate.score,
            'ev': candidate.ev,
            'payout': payout,
            'executed_today': executed_today,
        },
        timezone_name=scope.timezone,
        slot_profile=slot_profile,
    )
    learned = pack.get('learned_gate')
    learned_prob = None
    if bool(getattr(int_cfg, 'learned_gating_enable', True)):
        learned_prob = predict_probability(learned, feature_payload)

    learned_weight = float(getattr(int_cfg, 'learned_gating_weight', 0.60))
    if learned_prob is not None:
        blended_quality = ((1.0 - learned_weight) * base_quality_f) + (learned_weight * float(learned_prob))
    else:
        blended_quality = float(base_quality_f)
    quality_delta = float(blended_quality - base_quality_f)

    drift_report = {
        'kind': 'drift_report',
        'level': 'ok',
        'penalty': 0.0,
        'reason': 'disabled_or_missing',
    }
    drift_state = None
    retrain_payload = None
    if bool(getattr(int_cfg, 'drift_monitor_enable', True)) and runtime_paths is not None and pack.get('drift_baseline'):
        recent_rows = load_recent_signal_rows(
            runtime_paths.signals_db_path,
            asset=scope.asset,
            interval_sec=int(scope.interval_sec),
            limit=int(getattr(int_cfg, 'drift_recent_limit', 200)),
        )
        drift_report = assess_drift(
            pack.get('drift_baseline'),
            recent_rows,
            warn_psi=float(getattr(int_cfg, 'drift_warn_psi', 0.15)),
            block_psi=float(getattr(int_cfg, 'drift_block_psi', 0.30)),
        )
        drift_state = update_drift_state(
            drift_state_path(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir),
            level=str(drift_report.get('level') or 'ok'),
            warn_streak_threshold=int(getattr(int_cfg, 'retrain_warn_streak', 3)),
            block_streak_threshold=int(getattr(int_cfg, 'retrain_block_streak', 1)),
        )
        if bool(drift_state.get('retrain_recommended', False)):
            retrain_payload = {
                'kind': 'retrain_trigger',
                'schema_version': 'm5-retrain-trigger-v1',
                'generated_at_utc': _now_utc(),
                'scope_tag': scope.scope_tag,
                'asset': scope.asset,
                'interval_sec': int(scope.interval_sec),
                'reason': drift_state.get('retrain_reason'),
                'drift_state': drift_state,
            }
            _write_json(retrain_trigger_path(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir), retrain_payload)
        else:
            trigger_p = retrain_trigger_path(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)
            if trigger_p.exists():
                try:
                    trigger_p.unlink()
                except Exception:
                    pass

    anti = pack.get('anti_overfit') or {}
    anti_penalty = _safe_float(anti.get('penalty'), 0.0) if bool(getattr(int_cfg, 'anti_overfit_enable', True)) else 0.0
    slot_multiplier = _safe_float(slot.get('multiplier'), 1.0) if bool(getattr(int_cfg, 'slot_aware_enable', True)) else 1.0
    coverage_penalty = -float(cov.get('bias') or 0.0) if not bool(getattr(int_cfg, 'coverage_regulator_enable', True)) else 0.0
    coverage_adjustment = float(cov.get('bias') or 0.0) if bool(getattr(int_cfg, 'coverage_regulator_enable', True)) else 0.0

    intelligence_score = (float(base_rank_f) + float(quality_delta))
    intelligence_score = float(intelligence_score) * float(slot_multiplier)
    intelligence_score += float(coverage_adjustment)
    intelligence_score -= float(drift_report.get('penalty') or 0.0)
    intelligence_score -= float(anti_penalty)
    intelligence_score -= float(coverage_penalty)

    allow_trade = True
    block_reason = None
    if action in {'CALL', 'PUT'} and str(drift_report.get('level') or 'ok') == 'block' and bool(getattr(int_cfg, 'drift_fail_closed', False)):
        allow_trade = False
        block_reason = 'intelligence_drift_block'
    if action in {'CALL', 'PUT'} and bool(getattr(int_cfg, 'anti_overfit_fail_closed', False)) and bool(anti) and not bool(anti.get('accepted', True)):
        allow_trade = False
        block_reason = block_reason or 'intelligence_anti_overfit_block'

    final_action = action
    final_reason = candidate.reason
    final_blockers = str(candidate.blockers or '')
    if not allow_trade and action in {'CALL', 'PUT'}:
        final_action = 'HOLD'
        final_reason = block_reason
        blockers = [b for b in [final_blockers, block_reason] if b]
        final_blockers = ';'.join(dict.fromkeys(';'.join(blockers).split(';')))

    intelligence = {
        'pack_available': True,
        'base_rank': float(base_rank_f),
        'base_quality': float(base_quality_f),
        'learned_gate_prob': None if learned_prob is None else float(learned_prob),
        'slot': slot,
        'coverage': cov,
        'drift': drift_report,
        'drift_state': drift_state,
        'anti_overfit': anti,
        'intelligence_score': float(intelligence_score),
        'allow_trade': bool(allow_trade),
        'block_reason': block_reason,
        'retrain_trigger': retrain_payload,
    }

    eval_payload.update(intelligence)
    eval_payload['status'] = 'ok'
    _write_json(latest_eval_path(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir), eval_payload)

    raw['intelligence'] = intelligence
    raw['intelligence_score'] = float(intelligence_score)
    raw['learned_gate_prob'] = None if learned_prob is None else float(learned_prob)
    raw['slot_multiplier'] = float(slot_multiplier)
    raw['drift_level'] = str(drift_report.get('level') or 'ok')
    raw['coverage_bias'] = float(cov.get('bias') or 0.0)

    return replace(
        candidate,
        action=final_action,
        reason=final_reason,
        blockers=final_blockers or None,
        raw=raw,
        intelligence_score=float(intelligence_score),
        learned_gate_prob=None if learned_prob is None else float(learned_prob),
        slot_multiplier=float(slot_multiplier),
        drift_level=str(drift_report.get('level') or 'ok'),
        coverage_bias=float(cov.get('bias') or 0.0),
        intelligence=intelligence,
    )
