from __future__ import annotations

import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.control.plan import build_context  # noqa: E402
from natbin.ops.artifact_retention import build_retention_payload  # noqa: E402
from natbin.ops.production_doctor import build_production_doctor_payload  # noqa: E402
from natbin.state.control_repo import write_control_artifact  # noqa: E402


def ok(msg: str) -> None:
    print(f'[h9][OK] {msg}')


def fail(msg: str) -> None:
    print(f'[h9][FAIL] {msg}')
    raise SystemExit(2)


def _touch(path: Path, body: str = 'x\n', *, days_old: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    if days_old > 0:
        ts = (datetime.now(UTC) - timedelta(days=days_old)).timestamp()
        os.utime(path, (ts, ts))


def _seed_repo(repo: Path) -> Path:
    cfg = repo / 'config' / 'base.yaml'
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'runtime:',
                '  runtime_retention_days: 7',
                'execution:',
                '  enabled: false',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    ctx = build_context(repo_root=repo, config_path=cfg, dump_snapshot=False)
    dataset = repo / 'data' / 'dataset_phase2.csv'
    dataset.parent.mkdir(parents=True, exist_ok=True)
    with dataset.open('w', encoding='utf-8') as fh:
        fh.write('ts,feature_a\n')
        for idx in range(150):
            fh.write(f'{1773300000 + idx * 300},{1.0 + idx / 1000.0}\n')
    market_path = Path(ctx.scoped_paths['market_context'])
    market_path.parent.mkdir(parents=True, exist_ok=True)
    market_path.write_text(
        '{"asset": "EURUSD-OTC", "interval_sec": 300, "market_open": true, "open_source": "db_fresh", "payout": 0.85, "at_utc": "%s"}'
        % datetime.now(UTC).isoformat(timespec='seconds'),
        encoding='utf-8',
    )
    fresh = {'at_utc': datetime.now(UTC).isoformat(timespec='seconds'), 'state': 'healthy'}
    write_control_artifact(repo_root=repo, asset='EURUSD-OTC', interval_sec=300, name='loop_status', payload=fresh)
    write_control_artifact(repo_root=repo, asset='EURUSD-OTC', interval_sec=300, name='health', payload=fresh)
    return cfg


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        cfg = _seed_repo(repo)

        doctor = build_production_doctor_payload(repo_root=repo, config_path=cfg, probe_broker=False)
        if doctor.get('severity') != 'ok':
            fail(f'unexpected doctor severity: {doctor}')
        ok('production doctor reports ready local runtime')

        _touch(repo / 'runs' / 'logs' / 'runtime_old.log', days_old=10)
        _touch(repo / 'runs' / 'config' / 'effective_config_20260101_EURUSD-OTC_300s_010101.json', '{}\n', days_old=12)
        _touch(repo / 'runs' / 'config' / 'effective_config_20260102_EURUSD-OTC_300s_020202.json', '{}\n', days_old=11)

        preview = build_retention_payload(repo_root=repo, config_path=cfg, apply=False, days=7, keep_effective_config_snapshots=1)
        if int(preview.get('candidates_total') or 0) < 1:
            fail(f'retention preview found no candidates: {preview}')
        ok('retention preview identifies old artifacts')

        applied = build_retention_payload(repo_root=repo, config_path=cfg, apply=True, days=7, keep_effective_config_snapshots=1)
        if not bool(applied.get('ok')):
            fail(f'retention apply failed: {applied}')
        if int(applied.get('deleted_total') or 0) < 1:
            fail(f'retention apply deleted nothing: {applied}')
        ok('retention apply deletes old artifacts')

    print('[h9] ALL OK')


if __name__ == '__main__':
    main()
