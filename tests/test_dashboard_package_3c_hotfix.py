from __future__ import annotations

import subprocess
import sys
import types

from natbin.dashboard.__main__ import main


def test_dashboard_main_returns_zero_on_keyboard_interrupt(monkeypatch, capsys) -> None:
    def _raise(_cmd: list[str]) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, 'call', _raise)
    monkeypatch.setitem(sys.modules, 'streamlit', types.ModuleType('streamlit'))

    code = main(['--repo-root', '.', '--config', 'config/multi_asset.yaml', '--no-browser'])

    captured = capsys.readouterr()
    assert code == 0
    assert 'Dashboard stopped by user.' in captured.err
