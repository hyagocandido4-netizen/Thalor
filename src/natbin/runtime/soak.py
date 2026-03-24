from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..control.plan import build_context
from ..runtime.hardening import inspect_runtime_freshness
from ..state.control_repo import read_control_artifact
from .daemon import run_daemon


def _write_soak_artifact(*, repo: Path, scope_tag: str, payload: dict[str, Any]) -> Path:
    out_dir = repo / 'runs' / 'soak'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'soak_latest_{scope_tag}.json'
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return out_path


def build_runtime_soak_summary(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    topk: int = 3,
    lookback_candles: int = 2000,
    max_cycles: int = 12,
    sleep_align_offset_sec: int = 3,
    quota_aware_sleep: bool = False,
    precheck_market_context: bool = False,
    write_artifact: bool = True,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    ctx = build_context(
        repo_root=repo,
        config_path=config_path,
        asset=asset,
        interval_sec=interval_sec,
        dump_snapshot=False,
    )
    summary: dict[str, Any] = {
        'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
        'phase': 'runtime_soak',
        'state': 'running',
        'exit_code': None,
        'interrupted': False,
        'message': None,
        'config_path': str(ctx.config.config_path),
        'scope': {
            'asset': ctx.config.asset,
            'interval_sec': int(ctx.config.interval_sec),
            'scope_tag': ctx.scope.scope_tag,
        },
        'cycles_requested': int(max_cycles) if max_cycles is not None else None,
        'cycles_completed': None,
        'guard': None,
        'lifecycle': None,
        'loop_status': None,
        'freshness': {},
    }
    if write_artifact:
        _write_soak_artifact(repo=repo, scope_tag=str(ctx.scope.scope_tag), payload=summary)

    try:
        exit_code = run_daemon(
            repo_root=repo,
            config_path=config_path,
            asset=asset,
            interval_sec=interval_sec,
            topk=topk,
            lookback_candles=lookback_candles,
            max_cycles=max_cycles,
            sleep_align_offset_sec=sleep_align_offset_sec,
            stop_on_failure=True,
            quota_aware_sleep=bool(quota_aware_sleep),
            precheck_market_context=bool(precheck_market_context),
        )
        summary['exit_code'] = int(exit_code)
        summary['state'] = 'finished'
    except KeyboardInterrupt:
        summary['exit_code'] = 130
        summary['interrupted'] = True
        summary['state'] = 'interrupted'
        summary['message'] = 'interrupted'
        raise
    except Exception as exc:
        summary['exit_code'] = 2
        summary['state'] = 'error'
        summary['message'] = f'{type(exc).__name__}:{exc}'
        raise
    finally:
        freshness = inspect_runtime_freshness(repo_root=repo, ctx=ctx).as_dict()
        guard = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='guard')
        lifecycle = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='lifecycle')
        loop_status = read_control_artifact(repo_root=repo, asset=ctx.config.asset, interval_sec=ctx.config.interval_sec, name='loop_status')
        summary.update(
            {
                'at_utc': datetime.now(tz=UTC).isoformat(timespec='seconds'),
                'cycles_completed': (lifecycle or {}).get('cycles') if isinstance(lifecycle, dict) else None,
                'guard': guard,
                'lifecycle': lifecycle,
                'loop_status': loop_status,
                'freshness': freshness,
            }
        )
        if write_artifact:
            _write_soak_artifact(repo=repo, scope_tag=str(ctx.scope.scope_tag), payload=summary)
    return summary


__all__ = ['build_runtime_soak_summary']
