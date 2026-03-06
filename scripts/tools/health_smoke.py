#!/usr/bin/env python
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.runtime_health import build_health_payload, build_status_payload, write_health_payload, write_status_payload



def main() -> int:
    with tempfile.TemporaryDirectory(prefix='thalor_health_smoke_') as td:
        out_dir = Path(td) / 'runs'
        status = build_status_payload(
            asset='EURUSD-OTC',
            interval_sec=300,
            phase='precheck',
            state='blocked',
            message='kill_switch',
            next_wake_utc='2026-03-04T10:00:03+00:00',
            sleep_reason='kill_switch',
            report={'phase': 'precheck', 'ok': True},
            quota={'kind': 'open'},
            failsafe={'kill_switch_active': True},
            market_context={'stale': False},
        )
        health = build_health_payload(
            asset='EURUSD-OTC',
            interval_sec=300,
            state='blocked',
            message='kill_switch',
            quota={'kind': 'open'},
            failsafe={'kill_switch_active': True},
            market_context={'stale': False},
            last_cycle_ok=True,
        )
        status_path = write_status_payload(asset='EURUSD-OTC', interval_sec=300, payload=status, out_dir=out_dir)
        health_path = write_health_payload(asset='EURUSD-OTC', interval_sec=300, payload=health, out_dir=out_dir)
        assert status_path.exists(), status_path
        assert health_path.exists(), health_path
        status_loaded = json.loads(status_path.read_text(encoding='utf-8'))
        health_loaded = json.loads(health_path.read_text(encoding='utf-8'))
        assert status_loaded['phase'] == 'precheck'
        assert status_loaded['failsafe']['kill_switch_active'] is True
        assert health_loaded['state'] == 'blocked'
        assert health_loaded['last_cycle_ok'] is True
        print('[smoke][OK] runtime health payload builders and writers ok')
    print('[smoke] ALL OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
