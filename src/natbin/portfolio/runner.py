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
from ..runtime_perf import write_text_if_changed
from ..runtime_perf import load_json_cached
from ..state.control_repo import RuntimeControlRepository
from ..state.portfolio_repo import PortfolioRepository
from ..telemetry import TelemetryServer, TelemetryState
from ..telemetry.metrics import REGISTRY
from ..ops.lockfile import acquire_lock as acquire_lockfile
from ..ops.lockfile import release_lock as release_lockfile
from ..ops.structured_log import append_jsonl

from . import allocator as _allocator
from .models import CandidateDecision, PortfolioCycleReport, PortfolioScope
from .paths import (
    ScopeDataPaths,
    ScopeRuntimePaths,
    portfolio_allocation_latest_path,
    portfolio_cycle_latest_path,
    resolve_scope_data_paths,
    resolve_scope_runtime_paths,
    scope_tag as compute_scope_tag,
    scoped_env,
)
from .quota import compute_asset_quotas, compute_portfolio_quota
from .subprocess import SubprocessOutcome, run_python_module


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
        scopes.append(
            PortfolioScope(
                asset=asset,
                interval_sec=interval_sec,
                timezone=tz,
                scope_tag=tag,
                weight=float(getattr(a, 'weight', 1.0) or 1.0),
                cluster_key=str(getattr(a, 'cluster_key', 'default') or 'default'),
                topk_k=int(getattr(a, 'topk_k', 3) or 3),
                hard_max_trades_per_day=getattr(a, 'hard_max_trades_per_day', None),
                max_open_positions=getattr(a, 'max_open_positions', None),
                max_pending_unknown=getattr(a, 'max_pending_unknown', None),
            )
        )

    return scopes, cfg


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


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        return None


def _candidate_from_decision(scope: PortfolioScope, decision: dict[str, Any] | None, *, decision_path: Path) -> CandidateDecision:
    if not decision:
        return CandidateDecision(
            scope_tag=scope.scope_tag,
            asset=scope.asset,
            interval_sec=int(scope.interval_sec),
            day=None,
            ts=None,
            action='HOLD',
            score=None,
            conf=None,
            ev=None,
            reason='decision_missing',
            blockers=None,
            decision_path=str(decision_path),
            raw={},
        )

    action = str(decision.get('action') or decision.get('signal') or 'HOLD').upper()
    try:
        ts = int(decision.get('ts') or decision.get('signal_ts') or 0)
    except Exception:
        ts = None
    if ts == 0:
        ts = None
    day = decision.get('day')
    if day is not None:
        day = str(day)
    score = None
    conf = None
    ev = None
    try:
        if decision.get('score') is not None:
            score = float(decision.get('score'))
    except Exception:
        score = None
    try:
        if decision.get('conf') is not None:
            conf = float(decision.get('conf'))
    except Exception:
        conf = None
    try:
        if decision.get('ev') is not None:
            ev = float(decision.get('ev'))
    except Exception:
        ev = None

    return CandidateDecision(
        scope_tag=scope.scope_tag,
        asset=scope.asset,
        interval_sec=int(scope.interval_sec),
        day=day,
        ts=ts,
        action=action,
        score=score,
        conf=conf,
        ev=ev,
        reason=str(decision.get('reason') or decision.get('why') or '') or None,
        blockers=str(decision.get('blockers') or '') or None,
        decision_path=str(decision_path),
        raw=dict(decision),
    )


def prepare_scope(
    *,
    repo_root: str | Path,
    config_path: str | Path | None,
    scope: PortfolioScope,
    data_paths: ScopeDataPaths,
    lookback_candles: int,
) -> list[SubprocessOutcome]:
    """Prepare data for a single scope.

    We keep this step isolated per scope and safe for parallel execution.

    NOTE: The legacy pipeline writes to the signals DB; do not run observer here.
    """

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
            timeout_sec=_env_int('COLLECT_RECENT_TIMEOUT_SEC', 180),
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
) -> tuple[SubprocessOutcome, CandidateDecision]:
    """Run observer once for a scope (execution disabled) and return candidate decision."""

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
        execution_enabled=True,
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

    # Determine parallelism.
    workers = int(max_parallel_assets) if max_parallel_assets is not None else int(getattr(cfg.multi_asset, 'max_parallel_assets', 1) or 1)
    workers = max(1, min(int(workers), len(scopes) if scopes else 1))

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


    # --- Prepare phase (parallel-safe) ---
    if scopes:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {
                pool.submit(
                    prepare_scope,
                    repo_root=root,
                    config_path=cfg_path,
                    scope=s,
                    data_paths=data_paths_by_tag[s.scope_tag],
                    lookback_candles=lookback_candles,
                ): s
                for s in scopes
                if s.scope_tag in data_paths_by_tag
            }
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    outcomes = fut.result()
                    prepare_results.append(
                        {
                            'scope_tag': s.scope_tag,
                            'asset': s.asset,
                            'interval_sec': s.interval_sec,
                            'steps': [o.as_dict() for o in outcomes],
                        }
                    )
                    for o in outcomes:
                        if int(o.returncode) != 0:
                            errors.append(f'prepare_step_failed:{s.scope_tag}:{o.name}:rc={o.returncode}')
                except Exception as exc:
                    errors.append(f'prepare_failed:{s.scope_tag}:{type(exc).__name__}:{exc}')

    # --- Candidate phase ---
    # When multi-asset is enabled we partition runtime sqlite DBs per scope_tag
    # (signals/state) so candidate observation can run in parallel without
    # SQLite locking.
    candidate_parallel = (
        bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False))
        and bool(getattr(getattr(cfg, 'multi_asset', None), 'partition_data_paths', True))
        and len(scopes) > 1
        and workers > 1
    )

    def _run_candidate(s: PortfolioScope) -> tuple[SubprocessOutcome, CandidateDecision, str | None]:
        try:
            outcome, cand = candidate_scope(
                repo_root=root,
                config_path=cfg_path,
                scope=s,
                data_paths=data_paths_by_tag[s.scope_tag],
                runtime_paths=runtime_paths_by_tag[s.scope_tag],
                topk=topk,
                lookback_candles=lookback_candles,
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

    results_by_tag: dict[str, tuple[SubprocessOutcome, CandidateDecision, str | None]] = {}

    if candidate_parallel:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_run_candidate, s): s for s in scopes}
            for fut in as_completed(futs):
                s = futs[fut]
                results_by_tag[s.scope_tag] = fut.result()
    else:
        for s in scopes:
            results_by_tag[s.scope_tag] = _run_candidate(s)

    for s in scopes:
        outcome, cand, err = results_by_tag[s.scope_tag]
        candidate_results.append(
            {
                'scope_tag': s.scope_tag,
                'asset': s.asset,
                'interval_sec': s.interval_sec,
                'runtime_paths': runtime_paths_by_tag[s.scope_tag].as_dict(),
                'outcome': outcome.as_dict(),
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
        # persist latest allocation
        write_text_if_changed(portfolio_allocation_latest_path(root), json.dumps(allocation_payload, indent=2, ensure_ascii=False, default=str))
    except Exception as exc:
        errors.append(f'allocation_failed:{type(exc).__name__}:{exc}')

    # --- Execution phase (only selected) ---
    if allocation_payload is not None:
        if kill_active or drain_active:
            errors.append('execution_skipped:kill_or_drain')
        else:
            selected = allocation_payload.get('selected') or []
            selected_tags = [str(i.get('scope_tag')) for i in selected if isinstance(i, dict)]
            for tag in selected_tags:
                s = next((x for x in scopes if x.scope_tag == tag), None)
                if s is None:
                    continue
                try:
                    dp = data_paths_by_tag.get(s.scope_tag)
                    if dp is None:
                        continue
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
        gates=gates,
        failsafe_blocks=failsafe_blocks,
    )

    # Persist latest cycle
    report_payload = report.as_dict()
    write_text_if_changed(portfolio_cycle_latest_path(root), json.dumps(report_payload, indent=2, ensure_ascii=False, default=str))

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
