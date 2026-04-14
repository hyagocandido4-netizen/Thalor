from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from natbin.ops.release_readiness import build_release_readiness_payload


def _touch(path: Path, body: str = 'x\n') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')


def _write_repo(repo: Path) -> Path:
    # required release bundle files
    _touch(repo / 'README.md', '# repo\n')
    _touch(repo / '.env.example', 'THALOR_SECRETS_FILE="secrets/bundle.yaml"\n')
    _touch(repo / 'requirements.txt', 'pydantic>=2\n')
    _touch(repo / 'pyproject.toml', '[build-system]\nrequires=["setuptools"]\n')
    _touch(repo / 'setup.cfg', '[metadata]\nname=natbin\n')
    _touch(repo / 'src' / 'natbin' / 'runtime_app.py', '# placeholder\n')
    _touch(repo / 'src' / 'natbin' / 'incidents' / 'reporting.py', '# placeholder\n')
    _touch(repo / 'scripts' / 'tools' / 'release_bundle.py', '# placeholder\n')
    _touch(repo / 'scripts' / 'tools' / 'incident_ops_smoke.py', '# placeholder\n')
    _touch(repo / 'docs/history/package_legacy/README_PACKAGE_M7_1_APPEND.md', '# m71\n')
    _touch(repo / 'Dockerfile', 'FROM python:3.12-slim\n')
    _touch(repo / 'docker-compose.yml', 'services: {}\n')
    _touch(repo / 'docker-compose.prod.yml', 'services: {}\n')
    _touch(repo / 'docs' / 'OPERATIONS.md', '# ops\n')
    _touch(repo / 'docs' / 'DOCKER.md', '# docker\n')
    _touch(repo / 'docs' / 'ALERTING_M7.md', '# alerting\n')
    _touch(repo / 'docs' / 'PRODUCTION_CHECKLIST_M7.md', '# checklist\n')
    _touch(repo / 'docs' / 'DIAGRAMS_M7.md', '# diagrams\n')
    _touch(repo / 'docs' / 'INCIDENT_RUNBOOKS_M71.md', '# incidents\n')
    _touch(repo / 'docs' / 'LIVE_OPS_HARDENING_M71.md', '# live ops\n')
    _touch(repo / '.gitignore', '.env\nsecrets/\nruns/\ndata/\n')
    bundle = repo / 'secrets' / 'bundle.yaml'
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(
        '\n'.join(
            [
                'broker:',
                '  email: trader@example.com',
                '  password: trader-secret',
                '  balance_mode: PRACTICE',
                'telegram:',
                '  bot_token: 123456:ABCDEF',
                '  chat_id: "999888777"',
                '',
            ]
        ),
        encoding='utf-8',
    )
    cfg = repo / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  startup_invalidate_stale_artifacts: true',
                '  lock_refresh_enable: true',
                'security:',
                '  deployment_profile: live',
                '  secrets_file: secrets/bundle.yaml',
                '  live_require_external_credentials: true',
                'notifications:',
                '  enabled: true',
                '  telegram:',
                '    enabled: true',
                '    send_enabled: true',
                'multi_asset:',
                '  enabled: true',
                '  max_parallel_assets: 2',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    intel_dir = repo / 'runs' / 'intelligence' / 'EURUSD-OTC_300s'
    intel_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=UTC).isoformat(timespec='seconds')
    for name, payload in {
        'pack.json': {
            'kind': 'intelligence_pack',
            'generated_at_utc': now,
            'metadata': {'training_rows': 256},
        },
        'latest_eval.json': {
            'kind': 'intelligence_eval',
            'evaluated_at_utc': now,
            'allow_trade': True,
            'intelligence_score': 0.71,
            'portfolio_score': 0.74,
            'portfolio_feedback': {'allocator_blocked': False, 'portfolio_score': 0.74},
            'retrain_orchestration': {'state': 'idle', 'priority': 'low'},
        },
        'retrain_plan.json': {
            'kind': 'retrain_plan',
            'at_utc': now,
            'state': 'idle',
            'priority': 'low',
        },
        'retrain_status.json': {
            'kind': 'retrain_status',
            'updated_at_utc': now,
            'state': 'idle',
            'priority': 'low',
        },
    }.items():
        (intel_dir / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    return cfg


def test_build_release_readiness_payload_marks_repo_ready(tmp_path: Path) -> None:
    cfg_path = _write_repo(tmp_path)
    payload = build_release_readiness_payload(repo_root=tmp_path, config_path=cfg_path)
    assert payload['kind'] == 'release_readiness'
    assert payload['severity'] == 'ok'
    assert payload['ready_for_live'] is True
    assert payload['ready_for_practice'] is True
    assert payload['ready_for_real'] is False
    assert payload['execution_account_mode'] == 'PRACTICE'
    names = {item['name'] for item in payload['checks']}
    assert 'security_posture' in names
    assert 'telegram_alerting' in names
    assert 'production_doctor' in names
    assert 'intelligence_surface' in names
    assert payload['doctor']['severity'] == 'ok'
    assert payload['intelligence']['severity'] == 'ok'
    release_artifact = tmp_path / 'runs' / 'control' / 'EURUSD-OTC_300s' / 'release.json'
    assert release_artifact.exists()
