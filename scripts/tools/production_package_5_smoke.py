from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

from natbin.control.app import main as runtime_main
from natbin.runtime.scope import build_scope


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((repo / 'docker-compose.yml').read_text(encoding='utf-8'))
    compose_vps = yaml.safe_load((repo / 'docker-compose.vps.yml').read_text(encoding='utf-8'))
    if 'thalor-backup' not in compose.get('services', {}):
        raise SystemExit('missing thalor-backup service in docker-compose.yml')
    if 'healthcheck' not in compose.get('services', {}).get('thalor-runtime', {}):
        raise SystemExit('missing runtime healthcheck in docker-compose.yml')
    if 'thalor-backup' not in compose_vps.get('services', {}):
        raise SystemExit('missing thalor-backup service in docker-compose.vps.yml')

    tmp_dir = Path(tempfile.mkdtemp(prefix='thalor_production5_smoke_'))
    try:
        cfg = tmp_dir / 'config' / 'base.yaml'
        _write(
            cfg,
            """
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
""".strip() + "\n",
        )
        _write(tmp_dir / 'runs' / 'runtime_execution.sqlite3', 'db')
        _write(tmp_dir / 'runs' / 'logs' / 'runtime.log', 'hello')
        scope = build_scope('EURUSD-OTC', 300)
        _write(tmp_dir / 'runs' / 'control' / scope.scope_tag / 'loop_status.json', json.dumps({'at_utc': datetime.now(UTC).isoformat()}))

        for argv in (
            ['backup', '--repo-root', str(tmp_dir), '--config', str(cfg), '--json'],
            ['healthcheck', '--repo-root', str(tmp_dir), '--config', str(cfg), '--json'],
        ):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = runtime_main(argv)
            if code != 0:
                raise SystemExit(f'command failed: {argv}')
            payload = json.loads(buf.getvalue())
            if not bool(payload.get('ok')):
                raise SystemExit(f'payload not ok for {argv}: {payload}')
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print('OK production_package_5_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
