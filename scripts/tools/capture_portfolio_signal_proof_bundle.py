
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _now_utc() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _repo_root_from_script() -> Path:
    return ROOT.resolve()


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


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _extract_json_events(stdout_text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    events: list[dict[str, Any]] = []
    idx = 0
    text = stdout_text or ''
    length = len(text)
    while idx < length:
        start = text.find('{', idx)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text[start:])
        except Exception:
            idx = start + 1
            continue
        if isinstance(obj, dict):
            events.append({'index': len(events), 'start': start, 'end': start + end, 'type': 'dict', 'kind': obj.get('kind'), 'ok': obj.get('ok'), 'severity': obj.get('severity'), 'payload': obj})
        idx = start + end
    return events


def write_json_summary(*, base_dir: Path, stdout_text: str) -> dict[str, Any]:
    events = _extract_json_events(stdout_text)
    json_events_path = base_dir / 'json_events.jsonl'
    last_json_path = base_dir / 'last_json.json'
    with json_events_path.open('w', encoding='utf-8') as fh:
        for event in events:
            payload = dict(event)
            payload.pop('payload', None)
            fh.write(json.dumps(payload, ensure_ascii=False) + '\n')
    last_json = events[-1]['payload'] if events else None
    if isinstance(last_json, dict):
        last_json_path.write_text(json.dumps(last_json, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {
        'json_event_count': len(events),
        'events': [{k: v for k, v in event.items() if k != 'payload'} for event in events],
        'last_json_kind': last_json.get('kind') if isinstance(last_json, dict) else None,
        'last_json_ok': last_json.get('ok') if isinstance(last_json, dict) else None,
        'last_json_severity': last_json.get('severity') if isinstance(last_json, dict) else None,
    }
    (base_dir / 'parsed_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


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
    warmup = _last_json(bundle_dir, 'portfolio_canary_warmup') or {}
    scan = _last_json(bundle_dir, 'evidence_window_scan') or {}
    signal_proof = _last_json(bundle_dir, 'portfolio_canary_signal_proof') or {}
    portfolio_status = _last_json(bundle_dir, 'portfolio_status') or {}
    production_gate = _last_json(bundle_dir, 'production_gate_all_scopes') or {}
    recommended_scope = scan.get('recommended_scope') or scan.get('best_scope')
    return {
        'portfolio_canary_warmup': next((r.get('parsed_summary') for r in results if r.get('name') == 'portfolio_canary_warmup'), None),
        'evidence_window_scan': next((r.get('parsed_summary') for r in results if r.get('name') == 'evidence_window_scan'), None),
        'portfolio_canary_signal_proof': next((r.get('parsed_summary') for r in results if r.get('name') == 'portfolio_canary_signal_proof'), None),
        'portfolio_status': next((r.get('parsed_summary') for r in results if r.get('name') == 'portfolio_status'), None),
        'production_gate_all_scopes': next((r.get('parsed_summary') for r in results if r.get('name') == 'production_gate_all_scopes'), None),
        'portfolio_signal_proof': {
            'warmup_effective_ready_scopes': ((warmup.get('summary') or {}).get('effective_ready_scopes')) if isinstance(warmup, dict) else None,
            'warmup_scope_count': ((warmup.get('summary') or {}).get('scope_count')) if isinstance(warmup, dict) else None,
            'scan_severity': scan.get('severity') if isinstance(scan, dict) else None,
            'best_scope': recommended_scope,
            'signal_proof_severity': signal_proof.get('severity') if isinstance(signal_proof, dict) else None,
            'actionable_scopes': ((signal_proof.get('summary') or {}).get('actionable_scopes')) if isinstance(signal_proof, dict) else None,
            'watch_scopes': ((signal_proof.get('summary') or {}).get('watch_scopes')) if isinstance(signal_proof, dict) else None,
            'hold_scopes': ((signal_proof.get('summary') or {}).get('hold_scopes')) if isinstance(signal_proof, dict) else None,
            'cp_meta_missing_scopes': ((signal_proof.get('summary') or {}).get('cp_meta_missing_scopes')) if isinstance(signal_proof, dict) else None,
            'regime_block_scopes': ((signal_proof.get('summary') or {}).get('regime_block_scopes')) if isinstance(signal_proof, dict) else None,
            'recommended_action': ((signal_proof.get('summary') or {}).get('recommended_action')) if isinstance(signal_proof, dict) else None,
            'ready_for_cycle_all_scopes': production_gate.get('ready_for_all_scopes') if isinstance(production_gate, dict) else None,
            'portfolio_asset_count': ((portfolio_status.get('multi_asset') or {}).get('asset_count')) if isinstance(portfolio_status, dict) else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='Gera um ZIP read-only de signal proof do portfolio canary: warmup + scan + signal proof all-scopes.')
    parser.add_argument('--repo-root', default=None)
    parser.add_argument('--config', default=None)
    parser.add_argument('--out-dir', default=None, help='Default: runs/debug/bundles')
    parser.add_argument('--timeout-sec', type=int, default=1800)
    parser.add_argument('--sample-candles', type=int, default=3)
    parser.add_argument('--top-n', type=int, default=3)
    parser.add_argument('--skip-warmup', action='store_true')
    parser.add_argument('--skip-production-gate', action='store_true')
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else _repo_root_from_script()
    config_path = Path(args.config).resolve() if args.config else _default_config(repo_root)
    python_exe = _find_python(repo_root)
    env = _build_env(repo_root, config_path)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (repo_root / 'runs' / 'debug' / 'bundles')
    timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    bundle_dir = out_dir / f'{timestamp}_portfolio_signal_proof_bundle'
    bundle_dir.mkdir(parents=True, exist_ok=True)

    def runtime_cmd(*cmd_args: str) -> list[str]:
        cmd = [python_exe, '-m', 'natbin.runtime_app', '--repo-root', str(repo_root)]
        if config_path is not None:
            cmd.extend(['--config', str(config_path)])
        cmd.extend(cmd_args)
        return cmd

    warmup_cmd = [python_exe, str((repo_root / 'scripts' / 'tools' / 'portfolio_canary_warmup.py').resolve()), '--repo-root', str(repo_root), '--config', str(config_path), '--all-scopes', '--json', '--timeout-sec', str(min(args.timeout_sec, 900))]
    signal_proof_cmd = [python_exe, str((repo_root / 'scripts' / 'tools' / 'portfolio_canary_signal_proof.py').resolve()), '--repo-root', str(repo_root), '--config', str(config_path), '--all-scopes', '--json', '--timeout-sec', str(min(args.timeout_sec, 600))]
    scan_cmd = runtime_cmd('evidence-window-scan', '--all-scopes', '--active-provider-probe', '--json', '--sample-candles', str(args.sample_candles), '--top-n', str(args.top_n))
    gate_cmd = runtime_cmd('production-gate', '--all-scopes', '--probe-provider', '--json', '--sample-candles', str(args.sample_candles))
    status_cmd = runtime_cmd('status', '--json')
    portfolio_status_cmd = runtime_cmd('portfolio', 'status', '--json')

    commands: list[tuple[str, list[str], int]] = []
    if not args.skip_warmup:
        commands.append(('portfolio_canary_warmup', warmup_cmd, min(args.timeout_sec, 3600)))
    commands.append(('status', status_cmd, min(args.timeout_sec, 300)))
    commands.append(('portfolio_status', portfolio_status_cmd, min(args.timeout_sec, 600)))
    commands.append(('evidence_window_scan', scan_cmd, min(args.timeout_sec, 900)))
    if not args.skip_production_gate:
        commands.append(('production_gate_all_scopes', gate_cmd, min(args.timeout_sec, 900)))
    commands.append(('portfolio_canary_signal_proof', signal_proof_cmd, min(args.timeout_sec, 1800)))

    results: list[dict[str, Any]] = []
    for name, argv, timeout_sec in commands:
        results.append(_run_one(name=name, argv=argv, cwd=repo_root, env=env, timeout_sec=timeout_sec, output_dir=bundle_dir))

    summary = _bundle_summary(bundle_dir, results)
    _write_text(bundle_dir / 'bundle_summary.json', json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    _write_text(bundle_dir / 'manifest.json', json.dumps({'kind': 'portfolio_signal_proof_bundle', 'at_utc': _now_utc(), 'repo_root': str(repo_root), 'config_path': str(config_path), 'results': results}, ensure_ascii=False, indent=2) + '\n')
    zip_path = out_dir / f'{timestamp}_portfolio_signal_proof_bundle.zip'
    _zip_dir(bundle_dir, zip_path)
    payload = {
        'ok': True,
        'kind': 'portfolio_signal_proof_bundle_result',
        'dangerous': False,
        'zip_path': str(zip_path),
        'bundle_dir': str(bundle_dir),
        'commands': [{'name': r['name'], 'returncode': r['returncode'], 'timed_out': r['timed_out'], 'parsed_summary': r.get('parsed_summary')} for r in results],
        'bundle_summary': summary.get('portfolio_signal_proof'),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
