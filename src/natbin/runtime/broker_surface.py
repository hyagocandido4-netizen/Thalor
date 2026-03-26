from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..brokers import FakeBrokerAdapter, IQOptionAdapter
from ..config.execution_mode import execution_mode_enabled, normalize_execution_mode
from ..brokers.base import BrokerScope


def build_context(repo_root: str | Path = '.', config_path: str | Path | None = None):
    from ..control.plan import build_context as _build_context

    return _build_context(repo_root=repo_root, config_path=config_path)



def _to_mapping(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if hasattr(raw, 'model_dump'):
        return raw.model_dump(mode='python')
    try:
        return dict(raw)
    except Exception:
        return {}



def execution_cfg(ctx) -> dict[str, Any]:
    raw = ctx.resolved_config.get('execution') if isinstance(ctx.resolved_config, dict) else getattr(ctx.resolved_config, 'execution', None)
    return _to_mapping(raw)



def broker_cfg(ctx) -> dict[str, Any]:
    raw = ctx.resolved_config.get('broker') if isinstance(ctx.resolved_config, dict) else getattr(ctx.resolved_config, 'broker', None)
    return _to_mapping(raw)



def reconcile_cfg(ctx) -> dict[str, Any]:
    return _to_mapping(execution_cfg(ctx).get('reconcile'))



def execution_enabled(ctx) -> bool:
    cfg = execution_cfg(ctx)
    return bool(cfg.get('enabled')) and execution_mode_enabled(cfg.get('mode'))



def account_mode(ctx) -> str:
    cfg = execution_cfg(ctx)
    return str(cfg.get('account_mode') or 'PRACTICE').upper()



def execution_repo_path(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve() / 'runs' / 'runtime_execution.sqlite3'



def signals_repo_db_path(repo_root: str | Path) -> Path:
    """Resolve the signals sqlite DB path.

    Multi-asset runs may partition signals DB per scope_tag to avoid SQLite
    contention. The portfolio runner sets THALOR_SIGNALS_DB_PATH in the
    observer/execution subprocess environment.

    Precedence:
      1) THALOR_SIGNALS_DB_PATH
      2) SIGNALS_DB_PATH
      3) <repo_root>/runs/live_signals.sqlite3
    """

    root = Path(repo_root).resolve()
    override = os.getenv('THALOR_SIGNALS_DB_PATH') or os.getenv('SIGNALS_DB_PATH')
    if override is not None and str(override).strip() != '':
        p = Path(str(override).strip())
        if not p.is_absolute():
            p = root / p
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return p
    return root / 'runs' / 'live_signals.sqlite3'



def scope_from_context(ctx) -> BrokerScope:
    return BrokerScope(
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        scope_tag=str(ctx.scope.scope_tag),
        account_mode=account_mode(ctx),
    )



def adapter_from_context(ctx, *, repo_root: str | Path):
    cfg = execution_cfg(ctx)
    provider = str(cfg.get('provider') or 'fake').strip().lower()
    current_account_mode = account_mode(ctx)
    execution_mode = normalize_execution_mode(cfg.get('mode'), default='disabled')
    if provider == 'fake':
        fake = dict(cfg.get('fake') or {})
        return FakeBrokerAdapter(
            repo_root=repo_root,
            account_mode=current_account_mode,
            state_path=fake.get('state_path'),
            submit_behavior=str(fake.get('submit_behavior') or 'ack'),
            settlement=str(fake.get('settlement') or 'open'),
            settle_after_sec=int(fake.get('settle_after_sec') or 0),
            create_order_on_timeout=bool(fake.get('create_order_on_timeout', True)),
            payout=float(fake.get('payout') or 0.80),
            heartbeat_ok=bool(fake.get('heartbeat_ok', True)),
        )

    broker = broker_cfg(ctx)
    reconcile = reconcile_cfg(ctx)
    return IQOptionAdapter(
        repo_root=repo_root,
        account_mode=current_account_mode,
        execution_mode=execution_mode,
        broker_config=broker,
        settle_grace_sec=int(reconcile.get('settle_grace_sec') or 30),
        history_limit=max(10, int(reconcile.get('history_lookback_sec') or 3600) // max(60, int(ctx.config.interval_sec))),
    )


__all__ = [
    'account_mode',
    'adapter_from_context',
    'broker_cfg',
    'build_context',
    'execution_cfg',
    'execution_enabled',
    'execution_repo_path',
    'reconcile_cfg',
    'scope_from_context',
    'signals_repo_db_path',
]
