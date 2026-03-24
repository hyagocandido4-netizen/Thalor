#!/usr/bin/env python
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
import sys

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.control.app import main as runtime_main


if shutil.which('git') is None:
    raise SystemExit('sync1_repo_baseline_smoke: SKIPPED (git not available)')


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


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        _git(repo, 'init')
        _git(repo, 'config', 'user.email', 'smoke@example.com')
        _git(repo, 'config', 'user.name', 'Smoke Test')
        _git(repo, 'branch', '-M', 'main')
        _write(repo / 'README.md', '# repo\n')
        _write(repo / 'src' / 'natbin' / '__init__.py', '__all__ = []\n')
        _git(repo, 'add', '-A')
        _git(repo, 'commit', '-m', 'baseline')
        _write(repo / 'README.md', '# repo\n\nchanged\n')
        _write(repo / 'README_PACKAGE_SYNC_SMOKE_APPEND.md', '# smoke\n')

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = runtime_main([
                'sync',
                '--repo-root',
                str(repo),
                '--base-ref',
                'main',
                '--write-manifest',
                '--json',
            ])
        payload = json.loads(buffer.getvalue())
        assert code == 0, payload
        assert payload['kind'] == 'repo_sync', payload
        assert payload['status'] == 'dirty', payload
        assert payload['worktree']['tracked_modified_count'] == 1, payload
        assert payload['worktree']['untracked_count'] == 1, payload
        assert Path(payload['manifest_paths']['json']).exists(), payload
        assert Path(payload['manifest_paths']['markdown']).exists(), payload

    print('sync1_repo_baseline_smoke: OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
