from __future__ import annotations

"""Compatibility shim."""

from .research.train_walkforward import *  # noqa: F401,F403

if __name__ == "__main__":
    from .research.train_walkforward import main as _main
    raise SystemExit(_main())
