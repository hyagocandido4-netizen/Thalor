from __future__ import annotations

from natbin.control import app as control_app



def test_provider_probe_command_returns_zero_on_warn_only_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        control_app,
        'provider_probe_payload',
        lambda **kwargs: {
            'ok': True,
            'severity': 'warn',
        },
    )
    rc = control_app.main(['provider-probe', '--json'])
    assert rc == 0



def test_provider_probe_command_returns_nonzero_on_error_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        control_app,
        'provider_probe_payload',
        lambda **kwargs: {
            'ok': False,
            'severity': 'error',
        },
    )
    rc = control_app.main(['provider-probe', '--json'])
    assert rc == 2



def test_production_gate_command_returns_zero_on_warn_only_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        control_app,
        'production_gate_payload',
        lambda **kwargs: {
            'ok': True,
            'severity': 'warn',
            'ready_for_all_scopes': False,
        },
    )
    rc = control_app.main(['production-gate', '--json'])
    assert rc == 0



def test_production_gate_command_returns_nonzero_on_error_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        control_app,
        'production_gate_payload',
        lambda **kwargs: {
            'ok': False,
            'severity': 'error',
            'ready_for_all_scopes': False,
        },
    )
    rc = control_app.main(['production-gate', '--json'])
    assert rc == 2
