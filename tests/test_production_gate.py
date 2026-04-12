from __future__ import annotations

from pathlib import Path

from natbin.ops.production_gate import build_production_gate_payload
from natbin.state.control_repo import read_repo_control_artifact



def _write_multi_asset_config(repo_root: Path) -> Path:
    lines = [
        'version: "2.0"',
        'runtime:',
        '  profile: live_controlled_real',
        'execution:',
        '  enabled: true',
        '  mode: live',
        '  provider: iqoption',
        '  account_mode: REAL',
        'broker:',
        '  provider: iqoption',
        '  balance_mode: REAL',
        'multi_asset:',
        '  enabled: true',
        '  max_parallel_assets: 6',
        '  portfolio_topk_total: 6',
        '  portfolio_hard_max_positions: 6',
        '  partition_data_paths: true',
        'assets:',
        '  - asset: EURUSD-OTC',
        '    interval_sec: 300',
        '    timezone: UTC',
        '  - asset: GBPUSD-OTC',
        '    interval_sec: 300',
        '    timezone: UTC',
    ]
    cfg = repo_root / 'config' / 'production_gate.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return cfg



def _provider_scope(asset: str) -> dict:
    return {
        'scope': {'asset': asset, 'interval_sec': 300, 'scope_tag': f'{asset}_300s'},
        'severity': 'ok',
        'ok': True,
        'checks': [],
        'actions': [],
        'shared_provider_session': {'attempted': True, 'ok': True, 'reason': None},
        'remote_candles': {'attempted': True, 'ok': True, 'reason': None},
        'remote_market_context': {'attempted': True, 'ok': True, 'reason': None},
    }



def _doctor_scope(asset: str) -> dict:
    return {
        'kind': 'production_doctor',
        'scope': {'asset': asset, 'interval_sec': 300, 'scope_tag': f'{asset}_300s'},
        'severity': 'ok',
        'ready_for_cycle': True,
        'ready_for_live': True,
        'ready_for_practice': False,
        'ready_for_real': True,
        'blockers': [],
        'warnings': [],
        'actions': [],
    }



def test_production_gate_green_for_all_selected_scopes(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_multi_asset_config(tmp_path)

    from natbin.ops import production_gate as module

    monkeypatch.setattr(
        module,
        'build_provider_probe_payload',
        lambda **kwargs: {
            'severity': 'ok',
            'summary': {'scope_count': 2, 'multi_asset_enabled': True, 'max_parallel_assets': 6},
            'actions': [],
            'shared_provider_session': {'attempted': True, 'ok': True, 'reason': None},
            'transport_hint': {'configured': True, 'scheme': 'socks5h'},
            'scope_results': [_provider_scope('EURUSD-OTC'), _provider_scope('GBPUSD-OTC')],
        },
    )
    monkeypatch.setattr(module, 'build_production_doctor_payload', lambda **kwargs: _doctor_scope(str(kwargs['asset'])))
    monkeypatch.setattr(
        module,
        'release_payload',
        lambda **kwargs: {'severity': 'ok', 'ready_for_live': True, 'ready_for_practice': False, 'ready_for_real': True, 'checks': []},
    )

    payload = build_production_gate_payload(repo_root=tmp_path, config_path=cfg, all_scopes=True, probe_provider=True)

    assert payload['kind'] == 'production_gate'
    assert payload['ok'] is True
    assert payload['ready_for_all_scopes'] is True
    assert payload['summary']['scope_errors'] == 0
    assert payload['summary']['ready_for_live_count'] == 2
    assert payload['summary']['provider_ready_count'] == 2
    assert payload['summary']['multi_asset_enabled'] is True
    assert read_repo_control_artifact(repo_root=tmp_path, name='production_gate') is not None



def test_production_gate_classifies_provider_and_guardrail_blockers(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_multi_asset_config(tmp_path)

    from natbin.ops import production_gate as module

    monkeypatch.setattr(
        module,
        'build_provider_probe_payload',
        lambda **kwargs: {
            'severity': 'error',
            'summary': {'scope_count': 2, 'multi_asset_enabled': True, 'max_parallel_assets': 6},
            'actions': ['Corrija provider'],
            'shared_provider_session': {'attempted': True, 'ok': False, 'reason': 'timeout'},
            'transport_hint': {'configured': True, 'scheme': 'socks5h'},
            'scope_results': [
                {
                    'scope': {'asset': 'EURUSD-OTC', 'interval_sec': 300, 'scope_tag': 'EURUSD-OTC_300s'},
                    'severity': 'error',
                    'ok': False,
                    'checks': [{'name': 'remote_candles', 'status': 'error', 'message': 'Falha candles'}],
                    'actions': ['Revalidar provider'],
                    'shared_provider_session': {'attempted': True, 'ok': False, 'reason': 'timeout'},
                    'remote_candles': {'attempted': True, 'ok': False, 'reason': 'timeout'},
                    'remote_market_context': {'attempted': True, 'ok': False, 'reason': 'timeout'},
                },
                _provider_scope('GBPUSD-OTC'),
            ],
        },
    )
    monkeypatch.setattr(
        module,
        'build_production_doctor_payload',
        lambda **kwargs: {
            **_doctor_scope(str(kwargs['asset'])),
            'severity': 'error' if str(kwargs['asset']) == 'EURUSD-OTC' else 'ok',
            'ready_for_cycle': False if str(kwargs['asset']) == 'EURUSD-OTC' else True,
            'ready_for_live': False if str(kwargs['asset']) == 'EURUSD-OTC' else True,
            'blockers': ['circuit_breaker'] if str(kwargs['asset']) == 'EURUSD-OTC' else [],
            'warnings': [],
            'actions': ['Fechar breaker'] if str(kwargs['asset']) == 'EURUSD-OTC' else [],
        },
    )
    monkeypatch.setattr(
        module,
        'release_payload',
        lambda **kwargs: {'severity': 'warn', 'ready_for_live': False, 'ready_for_practice': False, 'ready_for_real': False, 'checks': []},
    )

    payload = build_production_gate_payload(repo_root=tmp_path, config_path=cfg, all_scopes=True, probe_provider=True)

    assert payload['ok'] is False
    eur = next(item for item in payload['scope_results'] if item['scope']['asset'] == 'EURUSD-OTC')
    categories = {(item['name'], item['category']) for item in eur['blockers']}
    assert ('circuit_breaker', 'guardrail') in categories
    assert ('remote_candles', 'provider') in categories
    assert 'Corrija provider' in payload['actions']

