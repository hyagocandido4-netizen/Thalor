from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import CandidateDecision, PortfolioScope


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def candidate_from_decision_payload(
    scope: PortfolioScope,
    decision: dict[str, Any] | None,
    *,
    decision_path: str | Path | None = None,
) -> CandidateDecision:
    """Build a canonical CandidateDecision from a decision_latest payload.

    This helper is shared by the portfolio runner and by intelligence recovery
    flows that need to reconstruct the latest candidate for a scope.
    """

    path_text = str(decision_path) if decision_path is not None else None
    if not isinstance(decision, dict):
        return CandidateDecision(
            scope_tag=scope.scope_tag,
            asset=scope.asset,
            interval_sec=int(scope.interval_sec),
            day=None,
            ts=None,
            action='HOLD',
            score=None,
            conf=None,
            ev=None,
            reason='decision_missing',
            blockers=None,
            decision_path=path_text,
            raw={},
        )

    action = str(decision.get('action') or decision.get('signal') or 'HOLD').upper()
    ts = _safe_int(decision.get('ts') or decision.get('signal_ts') or 0)
    if ts == 0:
        ts = None
    day = decision.get('day')
    if day is not None:
        day = str(day)

    intelligence = dict(decision.get('intelligence') or {}) if isinstance(decision.get('intelligence'), dict) else {}
    portfolio_feedback = dict(decision.get('portfolio_feedback') or {}) if isinstance(decision.get('portfolio_feedback'), dict) else {}
    if not portfolio_feedback and isinstance(intelligence.get('portfolio_feedback'), dict):
        portfolio_feedback = dict(intelligence.get('portfolio_feedback') or {})

    out = CandidateDecision(
        scope_tag=scope.scope_tag,
        asset=scope.asset,
        interval_sec=int(scope.interval_sec),
        day=day,
        ts=ts,
        action=action,
        score=_safe_float(decision.get('score')),
        conf=_safe_float(decision.get('conf')),
        ev=_safe_float(decision.get('ev')),
        reason=str(decision.get('reason') or decision.get('why') or '') or None,
        blockers=str(decision.get('blockers') or '') or None,
        decision_path=path_text,
        raw=dict(decision),
        intelligence_score=_safe_float(decision.get('intelligence_score')),
        learned_gate_prob=_safe_float(decision.get('learned_gate_prob')),
        slot_multiplier=_safe_float(decision.get('slot_multiplier')),
        drift_level=str(decision.get('drift_level')) if decision.get('drift_level') is not None else None,
        coverage_bias=_safe_float(decision.get('coverage_bias')),
        stack_decision=str(decision.get('stack_decision')) if decision.get('stack_decision') is not None else None,
        regime_level=str(decision.get('regime_level')) if decision.get('regime_level') is not None else None,
        portfolio_score=_safe_float(decision.get('portfolio_score')),
        retrain_state=str(decision.get('retrain_state')) if decision.get('retrain_state') is not None else None,
        retrain_priority=str(decision.get('retrain_priority')) if decision.get('retrain_priority') is not None else None,
        intelligence=intelligence,
        portfolio_feedback=portfolio_feedback,
    )
    return out


__all__ = ['candidate_from_decision_payload']
