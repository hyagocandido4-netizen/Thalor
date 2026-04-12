from __future__ import annotations

import json
from pathlib import Path

from natbin.ops.config_provenance import build_config_provenance_payload
from natbin.state.control_repo import read_control_artifact, read_repo_control_artifact


def _write_config(repo_root: Path, *, multi_asset: bool = True, execution_account_mode: str = 'REAL', broker_balance_mode: str = 'REAL') -> Path:
    lines = [
        'version: "2.0"',
        'runtime:',
        '  profile: live_controlled_real',
        'execution:',
        '  enabled: true',
        '  mode: live',
        '  provider: iqoption',
        f'  account_mode: {execution_account_mode}',
        'broker:',
        '  provider: iqoption',
        f'  balance_mode: {broker_balance_mode}',
        'data:',
        '  db_path: data/market_otc.sqlite3',
        '  dataset_path: data/dataset_phase2.csv',
        'security:',
        '  deployment_profile: local',
        'multi_asset:',
        f'  enabled: {str(multi_asset).lower()}',
        '  max_parallel_assets: 6',
        '  portfolio_topk_total: 6',
        '  portfolio_hard_max_positions: 6',
        '  partition_data_paths: true',
        'assets:',
        '  - asset: EURUSD-OTC',
        '    interval_sec: 300',
        '    timezone: UTC',
    ]
    if multi_asset:
        lines.extend(
            [
                '  - asset: GBPUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
            ]
        )
    cfg = repo_root / 'config' / 'audit.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return cfg


def test_config_provenance_flags_forbidden_secret_bundle_override(tmp_path: Path, monkeypatch) -> None:
    cfg = _write_config(tmp_path, execution_account_mode='REAL', broker_balance_mode='REAL')
    bundle = tmp_path / 'config' / 'broker_secrets.yaml'
    bundle.write_text('broker:\n  balance_mode: PRACTICE\n', encoding='utf-8')
    monkeypatch.setenv('THALOR_SECRETS_FILE', str(bundle))

    payload = build_config_provenance_payload(repo_root=tmp_path, config_path=cfg)

    assert payload['kind'] == 'config_provenance_audit'
    assert payload['ok'] is False
    balance = next(item for item in payload['field_provenance'] if item['field'] == 'broker.balance_mode')
    assert balance['winner'].startswith('secret_bundle:')
    check = next(item for item in payload['checks'] if item['name'] == 'secret_bundle_balance_mode_override')
    assert check['status'] == 'error'
    assert read_repo_control_artifact(repo_root=tmp_path, name='config_provenance') is not None
    assert read_control_artifact(repo_root=tmp_path, asset='EURUSD-OTC', interval_sec=300, name='config_provenance') is not None


def test_config_provenance_reports_transport_secret_file_and_multi_asset_capacity(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path, multi_asset=True)
    secret_dir = tmp_path / 'secrets'
    secret_dir.mkdir(parents=True, exist_ok=True)
    (secret_dir / 'transport_endpoint').write_text('socks5h://user:pass@gate.example.net:7000?name=primary\n', encoding='utf-8')

    payload = build_config_provenance_payload(repo_root=tmp_path, config_path=cfg, all_scopes=True)

    assert payload['transport']['configured'] is True
    assert payload['transport']['selected_source'] == 'secret_file:transport_endpoint'
    cap = next(item for item in payload['checks'] if item['name'] == 'multi_asset_capacity')
    assert cap['status'] == 'ok'
    assert payload['scope_count'] == 2
