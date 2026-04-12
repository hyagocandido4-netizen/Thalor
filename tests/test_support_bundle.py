from __future__ import annotations

import json
import zipfile
from pathlib import Path

from natbin.ops.support_bundle import build_support_bundle_payload


class DummyScope:
    def __init__(self, asset: str, interval_sec: int) -> None:
        self.asset = asset
        self.interval_sec = interval_sec
        self.scope_tag = f'{asset}_{interval_sec}s'


class DummyMultiAsset:
    enabled = True
    max_parallel_assets = 6
    portfolio_topk_total = 6
    portfolio_hard_max_positions = 6
    partition_data_paths = True


class DummyCfg:
    multi_asset = DummyMultiAsset()


class DummyContext:
    def __init__(self, repo_root: Path, config_path: Path) -> None:
        self.repo_root = str(repo_root)
        self.config = type('Cfg', (), {'config_path': str(config_path), 'asset': 'EURUSD-OTC', 'interval_sec': 300})()
        self.resolved_config = {
            'profile': 'live_controlled_real',
            'execution': {'enabled': True, 'mode': 'live', 'provider': 'iqoption', 'account_mode': 'REAL'},
            'broker': {'provider': 'iqoption', 'balance_mode': 'REAL', 'email': 'user@example.com', 'password': 'super-secret'},
            'security': {'deployment_profile': 'local'},
            'multi_asset': {'enabled': True, 'max_parallel_assets': 6, 'portfolio_topk_total': 6, 'portfolio_hard_max_positions': 6},
        }
        self.source_trace = ['yaml:config/live_controlled_real.yaml', 'secret_file:bundle:broker_secrets.yaml']


def _write_config(repo_root: Path) -> Path:
    cfg = repo_root / 'config' / 'support_bundle.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
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
            ]
        ) + '\n',
        encoding='utf-8',
    )
    return cfg


def test_support_bundle_creates_zip_with_sanitized_contents(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path)
    (tmp_path / 'config' / 'broker_secrets.yaml').write_text('broker:\n  email: user@example.com\n  password: super-secret\n', encoding='utf-8')
    secret_dir = tmp_path / 'secrets'
    secret_dir.mkdir(parents=True, exist_ok=True)
    proxy = 'socks5h://user:pass@gate.example.net:7000?name=primary'
    (secret_dir / 'transport_endpoint').write_text(proxy + '\n', encoding='utf-8')
    log_dir = tmp_path / 'runs' / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / 'runtime_structured.jsonl').write_text(json.dumps({'proxy_url': proxy, 'password': 'super-secret'}) + '\n', encoding='utf-8')

    from natbin.ops import support_bundle as module

    monkeypatch.setattr(module, 'load_selected_scopes', lambda **kwargs: (tmp_path, cfg, DummyCfg(), [DummyScope('EURUSD-OTC', 300)]))
    monkeypatch.setattr(module, 'build_context', lambda **kwargs: DummyContext(tmp_path, cfg))
    monkeypatch.setattr(module, 'build_config_provenance_payload', lambda **kwargs: {'kind': 'config_provenance_audit', 'ok': True, 'severity': 'ok', 'actions': [], 'selected_scopes': [{'scope_tag': 'EURUSD-OTC_300s', 'data_paths': {'dataset_path': str(tmp_path / 'data' / 'dataset.csv')}, 'runtime_paths': {'market_context_path': str(tmp_path / 'runs' / 'market_context_EURUSD-OTC_300s.json')}}]})
    monkeypatch.setattr(module, 'audit_security_posture', lambda **kwargs: {'kind': 'security', 'ok': True, 'severity': 'ok'})
    monkeypatch.setattr(module, 'build_provider_probe_payload', lambda **kwargs: {'kind': 'provider_probe', 'ok': True, 'severity': 'ok', 'proxy_url': proxy})
    monkeypatch.setattr(module, 'build_production_gate_payload', lambda **kwargs: {'kind': 'production_gate', 'ok': True, 'severity': 'ok', 'actions': ['Tudo certo']})
    monkeypatch.setattr(module, 'build_release_readiness_payload', lambda **kwargs: {'kind': 'release', 'ok': True, 'severity': 'ok'})
    monkeypatch.setattr(module, 'build_production_doctor_payload', lambda **kwargs: {'kind': 'doctor', 'ok': True, 'severity': 'ok', 'scope': {'scope_tag': 'EURUSD-OTC_300s'}})

    payload = build_support_bundle_payload(repo_root=tmp_path, config_path=cfg, include_logs=True, output_dir=tmp_path / 'diag_zips')

    assert payload['ok'] is True
    zip_path = Path(payload['zip_path'])
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = set(zf.namelist())
        assert 'manifest.json' in names
        assert 'diagnostics/provider_probe.json' in names
        provider_text = zf.read('diagnostics/provider_probe.json').decode('utf-8')
        secrets_text = zf.read('config/broker_secrets.sanitized.yaml').decode('utf-8')
        runtime_log = zf.read('logs/runtime_structured.jsonl').decode('utf-8')

    assert proxy not in provider_text
    assert 'super-secret' not in provider_text
    assert 'super-secret' not in secrets_text
    assert proxy not in runtime_log
