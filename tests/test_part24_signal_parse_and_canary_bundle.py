from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "scripts" / "tools"
for candidate in (SRC, TOOLS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import capture_portfolio_canary_bundle as canary_bundle  # type: ignore
import portfolio_canary_signal_proof as signal_proof  # type: ignore
from natbin.ops import provider_stability as provider_stability  # type: ignore


def test_signal_run_cmd_prefers_top_level_asset_candidate(monkeypatch, tmp_path: Path) -> None:
    stdout = json.dumps({
        "phase": "asset_candidate",
        "ok": True,
        "candidate": {
            "action": "HOLD",
            "reason": "regime_block",
            "raw": {
                "gate_mode": "cp_fail_closed_missing_cp_meta",
                "gate_fail_detail": "cp_fail_closed_missing_cp_meta",
            },
        },
        "materialized_portfolio": {
            "allocation": {
                "profile_key": "demo",
                "runtime_profile": "practice_portfolio_canary",
            }
        },
    }, ensure_ascii=False, indent=2)

    class _Proc:
        def __init__(self) -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    monkeypatch.setattr(signal_proof.subprocess, "run", lambda *args, **kwargs: _Proc())
    result = signal_proof._run_cmd(["python", "-m", "x"], cwd=tmp_path, env={}, timeout_sec=10)
    assert result["returncode"] == 0
    payload = result["last_json"]
    assert payload["phase"] == "asset_candidate"
    assert payload["candidate"]["raw"]["gate_fail_detail"] == "cp_fail_closed_missing_cp_meta"


def test_bundle_summary_prefers_signal_scan_best_scope(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    for name in [
        "evidence_window_scan",
        "portfolio_canary_signal_scan",
        "asset_candidate_best",
        "production_gate_all_scopes",
        "portfolio_status",
        "portfolio_canary_warmup",
        "provider_session_governor",
    ]:
        (bundle / name).mkdir(parents=True)

    (bundle / "evidence_window_scan" / "last_json.json").write_text(json.dumps({
        "kind": "evidence_window_scan",
        "ok": True,
        "severity": "warn",
        "recommended_scope": {
            "scope": {"asset": "EURUSD-OTC", "interval_sec": 300, "scope_tag": "EURUSD-OTC_300s"},
            "score": 40.0,
            "window_state": "watch",
        },
        "summary": {"provider_ready_scopes": 6, "scope_count": 6},
    }), encoding="utf-8")
    (bundle / "portfolio_canary_signal_scan" / "last_json.json").write_text(json.dumps({
        "kind": "portfolio_canary_signal_proof",
        "ok": True,
        "severity": "warn",
        "best_watch_scope": {
            "scope": {"asset": "EURGBP-OTC", "interval_sec": 300, "scope_tag": "EURGBP-OTC_300s"},
            "window_state": "hold",
        },
        "summary": {
            "recommended_action": "audit_cp_meta",
            "actionable_scopes": 0,
            "cp_meta_missing_scopes": 2,
        },
    }), encoding="utf-8")
    (bundle / "asset_candidate_best" / "last_json.json").write_text(json.dumps({
        "phase": "asset_candidate",
        "ok": True,
        "candidate": {"action": "HOLD"},
    }), encoding="utf-8")
    (bundle / "production_gate_all_scopes" / "last_json.json").write_text(json.dumps({
        "ready_for_all_scopes": False,
    }), encoding="utf-8")
    (bundle / "portfolio_status" / "last_json.json").write_text(json.dumps({
        "multi_asset": {"asset_count": 6},
    }), encoding="utf-8")
    (bundle / "portfolio_canary_warmup" / "last_json.json").write_text(json.dumps({
        "ok": True,
    }), encoding="utf-8")
    (bundle / "provider_session_governor" / "last_json.json").write_text(json.dumps({
        "summary": {"governor_mode": "serial_guarded", "sleep_between_scopes_ms": 1500},
    }), encoding="utf-8")

    results = [
        {"name": "evidence_window_scan", "parsed_summary": {"last_json_kind": "evidence_window_scan"}},
        {"name": "portfolio_canary_signal_scan", "parsed_summary": {"last_json_kind": "portfolio_canary_signal_proof"}},
        {"name": "asset_candidate_best", "parsed_summary": {"last_json_kind": "asset_candidate"}},
        {"name": "production_gate_all_scopes", "parsed_summary": {"last_json_kind": "production_gate"}},
        {"name": "portfolio_status", "parsed_summary": {"last_json_kind": "portfolio_status"}},
        {"name": "portfolio_canary_warmup", "parsed_summary": {"last_json_kind": "portfolio_canary_warmup"}},
        {"name": "provider_session_governor", "parsed_summary": {"last_json_kind": "provider_session_governor"}},
    ]
    summary = canary_bundle._bundle_summary(bundle, results)
    assert summary["portfolio_canary"]["recommended_scope"]["scope"]["scope_tag"] == "EURGBP-OTC_300s"
    assert summary["portfolio_canary"]["signal_scan_recommended_action"] == "audit_cp_meta"
    assert summary["portfolio_canary"]["signal_scan_cp_meta_missing_scopes"] == 2


def test_provider_stability_ignores_ok_bucket_messages() -> None:
    provider = {
        "scope_results": [
            {
                "checks": [],
                "remote_candles": {"status": "ok", "message": "candles_ok", "reason": "no_problem"},
                "remote_market_context": {"status": "ok", "message": "context_ok", "reason": "no_problem"},
            }
        ]
    }
    assert provider_stability._provider_texts(provider) == []


def test_provider_stability_scan_texts_ignores_watch_state() -> None:
    scan = {
        "best_scope": {"window_state": "watch", "recommended_action": "wait_signal_and_rescan"},
        "scope_results": [],
    }
    assert provider_stability._scan_texts(scan) == []
