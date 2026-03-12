from __future__ import annotations

"""Compatibility shim."""

from .research.dsio import *  # noqa: F401,F403

if __name__ == "__main__":
    from .research.dsio import main as _main
    raise SystemExit(_main())
