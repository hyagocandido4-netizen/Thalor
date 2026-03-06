from __future__ import annotations

from .state.control_repo import *  # type: ignore  # noqa: F401,F403

try:
    from .state.control_repo import main  # type: ignore  # noqa: F401
except Exception:
    main = None  # type: ignore

if __name__ == '__main__' and callable(main):  # pragma: no cover
    raise SystemExit(main())
