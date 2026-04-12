from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config.loader import load_thalor_config
from ..config.paths import resolve_config_path, resolve_repo_root
from ..runtime.scope import decision_latest_path as scope_decision_latest_path
from ..runtime.scope import market_context_path as scope_market_context_path
from ..runtime.failsafe import CircuitBreakerPolicy, RuntimeFailsafe
from ..runtime.precheck import run_precheck
from ..runtime.perf import load_json_cached
from ..runtime.broker_dependency import candle_db_snapshot
from ..state.control_repo import RuntimeControlRepository
from ..state.portfolio_repo import PortfolioRepository
from ..telemetry import TelemetryServer, TelemetryState
from ..telemetry.metrics import REGISTRY
from ..ops.lockfile import acquire_lock as acquire_lockfile
from ..ops.lockfile import release_lock as release_lockfile
from ..ops.structured_log import append_jsonl
from ..ops.safe_refresh import refresh_market_context_safe
from ..intelligence.runtime import enrich_candidate as enrich_candidate_intelligence

from . import allocator as _allocator
from .models import CandidateDecision, PortfolioCycleReport, PortfolioScope
from .board import build_execution_plan
from .candidate_utils import candidate_from_decision_payload
from .correlation import resolve_correlation_group
from .latest import write_portfolio_latest_payload
from .paths import (
    ScopeDataPaths,
    ScopeRuntimePaths,
    resolve_scope_data_paths,
    resolve_scope_runtime_paths,
    scope_tag as compute_scope_tag,
    scoped_env,
)
from .quota import compute_asset_quotas, compute_portfolio_quota
from .subprocess import SubprocessOutcome, run_python_module
from .runtime_budget import decide_prepare_strategy, select_governed_items


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return int(default)
    try:
        return int(float(str(raw).strip()))
    except Exception:
        return int(default)


def _acquire_lock(lock_path: Path):
    return acquire_lockfile(lock_path)


def _release_lock(lock_path: Path) -> None:
    release_lockfile(lock_path)


def _next_wake_sleep_sec(*, scopes: list[PortfolioScope], offset_sec: int = 3, now_utc: datetime | None = None) -> int:
    """Compute sleep until the next candle boundary across all scopes."""
    now = now_utc or datetime.now(tz=UTC)
    ts = int(now.timestamp())
    next_ts = None
    for s in scopes:
        iv = max(1, int(s.interval_sec))
        n = ((ts // iv) + 1) * iv + max(0, int(offset_sec))
        if next_ts is None or n < next_ts:
            next_ts = n
    if next_ts is None:
        return 1
    return max(0, int(next_ts - ts))


def load_scopes(*, repo_root: str | Path, config_path: str | Path | None = None) -> tuple[list[PortfolioScope], Any]:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    path = resolve_config_path(repo_root=root, config_path=config_path)
    cfg = load_thalor_config(config_path=path, repo_root=root)

    scopes: list[PortfolioScope] = []
    for a in list(cfg.assets or []):
        if not bool(getattr(a, 'enabled', True)):
            continue
        asset = str(getattr(a, 'asset', '')).strip()
        interval_sec = int(getattr(a, 'interval_sec', 300))
        tz = str(getattr(a, 'timezone', 'UTC'))
        tag = compute_scope_tag(asset, interval_sec)
        cluster_key = str(getattr(a, 'cluster_key', 'default') or 'default')
        scopes.append(
            PortfolioScope(
                asset=asset,
                interval_sec=interval_sec,
                timezone=tz,
                scope_tag=tag,
                weight=float(getattr(a, 'weight', 1.0) or 1.0),
                cluster_key=cluster_key,
                correlation_group=resolve_correlation_group(asset=asset, cluster_key=cluster_key),
                topk_k=int(getattr(a, 'topk_k', 3) or 3),
                hard_max_trades_per_day=getattr(a, 'hard_max_trades_per_day', None),
                max_open_positions=getattr(a, 'max_open_positions', None),
                max_pending_unknown=getattr(a, 'max_pending_unknown', None),
            )
        )

    return scopes, cfg


def compute_stagger_delay(idx: int, *, stagger_sec: float, workers: int) -> float:
    """Compute per-scope start delay for multi-asset phases.

    Behavior:
      - workers <= 1 (sequential): constant delay between scopes (idx>0 => stagger_sec)
      - workers > 1 (parallel): spread starts (idx>0 => idx*stagger_sec)
    """

    try:
        ss = float(stagger_sec or 0.0)
    except Exception:
        ss = 0.0
    if ss <= 0.0:
        return 0.0
    if int(idx) <= 0:
        return 0.0
    if int(workers) <= 1:
        return ss
    return float(idx) * ss


def _scope_data_paths(root: Path, cfg: Any, scope: PortfolioScope) -> ScopeDataPaths:
    partition = bool(getattr(cfg.multi_asset, 'partition_data_paths', True)) and bool(getattr(cfg.multi_asset, 'enabled', False))
    db_tpl = str(getattr(cfg.multi_asset, 'data_db_template', 'data/market_{scope_tag}.sqlite3'))
    ds_tpl = str(getattr(cfg.multi_asset, 'dataset_path_template', 'data/datasets/{scope_tag}/dataset.csv'))

    default_db = getattr(cfg.data, 'db_path', Path('data/market_otc.sqlite3'))
    default_ds = getattr(cfg.data, 'dataset_path', Path('data/dataset_phase2.csv'))

    return resolve_scope_data_paths(
        root,
        asset=scope.asset,
        interval_sec=scope.interval_sec,
        partition_enable=partition,
        db_template=db_tpl,
        dataset_template=ds_tpl,
        default_db_path=default_db,
        default_dataset_path=default_ds,
    )



def _scope_runtime_paths(root: Path, cfg: Any, scope: PortfolioScope) -> ScopeRuntimePaths:
    """Resolve per-scope runtime DB paths (signals/state).

    We partition runtime sqlite DBs when multi-asset is enabled, so candidate
    observation can run in parallel without SQLite locking.
    """

    partition = bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False))
    return resolve_scope_runtime_paths(root, scope_tag=str(scope.scope_tag), partition_enable=partition)



def _parse_iso(raw: Any) -> datetime | None:
    if raw in (None, ''):
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _artifact_age_sec(raw: Any) -> float | None:
    stamp = _parse_iso(raw)
    if stamp is None:
        return None
    return max(0.0, (datetime.now(tz=UTC) - stamp).total_seconds())


def _market_context_state(root: Path, scope: PortfolioScope) -> dict[str, Any]:
    path = root / scope_market_context_path(asset=scope.asset, interval_sec=int(scope.interval_sec))
    payload = load_json_cached(str(path)) if path.exists() else None
    max_age_sec = max(int(scope.interval_sec) * 3, 900)
    age_sec = None
    fresh = False
    if isinstance(payload, dict):
        age_sec = _artifact_age_sec(payload.get('at_utc'))
        fresh = bool(age_sec is not None and age_sec <= max_age_sec)
    return {
        'path': str(path),
        'exists': path.exists(),
        'fresh': fresh,
        'age_sec': None if age_sec is None else round(age_sec, 3),
        'max_age_sec': int(max_age_sec),
        'dependency_available': payload.get('dependency_available') if isinstance(payload, dict) else None,
        'dependency_reason': payload.get('dependency_reason') if isinstance(payload, dict) else None,
        'market_open': payload.get('market_open') if isinstance(payload, dict) else None,
        'open_source': payload.get('open_source') if isinstance(payload, dict) else None,
        'payout': payload.get('payout') if isinstance(payload, dict) else None,
        'payout_source': payload.get('payout_source') if isinstance(payload, dict) else None,
        'last_candle_ts': payload.get('last_candle_ts') if isinstance(payload, dict) else None,
    }


def _candle_db_state(data_paths: ScopeDataPaths, scope: PortfolioScope) -> dict[str, Any]:
    db_path = Path(str(data_paths.db_path))
    state: dict[str, Any] = {
        'path': str(db_path),
        'exists': db_path.exists(),
        'db_rows': 0,
        'last_candle_ts': None,
        'last_candle_age_sec': None,
        'fresh': False,
        'max_age_sec': max(int(scope.interval_sec) * 2 + 90, int(scope.interval_sec) + 90),
    }
    if not db_path.exists():
        return state
    try:
        snap = candle_db_snapshot(str(db_path), scope.asset, int(scope.interval_sec))
    except Exception as exc:
        state['error'] = f'{type(exc).__name__}:{exc}'
        return state
    rows = int(snap.get('db_rows') or 0)
    last_ts = snap.get('last_candle_ts')
    state['db_rows'] = rows
    state['last_candle_ts'] = int(last_ts) if last_ts is not None else None
    if last_ts is not None:
        try:
            age_sec = max(0.0, datetime.now(tz=UTC).timestamp() - int(last_ts))
            state['last_candle_age_sec'] = round(age_sec, 3)
            state['fresh'] = bool(rows > 0 and age_sec <= int(state['max_age_sec']))
        except Exception:
            state['fresh'] = False
    return state


def _load_runtime_governor(*, root: Path, cfg: Any, scopes: list[PortfolioScope]) -> dict[str, Any]:
    multi = getattr(cfg, 'multi_asset', None)
    artifact_path = root / 'runs' / 'control' / '_repo' / 'provider_session_governor.json'
    payload = _read_json(artifact_path)
    interval = min((int(s.interval_sec) for s in scopes), default=300)
    max_age_sec = max(600, int(interval) * 2)
    artifact_age = None
    artifact_fresh = False
    if isinstance(payload, dict):
        artifact_age = _artifact_age_sec(payload.get('at_utc'))
        artifact_fresh = bool(artifact_age is not None and artifact_age <= max_age_sec)
    governor = dict(payload.get('governor') or {}) if artifact_fresh and isinstance(payload, dict) else {}
    if not governor:
        governor = {
            'mode': 'runtime_default',
            'sleep_between_scopes_ms': int(max(0.0, float(getattr(multi, 'stagger_sec', 0.0) or 0.0)) * 1000),
            'sleep_between_candidate_scopes_ms': int(max(0.0, float(getattr(multi, 'stagger_sec', 0.0) or 0.0)) * 1000),
            'refresh_market_context_timeout_sec': _env_int('REFRESH_MARKET_CONTEXT_TIMEOUT_SEC', 120),
            'asset_prepare_timeout_sec': _env_int('COLLECT_RECENT_TIMEOUT_SEC', 300),
            'max_asset_prepare_fallback_scopes': len(scopes),
            'max_candidate_scopes_per_run': len(scopes),
            'prefer_cached_provider_artifacts': True,
            'skip_fresh_market_context_scopes': True,
            'scope_order': 'best_first_round_robin',
            'allow_parallel_execution': False,
        }
    return {
        'artifact_path': str(artifact_path),
        'artifact_present': artifact_path.exists(),
        'artifact_age_sec': None if artifact_age is None else round(artifact_age, 3),
        'artifact_fresh': bool(artifact_fresh),
        'governor': governor,
    }


def _refresh_only_step(*, repo_root: Path, config_path: Path | None, scope: PortfolioScope, timeout_sec: int) -> SubprocessOutcome:
    step = refresh_market_context_safe(
        repo_root=repo_root,
        config_path=config_path,
        asset=scope.asset,
        interval_sec=int(scope.interval_sec),
        timeout_sec=int(timeout_sec),
    )
    return SubprocessOutcome(
        name=f'refresh_market_context_safe:{scope.scope_tag}',
        argv=[str(x) for x in list(step.get('command') or [])],
        cwd=str(step.get('cwd') or str(repo_root)),
        returncode=int(step.get('returncode') or 0) if step.get('returncode') not in (None, '') else 1,
        duration_sec=float(step.get('duration_sec') or 0.0),
        stdout_tail=str(step.get('stdout_tail') or ''),
        stderr_tail=str(step.get('stderr_tail') or ''),
    )


def _prepare_scope_runtime(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    cfg: Any,
    scope: PortfolioScope,
    data_paths: ScopeDataPaths,
    lookback_candles: int,
    stagger_delay_sec: float = 0.0,
    refresh_timeout_sec: int | None = None,
    allow_prepare_fallback: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    multi = getattr(cfg, 'multi_asset', None)
    adaptive_prepare_enable = bool(getattr(multi, 'adaptive_prepare_enable', True))
    incremental_lookback = getattr(multi, 'prepare_incremental_lookback_candles', 256)
    before_market = _market_context_state(root, scope)
    before_db = _candle_db_state(data_paths, scope)
    strategy_meta = decide_prepare_strategy(
        adaptive_prepare_enable=adaptive_prepare_enable,
        db_exists=bool(before_db.get('exists')),
        db_rows=int(before_db.get('db_rows') or 0),
        db_fresh=bool(before_db.get('fresh')),
        market_context_exists=bool(before_market.get('exists')),
        market_context_fresh=bool(before_market.get('fresh')),
        market_context_dependency_available=before_market.get('dependency_available'),
        full_lookback_candles=int(lookback_candles),
        incremental_lookback_candles=None if incremental_lookback in (None, '') else int(incremental_lookback),
    )

    steps: list[SubprocessOutcome] = []
    fallback_used = False
    if bool(strategy_meta.get('skip_prepare')):
        after_market = before_market
        after_db = before_db
    else:
        if float(stagger_delay_sec or 0.0) > 0:
            time.sleep(float(stagger_delay_sec))
        if bool(strategy_meta.get('refresh_only')):
            refresh_timeout = int(refresh_timeout_sec or _env_int('REFRESH_MARKET_CONTEXT_TIMEOUT_SEC', 120))
            steps.append(_refresh_only_step(repo_root=root, config_path=Path(config_path) if config_path is not None else None, scope=scope, timeout_sec=refresh_timeout))
            refresh_ok = all(int(step.returncode) == 0 for step in steps)
            if (not refresh_ok) and bool(allow_prepare_fallback):
                fallback_used = True
                fallback_lookback = int(min(int(lookback_candles), max(32, int(incremental_lookback or 256))))
                steps.extend(
                    prepare_scope(
                        repo_root=root,
                        config_path=config_path,
                        scope=scope,
                        data_paths=data_paths,
                        lookback_candles=fallback_lookback,
                        stagger_delay_sec=0.0,
                    )
                )
                strategy_meta['strategy'] = 'refresh_only_fallback_incremental'
                strategy_meta['effective_lookback_candles'] = fallback_lookback
                strategy_meta['uses_incremental_lookback'] = True
        else:
            steps.extend(
                prepare_scope(
                    repo_root=root,
                    config_path=config_path,
                    scope=scope,
                    data_paths=data_paths,
                    lookback_candles=int(strategy_meta.get('effective_lookback_candles') or int(lookback_candles)),
                    stagger_delay_sec=0.0,
                )
            )
        after_market = _market_context_state(root, scope)
        after_db = _candle_db_state(data_paths, scope)

    return {
        'scope_tag': scope.scope_tag,
        'asset': scope.asset,
        'interval_sec': scope.interval_sec,
        'strategy': str(strategy_meta.get('strategy') or 'full_prepare'),
        'effective_lookback_candles': strategy_meta.get('effective_lookback_candles'),
        'uses_incremental_lookback': bool(strategy_meta.get('uses_incremental_lookback', False)),
        'fallback_used': bool(fallback_used),
        'market_context_before': before_market,
        'market_context_after': after_market,
        'candle_db_before': before_db,
        'candle_db_after': after_db,
        'steps': [step.as_dict() for step in steps],
    }


def _latest_allocation_score_map(root: Path) -> dict[str, float]:
    payload = _read_json(root / 'runs' / 'portfolio_allocation_latest.json') or {}
    items: list[dict[str, Any]] = []
    for key in ('selected', 'suppressed'):
        value = payload.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    scores: dict[str, float] = {}
    for item in items:
        tag = str(item.get('scope_tag') or '').strip()
        if not tag:
            continue
        score = item.get('portfolio_score')
        if score in (None, ''):
            score = item.get('rank_value')
        if score in (None, ''):
            score = item.get('score')
        if score in (None, ''):
            score = item.get('conf')
        try:
            value = float(score)
        except Exception:
            continue
        if tag not in scores or value > scores[tag]:
            scores[tag] = value
    return scores


def _ordered_scopes_for_candidate_budget(root: Path, scopes: list[PortfolioScope]) -> list[PortfolioScope]:
    if len(scopes) <= 1:
        return list(scopes)
    scores = _latest_allocation_score_map(root)
    indexed = {scope.scope_tag: idx for idx, scope in enumerate(scopes)}
    return sorted(
        scopes,
        key=lambda scope: (
            -float(scores.get(scope.scope_tag, float('-inf'))),
            indexed.get(scope.scope_tag, 0),
        ),
    )


def _budget_skipped_candidate(*, root: Path, scope: PortfolioScope, reason: str, governor_mode: str) -> tuple[SubprocessOutcome, CandidateDecision]:
    decision_path = scope_decision_latest_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=root / 'runs')
    outcome = SubprocessOutcome(
        name=f'observe_once:{scope.scope_tag}',
        argv=[],
        cwd=str(root),
        returncode=0,
        duration_sec=0.0,
        stdout_tail='',
        stderr_tail=f'skipped:{reason}',
    )
    candidate = CandidateDecision(
        scope_tag=scope.scope_tag,
        asset=scope.asset,
        interval_sec=scope.interval_sec,
        day=None,
        ts=None,
        action='HOLD',
        score=0.0,
        conf=0.0,
        ev=-1.0,
        reason='candidate_budget_skip',
        blockers='candidate_budget_skip',
        decision_path=str(decision_path),
        raw={'kind': 'candidate_budget_skip', 'reason': str(reason), 'governor_mode': str(governor_mode)},
    )
    return outcome, candidate



def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return None


def _candidate_from_decision(scope: PortfolioScope, decision: dict[str, Any] | None, *, decision_path: Path) -> CandidateDecision:
    return candidate_from_decision_payload(scope, decision, decision_path=decision_path)


def prepare_scope(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    scope: PortfolioScope,
    data_paths: ScopeDataPaths,
    lookback_candles: int,
    stagger_delay_sec: float = 0.0,
) -> list[SubprocessOutcome]:
    """Prepare data for a single scope.

    We keep this step isolated per scope and safe for parallel execution.

    NOTE: The legacy pipeline writes to the signals DB; do not run observer here.
    """

    if float(stagger_delay_sec or 0.0) > 0:
        time.sleep(float(stagger_delay_sec))

    env = scoped_env(scope.asset, scope.interval_sec, scope.timezone, data_paths=data_paths, execution_enabled=None)

    if config_path is not None:
        env['THALOR_CONFIG_PATH'] = str(config_path)

    # Propagate lookback.
    env['LOOKBACK_CANDLES'] = str(int(lookback_candles))

    out: list[SubprocessOutcome] = []

    # 1) collect_recent (candles) -> per-scope DB
    out.append(
        run_python_module(
            repo_root,
            name=f'collect_recent:{scope.scope_tag}',
            module='natbin.collect_recent',
            args=(),
            env=env,
            timeout_sec=_env_int('COLLECT_RECENT_TIMEOUT_SEC', 300),
        )
    )

    # 2) make_dataset -> per-scope dataset_path
    out.append(
        run_python_module(
            repo_root,
            name=f'make_dataset:{scope.scope_tag}',
            module='natbin.make_dataset',
            args=(),
            env=env,
            timeout_sec=_env_int('MAKE_DATASET_TIMEOUT_SEC', 180),
        )
    )

    # 3) refresh_market_context -> sidecar (uses db freshness + payout)
    out.append(
        run_python_module(
            repo_root,
            name=f'refresh_market_context:{scope.scope_tag}',
            module='natbin.refresh_market_context',
            args=(),
            env=env,
            timeout_sec=_env_int('REFRESH_MARKET_CONTEXT_TIMEOUT_SEC', 120),
        )
    )

    return out


def candidate_scope(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    scope: PortfolioScope,
    data_paths: ScopeDataPaths,
    runtime_paths: ScopeRuntimePaths | None,
    topk: int,
    lookback_candles: int,
    stagger_delay_sec: float = 0.0,
    cfg: Any | None = None,
) -> tuple[SubprocessOutcome, CandidateDecision]:
    """Run observer once for a scope (execution disabled) and return candidate decision."""

    if float(stagger_delay_sec or 0.0) > 0:
        time.sleep(float(stagger_delay_sec))

    env = scoped_env(
        scope.asset,
        scope.interval_sec,
        scope.timezone,
        data_paths=data_paths,
        runtime_paths=runtime_paths,
        execution_enabled=False,
    )

    if config_path is not None:
        env['THALOR_CONFIG_PATH'] = str(config_path)

    # Ensure observer uses correct per-scope TOPK_K.
    env['TOPK_K'] = str(int(topk))
    env['LOOKBACK_CANDLES'] = str(int(lookback_candles))

    args = ['--repo-root', str(repo_root)]
    if config_path is not None:
        args += ['--config', str(config_path)]
    args += ['--topk', str(int(topk)), '--lookback-candles', str(int(lookback_candles))]

    outcome = run_python_module(
        repo_root,
        name=f'observe_once:{scope.scope_tag}',
        module='natbin.runtime.observe_once',
        args=args,
        env=env,
        timeout_sec=_env_int('OBSERVE_ONCE_TIMEOUT_SEC', 300),
    )

    # Read decision_latest_{scope}.json
    decision_path = scope_decision_latest_path(asset=scope.asset, interval_sec=scope.interval_sec, out_dir=Path(repo_root) / 'runs')
    decision = _read_json(decision_path)
    cand = _candidate_from_decision(scope, decision, decision_path=decision_path)
    if cfg is not None:
        try:
            cand = enrich_candidate_intelligence(
                repo_root=repo_root,
                scope=scope,
                candidate=cand,
                runtime_paths=runtime_paths,
                cfg=cfg,
            )
        except Exception as exc:
            # Never fail the whole candidate phase because of intelligence extras.
            raw = dict(cand.raw or {})
            raw['intelligence_error'] = f'{type(exc).__name__}:{exc}'
            cand = CandidateDecision(
                scope_tag=cand.scope_tag,
                asset=cand.asset,
                interval_sec=cand.interval_sec,
                day=cand.day,
                ts=cand.ts,
                action=cand.action,
                score=cand.score,
                conf=cand.conf,
                ev=cand.ev,
                reason=cand.reason,
                blockers=cand.blockers,
                decision_path=cand.decision_path,
                raw=raw,
                intelligence_score=cand.intelligence_score,
                learned_gate_prob=cand.learned_gate_prob,
                slot_multiplier=cand.slot_multiplier,
                drift_level=cand.drift_level,
                coverage_bias=cand.coverage_bias,
                intelligence=cand.intelligence,
            )
    return outcome, cand


def execute_scope(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    scope: PortfolioScope,
    data_paths: ScopeDataPaths,
    runtime_paths: ScopeRuntimePaths | None,
) -> tuple[SubprocessOutcome, dict[str, Any] | None]:
    """Submit + reconcile the latest signal for a scope via Package N."""

    env = scoped_env(
        scope.asset,
        scope.interval_sec,
        scope.timezone,
        data_paths=data_paths,
        runtime_paths=runtime_paths,
    )

    if config_path is not None:
        env['THALOR_CONFIG_PATH'] = str(config_path)

    args = ['--repo-root', str(repo_root), '--json']
    if config_path is not None:
        args += ['--config', str(config_path)]
    # default command is 'process'

    outcome = run_python_module(
        repo_root,
        name=f'execution_process:{scope.scope_tag}',
        module='natbin.runtime.execution',
        args=args,
        env=env,
        timeout_sec=_env_int('EXECUTION_PROCESS_TIMEOUT_SEC', 120),
    )

    payload: dict[str, Any] | None = None
    if outcome.stdout_tail:
        try:
            payload = json.loads(outcome.stdout_tail)
        except Exception:
            # best-effort: sometimes stdout has extra logs; don't fail the cycle.
            payload = None

    return outcome, payload


def run_portfolio_cycle(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    topk: int,
    lookback_candles: int,
    max_parallel_assets: int | None = None,
) -> PortfolioCycleReport:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)

    started = _utc_now_iso()
    cycle_id = datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')

    errors: list[str] = []
    prepare_results: list[dict[str, Any]] = []
    candidates: list[CandidateDecision] = []
    candidate_results: list[dict[str, Any]] = []
    execution_results: list[dict[str, Any]] = []

    scopes, cfg = load_scopes(repo_root=root, config_path=cfg_path)

    # Package P: global gates (kill-switch/drain) and per-asset circuit breakers.
    control_repo = RuntimeControlRepository(Path(root) / 'runs' / 'runtime_control.sqlite3')
    fs_cfg = getattr(cfg, 'failsafe', None)
    kill_file = getattr(fs_cfg, 'kill_switch_file', Path('runs/KILL_SWITCH')) if fs_cfg is not None else Path('runs/KILL_SWITCH')
    if not Path(kill_file).is_absolute():
        kill_file = Path(root) / Path(kill_file)
    drain_file = getattr(fs_cfg, 'drain_mode_file', Path('runs/DRAIN_MODE')) if fs_cfg is not None else Path('runs/DRAIN_MODE')
    if not Path(drain_file).is_absolute():
        drain_file = Path(root) / Path(drain_file)
    policy = CircuitBreakerPolicy(
        failures_to_open=int(getattr(fs_cfg, 'breaker_failures_to_open', 3) if fs_cfg is not None else 3),
        cooldown_minutes=int(getattr(fs_cfg, 'breaker_cooldown_minutes', 15) if fs_cfg is not None else 15),
        half_open_trials=int(getattr(fs_cfg, 'breaker_half_open_trials', 1) if fs_cfg is not None else 1),
    )
    failsafe = RuntimeFailsafe(
        kill_switch_file=Path(kill_file),
        kill_switch_env_var=str(getattr(fs_cfg, 'kill_switch_env_var', 'THALOR_KILL_SWITCH') if fs_cfg is not None else 'THALOR_KILL_SWITCH'),
        drain_mode_file=Path(drain_file),
        drain_mode_env_var=str(getattr(fs_cfg, 'drain_mode_env_var', 'THALOR_DRAIN_MODE') if fs_cfg is not None else 'THALOR_DRAIN_MODE'),
        global_fail_closed=bool(getattr(fs_cfg, 'global_fail_closed', True) if fs_cfg is not None else True),
        market_context_fail_closed=bool(getattr(fs_cfg, 'market_context_fail_closed', True) if fs_cfg is not None else True),
        policy=policy,
    )
    kill_active, kill_reason = failsafe.is_kill_switch_active(dict(os.environ))
    drain_active, drain_reason = failsafe.is_drain_mode_active(dict(os.environ))
    gates = {
        'kill_switch_active': bool(kill_active),
        'kill_switch_reason': kill_reason,
        'drain_mode_active': bool(drain_active),
        'drain_mode_reason': drain_reason,
    }

    # Determine parallelism + optional staggering.
    multi = getattr(cfg, 'multi_asset', None)
    multi_enabled = bool(getattr(multi, 'enabled', False))
    partition_data_paths = bool(getattr(multi, 'partition_data_paths', True))
    try:
        stagger_sec = float(getattr(multi, 'stagger_sec', 0.0) or 0.0)
    except Exception:
        stagger_sec = 0.0
    if stagger_sec < 0.0:
        stagger_sec = 0.0

    if not multi_enabled:
        # Safety: multi-asset is not enabled -> never fan-out parallel prepares.
        workers = 1
    else:
        workers = int(max_parallel_assets) if max_parallel_assets is not None else int(getattr(multi, 'max_parallel_assets', 1) or 1)
        workers = max(1, min(int(workers), len(scopes) if scopes else 1))

    # Prepare phase writes to market DB + dataset files.
    # Keep it single-worker unless per-scope data paths are partitioned.
    workers_prepare = workers
    if multi_enabled and workers_prepare > 1 and not partition_data_paths:
        workers_prepare = 1
        errors.append('prepare_parallel_disabled:partition_data_paths_false')

    # Pre-resolve per-scope data paths.
    data_paths_by_tag: dict[str, ScopeDataPaths] = {}
    for s in scopes:
        try:
            data_paths_by_tag[s.scope_tag] = _scope_data_paths(Path(root), cfg, s)
        except Exception as exc:
            errors.append(f'data_paths_failed:{s.scope_tag}:{type(exc).__name__}:{exc}')

    # Pre-resolve per-scope runtime DB paths (signals/state).
    runtime_paths_by_tag: dict[str, ScopeRuntimePaths] = {}
    for s in scopes:
        try:
            runtime_paths_by_tag[s.scope_tag] = _scope_runtime_paths(Path(root), cfg, s)
        except Exception as exc:
            errors.append(f"runtime_paths_failed:{s.scope_tag}:{type(exc).__name__}:{exc}")
            runtime_paths_by_tag[s.scope_tag] = resolve_scope_runtime_paths(
                Path(root), scope_tag=str(s.scope_tag), partition_enable=False
            )


    # Runtime governor / cycle budget metadata.
    runtime_governor_meta = _load_runtime_governor(root=Path(root), cfg=cfg, scopes=scopes)
    runtime_governor = dict(runtime_governor_meta.get('governor') or {})
    governor_mode = str(runtime_governor.get('mode') or 'runtime_default')
    governor_prepare_sleep_sec = max(
        float(stagger_sec or 0.0),
        max(0.0, float(runtime_governor.get('sleep_between_scopes_ms') or 0) / 1000.0),
    )
    governor_candidate_sleep_sec = max(
        float(stagger_sec or 0.0),
        max(0.0, float(runtime_governor.get('sleep_between_candidate_scopes_ms') or 0) / 1000.0),
    )
    refresh_timeout_sec = int(runtime_governor.get('refresh_market_context_timeout_sec') or _env_int('REFRESH_MARKET_CONTEXT_TIMEOUT_SEC', 120))
    candidate_budget = max(1, min(len(scopes) if scopes else 1, int(runtime_governor.get('max_candidate_scopes_per_run') or (len(scopes) if scopes else 1))))
    scope_order = str(runtime_governor.get('scope_order') or 'best_first_round_robin')
    allow_budget_rotation = bool(getattr(getattr(cfg, 'multi_asset', None), 'candidate_budget_rotation_enable', True))
    prepare_fallback_budget = max(0, int(runtime_governor.get('max_asset_prepare_fallback_scopes') or len(scopes or [])))
    prepare_strategy_counts: dict[str, int] = {}

    # --- Prepare phase (market DB + dataset files) ---
    if scopes:
        if workers_prepare > 1:
            with ThreadPoolExecutor(max_workers=workers_prepare) as pool:
                futs: dict[Any, PortfolioScope] = {}
                for idx, s in enumerate(scopes):
                    if s.scope_tag not in data_paths_by_tag:
                        continue
                    fut = pool.submit(
                        _prepare_scope_runtime,
                        repo_root=root,
                        config_path=cfg_path,
                        cfg=cfg,
                        scope=s,
                        data_paths=data_paths_by_tag[s.scope_tag],
                        lookback_candles=lookback_candles,
                        stagger_delay_sec=compute_stagger_delay(idx, stagger_sec=governor_prepare_sleep_sec, workers=workers_prepare),
                        refresh_timeout_sec=refresh_timeout_sec,
                        allow_prepare_fallback=True,
                    )
                    futs[fut] = s
                for fut in as_completed(futs):
                    s = futs[fut]
                    try:
                        item = fut.result()
                        prepare_results.append(item)
                        prepare_strategy = str(item.get('strategy') or 'unknown')
                        prepare_strategy_counts[prepare_strategy] = int(prepare_strategy_counts.get(prepare_strategy, 0)) + 1
                        for o in list(item.get('steps') or []):
                            if int(o.get('returncode') or 0) != 0:
                                errors.append(f'prepare_step_failed:{s.scope_tag}:{o.get("name")}:rc={o.get("returncode")}')
                    except Exception as exc:
                        errors.append(f'prepare_failed:{s.scope_tag}:{type(exc).__name__}:{exc}')
        else:
            for idx, s in enumerate(scopes):
                if s.scope_tag not in data_paths_by_tag:
                    continue
                try:
                    item = _prepare_scope_runtime(
                        repo_root=root,
                        config_path=cfg_path,
                        cfg=cfg,
                        scope=s,
                        data_paths=data_paths_by_tag[s.scope_tag],
                        lookback_candles=lookback_candles,
                        stagger_delay_sec=compute_stagger_delay(idx, stagger_sec=governor_prepare_sleep_sec, workers=workers_prepare),
                        refresh_timeout_sec=refresh_timeout_sec,
                        allow_prepare_fallback=prepare_fallback_budget > 0,
                    )
                    if bool(item.get('fallback_used')):
                        prepare_fallback_budget = max(0, prepare_fallback_budget - 1)
                    prepare_results.append(item)
                    prepare_strategy = str(item.get('strategy') or 'unknown')
                    prepare_strategy_counts[prepare_strategy] = int(prepare_strategy_counts.get(prepare_strategy, 0)) + 1
                    for o in list(item.get('steps') or []):
                        if int(o.get('returncode') or 0) != 0:
                            errors.append(f'prepare_step_failed:{s.scope_tag}:{o.get("name")}:rc={o.get("returncode")}')
                except Exception as exc:
                    errors.append(f'prepare_failed:{s.scope_tag}:{type(exc).__name__}:{exc}')

    # --- Candidate phase ---
    # When multi-asset is enabled we partition runtime sqlite DBs per scope_tag
    # (signals/state) so candidate observation can run in parallel without
    # SQLite locking.
    candidate_parallel = (multi_enabled and partition_data_paths and len(scopes) > 1 and workers > 1 and candidate_budget >= len(scopes))

    def _run_candidate(s: PortfolioScope, idx: int) -> tuple[SubprocessOutcome, CandidateDecision, str | None]:
        try:
            outcome, cand = candidate_scope(
                repo_root=root,
                config_path=cfg_path,
                scope=s,
                data_paths=data_paths_by_tag[s.scope_tag],
                runtime_paths=runtime_paths_by_tag[s.scope_tag],
                topk=topk,
                lookback_candles=lookback_candles,
                stagger_delay_sec=compute_stagger_delay(
                    idx, stagger_sec=governor_candidate_sleep_sec, workers=(workers if candidate_parallel else 1)
                ),
                cfg=cfg,
            )
            err: str | None = None
            if outcome.returncode != 0:
                err = f"candidate_failed:{s.scope_tag}:rc={outcome.returncode}"
            return outcome, cand, err
        except Exception as e:
            outcome = SubprocessOutcome(
                name=f"observe_once:{s.scope_tag}",
                argv=[],
                cwd=str(Path(root).resolve()),
                returncode=1,
                duration_sec=0.0,
                stdout_tail='',
                stderr_tail=f"exception:{type(e).__name__}:{e}",
            )
            cand = CandidateDecision(
                scope_tag=s.scope_tag,
                asset=s.asset,
                interval_sec=s.interval_sec,
                day=None,
                ts=None,
                action='HOLD',
                score=0.0,
                conf=0.0,
                ev=-1.0,
                reason='candidate_exception',
                blockers='candidate_exception',
                decision_path=str(
                    scope_decision_latest_path(asset=s.asset, interval_sec=s.interval_sec, out_dir=Path(root) / 'runs')
                ),
                raw={'kind': 'candidate_exception', 'error': f"{type(e).__name__}:{e}"},
            )
            return outcome, cand, f"candidate_failed:{s.scope_tag}:exc={type(e).__name__}"

    ordered_candidate_scopes = _ordered_scopes_for_candidate_budget(Path(root), scopes)
    budget_scope_order = scope_order if allow_budget_rotation else 'best_first'
    budgeted_candidate_scopes, candidate_budget_meta = select_governed_items(
        ordered_candidate_scopes,
        repo_root=Path(root),
        budget=candidate_budget,
        scope_order=budget_scope_order,
    )
    budgeted_scope_tags = {scope.scope_tag for scope in budgeted_candidate_scopes}

    results_by_tag: dict[str, tuple[SubprocessOutcome, CandidateDecision, str | None]] = {}

    if candidate_parallel:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_run_candidate, s, idx): s for idx, s in enumerate(budgeted_candidate_scopes)}
            for fut in as_completed(futs):
                s = futs[fut]
                results_by_tag[s.scope_tag] = fut.result()
    else:
        for idx, s in enumerate(budgeted_candidate_scopes):
            results_by_tag[s.scope_tag] = _run_candidate(s, idx)

    for s in scopes:
        if s.scope_tag not in budgeted_scope_tags:
            results_by_tag[s.scope_tag] = (*_budget_skipped_candidate(root=Path(root), scope=s, reason='governed_scope_rotation', governor_mode=governor_mode), None)
        outcome, cand, err = results_by_tag[s.scope_tag]
        candidate_results.append(
            {
                'scope_tag': s.scope_tag,
                'asset': s.asset,
                'interval_sec': s.interval_sec,
                'runtime_paths': runtime_paths_by_tag[s.scope_tag].as_dict(),
                'outcome': outcome.as_dict(),
                'budget_skipped': bool(str(cand.reason or '') == 'candidate_budget_skip'),
            }
        )
        candidates.append(cand)
        if err is not None:
            errors.append(err)

    # --- Quota + allocation ---
    asset_quotas = []
    portfolio_quota = None
    allocation_payload = None
    failsafe_blocks: dict[str, str] = {}
    try:
        asset_quotas = compute_asset_quotas(root, scopes, config_path=cfg_path)
        portfolio_quota = compute_portfolio_quota(root, scopes, config_path=cfg_path)

        # Per-scope precheck (kill-switch/circuit/market context). Quotas are
        # handled at the allocator layer.
        for s in scopes:
            mc = None
            try:
                mc_path = Path(root) / scope_market_context_path(asset=s.asset, interval_sec=int(s.interval_sec))
                mc = load_json_cached(str(mc_path))
            except Exception:
                mc = None
            decision = run_precheck(
                failsafe,
                asset=s.asset,
                interval_sec=int(s.interval_sec),
                control_repo=control_repo,
                market_context=mc,
                quota_hard_block=False,
                quota_reason=None,
                env=dict(os.environ),
                enforce_market_context=True,
            )
            if bool(decision.blocked):
                failsafe_blocks[s.scope_tag] = str(decision.reason or 'blocked')
        allocation = _allocator.allocate(
            str(root),
            scopes=scopes,
            candidates=candidates,
            asset_quotas=asset_quotas,
            portfolio_quota=portfolio_quota,
            failsafe_blocks=failsafe_blocks,
            config_path=str(cfg_path),
        )
        allocation_payload = allocation.as_dict()
        allocation_payload['cycle_id'] = str(cycle_id)
        allocation_payload['scopes'] = [s.as_dict() for s in scopes]
        allocation_payload['persisted_paths'] = write_portfolio_latest_payload(
            root,
            name='portfolio_allocation_latest.json',
            payload=allocation_payload,
            config_path=cfg_path,
            profile=str(getattr(getattr(cfg, 'runtime', None), 'profile', 'default') or 'default'),
            write_legacy=True,
        )
    except Exception as exc:
        errors.append(f'allocation_failed:{type(exc).__name__}:{exc}')

    # --- Execution phase (only selected) ---
    execution_plan: list[dict[str, Any]] = []
    if allocation_payload is not None:
        if kill_active or drain_active:
            errors.append('execution_skipped:kill_or_drain')
        else:
            selected = allocation_payload.get('selected') or []
            selected_tags = [str(i.get('scope_tag')) for i in selected if isinstance(i, dict)]
            exec_disabled = bool(selected_tags) and not bool(getattr(cfg.execution, 'enabled', False))
            try:
                execution_stagger_sec = float(getattr(cfg.multi_asset, 'execution_stagger_sec', 0.0) or 0.0)
            except Exception:
                execution_stagger_sec = 0.0
            if execution_stagger_sec <= 0.0:
                try:
                    execution_stagger_sec = float(getattr(cfg.multi_asset, 'stagger_sec', 0.0) or 0.0)
                except Exception:
                    execution_stagger_sec = 0.0
            execution_plan = build_execution_plan(
                selected=selected,
                scopes=scopes,
                stagger_sec=execution_stagger_sec,
            )
            if exec_disabled:
                errors.append('execution_skipped:execution_disabled')
            for idx, tag in enumerate(selected_tags):
                s = next((x for x in scopes if x.scope_tag == tag), None)
                if s is None:
                    continue
                try:
                    if idx > 0 and execution_stagger_sec > 0.0:
                        time.sleep(float(execution_stagger_sec))
                    dp = data_paths_by_tag.get(s.scope_tag)
                    if dp is None:
                        continue
                    plan_item = next((item for item in execution_plan if str(item.get('scope_tag')) == str(s.scope_tag)), None)
                    outcome, payload = execute_scope(
                        repo_root=root,
                        config_path=cfg_path,
                        scope=s,
                        data_paths=dp,
                        runtime_paths=runtime_paths_by_tag.get(s.scope_tag),
                    )
                    execution_results.append(
                        {
                            'scope_tag': s.scope_tag,
                            'asset': s.asset,
                            'interval_sec': s.interval_sec,
                            'correlation_group': str(getattr(s, 'correlation_group', None) or resolve_correlation_group(s.asset, s.cluster_key)),
                            'stagger_delay_sec': (plan_item or {}).get('stagger_delay_sec'),
                            'scheduled_at_utc': (plan_item or {}).get('scheduled_at_utc'),
                            'outcome': outcome.as_dict(),
                            'payload': payload,
                        }
                    )
                    if int(outcome.returncode) != 0:
                        errors.append(f'execution_failed:{s.scope_tag}:rc={outcome.returncode}')
                except Exception as exc:
                    errors.append(f'execution_exception:{s.scope_tag}:{type(exc).__name__}:{exc}')

    # Package P: persist circuit-breaker outcomes for portfolio mode.
    try:
        now = datetime.now(tz=UTC)
        for s in scopes:
            # do not count failures while explicitly gated
            if kill_active:
                continue
            scope_ok = True
            for e in errors:
                if s.scope_tag in str(e) and ('failed' in str(e) or 'exception' in str(e)):
                    scope_ok = False
                    break
            snap = control_repo.load_breaker(s.asset, int(s.interval_sec))
            if scope_ok:
                snap = failsafe.record_success(snap)
            else:
                snap = failsafe.record_failure(snap, reason='portfolio_cycle_failure', now_utc=now)
            control_repo.save_breaker(snap)
    except Exception:
        pass

    finished = _utc_now_iso()
    ok = len([e for e in errors if 'failed' in e or 'exception' in e]) == 0
    msg = 'ok' if ok else 'errors'

    report = PortfolioCycleReport(
        cycle_id=str(cycle_id),
        started_at_utc=str(started),
        finished_at_utc=str(finished),
        ok=bool(ok),
        message=str(msg),
        scopes=[s.as_dict() for s in scopes],
        prepare=prepare_results,
        candidate_results=candidate_results,
        candidates=[c.as_dict() for c in candidates],
        allocation=allocation_payload,
        execution=execution_results,
        errors=errors,
        execution_plan=execution_plan,
        gates=gates,
        failsafe_blocks=failsafe_blocks,
    )

    # Persist latest cycle
    report_payload = report.as_dict()
    report_payload['runtime_governor'] = runtime_governor_meta
    report_payload['candidate_budget'] = {
        **candidate_budget_meta,
        'ordered_scope_tags': [scope.scope_tag for scope in ordered_candidate_scopes],
        'selected_scope_tags': [scope.scope_tag for scope in budgeted_candidate_scopes],
        'rotation_enabled': bool(allow_budget_rotation),
        'governor_mode': governor_mode,
    }
    report_payload['prepare_summary'] = {
        'strategy_counts': prepare_strategy_counts,
        'refresh_timeout_sec': int(refresh_timeout_sec),
    }
    report_payload['persisted_paths'] = write_portfolio_latest_payload(
        root,
        name='portfolio_cycle_latest.json',
        payload=report_payload,
        config_path=cfg_path,
        profile=str(getattr(getattr(cfg, 'runtime', None), 'profile', 'default') or 'default'),
        write_legacy=True,
    )

    # Persist history (sqlite)
    try:
        PortfolioRepository(Path(root) / 'runs' / 'runtime_portfolio.sqlite3').save_cycle(report_payload)
    except Exception:
        pass

    return report


def run_portfolio_observe(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    once: bool,
    max_cycles: int | None,
    topk: int,
    lookback_candles: int,
    sleep_sec: int = 1,
) -> tuple[int, dict[str, Any]]:
    """Portfolio observe loop.

    Package P hardens the loop with:

    * a portfolio-level lock (prevents concurrent runners)
    * optional /metrics + /healthz HTTP server (Prometheus-style)
    * candle-aligned scheduling across all scopes
    * structured JSONL logs
    """

    root = Path(repo_root).resolve()
    # resolve_config_path() is keyword-only (forces explicit semantics)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    scopes, cfg = load_scopes(repo_root=root, config_path=cfg_path)

    lock_path = root / 'runs' / 'portfolio_observe.lock'
    lock_res = _acquire_lock(lock_path)
    if not bool(getattr(lock_res, 'acquired', False)):
        return 3, {
            'ok': False,
            'message': f'lock_exists:{lock_path.name}',
            'lock_path': str(lock_path),
            'lock_pid': getattr(lock_res, 'pid', None),
            'lock_age_sec': getattr(lock_res, 'age_sec', None),
            'lock_detail': getattr(lock_res, 'detail', None),
        }

    telemetry_state = TelemetryState()
    telemetry_server: TelemetryServer | None = None
    obs_cfg = getattr(cfg, 'observability', None)

    # Register metrics once.
    m_portfolio_cycle_total = REGISTRY.counter(
        'thalor_portfolio_cycle_total',
        help='Total number of portfolio cycles',
        labelnames=('ok',),
    )
    m_portfolio_cycle_seconds = REGISTRY.histogram(
        'thalor_portfolio_cycle_duration_seconds',
        help='Portfolio cycle duration (seconds)',
        labelnames=(),
    )

    try:
        if bool(getattr(obs_cfg, 'metrics_enable', False)):
            bind = str(getattr(obs_cfg, 'metrics_bind', '127.0.0.1:9108'))
            telemetry_server = TelemetryServer(bind=bind, state=telemetry_state)
            telemetry_server.start()
            telemetry_state.update(ready=True, ready_reason='ok')
    except Exception:
        telemetry_server = None

    cycles = 0
    last: dict[str, Any] | None = None
    interrupted = False
    interrupted_at_utc: str | None = None
    try:
        while True:
            cycles += 1
            t0 = time.perf_counter()
            rep = run_portfolio_cycle(
                repo_root=str(root),
                config_path=str(cfg_path),
                topk=int(topk),
                lookback_candles=int(lookback_candles),
            )
            last = rep.as_dict()
            ok = bool(last.get('ok')) if last else False

            # Update telemetry.
            try:
                m_portfolio_cycle_total.inc(1, ok=str(ok).lower())
                m_portfolio_cycle_seconds.observe(max(0.0, time.perf_counter() - t0))
                telemetry_state.update(
                    last_cycle_ok=ok,
                    last_cycle_id=last.get('cycle_id') if last else None,
                    last_cycle_message=str(last.get('message') or '') if last else None,
                    kill_switch_active=bool((last.get('gates') or {}).get('kill_switch_active')) if last else False,
                    drain_mode_active=bool((last.get('gates') or {}).get('drain_mode_active')) if last else False,
                    ready=True,
                    ready_reason='ok' if ok else 'errors',
                )
                if last:
                    fb = last.get('failsafe_blocks') or {}
                    for s in scopes:
                        telemetry_state.scope_update(s.scope_tag, blocked_reason=fb.get(s.scope_tag))
            except Exception:
                pass

            # Structured JSONL logs.
            try:
                if bool(getattr(obs_cfg, 'structured_logs_enable', True)):
                    log_path = getattr(obs_cfg, 'structured_logs_path', Path('runs/logs/runtime_structured.jsonl'))
                    if not Path(log_path).is_absolute():
                        log_path = root / Path(log_path)
                    append_jsonl(
                        log_path,
                        {
                            'event': 'portfolio_cycle',
                            'ok': ok,
                            'cycle_id': last.get('cycle_id') if last else None,
                            'selected_count': len((last.get('allocation') or {}).get('selected') or []) if last else 0,
                            'kill_switch_active': bool((last.get('gates') or {}).get('kill_switch_active')) if last else False,
                            'drain_mode_active': bool((last.get('gates') or {}).get('drain_mode_active')) if last else False,
                        },
                    )
            except Exception:
                pass

            if once:
                break
            if max_cycles is not None and int(max_cycles) > 0 and cycles >= int(max_cycles):
                break

            # Candle-aligned scheduling across all configured scopes.
            sleep_for = _next_wake_sleep_sec(
                scopes=scopes,
                offset_sec=int(os.getenv('THALOR_SLEEP_ALIGN_OFFSET_SEC', '3')),
            )

            # Allow graceful shutdown during the sleep window (Ctrl+C).
            try:
                time.sleep(max(1.0, float(sleep_for)))
            except KeyboardInterrupt:
                interrupted = True
                interrupted_at_utc = datetime.now(tz=UTC).isoformat(timespec='seconds')
                break

        if interrupted:
            code = 130
        else:
            code = 0 if bool(last and last.get('ok')) else 2
        return code, {
            'ok': bool(last and last.get('ok')) and (not interrupted),
            'cycles': int(cycles),
            'interrupted': bool(interrupted),
            'interrupted_at_utc': interrupted_at_utc,
            'last': last,
        }
    finally:
        try:
            if telemetry_server is not None:
                telemetry_server.stop()
        except Exception:
            pass
        _release_lock(lock_path)
