from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.iq_client import IQClient, IQConfig, IQDependencyUnavailable, iqoption_dependency_status
from ..config.env import env_float
from ..config.loader import load_resolved_config
from ..config.paths import resolve_repo_root
from ..config.settings import load_settings
from ..runtime.scope import market_context_path as scoped_market_context_path
from natbin.runtime.broker_dependency import build_dependency_market_context, infer_market_open_from_db, read_cached_json, write_json


def default_market_context_path(asset: str, interval_sec: int) -> Path:
    return scoped_market_context_path(asset=asset, interval_sec=interval_sec, out_dir='runs')


def _market_context_path(asset: str, interval_sec: int) -> Path:
    out_env = os.getenv("MARKET_CONTEXT_PATH", "").strip()
    return Path(out_env) if out_env else default_market_context_path(asset=asset, interval_sec=interval_sec)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ''):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


def _broker_runtime_options(*, repo_root: Path) -> dict[str, Any]:
    try:
        resolved = load_resolved_config(repo_root=repo_root)
        broker = getattr(resolved, 'broker', None)
    except Exception:
        broker = None
    return {
        'market_context_cache_fallback_enable': _as_bool(getattr(broker, 'market_context_cache_fallback_enable', None), False),
        'market_context_cache_max_age_sec': int(getattr(broker, 'market_context_cache_max_age_sec', 21600) or 21600),
    }


def _parse_iso(raw: Any):
    if raw in (None, ''):
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _cached_payload(path: Path, *, max_age_sec: int) -> dict[str, Any] | None:
    payload = read_cached_json(path)
    if not isinstance(payload, dict):
        return None
    stamp = _parse_iso(payload.get('at_utc'))
    if stamp is None:
        return None
    age_sec = max(0.0, (datetime.now(UTC) - stamp).total_seconds())
    if age_sec > max_age_sec:
        return None
    return payload


def _broker_failure_cache_fallback(*, asset: str, interval_sec: int, db_path: str, out_path: Path, dependency_reason: str, broker_options: dict[str, Any], payout_fallback: float) -> dict[str, Any]:
    payload = build_dependency_market_context(
        asset=asset,
        interval_sec=interval_sec,
        db_path=db_path,
        payout_fallback=payout_fallback,
        ctx_path=out_path,
        dependency_reason=dependency_reason,
    )
    cached = _cached_payload(out_path, max_age_sec=int(broker_options.get('market_context_cache_max_age_sec') or 21600)) if broker_options.get('market_context_cache_fallback_enable') else None
    market_open, open_source, last_candle_ts = infer_market_open_from_db(db_path=db_path, asset=asset, interval_sec=interval_sec)
    payload.update({
        'market_open': bool(market_open),
        'open_source': str(open_source),
        'last_candle_ts': int(last_candle_ts) if last_candle_ts is not None else None,
        'broker_available': False,
        'fallback_mode': 'broker_failure_cache_fallback',
        'cache_used': bool(cached),
    })
    if isinstance(cached, dict):
        try:
            cached_payout = cached.get('payout')
            if cached_payout not in (None, ''):
                payload['payout'] = float(cached_payout)
                payload['payout_source'] = str(cached.get('payout_source') or 'cached')
        except Exception:
            pass
    write_json(out_path, payload)
    return payload




def _build_client(*, repo_root: Path, settings, asset: str, interval_sec: int) -> IQClient:
    client = IQClient.from_runtime_config(repo_root=repo_root, asset=asset, interval_sec=interval_sec)
    manager = getattr(client, '_transport_manager', None)
    if manager is not None and bool(getattr(manager, 'enabled', False)) and not bool(getattr(manager, 'ready', False)):
        return IQClient(
            IQConfig(
                email=str(getattr(settings.iq, 'email', '') or ''),
                password=str(getattr(settings.iq, 'password', '') or ''),
                balance_mode=str(getattr(settings.iq, 'balance_mode', 'PRACTICE') or 'PRACTICE'),
            )
        )
    return client

def main() -> None:
    repo_root = resolve_repo_root()
    broker_options = _broker_runtime_options(repo_root=repo_root)

    s = load_settings()
    asset = s.data.asset
    interval_sec = int(s.data.interval_sec)
    db_path = s.data.db_path
    out_path = _market_context_path(asset=asset, interval_sec=interval_sec)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payout_fallback = env_float("PAYOUT_FALLBACK", env_float("PAYOUT", 0.8))

    dep = iqoption_dependency_status()
    if not bool(dep.get("available", True)):
        payload = build_dependency_market_context(
            asset=asset,
            interval_sec=interval_sec,
            db_path=db_path,
            payout_fallback=payout_fallback,
            ctx_path=out_path,
            dependency_reason=str(dep.get("reason")),
        )
        write_json(out_path, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return

    try:
        client = _build_client(repo_root=repo_root, settings=s, asset=asset, interval_sec=interval_sec)
        client.connect()
        ctx = client.get_market_context(asset=asset, interval_sec=interval_sec, payout_fallback=payout_fallback)
    except IQDependencyUnavailable as exc:
        payload = build_dependency_market_context(
            asset=asset,
            interval_sec=interval_sec,
            db_path=db_path,
            payout_fallback=payout_fallback,
            ctx_path=out_path,
            dependency_reason=str(exc),
        )
        write_json(out_path, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return
    except Exception as exc:
        if broker_options.get('market_context_cache_fallback_enable'):
            payload = _broker_failure_cache_fallback(
                asset=asset,
                interval_sec=interval_sec,
                db_path=db_path,
                out_path=out_path,
                dependency_reason=f'{type(exc).__name__}: {exc}',
                broker_options=broker_options,
                payout_fallback=payout_fallback,
            )
            print(json.dumps(payload, ensure_ascii=False))
            return
        raise

    market_open, open_source, last_candle_ts = infer_market_open_from_db(db_path=db_path, asset=asset, interval_sec=interval_sec)

    payload = {
        "asset": asset,
        "interval_sec": interval_sec,
        "market_open": bool(market_open),
        "open_source": str(open_source),
        "payout": float(ctx.get("payout", payout_fallback)),
        "payout_source": str(ctx.get("payout_source", "fallback")),
        "last_candle_ts": int(last_candle_ts) if last_candle_ts is not None else None,
        "at_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "dependency_available": True,
        "dependency_reason": None,
    }

    write_json(out_path, payload)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
