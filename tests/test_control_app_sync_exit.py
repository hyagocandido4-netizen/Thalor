from __future__ import annotations

from natbin.control import app as control_app


def test_sync_command_returns_zero_on_warn_only_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        control_app,
        'sync_payload',
        lambda **kwargs: {
            'ok': True,
            'severity': 'warn',
        },
    )
    rc = control_app.main(['sync', '--json'])
    assert rc == 0


def test_sync_command_returns_nonzero_on_error_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        control_app,
        'sync_payload',
        lambda **kwargs: {
            'ok': False,
            'severity': 'error',
        },
    )
    rc = control_app.main(['sync', '--json'])
    assert rc == 2
