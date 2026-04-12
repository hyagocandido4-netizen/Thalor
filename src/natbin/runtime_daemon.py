from __future__ import annotations

from typing import Any

from .runtime.daemon import *  # type: ignore  # noqa: F401,F403

__all__ = [
    'main',
    'build_runtime_network_transport_config',
    'build_runtime_network_transport_manager',
    'build_runtime_request_metrics_config',
    'build_runtime_request_metrics',
    'build_runtime_connectivity_payload',
]

_CONNECTIVITY_EXPORTS = {
    'build_runtime_network_transport_config',
    'build_runtime_network_transport_manager',
    'build_runtime_request_metrics_config',
    'build_runtime_request_metrics',
    'build_runtime_connectivity_payload',
}

try:
    from .runtime.daemon import main  # type: ignore  # noqa: F401
except Exception:
    main = None  # type: ignore


def __getattr__(name: str) -> Any:
    if name in _CONNECTIVITY_EXPORTS:
        from .runtime import connectivity as runtime_connectivity

        return getattr(runtime_connectivity, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


if __name__ == '__main__' and callable(main):  # pragma: no cover
    raise SystemExit(main())
