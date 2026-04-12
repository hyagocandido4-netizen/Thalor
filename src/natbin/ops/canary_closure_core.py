from __future__ import annotations

import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class JsonEvent:
    index: int
    start: int
    end: int
    payload: Any


_HEALTHY_WAIT_REASONS = {
    "regime_block",
    "below_ev_threshold",
    "not_in_topk_today",
    "cp_reject",
}


@dataclass(frozen=True)
class ClosureDebt:
    name: str
    scope_tags: tuple[str, ...]
    count: int
    message: str
    recommended_action: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "scope_tags": list(self.scope_tags),
            "count": int(self.count),
            "message": self.message,
            "recommended_action": self.recommended_action,
        }


def extract_json_events(text: str) -> list[JsonEvent]:
    decoder = json.JSONDecoder()
    events: list[JsonEvent] = []
    i = 0
    event_index = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, i)
        except JSONDecodeError:
            i += 1
            continue
        events.append(JsonEvent(index=event_index, start=i, end=end, payload=obj))
        event_index += 1
        i = end
    return events


def choose_top_level_payload(events: Iterable[JsonEvent]) -> dict[str, Any] | None:
    scored: list[tuple[int, dict[str, Any]]] = []
    for event in events:
        payload = event.payload
        if not isinstance(payload, dict):
            continue
        score = 0
        if "kind" in payload:
            score += 100
        if "summary" in payload:
            score += 25
        if "scope_results" in payload:
            score += 10
        if "recommended_action" in payload:
            score += 5
        if "ok" in payload:
            score += 5
        if "severity" in payload:
            score += 5
        score += min(len(payload.keys()), 25)
        scored.append((score, payload))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def extract_top_level_json(text: str) -> dict[str, Any] | None:
    return choose_top_level_payload(extract_json_events(text))


def summarize_signal_audit(audit_payload: dict[str, Any] | None) -> dict[str, Any]:
    summary = dict((audit_payload or {}).get("summary") or {})
    return {
        "full_scope_count": int(summary.get("full_scope_count") or 0),
        "actionable_scopes": int(summary.get("actionable_scopes") or 0),
        "watch_scopes": int(summary.get("watch_scopes") or 0),
        "hold_scopes": int(summary.get("hold_scopes") or 0),
        "missing_artifact_scopes": int(summary.get("missing_artifact_scopes") or 0),
        "stale_artifact_scopes": int(summary.get("stale_artifact_scopes") or 0),
        "cp_meta_missing_scopes": int(summary.get("cp_meta_missing_scopes") or 0),
        "regime_block_scopes": int(summary.get("regime_block_scopes") or 0),
        "cp_reject_scopes": int(summary.get("cp_reject_scopes") or 0),
        "threshold_block_scopes": int(summary.get("threshold_block_scopes") or 0),
        "topk_suppressed_scopes": int(summary.get("topk_suppressed_scopes") or 0),
        "gate_fail_closed_scopes": int(summary.get("gate_fail_closed_scopes") or 0),
        "dominant_nontrade_reason": summary.get("dominant_nontrade_reason"),
        "recommended_action": summary.get("recommended_action"),
        "best_watch_scope_tag": summary.get("best_watch_scope_tag"),
        "best_hold_scope_tag": summary.get("best_hold_scope_tag"),
    }


def summarize_signal_scan(scan_payload: dict[str, Any] | None) -> dict[str, Any]:
    summary = dict((scan_payload or {}).get("summary") or {})
    return {
        "scope_count": int(summary.get("scope_count") or 0),
        "actionable_scopes": int(summary.get("actionable_scopes") or 0),
        "watch_scopes": int(summary.get("watch_scopes") or 0),
        "hold_scopes": int(summary.get("hold_scopes") or 0),
        "candidate_error_scopes": int(summary.get("candidate_error_scopes") or 0),
        "cp_meta_missing_scopes": int(summary.get("cp_meta_missing_scopes") or 0),
        "regime_block_scopes": int(summary.get("regime_block_scopes") or 0),
        "cp_reject_scopes": int(summary.get("cp_reject_scopes") or 0),
        "threshold_block_scopes": int(summary.get("threshold_block_scopes") or 0),
        "topk_suppressed_scopes": int(summary.get("topk_suppressed_scopes") or 0),
        "gate_fail_closed_scopes": int(summary.get("gate_fail_closed_scopes") or 0),
        "dominant_nontrade_reason": summary.get("dominant_nontrade_reason"),
        "recommended_action": summary.get("recommended_action"),
        "best_watch_scope_tag": summary.get("best_watch_scope_tag"),
        "best_hold_scope_tag": summary.get("best_hold_scope_tag"),
    }


def summarize_provider(provider_payload: dict[str, Any] | None) -> dict[str, Any]:
    summary = dict((provider_payload or {}).get("summary") or {})
    return {
        "scope_count": int(summary.get("scope_count") or 0),
        "provider_ready_scopes": int(summary.get("provider_ready_scopes") or 0),
        "warmup_effective_ready_scopes": int(summary.get("warmup_effective_ready_scopes") or 0),
        "signal_actionable_scopes": int(summary.get("signal_actionable_scopes") or 0),
        "parallel_execution_allowed": bool(summary.get("parallel_execution_allowed") or False),
        "hard_blockers": list(summary.get("hard_blockers") or []),
        "transient_noise_categories": list(summary.get("transient_noise_categories") or []),
        "stability_state": (provider_payload or {}).get("stability_state"),
        "severity": (provider_payload or {}).get("severity"),
        "ok": bool((provider_payload or {}).get("ok")),
    }


def _scope_results(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (payload or {}).get("scope_results") or []
    return [item for item in raw if isinstance(item, dict)]


def _scope_tag(item: Mapping[str, Any]) -> str | None:
    scope = item.get("scope")
    if not isinstance(scope, Mapping):
        return None
    tag = scope.get("scope_tag")
    return str(tag) if isinstance(tag, str) and tag else None


def _blocker_flags(item: Mapping[str, Any]) -> dict[str, bool]:
    raw = item.get("blocker_flags")
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): bool(v) for k, v in raw.items()}


def _candidate_blockers(item: Mapping[str, Any]) -> set[str]:
    raw = item.get("candidate_blockers") or []
    out: set[str] = set()
    if isinstance(raw, list):
        for value in raw:
            if isinstance(value, str) and value:
                out.add(value)
    return out


def _find_scope(audit_payload: dict[str, Any] | None, scope_tag: str | None) -> dict[str, Any] | None:
    if not scope_tag:
        return None
    for item in _scope_results(audit_payload):
        if _scope_tag(item) == scope_tag:
            return item
    return None


def _is_healthy_wait_scope(item: Mapping[str, Any] | None) -> bool:
    if not isinstance(item, Mapping):
        return False
    if bool(item.get("stale")) or not bool(item.get("exists", True)) or bool(item.get("cp_meta_missing")):
        return False
    window_state = str(item.get("window_state") or "")
    if window_state not in {"watch", "ready"}:
        return False
    reason = str(item.get("dominant_reason") or item.get("candidate_reason") or "")
    return reason in _HEALTHY_WAIT_REASONS


def _is_nonblocking_cp_meta_debt_item(item: Mapping[str, Any]) -> bool:
    if not bool(item.get("cp_meta_missing")):
        return False
    if bool(item.get("stale")) or not bool(item.get("exists", True)):
        return False
    if str(item.get("window_state") or "") != "hold":
        return False
    blockers = _candidate_blockers(item)
    if blockers - {"gate_fail_closed", "below_ev_threshold", "not_in_topk_today"}:
        return False
    reason = str(item.get("candidate_reason") or item.get("dominant_reason") or "")
    flags = _blocker_flags(item)
    if reason not in {"gate_fail_closed", "regime_block", "cp_meta_missing"} and not bool(item.get("regime_block")):
        return False
    return bool(flags.get("gate_fail_closed") or reason in {"gate_fail_closed", "regime_block", "cp_meta_missing"})


def classify_secondary_cp_meta_debt(signal_audit_payload: dict[str, Any] | None) -> ClosureDebt | None:
    audit = summarize_signal_audit(signal_audit_payload)
    if audit["cp_meta_missing_scopes"] <= 0:
        return None
    best_watch = _find_scope(signal_audit_payload, audit.get("best_watch_scope_tag"))
    if not _is_healthy_wait_scope(best_watch):
        return None
    debt_tags: list[str] = []
    for item in _scope_results(signal_audit_payload):
        tag = _scope_tag(item)
        if not tag:
            continue
        if _is_nonblocking_cp_meta_debt_item(item):
            debt_tags.append(tag)
        elif bool(item.get("cp_meta_missing")):
            return None
    if not debt_tags:
        return None
    return ClosureDebt(
        name="secondary_cp_meta_debt",
        scope_tags=tuple(debt_tags),
        count=len(debt_tags),
        message=(
            "Scopes secundários ainda estão em fail-closed por cp_meta ausente, mas o melhor scope do canary já está apenas em no-trade saudável. "
            "Trate isso como dívida de inteligência/backfill, não como bloqueio do envelope conservador top-1."
        ),
        recommended_action="backfill_cp_meta_as_maintenance",
    )


def classify_closure(
    provider_payload: dict[str, Any] | None,
    signal_scan_payload: dict[str, Any] | None,
    signal_audit_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    provider = summarize_provider(provider_payload)
    scan = summarize_signal_scan(signal_scan_payload)
    audit = summarize_signal_audit(signal_audit_payload)

    secondary_cp_meta_debt = classify_secondary_cp_meta_debt(signal_audit_payload)
    debts: list[dict[str, Any]] = []
    if secondary_cp_meta_debt is not None:
        debts.append(secondary_cp_meta_debt.to_payload())
    debt_scope_tags = set(secondary_cp_meta_debt.scope_tags) if secondary_cp_meta_debt else set()

    blocking_gate_fail_closed_scopes = 0
    for item in _scope_results(signal_audit_payload):
        tag = _scope_tag(item)
        if tag in debt_scope_tags:
            continue
        if bool(_blocker_flags(item).get("gate_fail_closed")):
            blocking_gate_fail_closed_scopes += 1

    blocking_cp_meta_missing_scopes = max(0, audit["cp_meta_missing_scopes"] - len(debt_scope_tags))

    repair_needed = (
        audit["missing_artifact_scopes"] > 0
        or audit["stale_artifact_scopes"] > 0
        or blocking_cp_meta_missing_scopes > 0
        or blocking_gate_fail_closed_scopes > 0
    )

    provider_unstable = (
        provider["stability_state"] == "unstable"
        or bool(provider["hard_blockers"])
        or provider["provider_ready_scopes"] <= 0
    )
    provider_degraded = provider["stability_state"] == "degraded"

    dominant_reason = scan["dominant_nontrade_reason"] or audit["dominant_nontrade_reason"] or None
    healthy_waiting_signal = (
        not repair_needed
        and not provider_unstable
        and scan["actionable_scopes"] == 0
        and (
            dominant_reason in _HEALTHY_WAIT_REASONS
            or (
                (audit["watch_scopes"] > 0 or audit["hold_scopes"] > 0)
                and audit["missing_artifact_scopes"] == 0
                and audit["stale_artifact_scopes"] == 0
                and blocking_cp_meta_missing_scopes == 0
                and blocking_gate_fail_closed_scopes == 0
            )
        )
    )

    if provider_unstable:
        state = "provider_unstable"
        recommended_action = "stabilize_provider_first"
    elif repair_needed:
        state = "repair_needed"
        recommended_action = "run_portfolio_artifact_repair"
    elif scan["actionable_scopes"] > 0:
        state = "actionable_scope_ready"
        recommended_action = "capture_best_scope_evidence"
    elif healthy_waiting_signal:
        state = "healthy_waiting_signal"
        if secondary_cp_meta_debt is not None:
            if dominant_reason == "regime_block":
                recommended_action = "wait_regime_rescan_track_cp_meta_debt"
            else:
                recommended_action = "wait_next_candle_track_cp_meta_debt"
        else:
            recommended_action = "wait_next_candle_and_rescan"
    elif provider_degraded:
        state = "observe_only_degraded_provider"
        recommended_action = "keep_top1_observe_mode"
    else:
        state = "needs_review"
        recommended_action = "capture_fresh_canary_bundle"

    ok = state in {"actionable_scope_ready", "healthy_waiting_signal", "observe_only_degraded_provider"}
    severity = "ok" if state == "healthy_waiting_signal" else ("warn" if ok else "error")

    return {
        "kind": "canary_closure_report",
        "ok": ok,
        "severity": severity,
        "closure_state": state,
        "recommended_action": recommended_action,
        "provider": provider,
        "signal_scan": scan,
        "signal_audit": audit,
        "repair_scope_tags": choose_repair_scope_tags(signal_audit_payload),
        "closure_debts": debts,
        "blocking_cp_meta_missing_scopes": blocking_cp_meta_missing_scopes,
        "blocking_gate_fail_closed_scopes": blocking_gate_fail_closed_scopes,
    }


def choose_repair_scope_tags(signal_audit_payload: dict[str, Any] | None) -> list[str]:
    payload = signal_audit_payload or {}
    debt = classify_secondary_cp_meta_debt(signal_audit_payload)
    debt_scope_tags = set(debt.scope_tags) if debt else set()
    tags: list[str] = []
    for item in payload.get("scope_results") or []:
        scope = item.get("scope") or {}
        tag = scope.get("scope_tag")
        if not tag or not isinstance(tag, str):
            continue
        if tag in debt_scope_tags:
            continue
        if (
            bool(item.get("stale"))
            or bool(item.get("cp_meta_missing"))
            or not bool(item.get("exists", True))
            or bool((item.get("blocker_flags") or {}).get("gate_fail_closed"))
        ):
            tags.append(tag)
    return tags
