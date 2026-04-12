from __future__ import annotations

"""Compatibility façade for the execution layer.

RCF-3 extracts the heavy execution runtime into focused modules so the
historically large ``runtime.execution`` entrypoint becomes a thin public
surface. Existing imports continue to work from this module.
"""

from .broker_surface import (
    account_mode as _account_mode,
    adapter_from_context,
    build_context as _build_context,
    execution_cfg as _execution_cfg,
    execution_enabled,
    execution_repo_path,
    signals_repo_db_path,
)
from .execution_artifacts import read_json as _read_json, write_execution_artifacts as _write_execution_artifacts
from .execution_process import (
    _enforce_entry_deadline,
    _failsafe_from_ctx,
    build_parser as _build_parser,
    check_order_status_payload,
    execution_hardening_payload,
    main,
    orders_payload,
    precheck_reconcile_if_enabled,
    process_latest_signal,
    reconcile_payload,
)
from .execution_signal import (
    float_or_none as _float_or_none,
    int_or_none as _int_or_none,
    intent_from_signal_row,
    json_object_or_none as _json_object_or_none,
    latest_trade_row as _latest_trade_row,
    selected_allocation_metadata as _selected_allocation_metadata,
)
from .execution_submit import submit_intent

__all__ = [
    '_account_mode',
    '_build_context',
    '_build_parser',
    '_enforce_entry_deadline',
    '_execution_cfg',
    '_failsafe_from_ctx',
    '_float_or_none',
    '_int_or_none',
    '_json_object_or_none',
    '_latest_trade_row',
    '_read_json',
    '_selected_allocation_metadata',
    '_write_execution_artifacts',
    'adapter_from_context',
    'check_order_status_payload',
    'execution_enabled',
    'execution_hardening_payload',
    'execution_repo_path',
    'intent_from_signal_row',
    'main',
    'orders_payload',
    'precheck_reconcile_if_enabled',
    'process_latest_signal',
    'reconcile_payload',
    'signals_repo_db_path',
    'submit_intent',
]


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
