from __future__ import annotations

"""Compatibility shim."""

from .research.sweep_thresholds import *  # noqa: F401,F403

if __name__ == "__main__":
    from .research.sweep_thresholds import main as _main
    raise SystemExit(_main())
