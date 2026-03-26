from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_dashboard_app_can_be_loaded_from_script_path_without_package_context(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = (repo_root / 'src').resolve()
    app_path = repo_root / 'src' / 'natbin' / 'dashboard' / 'app.py'

    cleaned = [p for p in list(sys.path) if Path(p or '.').resolve() != src_root]
    monkeypatch.setattr(sys, 'path', cleaned)

    spec = importlib.util.spec_from_file_location('dashboard_script_import', app_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert callable(getattr(module, 'run', None))
    assert str(src_root) in sys.path
