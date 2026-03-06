#!/usr/bin/env python
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def _ok(msg: str) -> None:
    print(f'[runtime-execution][OK] {msg}')


def _fail(msg: str) -> None:
    print(f'[runtime-execution][FAIL] {msg}')
    raise SystemExit(2)


def _write_cfg(repo: Path) -> Path:
    (repo / 'config').mkdir(parents=True, exist_ok=True)
    cfg = repo / 'config' / 'base.yaml'
    cfg.write_text(
        '\n'.join([
            'version: "2.0"',
            'assets:',
            '  - asset: EURUSD-OTC',
            '    interval_sec: 300',
            '    timezone: UTC',
            'execution:',
            '  enabled: true',
            '  mode: paper',
            '  provider: fake',
            '  account_mode: PRACTICE',
            '  stake:',
            '    amount: 2.0',
            '    currency: BRL',
            '  fake:',
            '    submit_behavior: ack',
            '    settlement: win',
            '    settle_after_sec: 0',
        ]),
        encoding='utf-8',
    )
    return cfg


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / 'src'
    import os

    env = dict(os.environ)
    env['PYTHONPATH'] = str(src) + ((env.get('PYTHONPATH') and (os.pathsep + env['PYTHONPATH'])) or '')
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from natbin.state.repos import SignalsRepository
    from natbin.runtime.execution import process_latest_signal, orders_payload
    from natbin.runtime.quota import build_quota_snapshot

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        cfg = _write_cfg(repo)
        sig_repo = SignalsRepository(repo / 'runs' / 'live_signals.sqlite3', default_interval=300)
        sig_repo.write_row({
            'dt_local': '2026-03-05 00:00:00',
            'day': '2026-03-05',
            'asset': 'EURUSD-OTC',
            'interval_sec': 300,
            'ts': 1772668800,
            'proba_up': 0.6,
            'conf': 0.6,
            'score': 0.7,
            'gate_mode': 'cp_meta_iso',
            'gate_mode_requested': 'cp_meta_iso',
            'gate_fail_closed': 0,
            'gate_fail_detail': '',
            'regime_ok': 1,
            'thresh_on': 'ev',
            'threshold': 0.02,
            'k': 3,
            'rank_in_day': 1,
            'executed_today': 1,
            'budget_left': 2,
            'action': 'CALL',
            'reason': 'topk_emit',
            'blockers': '',
            'close': 1.0,
            'payout': 0.85,
            'ev': 0.2,
            'model_version': 'smoke',
            'train_rows': 10,
            'train_end_ts': 1772668800,
            'best_source': 'smoke',
            'tune_dir': '',
            'feat_hash': 'smoke',
            'gate_version': 'smoke',
            'meta_model': 'hgb',
            'market_context_stale': 0,
            'market_context_fail_closed': 0,
        })
        payload = process_latest_signal(repo_root=repo, config_path=cfg)
        if not payload.get('intent_created'):
            _fail(f'expected intent creation, got {payload}')
        if payload.get('latest_intent', {}).get('intent_state') != 'settled':
            _fail(f'expected immediate settled intent after fake win, got {payload.get("latest_intent")}')
        order_view = orders_payload(repo_root=repo, config_path=cfg)
        if order_view['summary']['consuming_today'] != 1:
            _fail(f'execution orders summary mismatch: {order_view}')
        snap = build_quota_snapshot(repo, topk=3, config_path=cfg)
        if snap.source != 'execution':
            _fail(f'quota should read execution ledger, got {snap.as_dict()}')
        _ok('python integration path ok')

        cmd = [sys.executable, '-m', 'natbin.runtime_app', '--repo-root', str(repo), '--config', str(cfg), 'orders', '--json']
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            _fail(f'runtime_app orders failed: rc={proc.returncode} stderr={proc.stderr}')
        cli_payload = json.loads(proc.stdout)
        if cli_payload['summary']['consuming_today'] != 1:
            _fail(f'runtime_app orders payload mismatch: {cli_payload}')
        _ok('runtime_app Package N commands ok')

    print('[runtime-execution] ALL OK')


if __name__ == '__main__':
    main()
