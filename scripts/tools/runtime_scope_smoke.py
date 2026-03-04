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

from natbin.runtime_scope import (
    build_scope,
    decision_latest_path,
    decision_snapshot_path,
    effective_env_path,
    incident_jsonl_path,
    live_signals_csv_path,
    loop_status_path,
    market_context_path,
    transcript_log_path,
)
from natbin.runtime_perf import load_json_cached, write_text_if_changed


def _ok(msg: str) -> None:
    print(f'[smoke][OK] {msg}')


def _fail(msg: str) -> None:
    print(f'[smoke][FAIL] {msg}')
    raise SystemExit(2)


def test_scope_paths() -> None:
    scope = build_scope('EURUSD-OTC', 300)
    if scope.scope_tag != 'EURUSD-OTC_300s':
        _fail(f'unexpected scope_tag: {scope.scope_tag}')
    if effective_env_path(asset=scope.asset, interval_sec=scope.interval_sec).name != 'effective_env_EURUSD-OTC_300s.json':
        _fail('effective_env path mismatch')
    if market_context_path(asset=scope.asset, interval_sec=scope.interval_sec).name != 'market_context_EURUSD-OTC_300s.json':
        _fail('market_context path mismatch')
    if loop_status_path(asset=scope.asset, interval_sec=scope.interval_sec).name != 'observe_loop_auto_status_EURUSD-OTC_300s.json':
        _fail('loop status path mismatch')
    if live_signals_csv_path(day='2026-03-03', asset=scope.asset, interval_sec=scope.interval_sec).name != 'live_signals_v2_20260303_EURUSD-OTC_300s.csv':
        _fail('live_signals csv path mismatch')
    if transcript_log_path(day='2026-03-03', asset=scope.asset, interval_sec=scope.interval_sec).name != 'observe_loop_auto_EURUSD-OTC_300s_20260303.log':
        _fail('transcript path mismatch')
    if decision_latest_path(asset=scope.asset, interval_sec=scope.interval_sec).name != 'decision_latest_EURUSD-OTC_300s.json':
        _fail('latest decision path mismatch')
    if decision_snapshot_path(day='2026-03-03', asset=scope.asset, interval_sec=scope.interval_sec, ts=123).name != 'decision_20260303_EURUSD-OTC_300s_123.json':
        _fail('decision snapshot path mismatch')
    if incident_jsonl_path(day='2026-03-03', asset=scope.asset, interval_sec=scope.interval_sec).name != 'incidents_20260303_EURUSD-OTC_300s.jsonl':
        _fail('incident path mismatch')
    _ok('runtime scope path resolution ok')


def test_json_cache_and_write_if_changed() -> None:
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'sample.json'
        wrote = write_text_if_changed(p, json.dumps({'v': 1}))
        if not wrote:
            _fail('expected first write to happen')
        wrote = write_text_if_changed(p, json.dumps({'v': 1}))
        if wrote:
            _fail('expected unchanged write to be skipped')
        a = load_json_cached(p)
        if a != {'v': 1}:
            _fail(f'unexpected cached json: {a}')
        wrote = write_text_if_changed(p, json.dumps({'v': 2}))
        if not wrote:
            _fail('expected changed write to happen')
        b = load_json_cached(p)
        if b != {'v': 2}:
            _fail(f'cache did not invalidate after file change: {b}')
    _ok('runtime perf cache helpers ok')


def main() -> None:
    test_scope_paths()
    test_json_cache_and_write_if_changed()
    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
