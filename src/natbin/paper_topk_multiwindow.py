from __future__ import annotations

"""Compatibility shim."""

from .research.paper_topk_multiwindow import *  # noqa: F401,F403

if __name__ == "__main__":
    from .research.paper_topk_multiwindow import main as _main
    raise SystemExit(_main())
