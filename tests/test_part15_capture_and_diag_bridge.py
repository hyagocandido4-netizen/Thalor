from __future__ import annotations

import json
from pathlib import Path

from scripts.tools._capture_json import parse_json_events, write_json_summary
from natbin.control.commands import diag_suite_payload


def test_parse_json_events_returns_last_json_summary(tmp_path: Path) -> None:
    text = '\n'.join([
        '{"phase":"auto_cycle","ok":true}',
        'plain-text-line',
        '{"kind":"practice_preflight","ok":true,"severity":"ok","ready_for_long_practice":true}',
    ])
    summary = write_json_summary(base_dir=tmp_path, stdout_text=text)
    assert summary['json_event_count'] == 2
    assert summary['last_json_kind'] == 'practice_preflight'
    assert summary['last_json_ok'] is True
    assert summary['last_json_severity'] == 'ok'
    assert summary['ready_for_long_practice'] is True
    assert (tmp_path / 'last_json.json').exists()
    assert (tmp_path / 'json_events.jsonl').exists()


def test_parse_json_events_skips_non_json_noise() -> None:
    text = '[IQ][connect] attempt 1 failed\n{"kind":"doctor","ok":true,"severity":"ok"}\n'
    events = parse_json_events(text)
    assert len(events) == 1
    assert events[0]['kind'] == 'doctor'


def test_diag_suite_payload_accepts_heal_breaker(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_build_diag_suite_payload(**kwargs):
        captured.update(kwargs)
        return {'kind': 'diag_suite', 'ok': True, 'severity': 'ok'}

    import natbin.control.commands as module

    monkeypatch.setattr(module, 'build_diag_suite_payload', fake_build_diag_suite_payload, raising=False)

    # patch local import inside function by replacing module attribute after import fallback
    import types
    fake_ops = types.SimpleNamespace(build_diag_suite_payload=fake_build_diag_suite_payload)
    monkeypatch.setitem(__import__('sys').modules, 'natbin.ops.diag_suite', fake_ops)

    payload = diag_suite_payload(heal_breaker=True, breaker_stale_after_sec=123, heal_market_context=True, heal_control_freshness=True)
    assert payload['ok'] is True
    assert captured['heal_breaker'] is True
    assert captured['breaker_stale_after_sec'] == 123
    assert captured['heal_market_context'] is True
    assert captured['heal_control_freshness'] is True
