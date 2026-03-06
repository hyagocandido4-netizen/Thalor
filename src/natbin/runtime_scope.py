from __future__ import annotations

from .runtime.scope import *  # type: ignore  # noqa: F401,F403

try:
    from .runtime.scope import main  # type: ignore  # noqa: F401
except Exception:
    main = None  # type: ignore

if __name__ == '__main__' and callable(main):  # pragma: no cover
    raise SystemExit(main())
