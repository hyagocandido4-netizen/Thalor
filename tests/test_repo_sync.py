from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from natbin.control.app import main as runtime_main
from natbin.repo_sync import build_repo_sync_payload


pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='git not available')


def _git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ['git', *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    )
    if cp.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {cp.stderr}")
    return cp.stdout.strip()


def _write(path: Path, body: str = 'x\n') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')


def _init_repo(repo: Path) -> None:
    _git(repo, 'init')
    _git(repo, 'config', 'user.email', 'tests@example.com')
    _git(repo, 'config', 'user.name', 'Thalor Tests')
    _git(repo, 'branch', '-M', 'main')


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-m', message)


def test_build_repo_sync_payload_reports_dirty_workspace_and_writes_manifest(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / 'README.md', '# Thalor\n')
    _write(tmp_path / 'src' / 'natbin' / '__init__.py', '__all__ = []\n')
    _commit_all(tmp_path, 'baseline')

    _write(tmp_path / 'README.md', '# Thalor\n\nupdated\n')
    _write(tmp_path / 'README_PACKAGE_TEST_APPEND.md', '# test package\n')
    _write(tmp_path / 'docs' / 'TEST_SYNC1.md', '# sync1\n')

    payload = build_repo_sync_payload(
        repo_root=tmp_path,
        base_ref='main',
        write_manifest=True,
    )

    assert payload['kind'] == 'repo_sync'
    assert payload['status'] == 'dirty'
    assert payload['severity'] == 'warn'
    assert payload['base_ref']['exists'] is True
    assert payload['worktree']['tracked_modified_count'] == 1
    assert payload['worktree']['untracked_count'] == 2
    assert 'README_PACKAGE_TEST_APPEND.md' in payload['inventory']['changed_append_readmes']
    assert Path(payload['manifest_paths']['json']).exists()
    assert Path(payload['manifest_paths']['markdown']).exists()
    written = json.loads(Path(payload['manifest_paths']['json']).read_text(encoding='utf-8'))
    assert written['fingerprint'] == payload['fingerprint']


def test_runtime_app_sync_reports_divergence_and_strict_clean_fails(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path / 'README.md', '# Thalor\n')
    _commit_all(tmp_path, 'baseline')

    _git(tmp_path, 'checkout', '-b', 'feature/sync1')
    _write(tmp_path / 'README.md', '# Thalor\n\nfeature\n')
    _commit_all(tmp_path, 'feature delta')

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = runtime_main(['sync', '--repo-root', str(tmp_path), '--base-ref', 'main', '--json'])
    payload = json.loads(buffer.getvalue())
    assert code == 0
    assert payload['kind'] == 'repo_sync'
    assert payload['status'] == 'diverged'
    assert payload['divergence']['ahead'] == 1
    assert payload['divergence']['behind'] == 0

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        strict_code = runtime_main(['sync', '--repo-root', str(tmp_path), '--base-ref', 'main', '--json', '--strict-clean'])
    assert strict_code == 2
