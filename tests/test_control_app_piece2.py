from __future__ import annotations

from natbin.control import app as control_app


def test_config_provenance_command_returns_zero_on_ok(monkeypatch) -> None:
    monkeypatch.setattr(control_app, 'config_provenance_payload', lambda **kwargs: {'ok': True, 'severity': 'ok'})
    rc = control_app.main(['config-provenance-audit', '--json'])
    assert rc == 0


def test_config_provenance_command_returns_nonzero_on_error(monkeypatch) -> None:
    monkeypatch.setattr(control_app, 'config_provenance_payload', lambda **kwargs: {'ok': False, 'severity': 'error'})
    rc = control_app.main(['config-provenance-audit', '--json'])
    assert rc == 2


def test_support_bundle_command_returns_zero_when_bundle_created(monkeypatch) -> None:
    monkeypatch.setattr(control_app, 'support_bundle_payload', lambda **kwargs: {'ok': True, 'severity': 'ok', 'zip_path': 'diag_zips/support_bundle_test.zip'})
    rc = control_app.main(['support-bundle', '--json'])
    assert rc == 0
