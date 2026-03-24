from __future__ import annotations

"""Compatibility façade for reconciliation helpers.

RCF-3 moves matching/update logic into ``reconciliation_core`` and the batch
flow into ``reconciliation_flow`` while preserving the historical import path.
"""

from .reconciliation_core import apply_snapshot as _apply_snapshot, candidate_snapshots as _candidate_snapshots, event_id as _event_id
from .reconciliation_flow import reconcile_scope

__all__ = ['_apply_snapshot', '_candidate_snapshots', '_event_id', 'reconcile_scope']
