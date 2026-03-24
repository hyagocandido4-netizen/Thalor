from __future__ import annotations

"""Observer compatibility facade.

The TOPK observer runtime was decomposed into ``natbin.usecases.observer``
modules so config resolution, cache, storage, summaries and execution policy
can evolve independently. This module preserves the historic import path and
public helper names used by operational scripts and tests.
"""

from .observer import *  # noqa: F401,F403
from .observer.signal_store import resolve_live_signals_csv_path as _resolve_live_signals_csv_path

__all__ = [name for name in globals() if not (name.startswith("__") and name.endswith("__"))]

