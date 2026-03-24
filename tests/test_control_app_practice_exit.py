from __future__ import annotations

from natbin.control import app as control_app


def test_practice_command_returns_zero_on_warn_only_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        control_app,
        'practice_payload',
        lambda **kwargs: {
            'ok': True,
            'severity': 'warn',
            'ready_for_practice': False,
        },
    )
    rc = control_app.main(['practice', '--json'])
    assert rc == 0


def test_practice_command_returns_nonzero_on_error_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        control_app,
        'practice_payload',
        lambda **kwargs: {
            'ok': False,
            'severity': 'error',
            'ready_for_practice': False,
        },
    )
    rc = control_app.main(['practice', '--json'])
    assert rc == 2
