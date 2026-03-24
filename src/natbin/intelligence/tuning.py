from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .anti_overfit import build_anti_overfit_report
from .recovery import synthesize_multiwindow_summary_from_training_rows


_MIN_GAP_WEIGHT = 0.01


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _write_json(path: str | Path | None, payload: dict[str, Any]) -> None:
    if path in (None, ''):
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')


def _recent_training_rows(rows: list[dict[str, Any]], *, recent_rows_min: int) -> list[dict[str, Any]]:
    if not rows:
        return []
    target = max(int(recent_rows_min), int(len(rows) * 0.5))
    target = max(1, min(target, len(rows)))
    return [dict(row) for row in rows[-target:]]


def _variant_objective(
    *,
    report: dict[str, Any],
    min_robustness: float,
    min_windows: int,
    gap_penalty_weight: float,
    base_min_robustness: float,
    base_min_windows: int,
    base_gap_penalty_weight: float,
    source_kind: str,
) -> tuple[float, dict[str, float]]:
    robustness = float(_safe_float(report.get('robustness_score'), 0.0) or 0.0)
    accepted = bool(report.get('accepted', True))
    penalty = float(_safe_float(report.get('penalty'), 0.0) or 0.0)

    threshold_relief = max(0.0, float(base_min_robustness) - float(min_robustness))
    window_relief = max(0, int(base_min_windows) - int(min_windows))
    gap_relief = max(0.0, float(base_gap_penalty_weight) - float(gap_penalty_weight))

    relief_cost = (threshold_relief * 0.80) + (float(window_relief) * 0.03) + (gap_relief * 0.50)
    source_cost = 0.02 if str(source_kind).startswith('recent_') else 0.0
    accepted_bonus = 0.30 if accepted else 0.0
    objective = robustness + accepted_bonus - (penalty * 0.25) - relief_cost - source_cost
    return float(objective), {
        'threshold_relief': float(threshold_relief),
        'window_relief': float(window_relief),
        'gap_relief': float(gap_relief),
        'relief_cost': float(relief_cost),
        'source_cost': float(source_cost),
        'accepted_bonus': float(accepted_bonus),
    }


def _build_variant(
    *,
    variant_id: str,
    label: str,
    source_kind: str,
    summary_payload: dict[str, Any] | None,
    min_robustness: float,
    min_windows: int,
    gap_penalty_weight: float,
    base_min_robustness: float,
    base_min_windows: int,
    base_gap_penalty_weight: float,
) -> dict[str, Any]:
    report = build_anti_overfit_report(
        summary_payload,
        min_robustness=float(min_robustness),
        min_windows=int(min_windows),
        gap_penalty_weight=float(gap_penalty_weight),
    )
    objective, components = _variant_objective(
        report=report,
        min_robustness=float(min_robustness),
        min_windows=int(min_windows),
        gap_penalty_weight=float(gap_penalty_weight),
        base_min_robustness=float(base_min_robustness),
        base_min_windows=int(base_min_windows),
        base_gap_penalty_weight=float(base_gap_penalty_weight),
        source_kind=source_kind,
    )
    return {
        'variant_id': str(variant_id),
        'label': str(label),
        'source_kind': str(source_kind),
        'params': {
            'min_robustness': float(min_robustness),
            'min_windows': int(min_windows),
            'gap_penalty_weight': float(gap_penalty_weight),
        },
        'report': report,
        'objective': float(objective),
        'objective_components': components,
    }


def tune_anti_overfit(
    *,
    summary_payload: dict[str, Any] | None,
    summary_source_kind: str,
    training_rows: list[dict[str, Any]],
    timezone_name: str,
    base_min_robustness: float,
    base_min_windows: int,
    base_gap_penalty_weight: float,
    tuning_enable: bool = True,
    min_robustness_floor: float = 0.45,
    window_flex: int = 1,
    gap_penalty_flex: float = 0.03,
    recent_rows_min: int = 48,
    objective_min_delta: float = 0.015,
    out_path: str | Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline_variant = _build_variant(
        variant_id='baseline',
        label='Baseline anti-overfit policy',
        source_kind=str(summary_source_kind or 'missing'),
        summary_payload=summary_payload,
        min_robustness=float(base_min_robustness),
        min_windows=int(base_min_windows),
        gap_penalty_weight=float(base_gap_penalty_weight),
        base_min_robustness=float(base_min_robustness),
        base_min_windows=int(base_min_windows),
        base_gap_penalty_weight=float(base_gap_penalty_weight),
    )

    variants: list[dict[str, Any]] = [baseline_variant]
    selected_variant = baseline_variant
    selection_reason = 'baseline_only'

    if tuning_enable:
        floor = min(float(base_min_robustness), float(min_robustness_floor))
        floor = max(0.0, floor)
        relaxed_windows = max(1, int(base_min_windows) - max(0, int(window_flex)))
        relaxed_gap = max(_MIN_GAP_WEIGHT, float(base_gap_penalty_weight) - max(0.0, float(gap_penalty_flex)))

        extra_specs: list[tuple[str, str, str, dict[str, Any] | None, float, int, float]] = []
        if float(floor) < float(base_min_robustness):
            extra_specs.append(
                (
                    'robustness_relief',
                    'Relaxed robustness floor',
                    str(summary_source_kind or 'missing'),
                    summary_payload,
                    float(floor),
                    int(base_min_windows),
                    float(base_gap_penalty_weight),
                )
            )
            extra_specs.append(
                (
                    'robustness_relief_balanced',
                    'Relaxed robustness + gap relief',
                    str(summary_source_kind or 'missing'),
                    summary_payload,
                    float(floor),
                    int(base_min_windows),
                    float(relaxed_gap),
                )
            )
        if int(relaxed_windows) != int(base_min_windows):
            extra_specs.append(
                (
                    'window_relief',
                    'Relaxed window requirement',
                    str(summary_source_kind or 'missing'),
                    summary_payload,
                    float(base_min_robustness),
                    int(relaxed_windows),
                    float(base_gap_penalty_weight),
                )
            )

        recent_rows = _recent_training_rows(training_rows, recent_rows_min=int(recent_rows_min))
        recent_summary = synthesize_multiwindow_summary_from_training_rows(
            recent_rows,
            timezone_name=str(timezone_name or 'UTC'),
            min_windows=max(2, int(base_min_windows)),
        ) if recent_rows else None
        if recent_summary is not None:
            extra_specs.append(
                (
                    'recent_baseline',
                    'Recent rows baseline',
                    'recent_training_rows',
                    recent_summary,
                    float(base_min_robustness),
                    int(base_min_windows),
                    float(base_gap_penalty_weight),
                )
            )
            if float(floor) < float(base_min_robustness):
                extra_specs.append(
                    (
                        'recent_balanced_relief',
                        'Recent rows + relaxed floor',
                        'recent_training_rows',
                        recent_summary,
                        float(floor),
                        int(relaxed_windows),
                        float(relaxed_gap),
                    )
                )

        for variant_id, label, source_kind, payload, min_robustness, min_windows, gap_penalty_weight in extra_specs:
            variants.append(
                _build_variant(
                    variant_id=variant_id,
                    label=label,
                    source_kind=source_kind,
                    summary_payload=payload,
                    min_robustness=min_robustness,
                    min_windows=min_windows,
                    gap_penalty_weight=gap_penalty_weight,
                    base_min_robustness=float(base_min_robustness),
                    base_min_windows=int(base_min_windows),
                    base_gap_penalty_weight=float(base_gap_penalty_weight),
                )
            )

        ranked = sorted(
            variants,
            key=lambda item: (
                float(item.get('objective') or 0.0),
                1 if bool((item.get('report') or {}).get('accepted', False)) else 0,
                float(((item.get('report') or {}).get('robustness_score')) or 0.0),
            ),
            reverse=True,
        )
        best = ranked[0]
        baseline_objective = float(baseline_variant.get('objective') or 0.0)
        best_objective = float(best.get('objective') or 0.0)
        objective_delta = best_objective - baseline_objective
        baseline_accepted = bool((baseline_variant.get('report') or {}).get('accepted', False))
        best_accepted = bool((best.get('report') or {}).get('accepted', False))
        baseline_robustness = float((((baseline_variant.get('report') or {}).get('robustness_score')) or 0.0))
        best_robustness = float((((best.get('report') or {}).get('robustness_score')) or 0.0))

        if (
            str(best.get('variant_id')) != 'baseline'
            and (
                best_accepted != baseline_accepted
                or objective_delta >= float(objective_min_delta)
                or (best_robustness - baseline_robustness) >= 0.02
            )
        ):
            selected_variant = best
            selection_reason = 'objective_improved'
        else:
            selected_variant = baseline_variant
            selection_reason = 'baseline_retained'

    payload = {
        'kind': 'anti_overfit_tuning',
        'schema_version': 'phase1-anti-overfit-tuning-v1',
        'generated_at_utc': _iso_now(),
        'enabled': bool(tuning_enable),
        'base_source_kind': str(summary_source_kind or 'missing'),
        'baseline_variant': str(baseline_variant.get('variant_id') or 'baseline'),
        'selected_variant': str(selected_variant.get('variant_id') or 'baseline'),
        'selection_reason': selection_reason,
        'objective_min_delta': float(objective_min_delta),
        'improved': str(selected_variant.get('variant_id') or 'baseline') != str(baseline_variant.get('variant_id') or 'baseline'),
        'baseline': baseline_variant,
        'selected': selected_variant,
        'variants': variants,
        'tuning_params': {
            'min_robustness_floor': float(min_robustness_floor),
            'window_flex': int(window_flex),
            'gap_penalty_flex': float(gap_penalty_flex),
            'recent_rows_min': int(recent_rows_min),
        },
    }
    _write_json(out_path, payload)
    return payload, dict(selected_variant.get('report') or {})
