from __future__ import annotations

"""Compatibility shim."""

from .usecases.collect_candles import *  # noqa: F401,F403

if __name__ == "__main__":
    from .usecases.collect_candles import main as _main
    raise SystemExit(_main())
