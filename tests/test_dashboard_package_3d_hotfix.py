from __future__ import annotations

from pathlib import Path

from natbin.dashboard.analytics import build_control_display, build_dashboard_snapshot


PRACTICE_PAYLOAD = {
    'severity': 'error',
    'ok': False,
    'ready_for_practice': False,
    'execution': {'enabled': False, 'mode': 'disabled', 'provider': 'fake'},
    'controlled_scope': {
        'multi_asset_enabled': True,
        'assets_configured': 6,
        'portfolio_topk_total': 3,
    },
    'doctor': {'blockers': ['dataset_ready', 'market_context'], 'warnings': ['intelligence_surface']},
}

DOCTOR_PAYLOAD = {
    'severity': 'error',
    'ok': False,
    'blockers': ['dataset_ready', 'market_context'],
    'warnings': ['control_freshness'],
}


def _write_config(root: Path) -> Path:
    cfg = root / 'config' / 'multi_asset.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  profile: dashboard_package_3d_hotfix_test',
                'multi_asset:',
                '  enabled: true',
                '  max_parallel_assets: 2',
                '  portfolio_topk_total: 3',
                'execution:',
                '  enabled: false',
                '  mode: disabled',
                '  provider: fake',
                'dashboard:',
                '  title: Thalor Test Deck',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '  - asset: GBPUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def test_build_control_display_marks_practice_as_not_applicable_for_multi_asset_profile() -> None:
    display = build_control_display({'practice': PRACTICE_PAYLOAD, 'doctor': DOCTOR_PAYLOAD})

    practice = display['practice']
    doctor = display['doctor']
    assert practice['label'] == 'N/A'
    assert practice['tone'] == 'accent'
    assert 'Controlled practice' in practice['reason']
    assert 'multi-asset profile' in practice['meta']

    assert doctor['label'] == 'WAIT DATA'
    assert doctor['tone'] == 'warn'
    assert 'dataset_ready' in doctor['meta']


def test_build_dashboard_snapshot_exposes_control_display(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    snapshot = build_dashboard_snapshot(repo_root=tmp_path, config_path=cfg)

    control_display = dict(snapshot.get('control_display') or {})
    assert 'practice' in control_display
    assert 'doctor' in control_display
    assert str(control_display['practice'].get('label')) in {'N/A', 'ERROR'}
    assert control_display['doctor'].get('label') in {'WAIT DATA', 'ERROR', 'WARN'}
