from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config.execution_mode import MODE_LIVE, normalize_execution_mode
from ..runtime.execution_policy import ensure_utc_iso, utc_now_iso
from ..state.control_repo import read_control_artifact, write_control_artifact
from ..state.execution_repo import ExecutionRepository
from .broker_surface import execution_cfg, execution_repo_path


@dataclass(frozen=True)
class ExecutionHardeningDecision:
    kind: str
    allowed: bool
    action: str
    reason: str | None
    scope_tag: str
    asset: str
    interval_sec: int
    provider: str
    mode: str
    account_mode: str
    live_real_mode: bool
    multi_asset_enabled: bool
    checked_at_utc: str
    last_submit_at_utc: str | None = None
    open_positions_total: int | None = None
    pending_unknown_total: int | None = None
    recent_transport_failures: int | None = None
    lock_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PostSubmitVerification:
    enabled: bool
    verified: bool
    reason: str | None
    checked_at_utc: str
    external_order_id: str | None
    broker_status: str | None = None
    settlement_status: str | None = None
    poll_attempts: int = 0
    duration_sec: float = 0.0
    snapshot: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_mapping(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if hasattr(raw, 'model_dump'):
        try:
            return raw.model_dump(mode='python')
        except Exception:
            return raw.model_dump()
    try:
        return dict(raw)
    except Exception:
        return {}


def _multi_asset_cfg(ctx) -> dict[str, Any]:
    raw = ctx.resolved_config.get('multi_asset') if isinstance(ctx.resolved_config, dict) else getattr(ctx.resolved_config, 'multi_asset', None)
    return _to_mapping(raw)


def real_guard_cfg(ctx) -> dict[str, Any]:
    return _to_mapping(execution_cfg(ctx).get('real_guard'))


def is_live_real_mode(ctx) -> bool:
    cfg = execution_cfg(ctx)
    return normalize_execution_mode(cfg.get('mode'), default='disabled') == MODE_LIVE and str(cfg.get('account_mode') or 'PRACTICE').upper() == 'REAL'


@contextmanager
def _file_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, 'a+b')
    try:
        if os.name == 'nt':
            import msvcrt  # type: ignore

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl  # type: ignore

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield fh
    finally:
        try:
            if os.name == 'nt':
                import msvcrt  # type: ignore

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # type: ignore

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass


def _resolve_lock_path(repo_root: str | Path, ctx) -> Path:
    cfg = real_guard_cfg(ctx)
    raw = Path(str(cfg.get('submit_lock_path') or 'runs/runtime_execution.submit.lock'))
    if raw.is_absolute():
        return raw
    return Path(repo_root).resolve() / raw


@contextmanager
def live_submit_guard(*, repo_root: str | Path, ctx):
    if not is_live_real_mode(ctx):
        yield None
        return
    cfg = real_guard_cfg(ctx)
    if not bool(cfg.get('enabled', True)) or not bool(cfg.get('serialize_submits', True)):
        yield None
        return
    lock_path = _resolve_lock_path(repo_root, ctx)
    with _file_lock(lock_path):
        yield str(lock_path)


def _write_artifact(*, repo_root: str | Path, ctx, payload: dict[str, Any]) -> None:
    try:
        write_control_artifact(
            repo_root=repo_root,
            asset=ctx.config.asset,
            interval_sec=ctx.config.interval_sec,
            name='execution_hardening',
            payload=payload,
        )
    except Exception:
        pass


def evaluate_execution_hardening(*, repo_root: str | Path, ctx, write_artifact: bool = True) -> ExecutionHardeningDecision:
    now = datetime.now(tz=UTC)
    checked_at = now.isoformat(timespec='seconds')
    ex_cfg = execution_cfg(ctx)
    provider = str(ex_cfg.get('provider') or 'fake').strip().lower()
    mode = normalize_execution_mode(ex_cfg.get('mode'), default='disabled')
    account_mode = str(ex_cfg.get('account_mode') or 'PRACTICE').upper()
    live_real = is_live_real_mode(ctx)
    multi_cfg = _multi_asset_cfg(ctx)
    multi_asset_enabled = bool(multi_cfg.get('enabled', False))
    cfg = real_guard_cfg(ctx)
    details: dict[str, Any] = {
        'guard_enabled': bool(cfg.get('enabled', True)),
        'allow_multi_asset_live': bool(cfg.get('allow_multi_asset_live', False)),
        'require_env_allow_real': bool(cfg.get('require_env_allow_real', True)),
        'serialize_submits': bool(cfg.get('serialize_submits', True)),
        'min_submit_spacing_sec': int(cfg.get('min_submit_spacing_sec') or 0),
        'max_pending_unknown_total': cfg.get('max_pending_unknown_total'),
        'max_open_positions_total': cfg.get('max_open_positions_total'),
        'recent_failure_window_sec': int(cfg.get('recent_failure_window_sec') or 0),
        'max_recent_transport_failures': int(cfg.get('max_recent_transport_failures') or 0),
    }

    base = ExecutionHardeningDecision(
        kind='execution_hardening',
        allowed=True,
        action='skip' if not live_real else 'allow',
        reason='not_live_real_mode' if not live_real else None,
        scope_tag=str(ctx.scope.scope_tag),
        asset=str(ctx.config.asset),
        interval_sec=int(ctx.config.interval_sec),
        provider=provider,
        mode=mode,
        account_mode=account_mode,
        live_real_mode=bool(live_real),
        multi_asset_enabled=bool(multi_asset_enabled),
        checked_at_utc=checked_at,
        lock_path=str(_resolve_lock_path(repo_root, ctx)),
        details=details,
    )
    if not live_real:
        if write_artifact:
            _write_artifact(repo_root=repo_root, ctx=ctx, payload=base.as_dict())
        return base
    if not bool(cfg.get('enabled', True)):
        disabled = ExecutionHardeningDecision(**{**base.as_dict(), 'action': 'allow', 'reason': None, 'details': {**details, 'guard_enabled': False}})
        if write_artifact:
            _write_artifact(repo_root=repo_root, ctx=ctx, payload=disabled.as_dict())
        return disabled

    if bool(cfg.get('require_env_allow_real', True)) and str(os.getenv('THALOR_EXECUTION_ALLOW_REAL') or '').strip() != '1':
        blocked = ExecutionHardeningDecision(
            **{
                **base.as_dict(),
                'allowed': False,
                'action': 'block',
                'reason': 'real_env_not_armed',
                'details': {**details, 'required_env': 'THALOR_EXECUTION_ALLOW_REAL=1'},
            }
        )
        if write_artifact:
            _write_artifact(repo_root=repo_root, ctx=ctx, payload=blocked.as_dict())
        return blocked

    if multi_asset_enabled and not bool(cfg.get('allow_multi_asset_live', False)):
        blocked = ExecutionHardeningDecision(
            **{
                **base.as_dict(),
                'allowed': False,
                'action': 'block',
                'reason': 'real_multi_asset_not_enabled',
                'details': {**details, 'message': 'Set execution.real_guard.allow_multi_asset_live=true only after explicit operator review.'},
            }
        )
        if write_artifact:
            _write_artifact(repo_root=repo_root, ctx=ctx, payload=blocked.as_dict())
        return blocked

    repo = ExecutionRepository(execution_repo_path(repo_root))
    open_positions_total = repo.count_open_positions_global(account_mode=account_mode)
    pending_unknown_total = repo.count_pending_unknown_global(account_mode=account_mode)
    last_submit_at = repo.last_submit_attempt_utc(account_mode=account_mode)
    recent_failure_window_sec = max(0, int(cfg.get('recent_failure_window_sec') or 0))
    recent_transport_failures = 0
    if recent_failure_window_sec > 0:
        since = (now - timedelta(seconds=recent_failure_window_sec)).isoformat(timespec='seconds')
        recent_transport_failures = repo.count_transport_attempts_since(
            since_utc=since,
            account_mode=account_mode,
            transport_statuses=['reject', 'timeout', 'exception'],
        )

    with_counts = {
        **base.as_dict(),
        'open_positions_total': int(open_positions_total),
        'pending_unknown_total': int(pending_unknown_total),
        'recent_transport_failures': int(recent_transport_failures),
        'last_submit_at_utc': last_submit_at,
    }

    max_open_positions_total = cfg.get('max_open_positions_total', 1)
    if max_open_positions_total is not None and int(open_positions_total) >= int(max_open_positions_total):
        blocked = ExecutionHardeningDecision(
            **{
                **with_counts,
                'allowed': False,
                'action': 'block',
                'reason': 'real_open_positions_total_limit',
                'details': {**details, 'current_open_positions_total': int(open_positions_total)},
            }
        )
        if write_artifact:
            _write_artifact(repo_root=repo_root, ctx=ctx, payload=blocked.as_dict())
        return blocked

    max_pending_unknown_total = cfg.get('max_pending_unknown_total', 1)
    if max_pending_unknown_total is not None and int(pending_unknown_total) >= int(max_pending_unknown_total):
        blocked = ExecutionHardeningDecision(
            **{
                **with_counts,
                'allowed': False,
                'action': 'block',
                'reason': 'real_pending_unknown_total_limit',
                'details': {**details, 'current_pending_unknown_total': int(pending_unknown_total)},
            }
        )
        if write_artifact:
            _write_artifact(repo_root=repo_root, ctx=ctx, payload=blocked.as_dict())
        return blocked

    max_recent_transport_failures = max(0, int(cfg.get('max_recent_transport_failures') or 0))
    if max_recent_transport_failures > 0 and int(recent_transport_failures) >= max_recent_transport_failures:
        blocked = ExecutionHardeningDecision(
            **{
                **with_counts,
                'allowed': False,
                'action': 'block',
                'reason': 'real_recent_transport_failures',
                'details': {**details, 'current_recent_transport_failures': int(recent_transport_failures)},
            }
        )
        if write_artifact:
            _write_artifact(repo_root=repo_root, ctx=ctx, payload=blocked.as_dict())
        return blocked

    min_submit_spacing_sec = max(0, int(cfg.get('min_submit_spacing_sec') or 0))
    if min_submit_spacing_sec > 0 and last_submit_at:
        try:
            last_dt = datetime.fromisoformat(str(last_submit_at))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=UTC)
            spacing_open = (now - last_dt.astimezone(UTC)).total_seconds() >= float(min_submit_spacing_sec)
        except Exception:
            spacing_open = True
        if not spacing_open:
            blocked = ExecutionHardeningDecision(
                **{
                    **with_counts,
                    'allowed': False,
                    'action': 'block',
                    'reason': 'real_global_submit_spacing',
                    'details': {**details, 'last_submit_at_utc': last_submit_at},
                }
            )
            if write_artifact:
                _write_artifact(repo_root=repo_root, ctx=ctx, payload=blocked.as_dict())
            return blocked

    allowed = ExecutionHardeningDecision(
        **{
            **with_counts,
            'allowed': True,
            'action': 'allow',
            'reason': None,
            'details': {**details, 'message': 'live real execution hardening checks passed'},
        }
    )
    if write_artifact:
        _write_artifact(repo_root=repo_root, ctx=ctx, payload=allowed.as_dict())
    return allowed


def verify_live_submit(*, repo_root: str | Path, ctx, repo: ExecutionRepository, adapter, intent, external_order_id: str | None) -> PostSubmitVerification:
    cfg = real_guard_cfg(ctx)
    checked_at = utc_now_iso()
    if not is_live_real_mode(ctx):
        return PostSubmitVerification(
            enabled=False,
            verified=False,
            reason='not_live_real_mode',
            checked_at_utc=checked_at,
            external_order_id=external_order_id,
        )
    if not bool(cfg.get('post_submit_verify_enable', True)):
        return PostSubmitVerification(
            enabled=False,
            verified=False,
            reason='post_submit_verify_disabled',
            checked_at_utc=checked_at,
            external_order_id=external_order_id,
        )
    order_id = str(external_order_id or '').strip()
    if not order_id:
        return PostSubmitVerification(
            enabled=True,
            verified=False,
            reason='missing_external_order_id',
            checked_at_utc=checked_at,
            external_order_id=external_order_id,
        )

    timeout_sec = max(1, int(cfg.get('post_submit_verify_timeout_sec') or 8))
    poll_sec = float(cfg.get('post_submit_verify_poll_sec') or 0.5)
    if poll_sec <= 0:
        poll_sec = 0.5

    started = time.perf_counter()
    attempts = 0
    errors: list[str] = []
    deadline = started + float(timeout_sec)
    while time.perf_counter() <= deadline:
        attempts += 1
        try:
            snapshot = adapter.fetch_order(order_id)
        except Exception as exc:  # pragma: no cover - depends on runtime adapter
            errors.append(f'{type(exc).__name__}:{exc}')
            snapshot = None
        if snapshot is not None:
            repo.upsert_broker_snapshot(snapshot, intent_id=intent.intent_id)
            return PostSubmitVerification(
                enabled=True,
                verified=True,
                reason=None,
                checked_at_utc=utc_now_iso(),
                external_order_id=order_id,
                broker_status=str(snapshot.broker_status),
                settlement_status=snapshot.settlement_status,
                poll_attempts=attempts,
                duration_sec=round(max(0.0, time.perf_counter() - started), 3),
                snapshot=snapshot.as_dict(),
                errors=errors,
            )
        if time.perf_counter() + poll_sec > deadline:
            break
        time.sleep(float(poll_sec))

    return PostSubmitVerification(
        enabled=True,
        verified=False,
        reason='post_submit_snapshot_not_found',
        checked_at_utc=utc_now_iso(),
        external_order_id=order_id,
        poll_attempts=attempts,
        duration_sec=round(max(0.0, time.perf_counter() - started), 3),
        errors=errors,
    )


def execution_hardening_payload(*, repo_root: str | Path = '.', config_path: str | Path | None = None) -> dict[str, Any]:
    from .broker_surface import build_context

    ctx = build_context(repo_root=repo_root, config_path=config_path)
    decision = evaluate_execution_hardening(repo_root=repo_root, ctx=ctx, write_artifact=True)
    latest = read_control_artifact(
        repo_root=repo_root,
        asset=ctx.config.asset,
        interval_sec=ctx.config.interval_sec,
        name='execution_hardening',
    )
    payload = decision.as_dict()
    payload['artifact'] = latest
    return payload


__all__ = [
    'ExecutionHardeningDecision',
    'PostSubmitVerification',
    'evaluate_execution_hardening',
    'execution_hardening_payload',
    'is_live_real_mode',
    'live_submit_guard',
    'real_guard_cfg',
    'verify_live_submit',
]
