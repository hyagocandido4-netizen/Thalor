#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime
from typing import Any


def parse_last_json_dict(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    last: dict[str, Any] | None = None
    idx = 0
    while idx < len(text):
        next_obj = text.find('{', idx)
        if next_obj < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[next_obj:])
        except json.JSONDecodeError:
            idx = next_obj + 1
            continue
        if isinstance(obj, dict):
            last = obj
        idx = next_obj + end
    return last


def pick_python(repo_root: Path) -> str:
    candidates = [
        repo_root / '.venv' / 'Scripts' / 'python.exe',
        repo_root / '.venv' / 'bin' / 'python',
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def build_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    src = repo_root / 'src'
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = str(src) if not existing else f"{src}{os.pathsep}{existing}"
    env['THALOR_REPO_ROOT'] = str(repo_root)
    return env


def now_tag() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S')


def run_capture(name: str, cmd: list[str], *, cwd: Path, env: dict[str, str], log_dir: Path, timeout: int | None = None) -> tuple[int, str, str, dict[str, Any] | None]:
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f'{name}_{now_tag()}_stdout.txt'
    stderr_path = log_dir / f'{name}_{now_tag()}_stderr.txt'
    print(f"\n=== {name} ===")
    print(' '.join(cmd))
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout)
    stdout = proc.stdout or ''
    stderr = proc.stderr or ''
    stdout_path.write_text(stdout, encoding='utf-8', errors='replace')
    stderr_path.write_text(stderr, encoding='utf-8', errors='replace')
    if stdout:
        print(stdout, end='' if stdout.endswith('\n') else '\n')
    if stderr:
        print(stderr, file=sys.stderr, end='' if stderr.endswith('\n') else '\n')
    payload = parse_last_json_dict(stdout)
    return proc.returncode, stdout, stderr, payload


def run_stream(name: str, cmd: list[str], *, cwd: Path, env: dict[str, str], log_dir: Path) -> int:
    log_dir.mkdir(parents=True, exist_ok=True)
    console_path = log_dir / f'{name}_{now_tag()}.log'
    print(f"\n=== {name} ===")
    print(' '.join(cmd))
    with console_path.open('w', encoding='utf-8', errors='replace') as fh:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end='')
            fh.write(line)
        return proc.wait()


def require_ok(payload: dict[str, Any] | None, *, name: str) -> None:
    if not isinstance(payload, dict) or not bool(payload.get('ok')):
        raise SystemExit(f'{name} did not return ok=true')


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='Gate Thalor startup on a fresh live provider probe, then warm up canary scopes and start a long conservative PRACTICE soak.')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default='config\\practice_portfolio_canary.yaml')
    ap.add_argument('--probe-sleep-sec', type=int, default=30)
    ap.add_argument('--probe-max-attempts', type=int, default=0, help='0 = retry forever until provider-probe returns ok=true')
    ap.add_argument('--observe-max-cycles', type=int, default=96)
    ap.add_argument('--topk', type=int, default=1)
    ap.add_argument('--lookback-candles', type=int, default=2000)
    ap.add_argument('--skip-signal-proof', action='store_true')
    ap.add_argument('--skip-go-no-go', action='store_true')
    args = ap.parse_args(argv)

    repo = Path(args.repo_root).resolve()
    config = Path(args.config)
    if not config.is_absolute():
        config = (repo / config).resolve()
    py = pick_python(repo)
    env = build_env(repo)
    logs = repo / 'runs' / 'logs'
    logs.mkdir(parents=True, exist_ok=True)

    print('Thalor long soak launcher')
    print(f'Repo:   {repo}')
    print(f'Config: {config}')
    print(f'Python: {py}')
    print('\nImportant operational rule: do not restore old runs.zip before this flow. Fresh provider-probe must win over stale green artifacts.')

    probe_cmd = [
        py, '-m', 'natbin.runtime_app',
        'provider-probe', '--repo-root', str(repo), '--config', str(config), '--all-scopes', '--json',
    ]

    attempts = 0
    while True:
        attempts += 1
        rc, _stdout, _stderr, payload = run_capture('provider_probe', probe_cmd, cwd=repo, env=env, log_dir=logs)
        ok = isinstance(payload, dict) and bool(payload.get('ok')) and bool(((payload.get('shared_provider_session') or {}).get('ok')))
        if rc == 0 and ok:
            print(f'Fresh provider session is healthy on attempt {attempts}.')
            break
        if args.probe_max_attempts and attempts >= args.probe_max_attempts:
            raise SystemExit(f'provider-probe did not succeed after {attempts} attempts')
        print(f'Provider still unavailable. Sleeping {args.probe_sleep_sec}s before retry...')
        time.sleep(max(1, int(args.probe_sleep_sec)))

    warmup_cmd = [
        py, str((repo / 'scripts' / 'tools' / 'portfolio_canary_warmup.py').resolve()),
        '--repo-root', str(repo), '--config', str(config), '--all-scopes', '--refresh-stability', '--json',
    ]
    _rc, _stdout, _stderr, payload = run_capture('portfolio_canary_warmup', warmup_cmd, cwd=repo, env=env, log_dir=logs)
    require_ok(payload, name='portfolio_canary_warmup')

    stability_cmd = [
        py, '-m', 'natbin.runtime_app',
        'provider-stability-report', '--repo-root', str(repo), '--config', str(config), '--all-scopes', '--active-provider-probe', '--refresh-probe', '--json',
    ]
    _rc, _stdout, _stderr, payload = run_capture('provider_stability_report', stability_cmd, cwd=repo, env=env, log_dir=logs)
    require_ok(payload, name='provider_stability_report')

    governor_cmd = [
        py, '-m', 'natbin.runtime_app',
        'provider-session-governor', '--repo-root', str(repo), '--config', str(config), '--all-scopes', '--active-provider-probe', '--refresh-stability', '--json',
    ]
    _rc, _stdout, _stderr, payload = run_capture('provider_session_governor', governor_cmd, cwd=repo, env=env, log_dir=logs)
    require_ok(payload, name='provider_session_governor')

    if not args.skip_signal_proof:
        signal_cmd = [
            py, str((repo / 'scripts' / 'tools' / 'portfolio_canary_signal_proof.py').resolve()),
            '--repo-root', str(repo), '--config', str(config), '--all-scopes', '--json',
        ]
        _rc, _stdout, _stderr, payload = run_capture('portfolio_canary_signal_proof', signal_cmd, cwd=repo, env=env, log_dir=logs)
        require_ok(payload, name='portfolio_canary_signal_proof')

    if not args.skip_go_no_go:
        go_cmd = [
            py, str((repo / 'scripts' / 'tools' / 'canary_go_no_go.py').resolve()),
            '--repo-root', str(repo), '--config', str(config), '--json',
        ]
        _rc, _stdout, _stderr, payload = run_capture('canary_go_no_go', go_cmd, cwd=repo, env=env, log_dir=logs)
        require_ok(payload, name='canary_go_no_go')
        decision = str((payload or {}).get('decision') or '')
        if decision.startswith('NO_GO'):
            raise SystemExit(f'canary_go_no_go returned {decision}')

    observe_cmd = [
        py, '-m', 'natbin.runtime_app',
        'portfolio', 'observe', '--repo-root', str(repo), '--config', str(config),
        '--topk', str(int(args.topk)), '--lookback-candles', str(int(args.lookback_candles)),
        '--quota-aware-sleep', '--precheck-market-context',
    ]
    if int(args.observe_max_cycles) > 0:
        observe_cmd.extend(['--max-cycles', str(int(args.observe_max_cycles))])

    rc = run_stream('portfolio_observe', observe_cmd, cwd=repo, env=env, log_dir=logs)
    print(f'portfolio observe finished with return code {rc}')

    for name, extra_cmd in [
        ('capture_portfolio_canary_bundle', [py, str((repo / 'scripts' / 'tools' / 'capture_portfolio_canary_bundle.py').resolve()), '--repo-root', str(repo), '--config', str(config)]),
        ('capture_canary_closure_bundle', [py, str((repo / 'scripts' / 'tools' / 'capture_canary_closure_bundle.py').resolve()), '--repo-root', str(repo), '--config', str(config)]),
    ]:
        try:
            run_capture(name, extra_cmd, cwd=repo, env=env, log_dir=logs, timeout=1800)
        except Exception as exc:  # noqa: BLE001
            print(f'{name} failed after observe finished: {exc}', file=sys.stderr)

    return rc


if __name__ == '__main__':
    raise SystemExit(main())
