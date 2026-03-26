from __future__ import annotations

from pathlib import Path
from typing import Any
import time

from ..state.execution_repo import ExecutionRepository
from .broker_surface import adapter_from_context, build_context, execution_cfg, execution_enabled, execution_repo_path
from .execution_artifacts import write_execution_artifacts
from .execution_policy import signal_day_from_ts


def check_order_status_payload(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    external_order_id: str,
    refresh: bool = True,
) -> dict[str, Any]:
    """Inspect one order intent/snapshot and optionally refresh from the broker.

    The command is intentionally read-mostly: it does not create new intents or
    force a reconciliation batch. When ``refresh`` is enabled and execution is
    configured, we fetch the latest broker snapshot for the specific
    ``external_order_id`` and upsert it into the local execution repository.
    """

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    repo_root_path = Path(repo_root).resolve()
    repo = ExecutionRepository(execution_repo_path(repo_root_path))

    requested_id = str(external_order_id or '').strip()
    if not requested_id:
        payload = {
            'phase': 'check_order_status',
            'ok': False,
            'message': 'missing_external_order_id',
            'requested_external_order_id': requested_id,
        }
        write_execution_artifacts(repo_root=repo_root_path, ctx=ctx, orders_payload=payload)
        return payload

    intent = repo.get_intent_by_external_order_id(external_order_id=requested_id)
    provider = str((execution_cfg(ctx) or {}).get('provider') or 'fake')
    account_mode = str((execution_cfg(ctx) or {}).get('account_mode') or 'PRACTICE').upper()
    if intent is not None:
        provider = str(intent.broker_name or provider)
        account_mode = str(intent.account_mode or account_mode).upper()

    stored_snapshot = repo.get_broker_order(
        broker_name=provider,
        account_mode=account_mode,
        external_order_id=requested_id,
    )
    refreshed_snapshot = None
    refresh_error = None

    if refresh and execution_enabled(ctx):
        try:
            adapter = adapter_from_context(ctx, repo_root=repo_root_path)
            refreshed_snapshot = adapter.fetch_order(requested_id)
            if refreshed_snapshot is not None:
                repo.upsert_broker_snapshot(
                    refreshed_snapshot,
                    intent_id=intent.intent_id if intent is not None else None,
                )
        except Exception as exc:  # pragma: no cover - defensive for external runtime
            refresh_error = f'{type(exc).__name__}: {exc}'

    final_snapshot = refreshed_snapshot or repo.get_broker_order(
        broker_name=provider,
        account_mode=account_mode,
        external_order_id=requested_id,
    ) or stored_snapshot

    day = intent.day if intent is not None else signal_day_from_ts(int(time.time()), timezone_name=str(ctx.config.timezone))

    payload = {
        'phase': 'check_order_status',
        'ok': final_snapshot is not None or stored_snapshot is not None or refresh_error is None,
        'enabled': bool(execution_enabled(ctx)),
        'provider': provider,
        'account_mode': account_mode,
        'scope_tag': ctx.scope.scope_tag,
        'requested_external_order_id': requested_id,
        'refresh_requested': bool(refresh),
        'refresh_error': refresh_error,
        'intent': intent.as_dict() if intent is not None else None,
        'stored_snapshot': stored_snapshot.as_dict() if stored_snapshot is not None else None,
        'broker_snapshot': final_snapshot.as_dict() if final_snapshot is not None else None,
        'summary': repo.execution_summary(asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, day=day),
    }
    write_execution_artifacts(repo_root=repo_root_path, ctx=ctx, orders_payload=payload)
    return payload


__all__ = ['check_order_status_payload']
