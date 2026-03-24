from __future__ import annotations

from .ops.repo_sync import *  # type: ignore  # noqa: F401,F403

try:
    from .ops.repo_sync import main  # type: ignore  # noqa: F401
except Exception:
    main = None  # type: ignore

if __name__ == '__main__' and callable(main):  # pragma: no cover
    raise SystemExit(main())
