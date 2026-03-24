from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from natbin.ops.sync_state import (
    PUBLISHED_MAIN_BASELINE_REL,
    WORKSPACE_MANIFEST_REL,
    build_sync_payload,
)


def _run_git(tmp_path: Path, *args: str) -> None:
    try:
        subprocess.run(['git', *args], cwd=str(tmp_path), check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:  # pragma: no cover - environment guard
        pytest.skip(f'git not available: {exc}')


def _init_repo(tmp_path: Path) -> None:
    _run_git(tmp_path, 'init')
    _run_git(tmp_path, 'config', 'user.email', 'sync1@example.com')
    _run_git(tmp_path, 'config', 'user.name', 'SYNC-1 Test')
    (tmp_path / 'README.md').write_text('base\n', encoding='utf-8')
    src = tmp_path / 'src'
    src.mkdir(parents=True, exist_ok=True)
    (src / 'app.py').write_text('print(1)\n', encoding='utf-8')
    _run_git(tmp_path, 'add', 'README.md', 'src/app.py')
    _run_git(tmp_path, 'commit', '-m', 'base state')


def test_sync_freeze_roundtrip_matches_frozen_manifest(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / 'README.md').write_text('base changed\n', encoding='utf-8')
    docs = tmp_path / 'docs'
    docs.mkdir(parents=True, exist_ok=True)
    (docs / 'notes.md').write_text('hello\n', encoding='utf-8')
    (tmp_path / 'README_PACKAGE_SAMPLE_APPEND.md').write_text('# sample\n', encoding='utf-8')

    payload = build_sync_payload(repo_root=tmp_path, freeze_docs=True, write_artifact=False)

    assert payload['ok'] is True
    assert payload['compare']['workspace_matches_frozen'] is True
    assert (tmp_path / PUBLISHED_MAIN_BASELINE_REL).exists()
    assert (tmp_path / WORKSPACE_MANIFEST_REL).exists()

    second = build_sync_payload(repo_root=tmp_path, freeze_docs=False, write_artifact=False)
    assert second['compare']['workspace_matches_frozen'] is True
    assert 'docs/notes.md' in second['current_workspace']['status']['untracked']
    assert 'README.md' in second['current_workspace']['status']['modified']


def test_sync_detects_drift_after_freeze(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / 'README.md').write_text('base changed\n', encoding='utf-8')
    build_sync_payload(repo_root=tmp_path, freeze_docs=True, write_artifact=False)

    (tmp_path / 'src' / 'new_file.py').write_text('print(2)\n', encoding='utf-8')

    payload = build_sync_payload(repo_root=tmp_path, freeze_docs=False, write_artifact=False)

    assert payload['compare']['workspace_matches_frozen'] is False
    drift = payload['compare']['workspace_drift']
    assert 'untracked' in drift
    assert 'src/new_file.py' in drift['untracked']['extra']


def test_sync_writes_repo_artifact(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    payload = build_sync_payload(repo_root=tmp_path, freeze_docs=True, write_artifact=True)

    artifact_path = Path(payload['paths']['repo_sync_artifact'])
    assert artifact_path.exists()
    stored = json.loads(artifact_path.read_text(encoding='utf-8'))
    assert stored['kind'] == 'sync_state'
    assert stored['compare']['workspace_matches_frozen'] is True
