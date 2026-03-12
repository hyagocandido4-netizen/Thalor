from __future__ import annotations

"""Compatibility shim."""

from .usecases.refresh_daily_summary import *  # noqa: F401,F403

if __name__ == "__main__":
    from .usecases.refresh_daily_summary import main as _main
    raise SystemExit(_main())
