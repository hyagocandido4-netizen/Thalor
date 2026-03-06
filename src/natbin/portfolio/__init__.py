"""Portfolio runtime layer (Package O).

This package introduces a portfolio-level control loop that can operate across
multiple (asset, interval) scopes:

- prepare: refresh data partitions per scope
- candidate: run observer once per scope (execution disabled) to produce a decision
- allocate: pick the best candidates with portfolio + per-asset quota constraints
- execute: submit only the selected candidates via Package N execution layer

The implementation intentionally leans on subprocess isolation so that legacy
observer code (and its global env usage) remains safe and deterministic.
"""

from __future__ import annotations

__all__ = []
