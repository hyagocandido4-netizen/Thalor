from __future__ import annotations

"""Compatibility shim."""

from .usecases.validate_gaps import *  # noqa: F401,F403

if __name__ == "__main__":
    from .usecases.validate_gaps import main as _main
    raise SystemExit(_main())
