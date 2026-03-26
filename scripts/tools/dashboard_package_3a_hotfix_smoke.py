from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    src_root = (repo_root / 'src').resolve()
    app_path = src_root / 'natbin' / 'dashboard' / 'app.py'

    sys.path = [p for p in list(sys.path) if Path(p or '.').resolve() != src_root]

    spec = importlib.util.spec_from_file_location('dashboard_script_import', app_path)
    if spec is None or spec.loader is None:
        raise SystemExit('FAIL: unable to build import spec for dashboard app')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not callable(getattr(module, 'run', None)):
        raise SystemExit('FAIL: dashboard app did not expose run()')
    if str(src_root) not in sys.path:
        raise SystemExit('FAIL: dashboard app did not bootstrap src root into sys.path')

    print('OK dashboard_package_3a_hotfix_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
