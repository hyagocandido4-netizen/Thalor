from __future__ import annotations

import sys

from natbin.dashboard.analytics import build_control_display


def run() -> int:
    practice_payload = {
        'severity': 'error',
        'ok': False,
        'execution': {'enabled': False, 'mode': 'disabled'},
        'controlled_scope': {
            'multi_asset_enabled': True,
            'assets_configured': 6,
            'portfolio_topk_total': 3,
        },
        'doctor': {'blockers': ['dataset_ready', 'market_context']},
    }
    doctor_payload = {
        'severity': 'error',
        'ok': False,
        'blockers': ['dataset_ready', 'market_context'],
    }
    display = build_control_display({'practice': practice_payload, 'doctor': doctor_payload})
    if str((display.get('practice') or {}).get('label')) != 'N/A':
        print('ERROR practice label not normalized to N/A', file=sys.stderr)
        return 1
    if str((display.get('doctor') or {}).get('label')) != 'WAIT DATA':
        print('ERROR doctor label not normalized to WAIT DATA', file=sys.stderr)
        return 1
    print('OK dashboard_package_3d_status_context_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
