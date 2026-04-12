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
import portfolio_canary_signal_proof as signal_proof  # type: ignore
from natbin.utils.provider_issue_taxonomy import aggregate_provider_issue_texts


class _Scope:
    def __init__(self, asset: str, interval_sec: int = 300) -> None:
        self.asset = asset
        self.interval_sec = interval_sec
        self.scope_tag = f"{asset}_{interval_sec}s"


def _payload(*, action: str = 'HOLD', reason: str = 'regime_block', blockers: str = '', gate_fail_detail: str = '', gate_mode: str = '', conf: float = 0.55, score: float = 0.0, ev: float = -1.0) -> dict[str, object]:
    return {
        'phase': 'asset_candidate',
        'ok': True,
        'candidate': {
            'action': action,
            'reason': reason,
            'blockers': blockers,
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
            },
        },
    }


def test_issue_taxonomy_classifies_cp_meta_and_non_trade_gate_noise() -> None:
    payload = aggregate_provider_issue_texts([
        'cp_fail_closed_missing_cp_meta',
        'cp_reject;below_ev_threshold;not_in_topk_today',
        'cp_meta_iso',
        'wait_regime_rescan',
    ])
    cats = {row['category']: row for row in payload['categories']}
    assert payload['total_events'] == 2
    assert cats['intelligence_cp_meta']['severity_hint'] == 'warn'
    assert cats['strategy_no_trade']['severity_hint'] == 'ok'
    assert 'unknown' not in cats


def test_signal_proof_prioritizes_best_watch_and_backfill_when_secondary_cp_meta(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    cfg = repo / 'config.yaml'
    cfg.write_text('x: 1\n', encoding='utf-8')
    scopes = [_Scope('USDJPY-OTC'), _Scope('EURUSD-OTC'), _Scope('EURGBP-OTC')]
    monkeypatch.setattr(signal_proof, 'load_selected_scopes', lambda **kwargs: (repo, cfg, object(), scopes))
    monkeypatch.setattr(signal_proof, '_read_best_first', lambda repo_root, ordered_scopes: ordered_scopes)

    payloads = {
        'USDJPY-OTC': _payload(blockers='cp_reject;below_ev_threshold;not_in_topk_today'),
        'EURUSD-OTC': _payload(blockers='cp_reject;below_ev_threshold;not_in_topk_today', conf=0.54),
        'EURGBP-OTC': _payload(blockers='gate_fail_closed;below_ev_threshold;not_in_topk_today', gate_fail_detail='cp_fail_closed_missing_cp_meta', gate_mode='cp_fail_closed_missing_cp_meta', conf=0.51),
    }

    def fake_run(cmd: list[str], *, cwd: Path, env: dict[str, str], timeout_sec: int) -> dict[str, object]:
        asset = cmd[cmd.index('--asset') + 1]
        payload = payloads[asset]
        return {'returncode': 0, 'timed_out': False, 'duration_sec': 1.0, 'stdout': json.dumps(payload), 'stderr': '', 'last_json': payload}

    monkeypatch.setattr(signal_proof, '_candidate_cmd', lambda repo_root, cfg_path, asset, interval_sec: ['python', '--asset', asset])
    monkeypatch.setattr(signal_proof, '_run_cmd', fake_run)
    monkeypatch.setattr(signal_proof, 'build_provider_session_governor_payload', lambda **kwargs: {'governor': {'mode': 'serial_guarded', 'sleep_between_candidate_scopes_ms': 0, 'max_candidate_scopes_per_run': 3, 'scope_order': 'best_first_round_robin'}})

    out = signal_proof.build_signal_proof_payload(repo_root=repo, config_path=cfg, all_scopes=True)
    assert out['summary']['cp_meta_missing_scopes'] == 1
    assert out['summary']['regime_block_scopes'] == 2
    assert out['summary']['cp_reject_scopes'] == 2
    assert out['summary']['threshold_block_scopes'] == 3
    assert out['summary']['topk_suppressed_scopes'] == 3
    assert out['summary']['dominant_nontrade_reason'] == 'regime_block'
    assert out['summary']['best_watch_scope_tag'] == 'USDJPY-OTC_300s'
    assert out['summary']['best_hold_scope_tag'] == 'EURGBP-OTC_300s'
    assert out['summary']['recommended_action'] == 'wait_regime_rescan_backfill_cp_meta'
    assert out['summary']['healthy_waiting_signal'] is False


def test_signal_proof_all_regime_block_is_healthy_waiting(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    cfg = repo / 'config.yaml'
    cfg.write_text('x: 1\n', encoding='utf-8')
    scope = _Scope('EURUSD-OTC')
    monkeypatch.setattr(signal_proof, 'load_selected_scopes', lambda **kwargs: (repo, cfg, object(), [scope]))
    monkeypatch.setattr(signal_proof, '_read_best_first', lambda repo_root, ordered_scopes: ordered_scopes)
    payload = _payload(blockers='cp_reject;below_ev_threshold;not_in_topk_today')
    monkeypatch.setattr(signal_proof, '_candidate_cmd', lambda repo_root, cfg_path, asset, interval_sec: ['python', '--asset', asset])
    monkeypatch.setattr(signal_proof, '_run_cmd', lambda *args, **kwargs: {'returncode': 0, 'timed_out': False, 'duration_sec': 1.0, 'stdout': json.dumps(payload), 'stderr': '', 'last_json': payload})
    monkeypatch.setattr(signal_proof, 'build_provider_session_governor_payload', lambda **kwargs: {'governor': {'mode': 'serial_guarded', 'sleep_between_candidate_scopes_ms': 0, 'max_candidate_scopes_per_run': 1, 'scope_order': 'best_first_round_robin'}})

    out = signal_proof.build_signal_proof_payload(repo_root=repo, config_path=cfg, all_scopes=True)
    assert out['summary']['healthy_waiting_signal'] is True
    assert out['summary']['recommended_action'] == 'wait_regime_rescan'
    assert out['summary']['dominant_nontrade_reason'] == 'regime_block'


def test_bundle_summary_surfaces_signal_reason_fields(tmp_path: Path) -> None:
    bundle = tmp_path / 'bundle'
    for name in [
        'evidence_window_scan',
        'portfolio_canary_signal_scan',
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
        'best_watch_scope': {'scope': {'asset': 'USDJPY-OTC', 'interval_sec': 300, 'scope_tag': 'USDJPY-OTC_300s'}, 'window_state': 'watch'},
        'best_hold_scope': {'scope': {'asset': 'EURGBP-OTC', 'interval_sec': 300, 'scope_tag': 'EURGBP-OTC_300s'}, 'window_state': 'hold'},
        'summary': {
            'recommended_action': 'wait_regime_rescan_backfill_cp_meta',
            'actionable_scopes': 0,
            'watch_scopes': 2,
            'hold_scopes': 1,
            'cp_meta_missing_scopes': 1,
            'regime_block_scopes': 2,
            'threshold_block_scopes': 3,
            'topk_suppressed_scopes': 3,
            'healthy_waiting_signal': False,
            'dominant_nontrade_reason': 'regime_block',
            'best_watch_scope_tag': 'USDJPY-OTC_300s',
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
        {'name': 'asset_candidate_best', 'parsed_summary': {'last_json_kind': 'asset_candidate'}},
        {'name': 'production_gate_all_scopes', 'parsed_summary': {'last_json_kind': 'production_gate'}},
        {'name': 'portfolio_status', 'parsed_summary': {'last_json_kind': 'portfolio_status'}},
        {'name': 'portfolio_canary_warmup', 'parsed_summary': {'last_json_kind': 'portfolio_canary_warmup'}},
        {'name': 'provider_session_governor', 'parsed_summary': {'last_json_kind': 'provider_session_governor'}},
    ]
    summary = canary_bundle._bundle_summary(bundle, results)
    pc = summary['portfolio_canary']
    assert pc['recommended_scope']['scope']['scope_tag'] == 'USDJPY-OTC_300s'
    assert pc['signal_scan_recommended_action'] == 'wait_regime_rescan_backfill_cp_meta'
    assert pc['signal_scan_dominant_nontrade_reason'] == 'regime_block'
    assert pc['signal_scan_best_hold_scope_tag'] == 'EURGBP-OTC_300s'
    assert pc['signal_scan_threshold_block_scopes'] == 3
