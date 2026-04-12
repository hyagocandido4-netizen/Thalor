from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import natbin.ops.evidence_window_scan as scan_module

TOOLS = Path(__file__).resolve().parents[1] / 'scripts' / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import capture_portfolio_canary_bundle as bundle_module  # type: ignore
import portfolio_canary_warmup as warmup_module  # type: ignore


def test_evidence_window_scan_hold_only_is_warn_not_error(monkeypatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / 'config.yaml'
    cfg_path.write_text('version: "2.0"\n', encoding='utf-8')
    cfg = SimpleNamespace(
        multi_asset=SimpleNamespace(enabled=True, max_parallel_assets=1, portfolio_topk_total=1, portfolio_hard_max_positions=1),
        execution=SimpleNamespace(account_mode='PRACTICE', stake=SimpleNamespace(amount=2.0), limits=SimpleNamespace(max_pending_unknown=1, max_open_positions=1)),
        broker=SimpleNamespace(balance_mode='PRACTICE'),
    )
    scopes = [SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s')]
    monkeypatch.setattr(scan_module, 'load_selected_scopes', lambda **kwargs: (tmp_path, cfg_path, cfg, scopes))
    monkeypatch.setattr('natbin.control.commands.portfolio_status_payload', lambda **kwargs: {
        'asset_board': [{'scope_tag': 'EURUSD-OTC_300s', 'budget_left': 1, 'pending_unknown': 0, 'open_positions': 0}],
        'intelligence': {'items': [{'scope_tag': 'EURUSD-OTC_300s', 'feedback_blocked': False, 'portfolio_score': 0.1, 'intelligence_score': 0.2}]},
    })
    monkeypatch.setattr(scan_module, 'build_provider_probe_payload', lambda **kwargs: {
        'severity': 'ok',
        'summary': {'provider_ready_scopes': 1},
        'scope_results': [{
            'scope': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': 'EURUSD-OTC_300s'},
            'severity': 'ok',
            'ok': True,
            'checks': [],
            'actions': [],
            'shared_provider_session': {'attempted': True, 'ok': True},
            'local_market_context': {'fresh': False, 'market_open': True},
            'remote_candles': {'attempted': True, 'ok': True},
            'remote_market_context': {'attempted': True, 'ok': True, 'market_open': True},
        }],
        'shared_provider_session': {'attempted': True, 'ok': True},
        'transport_hint': {'configured': True},
    })
    monkeypatch.setattr(scan_module, 'build_production_doctor_payload', lambda **kwargs: {
        'severity': 'error',
        'ready_for_cycle': False,
        'ready_for_practice': False,
        'blockers': ['market_context'],
        'warnings': ['control_freshness'],
        'actions': ['refresh market context'],
    })

    payload = scan_module.build_evidence_window_scan_payload(repo_root=tmp_path, config_path=cfg_path, all_scopes=True, active_provider_probe=True, write_artifact=False)
    assert payload['severity'] == 'warn'
    assert payload['ok'] is True
    assert payload['provider_ready_scopes'] == 1
    assert payload['recommended_scope']['scope']['scope_tag'] == 'EURUSD-OTC_300s'
    assert 'market_context' in payload['actionable_blockers']


def test_bundle_summary_uses_scan_fallbacks_and_warmup(tmp_path: Path) -> None:
    bundle = tmp_path / 'bundle'
    for name in ('portfolio_canary_warmup', 'evidence_window_scan', 'production_gate_all_scopes', 'portfolio_status', 'asset_candidate_best'):
        (bundle / name).mkdir(parents=True, exist_ok=True)
    (bundle / 'portfolio_canary_warmup' / 'last_json.json').write_text(json.dumps({'ok': True}), encoding='utf-8')
    (bundle / 'evidence_window_scan' / 'last_json.json').write_text(json.dumps({
        'kind': 'evidence_window_scan',
        'ok': True,
        'severity': 'warn',
        'summary': {'provider_ready_scopes': 6, 'scope_count': 6},
        'best_scope': {'scope': {'scope_tag': 'EURUSD-OTC_300s'}, 'window_state': 'hold'},
    }), encoding='utf-8')
    (bundle / 'production_gate_all_scopes' / 'last_json.json').write_text(json.dumps({'ready_for_all_scopes': False}), encoding='utf-8')
    (bundle / 'portfolio_status' / 'last_json.json').write_text(json.dumps({'multi_asset': {'asset_count': 6}}), encoding='utf-8')
    (bundle / 'asset_candidate_best' / 'last_json.json').write_text(json.dumps({'ok': True, 'candidate': {'action': 'HOLD'}}), encoding='utf-8')

    results = [
        {'name': 'portfolio_canary_warmup', 'parsed_summary': {'last_json_kind': 'portfolio_canary_warmup'}},
        {'name': 'evidence_window_scan', 'parsed_summary': {'last_json_kind': 'evidence_window_scan'}},
        {'name': 'production_gate_all_scopes', 'parsed_summary': {'last_json_kind': 'production_gate'}},
        {'name': 'portfolio_status', 'parsed_summary': {'last_json_kind': 'portfolio_status'}},
        {'name': 'asset_candidate_best', 'parsed_summary': {'last_json_kind': 'asset_candidate'}},
    ]
    summary = bundle_module._bundle_summary(bundle, results)
    assert summary['portfolio_canary']['provider_ready_scopes'] == 6
    assert summary['portfolio_canary']['warmup_ok'] is True
    assert summary['portfolio_canary']['best_scope_window_state'] == 'hold'
    assert summary['portfolio_canary']['recommended_scope']['scope']['scope_tag'] == 'EURUSD-OTC_300s'


def test_portfolio_canary_warmup_builds_payload(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    cfg_path = repo / 'config.yaml'
    cfg_path.write_text('version: "2.0"\n', encoding='utf-8')
    runs = repo / 'runs'
    runs.mkdir(parents=True, exist_ok=True)
    (runs / 'market_context_EURUSD-OTC_300s.json').write_text(json.dumps({'at_utc': '2026-04-06T18:00:00+00:00', 'market_open': True, 'open_source': 'db_fresh', 'dependency_available': True}), encoding='utf-8')
    scopes = [SimpleNamespace(asset='EURUSD-OTC', interval_sec=300, scope_tag='EURUSD-OTC_300s')]
    monkeypatch.setattr(warmup_module, 'load_selected_scopes', lambda **kwargs: (repo, cfg_path, SimpleNamespace(), scopes))
    monkeypatch.setattr(warmup_module, '_find_python', lambda repo_root: 'python')

    class Result:
        returncode = 0
        stdout = '{"kind":"asset_prepare","ok":true}\n'
        stderr = ''

    monkeypatch.setattr(warmup_module.subprocess, 'run', lambda *args, **kwargs: Result())
    payload = warmup_module.build_warmup_payload(repo_root=repo, config_path=cfg_path, all_scopes=True, timeout_sec=10)
    assert payload['ok'] is True
    assert payload['summary']['prepare_ok_scopes'] == 1
    assert payload['scope_results'][0]['last_json']['kind'] == 'asset_prepare'


def test_iqoption_dependency_status_patches_digital_open(monkeypatch) -> None:
    import natbin.adapters.iq_client as iq_module

    monkeypatch.setattr(iq_module, '_IQ_OPTION_CLASS', None)
    monkeypatch.setattr(iq_module, '_IQ_OPTION_IMPORT_ERROR', None)
    monkeypatch.setattr(iq_module, 'env_bool', lambda *args, **kwargs: False)

    fake_pkg = types.ModuleType('iqoptionapi')
    fake_stable = types.ModuleType('iqoptionapi.stable_api')

    class FakeIQOption:
        OPEN_TIME = {'digital': __import__('collections').defaultdict(dict)}

        def __init__(self, *args, **kwargs):
            self.OPEN_TIME = {'digital': __import__('collections').defaultdict(dict)}

        def get_digital_underlying_list_data(self):
            return {}

        def _IQ_Option__get_digital_open(self):
            raise AssertionError('unpatched private method executed')

    fake_stable.IQ_Option = FakeIQOption
    monkeypatch.setitem(sys.modules, 'iqoptionapi', fake_pkg)
    monkeypatch.setitem(sys.modules, 'iqoptionapi.stable_api', fake_stable)

    status = iq_module.iqoption_dependency_status()
    assert status['available'] is True
    cls = iq_module.require_iqoption_class()
    inst = cls('a', 'b')
    # should not raise even though payload lacks "underlying"
    inst._IQ_Option__get_digital_open()
    assert getattr(cls, '__thalor_digital_open_patched__', False) is True
