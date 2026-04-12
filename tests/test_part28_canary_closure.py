
from __future__ import annotations

from natbin.ops.canary_closure_core import (
    JsonEvent,
    choose_repair_scope_tags,
    classify_closure,
    extract_json_events,
    extract_top_level_json,
)


def test_extract_top_level_json_prefers_kind_payload() -> None:
    text = 'noise {"foo": 1} middle {"kind": "signal_artifact_audit", "ok": true, "summary": {"watch_scopes": 1}} tail'
    payload = extract_top_level_json(text)
    assert payload is not None
    assert payload["kind"] == "signal_artifact_audit"


def test_choose_repair_scope_tags_picks_stale_missing_and_cp_meta() -> None:
    payload = {
        "scope_results": [
            {"scope": {"scope_tag": "EURUSD-OTC_300s"}, "exists": True, "stale": False, "cp_meta_missing": False},
            {"scope": {"scope_tag": "GBPUSD-OTC_300s"}, "exists": True, "stale": True, "cp_meta_missing": False},
            {"scope": {"scope_tag": "AUDUSD-OTC_300s"}, "exists": False, "stale": False, "cp_meta_missing": False},
            {"scope": {"scope_tag": "USDJPY-OTC_300s"}, "exists": True, "stale": False, "cp_meta_missing": True},
        ]
    }
    assert choose_repair_scope_tags(payload) == [
        "GBPUSD-OTC_300s",
        "AUDUSD-OTC_300s",
        "USDJPY-OTC_300s",
    ]


def test_classify_closure_repair_needed() -> None:
    provider = {
        "ok": True,
        "severity": "warn",
        "stability_state": "degraded",
        "summary": {"provider_ready_scopes": 6, "hard_blockers": []},
    }
    signal_scan = {
        "summary": {
            "actionable_scopes": 0,
            "dominant_nontrade_reason": "regime_block",
        }
    }
    audit = {
        "summary": {
            "stale_artifact_scopes": 1,
            "cp_meta_missing_scopes": 2,
            "missing_artifact_scopes": 0,
            "gate_fail_closed_scopes": 0,
        }
    }
    payload = classify_closure(provider, signal_scan, audit)
    assert payload["closure_state"] == "repair_needed"
    assert payload["recommended_action"] == "run_portfolio_artifact_repair"


def test_classify_closure_healthy_waiting_signal() -> None:
    provider = {
        "ok": True,
        "severity": "warn",
        "stability_state": "degraded",
        "summary": {"provider_ready_scopes": 6, "hard_blockers": [], "parallel_execution_allowed": False},
    }
    signal_scan = {
        "summary": {
            "actionable_scopes": 0,
            "dominant_nontrade_reason": "regime_block",
        }
    }
    audit = {
        "summary": {
            "stale_artifact_scopes": 0,
            "cp_meta_missing_scopes": 0,
            "missing_artifact_scopes": 0,
            "gate_fail_closed_scopes": 0,
        }
    }
    payload = classify_closure(provider, signal_scan, audit)
    assert payload["closure_state"] == "healthy_waiting_signal"
    assert payload["recommended_action"] == "wait_next_candle_and_rescan"


def test_classify_closure_secondary_cp_meta_debt_becomes_healthy_waiting() -> None:
    provider = {
        "ok": True,
        "severity": "warn",
        "stability_state": "degraded",
        "summary": {"provider_ready_scopes": 6, "hard_blockers": [], "parallel_execution_allowed": False},
    }
    signal_scan = {
        "summary": {
            "actionable_scopes": 0,
            "watch_scopes": 1,
            "hold_scopes": 2,
            "cp_meta_missing_scopes": 2,
            "dominant_nontrade_reason": "regime_block",
            "best_watch_scope_tag": "EURUSD-OTC_300s",
        }
    }
    audit = {
        "summary": {
            "missing_artifact_scopes": 0,
            "stale_artifact_scopes": 0,
            "cp_meta_missing_scopes": 2,
            "gate_fail_closed_scopes": 2,
            "watch_scopes": 1,
            "hold_scopes": 2,
            "best_watch_scope_tag": "EURUSD-OTC_300s",
            "dominant_nontrade_reason": "regime_block",
        },
        "scope_results": [
            {
                "scope": {"scope_tag": "EURUSD-OTC_300s"},
                "exists": True,
                "stale": False,
                "window_state": "watch",
                "cp_meta_missing": False,
                "candidate_reason": "regime_block",
                "dominant_reason": "regime_block",
                "candidate_blockers": ["cp_reject", "below_ev_threshold", "not_in_topk_today"],
                "blocker_flags": {"cp_reject": True, "below_ev_threshold": True, "not_in_topk_today": True, "gate_fail_closed": False},
            },
            {
                "scope": {"scope_tag": "AUDUSD-OTC_300s"},
                "exists": True,
                "stale": False,
                "window_state": "hold",
                "cp_meta_missing": True,
                "candidate_reason": "regime_block",
                "dominant_reason": "cp_meta_missing",
                "regime_block": True,
                "candidate_blockers": ["below_ev_threshold", "not_in_topk_today"],
                "blocker_flags": {"gate_fail_closed": True, "below_ev_threshold": True, "not_in_topk_today": True},
            },
            {
                "scope": {"scope_tag": "EURGBP-OTC_300s"},
                "exists": True,
                "stale": False,
                "window_state": "hold",
                "cp_meta_missing": True,
                "candidate_reason": "gate_fail_closed",
                "dominant_reason": "cp_meta_missing",
                "candidate_blockers": ["below_ev_threshold", "not_in_topk_today"],
                "blocker_flags": {"gate_fail_closed": False, "below_ev_threshold": True, "not_in_topk_today": True},
            },
        ],
    }
    payload = classify_closure(provider, signal_scan, audit)
    assert payload["closure_state"] == "healthy_waiting_signal"
    assert payload["recommended_action"] == "wait_regime_rescan_track_cp_meta_debt"
    assert payload["blocking_cp_meta_missing_scopes"] == 0
    assert payload["repair_scope_tags"] == []
    assert payload["closure_debts"][0]["name"] == "secondary_cp_meta_debt"


def test_choose_repair_scope_tags_skips_secondary_cp_meta_debt() -> None:
    payload = {
        "summary": {
            "cp_meta_missing_scopes": 2,
            "best_watch_scope_tag": "EURUSD-OTC_300s",
            "dominant_nontrade_reason": "regime_block",
        },
        "scope_results": [
            {
                "scope": {"scope_tag": "EURUSD-OTC_300s"},
                "exists": True,
                "stale": False,
                "window_state": "watch",
                "cp_meta_missing": False,
                "candidate_reason": "regime_block",
                "dominant_reason": "regime_block",
                "candidate_blockers": ["cp_reject", "below_ev_threshold", "not_in_topk_today"],
                "blocker_flags": {"cp_reject": True, "below_ev_threshold": True, "not_in_topk_today": True, "gate_fail_closed": False},
            },
            {
                "scope": {"scope_tag": "AUDUSD-OTC_300s"},
                "exists": True,
                "stale": False,
                "window_state": "hold",
                "cp_meta_missing": True,
                "candidate_reason": "regime_block",
                "dominant_reason": "cp_meta_missing",
                "regime_block": True,
                "candidate_blockers": ["gate_fail_closed", "below_ev_threshold", "not_in_topk_today"],
                "blocker_flags": {"gate_fail_closed": True, "below_ev_threshold": True, "not_in_topk_today": True},
            },
            {
                "scope": {"scope_tag": "EURGBP-OTC_300s"},
                "exists": True,
                "stale": False,
                "window_state": "hold",
                "cp_meta_missing": True,
                "candidate_reason": "gate_fail_closed",
                "dominant_reason": "cp_meta_missing",
                "candidate_blockers": ["below_ev_threshold", "not_in_topk_today"],
                "blocker_flags": {"gate_fail_closed": False, "below_ev_threshold": True, "not_in_topk_today": True},
            },
        ],
    }
    assert choose_repair_scope_tags(payload) == []


def test_classify_closure_healthy_waiting_when_audit_has_watch_without_scan_reason() -> None:
    provider = {
        'ok': True,
        'severity': 'warn',
        'stability_state': 'degraded',
        'summary': {'provider_ready_scopes': 6, 'hard_blockers': [], 'parallel_execution_allowed': False},
    }
    signal_scan = {
        'summary': {
            'actionable_scopes': 0,
            'candidate_error_scopes': 3,
            'dominant_nontrade_reason': None,
        }
    }
    audit = {
        'summary': {
            'missing_artifact_scopes': 0,
            'stale_artifact_scopes': 0,
            'cp_meta_missing_scopes': 0,
            'gate_fail_closed_scopes': 0,
            'watch_scopes': 2,
            'hold_scopes': 4,
        }
    }
    payload = classify_closure(provider, signal_scan, audit)
    assert payload['closure_state'] == 'healthy_waiting_signal'
    assert payload['recommended_action'] == 'wait_next_candle_and_rescan'
