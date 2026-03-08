from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from ..runtime.scope import build_scope


@dataclass(frozen=True)
class ScopeDataPaths:
    db_path: Path
    dataset_path: Path

    def as_dict(self) -> dict[str, Any]:
        return {k: str(v) for k, v in asdict(self).items()}



@dataclass(frozen=True)
class ScopeRuntimePaths:
    """Per-scope runtime DB paths.

    These are *runtime artifacts* (not market data) used by the observer and
    execution layer:
      - signals DB (signals_v2)
      - state DB (executed_state)

    When running multi-asset in parallel, we partition these DBs per scope_tag
    to avoid SQLite contention.
    """

    signals_db_path: Path
    state_db_path: Path

    def as_dict(self) -> dict[str, Any]:
        return {k: str(v) for k, v in asdict(self).items()}


def resolve_scope_runtime_paths(
    repo_root: str | Path,
    *,
    scope_tag: str,
    partition_enable: bool,
) -> ScopeRuntimePaths:
    root = Path(repo_root).resolve()

    if not partition_enable:
        sig = root / 'runs' / 'live_signals.sqlite3'
        st = root / 'runs' / 'live_topk_state.sqlite3'
    else:
        sig = root / 'runs' / 'signals' / str(scope_tag) / 'live_signals.sqlite3'
        st = root / 'runs' / 'state' / str(scope_tag) / 'live_topk_state.sqlite3'

    sig.parent.mkdir(parents=True, exist_ok=True)
    st.parent.mkdir(parents=True, exist_ok=True)
    return ScopeRuntimePaths(signals_db_path=sig, state_db_path=st)


def scope_tag(asset: str, interval_sec: int) -> str:
    return build_scope(asset=str(asset), interval_sec=int(interval_sec)).scope_tag


def _safe_format(template: str, *, asset: str, interval_sec: int, scope_tag: str) -> str:
    tpl = str(template or '').strip()
    if not tpl:
        return ''
    try:
        return tpl.format(asset=str(asset), interval_sec=int(interval_sec), scope_tag=str(scope_tag))
    except Exception:
        # If template is malformed, fall back to raw string.
        return tpl


def resolve_scope_data_paths(
    repo_root: str | Path,
    *,
    asset: str,
    interval_sec: int,
    partition_enable: bool,
    db_template: str,
    dataset_template: str,
    default_db_path: str | Path,
    default_dataset_path: str | Path,
) -> ScopeDataPaths:
    root = Path(repo_root).resolve()
    tag = scope_tag(asset, interval_sec)

    if not partition_enable:
        db_path = Path(default_db_path)
        dataset_path = Path(default_dataset_path)
        if not db_path.is_absolute():
            db_path = root / db_path
        if not dataset_path.is_absolute():
            dataset_path = root / dataset_path
        return ScopeDataPaths(db_path=db_path, dataset_path=dataset_path)

    db_rel = _safe_format(db_template, asset=asset, interval_sec=interval_sec, scope_tag=tag) or str(default_db_path)
    ds_rel = _safe_format(dataset_template, asset=asset, interval_sec=interval_sec, scope_tag=tag) or str(default_dataset_path)

    db_path = Path(db_rel)
    dataset_path = Path(ds_rel)
    if not db_path.is_absolute():
        db_path = root / db_path
    if not dataset_path.is_absolute():
        dataset_path = root / dataset_path

    # Ensure parent dirs exist for early failures.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    return ScopeDataPaths(db_path=db_path, dataset_path=dataset_path)


def scoped_env(
    scope_asset: str,
    scope_interval_sec: int,
    scope_timezone: str,
    *,
    data_paths: ScopeDataPaths | None = None,
    runtime_paths: ScopeRuntimePaths | None = None,
    execution_enabled: bool | None = None,
) -> dict[str, str]:
    """Environment overrides for legacy modules.

    We set BOTH the modern THALOR__* keys (pydantic-settings) and the legacy
    flat keys used by older modules.
    """

    env: dict[str, str] = {
        'ASSET': str(scope_asset),
        'INTERVAL_SEC': str(int(scope_interval_sec)),
        'TIMEZONE': str(scope_timezone),
    }

    if data_paths is not None:
        env.update(
            {
                'THALOR__DATA__DB_PATH': str(data_paths.db_path),
                'THALOR__DATA__DATASET_PATH': str(data_paths.dataset_path),
                # legacy fallbacks
                'MARKET_DB_PATH': str(data_paths.db_path),
                'DATASET_PATH': str(data_paths.dataset_path),
            }
        )


    if runtime_paths is not None:
        env.update(
            {
                'THALOR_SIGNALS_DB_PATH': str(runtime_paths.signals_db_path),
                'THALOR_STATE_DB_PATH': str(runtime_paths.state_db_path),
                # convenient aliases for ad-hoc tools
                'SIGNALS_DB_PATH': str(runtime_paths.signals_db_path),
                'STATE_DB_PATH': str(runtime_paths.state_db_path),
            }
        )

    if execution_enabled is not None:
        env['THALOR__EXECUTION__ENABLED'] = '1' if bool(execution_enabled) else '0'
        if not bool(execution_enabled):
            # Force mode disabled as an extra guard.
            env['THALOR__EXECUTION__MODE'] = 'disabled'

    # Defensive: avoid child processes inheriting a kill switch from a parent shell.
    # The fail-safe layer reads THALOR_KILL_SWITCH, so keep it explicit.
    if 'THALOR_KILL_SWITCH' not in env and 'THALOR_KILL_SWITCH' in os.environ:
        env['THALOR_KILL_SWITCH'] = os.environ.get('THALOR_KILL_SWITCH', '')

    return env


def portfolio_runs_dir(repo_root: str | Path) -> Path:
    p = Path(repo_root).resolve() / 'runs' / 'portfolio'
    p.mkdir(parents=True, exist_ok=True)
    return p


def portfolio_cycle_latest_path(repo_root: str | Path) -> Path:
    return portfolio_runs_dir(repo_root) / 'portfolio_cycle_latest.json'


def portfolio_allocation_latest_path(repo_root: str | Path) -> Path:
    return portfolio_runs_dir(repo_root) / 'portfolio_allocation_latest.json'
