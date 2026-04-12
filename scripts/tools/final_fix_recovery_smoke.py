from __future__ import annotations

import importlib

MODULES = [
    'natbin.dashboard.analytics',
    'natbin.dashboard.app',
    'natbin.control.commands',
    'natbin.intelligence.paths',
    'natbin.intelligence.policy',
    'natbin.intelligence.ops_state',
    'natbin.ops.intelligence_surface',
    'natbin.ops.practice_bootstrap',
    'natbin.ops.practice_round',
    'natbin.ops.container_health',
    'natbin.runtime.observer_surface',
]


def main() -> int:
    for name in MODULES:
        importlib.import_module(name)
    print('OK final_fix_recovery_smoke')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
