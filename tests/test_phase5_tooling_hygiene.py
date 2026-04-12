from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from natbin.ops.root_cause_triage import build_root_cause_triage_payload
from natbin.ops.workspace_hygiene import SCANNER_EXTRA_SKIP_DIRS, build_workspace_hygiene_payload, is_workspace_noise
from natbin.release_hygiene import build_release_report
from natbin.repo_sync import build_repo_sync_payload


pytestmark_git = pytest.mark.skipif(shutil.which('git') is None, reason='git not available')


def _write(path: Path, text: str = 'x\n') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(['git', *args], cwd=str(repo), capture_output=True, text=True, encoding='utf-8', errors='replace')
    if cp.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {cp.stderr}")
    return cp.stdout.strip()


def _init_repo(repo: Path) -> None:
    _git(repo, 'init')
    _git(repo, 'config', 'user.email', 'tests@example.com')
    _git(repo, 'config', 'user.name', 'Thalor Tests')
    _git(repo, 'branch', '-M', 'main')


@pytestmark_git
def test_repo_sync_marks_noise_only_dirty_workspace(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / 'README.md', '# Thalor\n')
    _git(tmp_path, 'add', '-A')
    _git(tmp_path, 'commit', '-m', 'baseline')
    _write(tmp_path / 'test_battery' / 'run1' / '01_baseline.txt', 'generated\n')
    _write(tmp_path / 'coverage.xml', '<coverage/>\n')
    payload = build_repo_sync_payload(repo_root=tmp_path, base_ref='main')
    assert payload['status'] == 'dirty'
    assert payload['worktree']['noise_only_dirty'] is True
    assert payload['worktree']['meaningful_dirty'] is False
    assert 'coverage.xml' in payload['worktree']['noise']['paths']
    assert any('workspace-hygiene' in item for item in payload['recommendations'])


def test_workspace_hygiene_detects_and_applies_safe_noise_cleanup(tmp_path: Path) -> None:
    _write(tmp_path / 'README.md', '# keep\n')
    _write(tmp_path / 'test_battery' / 'run1' / 'summary.txt', 'summary\n')
    _write(tmp_path / 'diag_zips' / '20260328_131954' / 'index.json', '{}\n')
    _write(tmp_path / 'coverage.xml', '<coverage/>\n')
    _write(tmp_path / '.pytest_cache' / 'README.md', 'cache\n')
    _write(tmp_path / 'src' / 'natbin.egg-info' / 'SOURCES.txt', 'generated\n')
    preview = build_workspace_hygiene_payload(repo_root=tmp_path, apply=False, list_limit=20, write_artifact=True)
    repo_artifact = tmp_path / 'runs' / 'control' / '_repo' / 'workspace_hygiene.json'
    assert preview['candidates_total'] >= 4
    assert repo_artifact.exists()
    assert json.loads(repo_artifact.read_text(encoding='utf-8'))['kind'] == 'workspace_hygiene'
    assert any('test_battery' in item['path'] for item in preview['sample_candidates'])
    applied = build_workspace_hygiene_payload(repo_root=tmp_path, apply=True, list_limit=20, write_artifact=True)
    assert applied['deleted_total'] >= 4
    assert (tmp_path / 'README.md').exists()
    assert not (tmp_path / 'test_battery').exists()
    assert not (tmp_path / 'diag_zips').exists()
    assert not (tmp_path / 'coverage.xml').exists()
    assert not (tmp_path / '.pytest_cache').exists()
    assert not (tmp_path / 'src' / 'natbin.egg-info').exists()
    assert 'test_battery' in SCANNER_EXTRA_SKIP_DIRS
    assert is_workspace_noise('test_battery/run1/01_baseline.txt') is True


def _write_m7_required(repo: Path) -> None:
    _write(repo / 'README.md', 'hello')
    _write(repo / '.env.example', 'IQ_EMAIL=')
    _write(repo / 'requirements.txt', 'pytest')
    _write(repo / 'pyproject.toml', '[build-system]\nrequires=[]\n')
    _write(repo / 'setup.cfg', '[metadata]\nname=natbin\n')
    _write(repo / 'docker-compose.prod.yml', 'services: {}\n')
    _write(repo / 'docs' / 'ALERTING_M7.md', '# alerting\n')
    _write(repo / 'docs' / 'PRODUCTION_CHECKLIST_M7.md', '# checklist\n')
    _write(repo / 'docs' / 'DIAGRAMS_M7.md', '# diagrams\n')
    _write(repo / 'docs' / 'INCIDENT_RUNBOOKS_M71.md', '# incidents\n')
    _write(repo / 'docs' / 'LIVE_OPS_HARDENING_M71.md', '# live ops\n')
    _write(repo / 'README_PACKAGE_M7_1_APPEND.md', '# m71\n')
    _write(repo / 'src' / 'natbin' / 'runtime_app.py', 'print(\'ok\')\n')
    _write(repo / 'src' / 'natbin' / 'incidents' / 'reporting.py', '# placeholder\n')
    _write(repo / 'scripts' / 'tools' / 'release_bundle.py', 'print(\'ok\')\n')
    _write(repo / 'scripts' / 'tools' / 'incident_ops_smoke.py', '# placeholder\n')


def test_release_hygiene_excludes_test_battery_and_diag_bundles(tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    _write_m7_required(repo)
    _write(repo / 'test_battery' / 'run1' / '01_baseline.zip', 'zip\n')
    _write(repo / 'diag_zips' / 'session1' / 'summary.json', '{}\n')
    _write(repo / 'coverage.xml', '<coverage/>\n')
    _write(repo / 'diag_bundle_20260328_131954.zip', 'zip\n')
    report = build_release_report(repo)
    assert report.ok is True
    assert 'test_battery' in report.safe_prune_candidates
    assert 'diag_zips' in report.safe_prune_candidates
    assert 'coverage.xml' in report.safe_prune_candidates
    assert 'diag_bundle_20260328_131954.zip' in report.safe_prune_candidates
    assert any('test_battery/' in item for item in report.warnings)


def test_root_cause_triage_writes_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / 'config.yaml'
    cfg.write_text('asset: EURUSD-OTC\n', encoding='utf-8')
    ctx = SimpleNamespace(repo_root=str(tmp_path), config=SimpleNamespace(config_path=cfg, asset='EURUSD-OTC', interval_sec=300, timezone='UTC'), scope=SimpleNamespace(scope_tag='EURUSD-OTC_300s'))
    monkeypatch.setattr('natbin.ops.root_cause_triage.build_context', lambda **_: ctx)
    monkeypatch.setattr('natbin.ops.root_cause_triage.incident_status_payload', lambda **_: {
        'ok': False,
        'severity': 'error',
        'breaker': {
            'primary_cause': {'code': 'broker_transport_failure', 'detail': 'proxy upstream bad gateway'},
            'symptom': {'code': 'circuit_half_open_blocked', 'detail': 'awaiting positive retry'},
            'connectivity': {
                'transport_enabled': True,
                'transport_ready': False,
                'endpoint_count': 1,
                'active_endpoint_name': 'primary',
                'last_transport_error': 'proxy upstream bad gateway',
                'last_transport_failure_utc': '2026-03-29T12:00:00+00:00',
            },
        },
        'open_issues': [{'code': 'breaker_primary_cause'}],
        'recommended_actions': ['review broker transport'],
    })
    monkeypatch.setattr('natbin.ops.root_cause_triage.build_production_doctor_payload', lambda **_: {'ok': False, 'checks': [{'name': 'market_context', 'status': 'error', 'message': 'Market context stale'}, {'name': 'circuit_breaker', 'status': 'warn', 'message': 'Half-open'}]})
    payload = build_root_cause_triage_payload(repo_root=tmp_path, config_path=cfg, write_artifact=True)
    assert payload['primary_cause']['code'] == 'broker_transport_failure'
    assert payload['current_symptom']['code'] == 'circuit_half_open_blocked'
    assert payload['root_cause_chain'][0] == 'broker_transport_failure'
    triage_path = tmp_path / 'runs' / 'control' / 'EURUSD-OTC_300s' / 'triage.json'
    assert triage_path.exists()
    stored = json.loads(triage_path.read_text(encoding='utf-8'))
    assert stored['connectivity']['last_transport_error'] == 'proxy upstream bad gateway'
    assert stored['doctor_blocker_checks'][0]['name'] == 'market_context'
