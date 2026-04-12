from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
TOOLS = ROOT / 'scripts' / 'tools'
for candidate in (SRC, TOOLS):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import capture_portfolio_canary_bundle as canary_bundle  # type: ignore
from natbin.ops.signal_artifact_audit import build_signal_artifact_audit_payload
from natbin.runtime.scope import decision_latest_path
from natbin.utils.provider_issue_taxonomy import aggregate_provider_issue_texts


class _Scope:
    def __init__(self, asset: str, interval_sec: int = 300) -> None:
        self.asset = asset
        self.interval_sec = interval_sec
        self.scope_tag = f"{asset}_{interval_sec}s"


def _decision_payload(*, action: str = 'HOLD', reason: str = 'regime_block', blockers: str = '', gate_fail_detail: str = '', gate_mode: str = 'cp_meta_iso', observed_at_utc: str = '2026-04-07T16:00:00+00:00', conf: float = 0.55, score: float = 0.0, ev: float = -1.0) -> dict[str, object]:
    return {
        'kind': 'decision',
        'observed_at_utc': observed_at_utc,
        'action': action,
        'reason': reason,
        'blockers': blockers,
        'gate_fail_detail': gate_fail_detail,
        'gate_mode': gate_mode,
        'conf': conf,
        'score': score,
        'ev': ev,
        'raw': {
            'action': action,
            'reason': reason,
            'blockers': blockers,
            'gate_fail_detail': gate_fail_detail,
            'gate_mode': gate_mode,
            'conf': conf,
            'score': score,
            'ev': ev,
            'observed_at_utc': observed_at_utc,
        },
    }


def test_provider_issue_taxonomy_ignores_canary_reason_trace_markers() -> None:
    payload = aggregate_provider_issue_texts([
        'provider_ready',
        'ready_for_cycle',
        'ready_for_practice',
        'market_context_fresh',
        'market_open',
        'quota_available',
        'window_state=watch',
        'recommended_action=wait_signal_and_rescan',
    ])
    assert payload['total_events'] == 0
    assert payload['categories'] == []


def test_signal_artifact_audit_summarizes_cp_meta_and_regime(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    cfg = repo / 'config.yaml'
    cfg.write_text('x: 1\n', encoding='utf-8')
    scopes = [_Scope('EURUSD-OTC'), _Scope('EURGBP-OTC'), _Scope('USDCAD-OTC')]
    monkeypatch.setattr('natbin.ops.signal_artifact_audit.load_selected_scopes', lambda **kwargs: (repo, cfg, object(), scopes))

    payloads = {
        'EURUSD-OTC': _decision_payload(blockers='cp_reject;below_ev_threshold;not_in_topk_today'),
        'EURGBP-OTC': _decision_payload(blockers='gate_fail_closed;below_ev_threshold;not_in_topk_today', gate_fail_detail='cp_fail_closed_missing_cp_meta'),
        'USDCAD-OTC': _decision_payload(blockers='gate_fail_closed;below_ev_threshold;not_in_topk_today', gate_fail_detail='cp_fail_closed_missing_cp_meta'),
    }
    for scope in scopes:
        path = decision_latest_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=repo / 'runs')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payloads[scope.asset]) + '\n', encoding='utf-8')

    out = build_signal_artifact_audit_payload(repo_root=repo, config_path=cfg, all_scopes=True, decision_max_age_sec=86400)
    summary = out['summary']
    assert out['ok'] is True
    assert out['severity'] == 'ok'
    assert summary['full_scope_count'] == 3
    assert summary['watch_scopes'] == 1
    assert summary['hold_scopes'] == 2
    assert summary['cp_meta_missing_scopes'] == 2
    assert summary['regime_block_scopes'] == 1
    assert summary['threshold_block_scopes'] == 3
    assert summary['topk_suppressed_scopes'] == 3
    assert summary['best_watch_scope_tag'] == 'EURUSD-OTC_300s'
    assert summary['best_hold_scope_tag'] == 'EURGBP-OTC_300s'
    assert summary['recommended_action'] == 'wait_regime_rescan_backfill_cp_meta'


def test_bundle_summary_surfaces_signal_artifact_audit_fields(tmp_path: Path) -> None:
    bundle = tmp_path / 'bundle'
    for name in [
        'evidence_window_scan',
        'portfolio_canary_signal_scan',
        'signal_artifact_audit',
        'asset_candidate_best',
        'production_gate_all_scopes',
        'portfolio_status',
        'portfolio_canary_warmup',
        'provider_session_governor',
    ]:
        (bundle / name).mkdir(parents=True)

    (bundle / 'evidence_window_scan' / 'last_json.json').write_text(json.dumps({'kind': 'evidence_window_scan', 'ok': True, 'severity': 'warn', 'summary': {'provider_ready_scopes': 6, 'scope_count': 6}}), encoding='utf-8')
    (bundle / 'portfolio_canary_signal_scan' / 'last_json.json').write_text(json.dumps({
        'kind': 'portfolio_canary_signal_proof',
        'ok': True,
        'severity': 'warn',
        'summary': {'recommended_action': 'wait_regime_rescan_backfill_cp_meta', 'watch_scopes': 1},
    }), encoding='utf-8')
    (bundle / 'signal_artifact_audit' / 'last_json.json').write_text(json.dumps({
        'kind': 'signal_artifact_audit',
        'ok': True,
        'severity': 'ok',
        'best_watch_scope': {'scope': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': 'EURUSD-OTC_300s'}, 'window_state': 'watch'},
        'summary': {
            'recommended_action': 'wait_regime_rescan_backfill_cp_meta',
            'full_scope_count': 6,
            'watch_scopes': 3,
            'hold_scopes': 3,
            'cp_meta_missing_scopes': 2,
            'regime_block_scopes': 3,
            'threshold_block_scopes': 6,
            'topk_suppressed_scopes': 6,
            'dominant_nontrade_reason': 'regime_block',
            'best_watch_scope_tag': 'EURUSD-OTC_300s',
            'best_hold_scope_tag': 'EURGBP-OTC_300s',
        },
    }), encoding='utf-8')
    (bundle / 'asset_candidate_best' / 'last_json.json').write_text(json.dumps({'phase': 'asset_candidate', 'ok': True, 'candidate': {'action': 'HOLD'}}), encoding='utf-8')
    (bundle / 'production_gate_all_scopes' / 'last_json.json').write_text(json.dumps({'ready_for_all_scopes': True}), encoding='utf-8')
    (bundle / 'portfolio_status' / 'last_json.json').write_text(json.dumps({'multi_asset': {'asset_count': 6}}), encoding='utf-8')
    (bundle / 'portfolio_canary_warmup' / 'last_json.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
    (bundle / 'provider_session_governor' / 'last_json.json').write_text(json.dumps({'summary': {'governor_mode': 'serial_guarded', 'sleep_between_scopes_ms': 1500}}), encoding='utf-8')

    results = [
        {'name': 'evidence_window_scan', 'parsed_summary': {'last_json_kind': 'evidence_window_scan'}},
        {'name': 'portfolio_canary_signal_scan', 'parsed_summary': {'last_json_kind': 'portfolio_canary_signal_proof'}},
        {'name': 'signal_artifact_audit', 'parsed_summary': {'last_json_kind': 'signal_artifact_audit'}},
        {'name': 'asset_candidate_best', 'parsed_summary': {'last_json_kind': 'asset_candidate'}},
        {'name': 'production_gate_all_scopes', 'parsed_summary': {'last_json_kind': 'production_gate'}},
        {'name': 'portfolio_status', 'parsed_summary': {'last_json_kind': 'portfolio_status'}},
        {'name': 'portfolio_canary_warmup', 'parsed_summary': {'last_json_kind': 'portfolio_canary_warmup'}},
        {'name': 'provider_session_governor', 'parsed_summary': {'last_json_kind': 'provider_session_governor'}},
    ]
    summary = canary_bundle._bundle_summary(bundle, results)
    pc = summary['portfolio_canary']
    assert summary['signal_artifact_audit']['last_json_kind'] == 'signal_artifact_audit'
    assert pc['recommended_scope']['scope']['scope_tag'] == 'EURUSD-OTC_300s'
    assert pc['signal_audit_full_scope_count'] == 6
    assert pc['signal_audit_cp_meta_missing_scopes'] == 2
    assert pc['signal_audit_regime_block_scopes'] == 3
    assert pc['signal_audit_dominant_nontrade_reason'] == 'regime_block'
    assert pc['signal_audit_best_hold_scope_tag'] == 'EURGBP-OTC_300s'
