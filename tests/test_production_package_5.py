from __future__ import annotations

import contextlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from natbin.control.app import main as runtime_main
from natbin.ops.container_health import build_container_health_payload
from natbin.ops.production_backup import build_backup_payload
from natbin.runtime.scope import build_scope


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


MIN_CONFIG = """
version: "2.0"
production:
  enabled: true
  backup:
    interval_minutes: 30
    retention_days: 1
    max_archives: 2
  healthcheck:
    enabled: true
    require_loop_status: true
    max_loop_status_age_sec: 1800
assets:
  - asset: EURUSD-OTC
    interval_sec: 300
"""


def test_production_backup_creates_archive_and_manifest(tmp_path: Path) -> None:
    cfg = tmp_path / 'config' / 'base.yaml'
    _write(cfg, MIN_CONFIG)
    _write(tmp_path / 'runs' / 'runtime_execution.sqlite3', 'db')
    _write(tmp_path / 'runs' / 'control' / 'EURUSD-OTC_300s' / 'health.json', '{"ok": true}')
    _write(tmp_path / 'runs' / 'logs' / 'runtime.log', 'hello')
    _write(tmp_path / 'data' / 'market.sqlite3', 'market')

    payload = build_backup_payload(repo_root=tmp_path, config_path=cfg)
    assert payload['ok'] is True
    assert Path(payload['paths']['archive']).exists()
    assert Path(payload['paths']['manifest']).exists()
    assert Path(payload['paths']['latest_manifest']).exists()
    assert payload['selection']['file_count'] >= 3



def test_container_health_reports_fresh_loop_status(tmp_path: Path) -> None:
    cfg = tmp_path / 'config' / 'base.yaml'
    _write(cfg, MIN_CONFIG)
    scope = build_scope('EURUSD-OTC', 300)
    loop_path = tmp_path / 'runs' / 'control' / scope.scope_tag / 'loop_status.json'
    _write(loop_path, json.dumps({'at_utc': datetime.now(UTC).isoformat()}))

    payload = build_container_health_payload(repo_root=tmp_path, config_path=cfg)
    assert payload['ok'] is True
    assert payload['severity'] == 'ok'



def test_container_health_errors_when_kill_switch_is_active(tmp_path: Path) -> None:
    cfg = tmp_path / 'config' / 'base.yaml'
    _write(cfg, MIN_CONFIG)
    scope = build_scope('EURUSD-OTC', 300)
    loop_path = tmp_path / 'runs' / 'control' / scope.scope_tag / 'loop_status.json'
    _write(loop_path, json.dumps({'at_utc': datetime.now(UTC).isoformat()}))
    _write(tmp_path / 'runs' / 'KILL_SWITCH', 'on')

    payload = build_container_health_payload(repo_root=tmp_path, config_path=cfg)
    assert payload['ok'] is False
    assert payload['severity'] == 'error'
    assert any(item['name'] == 'kill_switch' and item['status'] == 'error' for item in payload['checks'])



def test_runtime_app_backup_cli_and_compose_files() -> None:
    repo = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((repo / 'docker-compose.yml').read_text(encoding='utf-8'))
    compose_vps = yaml.safe_load((repo / 'docker-compose.vps.yml').read_text(encoding='utf-8'))
    dockerfile = (repo / 'Dockerfile').read_text(encoding='utf-8')

    assert 'thalor-backup' in compose['services']
    assert 'healthcheck' in compose['services']['thalor-runtime']
    assert 'thalor-backup' in compose_vps['services']
    assert 'ENTRYPOINT' in dockerfile
    assert 'scripts/docker/healthcheck.sh' in (repo / 'docker-compose.yml').read_text(encoding='utf-8')

    tmp_repo = repo / 'runs' / 'tests' / 'production_package_5_cli'
    if tmp_repo.exists():
        for path in sorted(tmp_repo.rglob('*'), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
    tmp_repo.mkdir(parents=True, exist_ok=True)
    cfg = tmp_repo / 'config' / 'base.yaml'
    _write(cfg, MIN_CONFIG)
    _write(tmp_repo / 'runs' / 'runtime_execution.sqlite3', 'db')
    scope = build_scope('EURUSD-OTC', 300)
    _write(tmp_repo / 'runs' / 'control' / scope.scope_tag / 'loop_status.json', json.dumps({'at_utc': datetime.now(UTC).isoformat()}))

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = runtime_main(['backup', '--repo-root', str(tmp_repo), '--config', str(cfg), '--dry-run', '--json'])
    assert code == 0
