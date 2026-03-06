from __future__ import annotations

from .runtime.health import *  # type: ignore  # noqa: F401,F403

try:
    from .runtime.health import main  # type: ignore  # noqa: F401
except Exception:
    main = None  # type: ignore

if __name__ == '__main__' and callable(main):  # pragma: no cover
    raise SystemExit(main())
