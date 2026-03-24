from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from natbin import runtime_app
from natbin.ops import sync_cli
from natbin.ops.sync_state import PUBLISHED_MAIN_BASELINE_REL, WORKSPACE_MANIFEST_REL, REPO_SYNC_ARTIFACT_REL


def _run_git(tmp_path: Path, *args: str) -> None:
    try:
        subprocess.run(['git', *args], cwd=str(tmp_path), check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:  # pragma: no cover - environment guard
        pytest.skip(f'git not available: {exc}')


def _init_repo(tmp_path: Path) -> None:
    _run_git(tmp_path, 'init')
    _run_git(tmp_path, 'config', 'user.email', 'sync1a@example.com')
    _run_git(tmp_path, 'config', 'user.name', 'SYNC-1A Test')
    (tmp_path / 'README.md').write_text('base\n', encoding='utf-8')
    _run_git(tmp_path, 'add', 'README.md')
    _run_git(tmp_path, 'commit', '-m', 'base state')


def test_primary_command_detects_sync_with_global_prefix() -> None:
    assert runtime_app._primary_command(['sync', '--repo-root', '.', '--json']) == 'sync'
    assert runtime_app._primary_command(['--repo-root', '.', 'sync', '--json']) == 'sync'
    assert runtime_app._primary_command(['--config=base.yaml', 'sync', '--json']) == 'sync'


def test_sync_cli_accepts_write_manifest_alias(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / 'README.md').write_text('changed\n', encoding='utf-8')

    rc = sync_cli.main(['sync', '--repo-root', str(tmp_path), '--write-manifest'])

    assert rc == 0
    assert (tmp_path / PUBLISHED_MAIN_BASELINE_REL).exists()
    assert (tmp_path / WORKSPACE_MANIFEST_REL).exists()
    assert (tmp_path / REPO_SYNC_ARTIFACT_REL).exists()
