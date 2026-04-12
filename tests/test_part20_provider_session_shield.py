from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

TOOLS = Path(__file__).resolve().parents[1] / 'scripts' / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import capture_provider_stability_bundle as bundle_module  # type: ignore

from natbin.ops.provider_stability import build_provider_stability_payload
from natbin.utils.provider_issue_taxonomy import aggregate_provider_issue_texts, classify_provider_issue


def test_provider_issue_taxonomy_classifies_core_buckets() -> None:
    assert classify_provider_issue('JSONDecodeError: Expecting value')['category'] == 'session_parse'
    assert classify_provider_issue('Connection is already closed.')['category'] == 'websocket_lifecycle'
    assert classify_provider_issue("KeyError: 'underlying'")['category'] == 'upstream_digital_metadata'
    assert classify_provider_issue('market_context stale')['category'] == 'local_artifact'
    payload = aggregate_provider_issue_texts([
        'JSONDecodeError: Expecting value',
        'Connection is already closed.',
        "KeyError: 'underlying'",
        'regime_block',
    ])
    cats = {item['category']: item['count'] for item in payload['categories']}
    assert cats['session_parse'] == 1
    assert cats['websocket_lifecycle'] == 1
    assert cats['upstream_digital_metadata'] == 1
    assert cats['strategy_no_trade'] == 1


def test_provider_stability_report_degraded_with_transient_noise(monkeypatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / 'config.yaml'
    cfg_path.write_text('version: "2.0"\n', encoding='utf-8')
    scope = SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s')
    monkeypatch.setattr(
        'natbin.ops.provider_stability.load_selected_scopes',
        lambda **kwargs: (tmp_path, cfg_path, SimpleNamespace(), [scope]),
    )
    repo_artifacts = tmp_path / 'runs' / 'control' / '_repo'
    repo_artifacts.mkdir(parents=True, exist_ok=True)
    (repo_artifacts / 'provider_probe.json').write_text(json.dumps({
        'summary': {'scope_count': 1, 'provider_ready_scopes': 1},
        'severity': 'ok',
        'shared_provider_session': {'ok': True, 'latency_ms': 2000.0},
        'transport_hint': {'configured': True, 'scheme': 'socks5h'},
        'checks': [{'name': 'provider_session', 'status': 'ok', 'message': 'Login no provider concluído com sucesso'}],
        'scope_results': [
            {
                'scope': {'scope_tag': 'EURUSD-OTC_300s', 'asset': 'EURUSD-OTC', 'interval_sec': 300},
                'checks': [{'name': 'remote_candles', 'status': 'ok', 'message': 'Provider retornou amostra de candles'}],
                'remote_candles': {'attempted': True, 'ok': True},
                'remote_market_context': {'attempted': True, 'ok': True},
                'local_market_context': {'fresh': True},
            }
        ],
    }), encoding='utf-8')
    (repo_artifacts / 'portfolio_canary_warmup.json').write_text(json.dumps({
        'summary': {'effective_ready_scopes': 1},
        'scope_results': [{'stderr_tail': 'ERROR:root:**warning** get_all_init late 30 sec\nERROR:iqoptionapi.ws.client:Connection is already closed.'}],
    }), encoding='utf-8')
    (repo_artifacts / 'evidence_window_scan.json').write_text(json.dumps({
        'severity': 'warn',
        'summary': {'scope_count': 1, 'provider_ready_scopes': 1},
        'best_scope': {'scope': {'scope_tag': 'EURUSD-OTC_300s'}, 'window_state': 'watch', 'recommended_action': 'wait_signal_and_rescan'},
        'scope_results': [],
    }), encoding='utf-8')
    (repo_artifacts / 'portfolio_canary_signal_scan.json').write_text(json.dumps({
        'severity': 'warn',
        'summary': {'actionable_scopes': 0, 'healthy_waiting_signal': True},
        'results': [{'reason': 'regime_block', 'action': 'HOLD'}],
    }), encoding='utf-8')
    log_path = tmp_path / 'runs' / 'logs'
    log_path.mkdir(parents=True, exist_ok=True)
    (log_path / 'provider_issues.jsonl').write_text(
        json.dumps({'reason': 'JSONDecodeError: Expecting value', 'category': 'session_parse'}) + '\n',
        encoding='utf-8',
    )

    payload = build_provider_stability_payload(repo_root=tmp_path, config_path=cfg_path, all_scopes=True, write_artifact=False)
    assert payload['stability_state'] == 'degraded'
    assert payload['severity'] == 'warn'
    cats = {item['category']: item['count'] for item in payload['categories']}
    assert cats['session_parse'] >= 1
    assert cats['websocket_lifecycle'] >= 1
    assert payload['summary']['parallel_execution_allowed'] is False


def test_iq_client_instance_patch_protects_unpatched_cached_class(monkeypatch) -> None:
    import natbin.adapters.iq_client as iq_module

    monkeypatch.setattr(iq_module, 'env_bool', lambda *args, **kwargs: False)
    monkeypatch.setattr(iq_module, '_IQ_OPTION_IMPORT_ERROR', None)

    class FakeIQOption:
        OPEN_TIME = {'digital': __import__('collections').defaultdict(dict)}

        def __init__(self, *args, **kwargs):
            self.OPEN_TIME = {'digital': __import__('collections').defaultdict(dict)}

        def get_digital_underlying_list_data(self):
            return {}

        def _IQ_Option__get_digital_open(self):
            raise AssertionError('original private method should be shadowed on instance')

    monkeypatch.setattr(iq_module, '_IQ_OPTION_CLASS', FakeIQOption)
    cfg = iq_module.IQConfig(email='a', password='b', balance_mode='PRACTICE', transport=None)
    client = iq_module.IQClient(cfg)
    payload = client.iq.get_digital_underlying_list_data()
    assert payload['underlying'] == []
    client.iq._IQ_Option__get_digital_open()


def test_capture_provider_stability_bundle_summary(tmp_path: Path) -> None:
    bundle = tmp_path / 'bundle'
    for name in ('provider_probe_all_scopes', 'portfolio_canary_warmup', 'evidence_window_scan', 'portfolio_canary_signal_scan', 'provider_stability_report'):
        (bundle / name).mkdir(parents=True, exist_ok=True)
    (bundle / 'provider_probe_all_scopes' / 'last_json.json').write_text(json.dumps({'summary': {'provider_ready_scopes': 6}}), encoding='utf-8')
    (bundle / 'portfolio_canary_warmup' / 'last_json.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
    (bundle / 'evidence_window_scan' / 'last_json.json').write_text(json.dumps({'best_scope': {'scope': {'scope_tag': 'AUDUSD-OTC_300s'}}}), encoding='utf-8')
    (bundle / 'portfolio_canary_signal_scan' / 'last_json.json').write_text(json.dumps({'summary': {'actionable_scopes': 0, 'healthy_waiting_signal': True}}), encoding='utf-8')
    (bundle / 'provider_stability_report' / 'last_json.json').write_text(json.dumps({'stability_state': 'degraded', 'severity': 'warn', 'summary': {'transient_noise_categories': ['session_parse'], 'recorded_issue_events': 4}}), encoding='utf-8')
    results = [
        {'name': 'provider_probe_all_scopes', 'parsed_summary': {'last_json_kind': 'provider_probe'}},
        {'name': 'portfolio_canary_warmup', 'parsed_summary': {'last_json_kind': 'portfolio_canary_warmup'}},
        {'name': 'evidence_window_scan', 'parsed_summary': {'last_json_kind': 'evidence_window_scan'}},
        {'name': 'portfolio_canary_signal_scan', 'parsed_summary': {'last_json_kind': 'portfolio_canary_signal_scan'}},
        {'name': 'provider_stability_report', 'parsed_summary': {'last_json_kind': 'provider_stability_report'}},
    ]
    summary = bundle_module._bundle_summary(bundle, results)
    assert summary['provider_session_shield']['stability_state'] == 'degraded'
    assert summary['provider_session_shield']['signal_healthy_waiting'] is True
