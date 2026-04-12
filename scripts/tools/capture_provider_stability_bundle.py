from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from _capture_json import write_json_summary

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _repo_root_from_script() -> Path:
    return ROOT


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _find_python(repo_root: Path) -> str:
    for candidate in (repo_root / '.venv' / 'Scripts' / 'python.exe', repo_root / '.venv' / 'bin' / 'python'):
        if candidate.exists():
            return str(candidate)
    return shutil.which('python') or sys.executable


def _default_config(repo_root: Path) -> Path:
    for rel in ('config/practice_portfolio_canary.yaml', 'config/live_controlled_practice.yaml', 'config/base.yaml'):
        candidate = repo_root / rel
        if candidate.exists():
            return candidate.resolve()
    return (repo_root / 'config' / 'base.yaml').resolve()


def _build_env(repo_root: Path, config_path: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    src = repo_root / 'src'
    existing = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = str(src) if not existing else f"{src}{os.pathsep}{existing}"
    env['THALOR_REPO_ROOT'] = str(repo_root)
    if config_path is not None:
        env['THALOR_CONFIG'] = str(config_path)
        env['THALOR_CONFIG_PATH'] = str(config_path)
    return env


def _safe_text(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _zip_dir(source_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(source_dir.rglob('*')):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(source_dir)))


def _run_one(*, name: str, argv: list[str], cwd: Path, env: dict[str, str], timeout_sec: int, output_dir: Path) -> dict[str, Any]:
    started = _now_utc()
    started_monotonic = time.monotonic()
    timed_out = False
    stdout_text = ''
    stderr_text = ''
    returncode = None
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=max(1, int(timeout_sec)))
        returncode = int(proc.returncode)
        stdout_text = _safe_text(proc.stdout)
        stderr_text = _safe_text(proc.stderr)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = 124
        stdout_text = _safe_text(exc.stdout)
        stderr_text = _safe_text(exc.stderr)
    duration_sec = round(time.monotonic() - started_monotonic, 3)
    finished = _now_utc()

    step_dir = output_dir / name
    step_dir.mkdir(parents=True, exist_ok=True)
    _write_text(step_dir / 'stdout.txt', stdout_text)
    _write_text(step_dir / 'stderr.txt', stderr_text)
    _write_text(step_dir / 'command.txt', ' '.join(argv) + '\n')
    _write_text(step_dir / 'exit_code.txt', f'{returncode}\n')
    parsed_summary = write_json_summary(base_dir=step_dir, stdout_text=stdout_text)
    result = {
        'name': name,
        'command': argv,
        'started_at_utc': started,
        'finished_at_utc': finished,
        'duration_sec': duration_sec,
        'timeout_sec': timeout_sec,
        'timed_out': timed_out,
        'returncode': returncode,
        'parsed_summary': parsed_summary,
    }
    _write_text(step_dir / 'manifest.json', json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result


def _last_json(bundle_dir: Path, name: str) -> dict[str, Any] | None:
    path = bundle_dir / name / 'last_json.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _bundle_summary(bundle_dir: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
    provider = _last_json(bundle_dir, 'provider_probe_all_scopes') or {}
    stability = _last_json(bundle_dir, 'provider_stability_report') or {}
    governor = _last_json(bundle_dir, 'provider_session_governor') or {}
    warmup = _last_json(bundle_dir, 'portfolio_canary_warmup') or {}
    scan = _last_json(bundle_dir, 'evidence_window_scan') or {}
    signal_scan = _last_json(bundle_dir, 'portfolio_canary_signal_scan') or {}
    provider_summary = provider.get('summary') if isinstance(provider, dict) else {}
    stability_summary = stability.get('summary') if isinstance(stability, dict) else {}
    signal_summary = signal_scan.get('summary') if isinstance(signal_scan, dict) else {}
    return {
        'provider_probe_all_scopes': next((r.get('parsed_summary') for r in results if r.get('name') == 'provider_probe_all_scopes'), None),
        'portfolio_canary_warmup': next((r.get('parsed_summary') for r in results if r.get('name') == 'portfolio_canary_warmup'), None),
        'evidence_window_scan': next((r.get('parsed_summary') for r in results if r.get('name') == 'evidence_window_scan'), None),
        'portfolio_canary_signal_scan': next((r.get('parsed_summary') for r in results if r.get('name') == 'portfolio_canary_signal_scan'), None),
        'provider_stability_report': next((r.get('parsed_summary') for r in results if r.get('name') == 'provider_stability_report'), None),
        'provider_session_governor': next((r.get('parsed_summary') for r in results if r.get('name') == 'provider_session_governor'), None),
        'provider_session_shield': {
            'stability_state': stability.get('stability_state'),
            'severity': stability.get('severity'),
            'provider_ready_scopes': (stability_summary or {}).get('provider_ready_scopes', (provider_summary or {}).get('provider_ready_scopes')),
            'hard_blockers': (stability_summary or {}).get('hard_blockers'),
            'transient_noise_categories': (stability_summary or {}).get('transient_noise_categories'),
            'recorded_issue_events': (stability_summary or {}).get('recorded_issue_events'),
            'signal_actionable_scopes': (stability_summary or {}).get('signal_actionable_scopes', (signal_summary or {}).get('actionable_scopes')),
            'signal_healthy_waiting': (stability_summary or {}).get('signal_healthy_waiting', (signal_summary or {}).get('healthy_waiting_signal')),
            'recommended_scope': (scan.get('best_scope') or scan.get('recommended_scope')) if isinstance(scan, dict) else None,
            'warmup_ok': warmup.get('ok') if isinstance(warmup, dict) else None,
            'governor_mode': (governor.get('summary') or {}).get('governor_mode') if isinstance(governor, dict) else None,
            'governor_sleep_between_scopes_ms': (governor.get('summary') or {}).get('sleep_between_scopes_ms') if isinstance(governor, dict) else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Provider Session Shield bundle: provider-probe + canary warmup + scan + signal scan + stability report em um ZIP único.')
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--config', default=None)
    parser.add_argument('--out-dir', default=None)
    parser.add_argument('--timeout-sec', type=int, default=2400)
    parser.add_argument('--sample-candles', type=int, default=3)
    parser.add_argument('--recorded-event-limit', type=int, default=200)
    parser.add_argument('--skip-warmup', action='store_true')
    parser.add_argument('--skip-signal-scan', action='store_true')
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script()
    config_path = Path(args.config).resolve() if args.config else _default_config(repo_root)
    python_exe = _find_python(repo_root)
    env = _build_env(repo_root, config_path)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (repo_root / 'runs' / 'debug' / 'bundles')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    bundle_dir = out_dir / f'{timestamp}_provider_stability_bundle'
    bundle_dir.mkdir(parents=True, exist_ok=True)

    def runtime_cmd(*cmd_args: str) -> list[str]:
        cmd = [python_exe, '-m', 'natbin.runtime_app', '--repo-root', str(repo_root)]
        if config_path is not None:
            cmd.extend(['--config', str(config_path)])
        cmd.extend(cmd_args)
        return cmd

    def tool_cmd(script_name: str, *tool_args: str) -> list[str]:
        cmd = [python_exe, str((repo_root / 'scripts' / 'tools' / script_name).resolve()), '--repo-root', str(repo_root)]
        if config_path is not None:
            cmd.extend(['--config', str(config_path)])
        cmd.extend(tool_args)
        return cmd

    commands: list[tuple[str, list[str], int]] = [
        ('status', runtime_cmd('status', '--json'), min(args.timeout_sec, 300)),
        ('provider_probe_all_scopes', runtime_cmd('provider-probe', '--all-scopes', '--json', '--sample-candles', str(args.sample_candles)), min(args.timeout_sec, 900)),
    ]
    if not args.skip_warmup:
        commands.append(('portfolio_canary_warmup', [python_exe, str((repo_root / 'scripts' / 'tools' / 'portfolio_canary_warmup.py').resolve()), '--repo-root', str(repo_root), '--config', str(config_path), '--all-scopes', '--json', '--timeout-sec', str(min(args.timeout_sec, 900))], min(args.timeout_sec, 1800)))
    commands.append(('evidence_window_scan', runtime_cmd('evidence-window-scan', '--all-scopes', '--active-provider-probe', '--json', '--sample-candles', str(args.sample_candles)), min(args.timeout_sec, 900)))
    if not args.skip_signal_scan:
        commands.append(('portfolio_canary_signal_scan', tool_cmd('portfolio_canary_signal_scan.py', '--all-scopes', '--json', '--timeout-sec', str(min(args.timeout_sec, 600))), min(args.timeout_sec, 1800)))
    commands.append(('provider_stability_report', tool_cmd('provider_stability_report.py', '--all-scopes', '--active-provider-probe', '--json', '--recorded-event-limit', str(args.recorded_event_limit), '--sample-candles', str(args.sample_candles)), min(args.timeout_sec, 900)))
    commands.append(('provider_session_governor', tool_cmd('provider_session_governor.py', '--all-scopes', '--active-provider-probe', '--json', '--recorded-event-limit', str(args.recorded_event_limit), '--sample-candles', str(args.sample_candles)), min(args.timeout_sec, 900)))

    started = _now_utc()
    started_monotonic = time.monotonic()
    results: list[dict[str, Any]] = []
    for name, argv, timeout_sec in commands:
        results.append(_run_one(name=name, argv=argv, cwd=repo_root, env=env, timeout_sec=timeout_sec, output_dir=bundle_dir))

    finished = _now_utc()
    duration_sec = round(time.monotonic() - started_monotonic, 3)
    bundle_summary = _bundle_summary(bundle_dir, results)
    _write_text(bundle_dir / 'bundle_summary.json', json.dumps(bundle_summary, ensure_ascii=False, indent=2) + '\n')
    manifest = {
        'kind': 'provider_stability_bundle',
        'repo_root': str(repo_root),
        'config_path': str(config_path) if config_path else None,
        'python_executable': python_exe,
        'started_at_utc': started,
        'finished_at_utc': finished,
        'duration_sec': duration_sec,
        'bundle_dir': str(bundle_dir),
        'commands': results,
        'bundle_summary': bundle_summary,
        'dangerous': False,
        'note': 'Bundle read-only: categoriza ruído transitório do provider e separa blocker estrutural sem submeter ordens.',
    }
    _write_text(bundle_dir / 'manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    zip_path = out_dir / f'{timestamp}_provider_stability_bundle.zip'
    _zip_dir(bundle_dir, zip_path)

    print(json.dumps({
        'ok': True,
        'kind': 'provider_stability_bundle_result',
        'dangerous': False,
        'zip_path': str(zip_path),
        'bundle_dir': str(bundle_dir),
        'commands': [{'name': r['name'], 'returncode': r['returncode'], 'timed_out': r['timed_out'], 'parsed_summary': r.get('parsed_summary')} for r in results],
        'bundle_summary': bundle_summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
