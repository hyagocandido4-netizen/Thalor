"""Compatibility shim for the legacy root import path.

Re-exports *all* public and helper names from
``natbin.usecases.observe_signal_topk_perday`` (including selected
underscore-prefixed helpers used by repo smoke/regression tests).
"""
from __future__ import annotations

from importlib import import_module as _import_module

_impl = _import_module("natbin.usecases.observe_signal_topk_perday")

# Re-export every non-dunder attribute to preserve backward compatibility.
for _name, _value in vars(_impl).items():
    if not (_name.startswith("__") and _name.endswith("__")):
        globals()[_name] = _value

__all__ = [
    _name
    for _name in vars(_impl).keys()
    if not (_name.startswith("__") and _name.endswith("__"))
]

if __name__ == "__main__":
    globals()["main"]()
