#!/usr/bin/env python
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


def _ok(msg: str) -> None:
    print(f"[smoke][OK] {msg}")


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from natbin.runtime_observability import (
        append_incident_event,
        build_incident_from_decision,
        latest_decision_snapshot_path,
        write_detailed_decision_snapshot,
        write_latest_decision_snapshot,
    )

    row = {
        'asset': 'EURUSD-OTC',
        'interval_sec': 300,
        'day': '2026-03-03',
        'ts': 1772552400,
        'dt_local': '2026-03-03 12:40:00',
        'action': 'CALL',
        'reason': 'topk_emit',
        'blockers': '',
        'executed_today': 1,
        'budget_left': 2,
        'gate_mode': 'cp_meta_iso',
        'gate_fail_closed': 0,
        'market_context_stale': 0,
        'market_context_fail_closed': 1,
        'regime_ok': 1,
        'threshold': 0.02,
        'thresh_on': 'ev',
        'k': 3,
        'rank_in_day': 1,
        'payout': 0.85,
        'ev': 0.12,
        'proba_up': 0.56,
        'conf': 0.56,
        'score': 0.61,
        'meta_model': 'hgb',
        'model_version': 'smoke',
    }

    with tempfile.TemporaryDirectory() as td:
        runs = Path(td)
        latest = write_latest_decision_snapshot(row, out_dir=runs)
        if not latest.exists():
            raise SystemExit('latest decision snapshot not written')
        payload = json.loads(latest.read_text(encoding='utf-8'))
        if payload.get('action') != 'CALL':
            raise SystemExit('latest decision snapshot action mismatch')
        _ok('latest decision snapshot ok')

        detailed = write_detailed_decision_snapshot(row, out_dir=runs)
        if detailed is None or not detailed.exists():
            raise SystemExit('detailed decision snapshot not written for trade emit')
        _ok('detailed decision snapshot ok')

        incident = build_incident_from_decision(row)
        if not incident or incident.get('incident_type') != 'trade_emit':
            raise SystemExit('trade emit incident not classified')
        path = append_incident_event(incident, out_dir=runs)
        if not path.exists():
            raise SystemExit('incident jsonl not written')
        lines = [ln for ln in path.read_text(encoding='utf-8').splitlines() if ln.strip()]
        if len(lines) != 1:
            raise SystemExit('incident jsonl line count mismatch')
        _ok('incident append ok')

        hold_row = dict(row)
        hold_row.update({'action': 'HOLD', 'reason': 'cp_reject'})
        detailed2 = write_detailed_decision_snapshot(hold_row, out_dir=runs)
        if detailed2 is not None:
            raise SystemExit('detailed snapshot should not be written for cp_reject hold')
        _ok('non-serious hold does not emit detailed snapshot')

        stale_row = dict(row)
        stale_row.update({'action': 'HOLD', 'reason': 'market_context_stale'})
        incident2 = build_incident_from_decision(stale_row)
        if not incident2 or incident2.get('incident_type') != 'market_context_stale':
            raise SystemExit('stale market context incident not classified')
        _ok('serious hold incident classification ok')

    print('[smoke] ALL OK')


if __name__ == '__main__':
    main()
