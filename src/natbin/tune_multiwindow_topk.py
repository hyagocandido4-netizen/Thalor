from __future__ import annotations

"""Compatibility shim."""

from .research.tune_multiwindow_topk import *  # noqa: F401,F403

if __name__ == "__main__":
    from .research.tune_multiwindow_topk import main as _main
    raise SystemExit(_main())
