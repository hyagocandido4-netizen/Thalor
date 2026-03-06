from __future__ import annotations

from .runtime.execution import *  # type: ignore  # noqa: F401,F403
from .runtime.execution import main

if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
