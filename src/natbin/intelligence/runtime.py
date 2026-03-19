from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..portfolio.models import CandidateDecision, PortfolioScope
from ..portfolio.paths import ScopeRuntimePaths
from .coverage import coverage_bias
from .drift import assess_drift, load_recent_signal_rows, update_drift_state
from .learned_gate import feature_row_from_signal, predict_probability, stack_decision
from .paths import drift_state_path, latest_eval_path, pack_path, retrain_trigger_path
from .policy import resolve_scope_policy
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
    policy = resolve_scope_policy(int_cfg, scope)
    pack = load_intelligence_pack(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir)
    eval_payload: dict[str, Any] = {
        'kind': 'intelligence_eval',
        'schema_version': 'phase1-intelligence-eval-v2',
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
    slot_rec = dict(slot.get('recommendation') or {})

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
        curve_power=float(getattr(int_cfg, 'coverage_curve_power', 1.20)),
        max_bonus=float(getattr(int_cfg, 'coverage_max_bonus', 0.05)),
        max_penalty=float(getattr(int_cfg, 'coverage_max_penalty', 0.05)),
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
    learned_reliability = None
    if bool(getattr(int_cfg, 'learned_gating_enable', True)):
        learned_prob = predict_probability(
            learned,
            feature_payload,
            apply_calibration=bool(getattr(int_cfg, 'learned_calibration_enable', True)),
        )
        if isinstance(learned, dict) and learned.get('reliability_score') is not None:
            learned_reliability = _safe_float(learned.get('reliability_score'), 0.0)

    stack = stack_decision(
        base_quality=base_quality_f,
        learned_prob=learned_prob,
        weight=float(policy.get('learned_weight') or getattr(int_cfg, 'learned_gating_weight', 0.60)),
        promote_above=float(policy.get('promote_above') or getattr(int_cfg, 'learned_promote_above', 0.62)),
        suppress_below=float(policy.get('suppress_below') or getattr(int_cfg, 'learned_suppress_below', 0.42)),
        abstain_band=float(policy.get('abstain_band') or getattr(int_cfg, 'learned_abstain_band', 0.03)),
        reliability_score=learned_reliability,
        min_reliability=float(policy.get('min_reliability') or getattr(int_cfg, 'learned_min_reliability', 0.50)),
        neutralize_low_reliability=bool(policy.get('neutralize_low_reliability', getattr(int_cfg, 'learned_neutralize_low_reliability', True))),
        max_bonus=float(policy.get('stack_max_bonus') or getattr(int_cfg, 'stack_max_bonus', 0.05)),
        max_penalty=float(policy.get('stack_max_penalty') or getattr(int_cfg, 'stack_max_penalty', 0.05)),
    )
    blended_quality = float(stack.get('blended_quality') or base_quality_f)
    quality_delta = float(stack.get('delta') or 0.0)

    drift_report = {
        'kind': 'drift_report',
        'level': 'ok',
        'penalty': 0.0,
        'reason': 'disabled_or_missing',
        'regime': {'level': 'ok', 'severity': 0.0, 'direction': 'flat'},
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
            regime_warn_shift=float(getattr(int_cfg, 'regime_warn_shift', 0.10)),
            regime_block_shift=float(getattr(int_cfg, 'regime_block_shift', 0.20)),
        )
        drift_state = update_drift_state(
            drift_state_path(repo_root=repo_root, scope_tag=scope.scope_tag, artifact_dir=artifact_dir),
            level=str(drift_report.get('level') or 'ok'),
            warn_streak_threshold=int(getattr(int_cfg, 'retrain_warn_streak', 3)),
            block_streak_threshold=int(getattr(int_cfg, 'retrain_block_streak', 1)),
            cooldown_hours=int(getattr(int_cfg, 'retrain_cooldown_hours', 12)),
        )
        if bool(drift_state.get('retrain_recommended', False)):
            retrain_priority = 'high' if str(drift_report.get('level') or 'ok') == 'block' else 'medium'
            if learned_reliability is not None and learned_reliability < float(policy.get('min_reliability') or 0.50):
                retrain_priority = 'high'
            retrain_payload = {
                'kind': 'retrain_trigger',
                'schema_version': 'phase1-retrain-trigger-v3',
                'generated_at_utc': _now_utc(),
                'scope_tag': scope.scope_tag,
                'asset': scope.asset,
                'interval_sec': int(scope.interval_sec),
                'reason': drift_state.get('retrain_reason'),
                'priority': retrain_priority,
                'drift_state': drift_state,
                'regime': drift_report.get('regime'),
                'learned_reliability': learned_reliability,
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
    slot_score_delta = _safe_float(slot_rec.get('score_delta'), 0.0) if bool(getattr(int_cfg, 'slot_aware_enable', True)) else 0.0
    coverage_adjustment = float(cov.get('bias') or 0.0) if bool(getattr(int_cfg, 'coverage_regulator_enable', True)) else 0.0

    stack_bonus = 0.0
    if bool(getattr(int_cfg, 'learned_stacking_enable', True)):
        if str(stack.get('decision')) == 'promote':
            stack_bonus = min(float(policy.get('stack_max_bonus') or 0.05), max(0.0, float(stack.get('delta') or 0.0)))
        elif str(stack.get('decision')) == 'suppress':
            stack_bonus = -min(float(policy.get('stack_max_penalty') or 0.05), max(0.0, abs(float(stack.get('delta') or 0.0))))

    intelligence_score = float(base_rank_f) + float(quality_delta) + float(slot_score_delta) + float(stack_bonus)
    intelligence_score = float(intelligence_score) * float(slot_multiplier)
    intelligence_score += float(coverage_adjustment)
    intelligence_score -= float(drift_report.get('penalty') or 0.0)
    intelligence_score -= float(anti_penalty)

    allow_trade = True
    block_reason = None
    if action in {'CALL', 'PUT'} and str(drift_report.get('level') or 'ok') == 'block' and bool(policy.get('drift_fail_closed', getattr(int_cfg, 'drift_fail_closed', False))):
        allow_trade = False
        block_reason = 'intelligence_drift_block'
    if action in {'CALL', 'PUT'} and bool(getattr(int_cfg, 'anti_overfit_fail_closed', False)) and bool(anti) and not bool(anti.get('accepted', True)):
        allow_trade = False
        block_reason = block_reason or 'intelligence_anti_overfit_block'
    if action in {'CALL', 'PUT'} and bool(policy.get('learned_fail_closed', getattr(int_cfg, 'learned_fail_closed', False))) and str(stack.get('decision')) == 'suppress':
        allow_trade = False
        block_reason = block_reason or 'intelligence_stack_suppress'
    if action in {'CALL', 'PUT'} and bool(policy.get('learned_fail_closed', getattr(int_cfg, 'learned_fail_closed', False))) and learned_reliability is not None and learned_reliability < float(policy.get('min_reliability') or 0.50):
        allow_trade = False
        block_reason = block_reason or 'intelligence_low_reliability'

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
        'stack': stack,
        'policy': policy,
        'learned_gate_prob': None if learned_prob is None else float(learned_prob),
        'learned_reliability': learned_reliability,
        'learned_probability_source': None if not isinstance(learned, dict) else learned.get('probability_source'),
        'slot': slot,
        'coverage': cov,
        'drift': drift_report,
        'regime': drift_report.get('regime') or {'level': 'ok', 'severity': 0.0, 'direction': 'flat'},
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
    raw['stack_decision'] = str(stack.get('decision') or 'neutral')
    raw['learned_reliability'] = learned_reliability
    raw['regime_level'] = str(((drift_report.get('regime') or {}).get('level')) or 'ok')

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
        stack_decision=str(stack.get('decision') or 'neutral'),
        regime_level=str(((drift_report.get('regime') or {}).get('level')) or 'ok'),
        intelligence=intelligence,
    )
