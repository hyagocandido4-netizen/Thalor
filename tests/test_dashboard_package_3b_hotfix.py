from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from natbin.dashboard.app import _normalize_rows_for_dataframe


def test_normalize_rows_for_dataframe_serializes_nested_payloads_and_paths(tmp_path: Path) -> None:
    rows = [
        {
            'source': 'incident_action',
            'payload': {'action': {'kind': 'review', 'count': 2}},
            'details': ['a', 'b'],
            'path': tmp_path / 'runs' / 'alerts.json',
            'created_at': datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
            'ok': True,
            'score': 0.5,
        }
    ]

    normalized = _normalize_rows_for_dataframe(rows)

    assert len(normalized) == 1
    row = normalized[0]
    assert row['payload'] == '{"action": {"count": 2, "kind": "review"}}'
    assert row['details'] == '["a", "b"]'
    assert row['path'].endswith('/runs/alerts.json')
    assert row['created_at'] == '2026-03-25T12:00:00+00:00'
    assert row['ok'] is True
    assert row['score'] == 0.5


def test_dashboard_app_source_does_not_use_deprecated_use_container_width() -> None:
    app_path = Path(__file__).resolve().parents[1] / 'src' / 'natbin' / 'dashboard' / 'app.py'
    source = app_path.read_text(encoding='utf-8')

    assert 'use_container_width' not in source
    assert "width='stretch'" in source
