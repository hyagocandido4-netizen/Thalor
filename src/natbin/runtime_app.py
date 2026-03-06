from __future__ import annotations

"""Public Package M control-plane shim.

This module preserves the historical ``natbin.runtime_app`` import path while
moving the real implementation to ``natbin.control.*``.
"""

import json
import sys
from datetime import UTC, datetime

from .control.app import main
from .control.models import RuntimeAppCapabilities, RuntimeAppConfig, RuntimeAppInfo, RuntimeContext
from .control.plan import (
    DEFAULT_CONFIG_PATH,
    build_context,
    build_runtime_app_info,
    derive_scoped_paths,
    detect_capabilities,
    load_runtime_app_config,
    to_json_dict,
)

__all__ = [
    'DEFAULT_CONFIG_PATH',
    'RuntimeAppCapabilities',
    'RuntimeAppConfig',
    'RuntimeAppInfo',
    'RuntimeContext',
    'build_context',
    'build_runtime_app_info',
    'derive_scoped_paths',
    'detect_capabilities',
    'load_runtime_app_config',
    'main',
    'to_json_dict',
]


if __name__ == '__main__':  # pragma: no cover
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        payload = {
            'ok': False,
            'message': 'interrupted',
            'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        }
        if '--json' in sys.argv:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print('Interrupted')
        raise SystemExit(130)
