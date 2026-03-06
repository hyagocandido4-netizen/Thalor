"""Repo-local import shim for src-layout entrypoints.

Allows commands like ``python -m natbin.runtime_app`` to work directly from the
repository root without requiring an editable install first.
"""
from __future__ import annotations

from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent.parent / "src" / "natbin"
if not _PKG_ROOT.is_dir():
    raise ImportError(f"Could not locate src package at {_PKG_ROOT}")

__path__ = [str(_PKG_ROOT)]

_init_file = _PKG_ROOT / "__init__.py"
if _init_file.is_file():
    exec(compile(_init_file.read_text(encoding="utf-8"), str(_init_file), "exec"), globals(), globals())
