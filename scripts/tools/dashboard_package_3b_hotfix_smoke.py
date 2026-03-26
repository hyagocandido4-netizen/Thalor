from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from natbin.dashboard.app import _normalize_rows_for_dataframe


def main() -> int:
    rows = [
        {
            'source': 'execution',
            'payload': {'intent': {'asset': 'EURUSD-OTC'}, 'status': 'pending'},
            'details': ['alpha', 'beta'],
            'path': Path('runs') / 'logs' / 'account_protection.jsonl',
            'created_at': datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
        }
    ]
    normalized = _normalize_rows_for_dataframe(rows)
    row = normalized[0]
    if row['payload'] != '{"intent": {"asset": "EURUSD-OTC"}, "status": "pending"}':
        raise SystemExit('FAIL: payload was not normalized to a JSON string')
    if row['details'] != '["alpha", "beta"]':
        raise SystemExit('FAIL: list payload was not normalized to a JSON string')
    if row['path'] != 'runs/logs/account_protection.jsonl':
        raise SystemExit('FAIL: path payload was not normalized to POSIX text')
    app_source = (Path(__file__).resolve().parents[2] / 'src' / 'natbin' / 'dashboard' / 'app.py').read_text(encoding='utf-8')
    if 'use_container_width' in app_source:
        raise SystemExit('FAIL: deprecated use_container_width is still present in dashboard app')
    print('OK dashboard_package_3b_hotfix_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
