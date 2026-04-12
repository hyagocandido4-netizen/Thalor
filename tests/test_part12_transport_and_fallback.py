from __future__ import annotations

import os
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

from natbin.adapters.iq_client import IQClient, IQConfig
from natbin.ops.practice_preflight import build_practice_preflight_payload
import natbin.ops.safe_refresh as safe_refresh


def _write_cfg(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'live_controlled_practice.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: live_controlled_practice',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                '  account_mode: PRACTICE',
                'broker:',
                '  provider: iqoption',
                '  balance_mode: PRACTICE',
                'data:',
                '  db_path: data/market_otc.sqlite3',
                '  dataset_path: data/dataset_phase2.csv',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
            ]
        )
        + '\n',
        encoding='utf-8',
    )
    return cfg


def test_refresh_market_context_safe_falls_back_to_local_only(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / 'src' / 'natbin').mkdir(parents=True)
    calls: list[dict[str, object]] = []

    def fake_run(cmd, cwd, env, capture_output, text, timeout, check):
        calls.append({'cmd': list(cmd), 'cwd': cwd, 'env': dict(env), 'timeout': timeout})
        if len(calls) == 1:
            raise TimeoutExpired(cmd=list(cmd), timeout=timeout)
        assert env.get('THALOR_FORCE_IQOPTIONAPI_MISSING') == '1'
        return CompletedProcess(cmd, 0, stdout='{"fallback": true}', stderr='')

    monkeypatch.setattr(safe_refresh.subprocess, 'run', fake_run)
    payload = safe_refresh.refresh_market_context_safe(
        repo_root=tmp_path,
        config_path=tmp_path / 'config.yaml',
        asset='EURUSD-OTC',
        interval_sec=300,
        timeout_sec=90,
    )

    assert payload['returncode'] == 0
    assert payload['strategy'] == 'local_only_fallback'
    assert payload['local_only'] is True
    assert payload['fallback_used'] is True
    assert len(calls) == 2


class _FakeIQOption:
    init_proxy: str | None = None
    connect_proxy: str | None = None
    balance_proxy: str | None = None

    def __init__(self, email: str, password: str):
        type(self).init_proxy = os.getenv('ALL_PROXY')

    def connect(self):
        type(self).connect_proxy = os.getenv('ALL_PROXY')
        return True, None

    def change_balance(self, mode: str):
        type(self).balance_proxy = os.getenv('ALL_PROXY')
        return True

    def check_connect(self):
        return True



def test_iq_client_applies_transport_env_overlay(monkeypatch) -> None:
    from natbin.adapters import iq_client as module

    monkeypatch.setattr(module, 'require_iqoption_class', lambda: _FakeIQOption)
    cfg = IQConfig(
        email='user@example.com',
        password='super-secret',
        balance_mode='PRACTICE',
        transport={
            'enabled': True,
            'endpoint': 'socks5h://proxy-user:proxy-pass@proxy.internal:7000',
        },
    )
    client = IQClient(cfg)
    client.connect(retries=1, sleep_s=0.01)

    assert _FakeIQOption.connect_proxy == 'socks5h://proxy-user:proxy-pass@proxy.internal:7000'
    assert _FakeIQOption.balance_proxy == 'socks5h://proxy-user:proxy-pass@proxy.internal:7000'



def test_practice_preflight_treats_practice_warn_as_warning_not_error(monkeypatch, tmp_path: Path) -> None:
    cfg = _write_cfg(tmp_path)
    import natbin.ops.practice_preflight as module

    monkeypatch.setattr(module, 'maybe_heal_market_context', lambda **kwargs: {'name': 'market_context', 'status': 'skip', 'enabled': True, 'attempted': False, 'message': 'test_skip'})
    monkeypatch.setattr(module, 'maybe_heal_control_freshness', lambda **kwargs: {'name': 'control_freshness', 'status': 'skip', 'enabled': True, 'attempted': False, 'message': 'test_skip'})
    monkeypatch.setattr(
        module,
        'build_diag_suite_payload',
        lambda **kwargs: {
            'kind': 'diag_suite',
            'ok': True,
            'severity': 'ok',
            'checks': [],
            'actions': [],
            'results': {
                'practice': {
                    'kind': 'practice_readiness',
                    'ok': True,
                    'severity': 'warn',
                    'ready_for_practice': False,
                    'checks': [
                        {'name': 'drain_mode', 'status': 'ok'},
                        {'name': 'runtime_soak', 'status': 'warn'},
                        {'name': 'production_doctor', 'status': 'ok'},
                    ],
                }
            },
        },
    )
    monkeypatch.setattr(module, 'build_transport_smoke_payload', lambda **kwargs: {'kind': 'transport_smoke', 'ok': True, 'severity': 'ok', 'actions': [], 'scope_results': [{'actions': []}]})
    monkeypatch.setattr(module, 'build_module_smoke_payload', lambda **kwargs: {'kind': 'module_smoke', 'ok': True, 'severity': 'ok', 'actions': []})

    payload = build_practice_preflight_payload(repo_root=tmp_path, config_path=cfg, dry_run=False)

    assert 'practice_readiness' not in payload['blockers']
    assert 'practice_readiness' in payload['warnings']
    assert 'warnings_present' in payload['blockers']


def test_resolve_config_path_prefers_live_controlled_practice(tmp_path: Path, monkeypatch) -> None:
    from natbin.config.paths import resolve_config_path

    (tmp_path / 'config').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'base.yaml').write_text('version: "2.0"\n', encoding='utf-8')
    live = tmp_path / 'config' / 'live_controlled_practice.yaml'
    live.write_text('version: "2.0"\n', encoding='utf-8')

    resolved = resolve_config_path(repo_root=tmp_path, config_path=None)
    assert resolved == live.resolve()
