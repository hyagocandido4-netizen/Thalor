from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.ops.provider_probe import build_provider_probe_payload  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description='Wait until provider-probe passes consecutively before starting a soak.')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', dest='config_path', default=None)
    ap.add_argument('--asset', default=None)
    ap.add_argument('--interval-sec', type=int, default=None)
    ap.add_argument('--all-scopes', action='store_true')
    ap.add_argument('--sample-candles', type=int, default=3)
    ap.add_argument('--market-context-max-age-sec', type=int, default=None)
    ap.add_argument('--consecutive-ok', type=int, default=2)
    ap.add_argument('--sleep-sec', type=float, default=30.0)
    ap.add_argument('--max-attempts', type=int, default=0)
    ap.add_argument('--probe-connect-retries', type=int, default=3)
    ap.add_argument('--probe-connect-sleep-s', type=float, default=1.0)
    ap.add_argument('--probe-connect-timeout-s', type=float, default=None)
    ap.add_argument('--json', action='store_true')
    return ap


def _run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    consecutive_target = max(1, int(args.consecutive_ok))
    sleep_sec = max(0.0, float(args.sleep_sec))
    max_attempts = max(0, int(args.max_attempts))
    previous_env = {
        'THALOR_PROVIDER_PROBE_CONNECT_RETRIES': os.environ.get('THALOR_PROVIDER_PROBE_CONNECT_RETRIES'),
        'THALOR_PROVIDER_PROBE_CONNECT_SLEEP_S': os.environ.get('THALOR_PROVIDER_PROBE_CONNECT_SLEEP_S'),
        'THALOR_PROVIDER_PROBE_CONNECT_TIMEOUT_S': os.environ.get('THALOR_PROVIDER_PROBE_CONNECT_TIMEOUT_S'),
    }
    os.environ['THALOR_PROVIDER_PROBE_CONNECT_RETRIES'] = str(max(1, int(args.probe_connect_retries)))
    os.environ['THALOR_PROVIDER_PROBE_CONNECT_SLEEP_S'] = str(max(0.0, float(args.probe_connect_sleep_s)))
    if args.probe_connect_timeout_s not in (None, ''):
        os.environ['THALOR_PROVIDER_PROBE_CONNECT_TIMEOUT_S'] = str(max(0.1, float(args.probe_connect_timeout_s)))
    else:
        os.environ.pop('THALOR_PROVIDER_PROBE_CONNECT_TIMEOUT_S', None)

    attempts = 0
    consecutive_ok = 0
    last_payload: dict[str, Any] | None = None
    started = time.time()
    try:
        while True:
            attempts += 1
            payload = build_provider_probe_payload(
                repo_root=repo_root,
                config_path=args.config_path,
                asset=args.asset,
                interval_sec=args.interval_sec,
                all_scopes=bool(args.all_scopes),
                active=True,
                sample_candles=max(1, int(args.sample_candles)),
                probe_market_context=True,
                market_context_max_age_sec=args.market_context_max_age_sec,
                write_artifact=True,
            )
            last_payload = payload
            if bool(payload.get('ok')):
                consecutive_ok += 1
                if consecutive_ok >= consecutive_target:
                    return {
                        'kind': 'provider_start_gate',
                        'ok': True,
                        'repo_root': str(repo_root),
                        'config_path': str(args.config_path) if args.config_path else None,
                        'attempts': attempts,
                        'consecutive_ok': consecutive_ok,
                        'required_consecutive_ok': consecutive_target,
                        'elapsed_sec': round(max(0.0, time.time() - started), 3),
                        'last_probe': payload,
                    }
            else:
                consecutive_ok = 0
            if max_attempts > 0 and attempts >= max_attempts:
                return {
                    'kind': 'provider_start_gate',
                    'ok': False,
                    'repo_root': str(repo_root),
                    'config_path': str(args.config_path) if args.config_path else None,
                    'attempts': attempts,
                    'consecutive_ok': consecutive_ok,
                    'required_consecutive_ok': consecutive_target,
                    'elapsed_sec': round(max(0.0, time.time() - started), 3),
                    'last_probe': payload,
                    'reason': 'max_attempts_exhausted',
                }
            if sleep_sec > 0.0:
                time.sleep(sleep_sec)
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = _run(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        state = 'READY' if payload.get('ok') else 'BLOCKED'
        print(f'provider_start_gate={state} attempts={payload.get("attempts")} consecutive_ok={payload.get("consecutive_ok")}/{payload.get("required_consecutive_ok")}')
    return 0 if bool(payload.get('ok')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
