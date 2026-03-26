from __future__ import annotations

import subprocess
import sys
import types

from natbin.dashboard.__main__ import main


def _fake_call(_cmd: list[str]) -> int:
    raise KeyboardInterrupt


def run() -> int:
    original = subprocess.call
    original_streamlit = sys.modules.get('streamlit')
    try:
        subprocess.call = _fake_call  # type: ignore[assignment]
        sys.modules['streamlit'] = types.ModuleType('streamlit')
        code = main(['--repo-root', '.', '--config', 'config/multi_asset.yaml', '--no-browser'])
    finally:
        subprocess.call = original  # type: ignore[assignment]
        if original_streamlit is None:
            sys.modules.pop('streamlit', None)
        else:
            sys.modules['streamlit'] = original_streamlit
    if code != 0:
        print(f'ERROR dashboard_package_3c_hotfix_smoke code={code}', file=sys.stderr)
        return 1
    print('OK dashboard_package_3c_hotfix_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(run())
