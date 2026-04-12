from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.iq_client import IQClient, IQConfig, IQDependencyUnavailable, iqoption_dependency_status
from ..config.env import env_float, env_int
from ..config.loader import load_resolved_config
from ..config.paths import resolve_repo_root
from ..config.settings import load_settings
from ..runtime.scope import market_context_path as scoped_market_context_path
from ..state.db import open_db, upsert_candles
from natbin.runtime.broker_dependency import build_dependency_market_context, candle_db_snapshot, write_json


def norm_ts(x: int) -> int:
    x = int(x)
    if x > 1_000_000_000_000:
        x //= 1000
    return x


def is_closed(candle: dict, now_ts: int, interval_sec: int) -> bool:
    f = candle.get("from") or candle.get("time") or 0
    f = norm_ts(int(f))
    return now_ts >= (f + interval_sec)


def default_market_context_path(asset: str, interval_sec: int) -> Path:
    return scoped_market_context_path(asset=asset, interval_sec=interval_sec, out_dir='runs')


def _market_context_path(asset: str, interval_sec: int) -> Path:
    ctx_env = os.getenv("MARKET_CONTEXT_PATH", "").strip()
    return Path(ctx_env) if ctx_env else default_market_context_path(asset=asset, interval_sec=interval_sec)


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
        'collect_reuse_local_data_on_failure': _as_bool(getattr(broker, 'collect_reuse_local_data_on_failure', None), False),
        'collect_reuse_local_max_age_sec': int(getattr(broker, 'collect_reuse_local_max_age_sec', 3600) or 3600),
    }




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

def maybe_write_market_context(client: IQClient, *, asset: str, interval_sec: int, db_path: str) -> None:
    ctx_path = _market_context_path(asset=asset, interval_sec=interval_sec)
    ctx_path.parent.mkdir(parents=True, exist_ok=True)

    payout_fallback = env_float("PAYOUT_FALLBACK", env_float("PAYOUT", 0.8))

    try:
        ctx = client.get_market_context(asset=asset, interval_sec=interval_sec, payout_fallback=payout_fallback)
        from natbin.runtime.broker_dependency import infer_market_open_from_db

        market_open, open_source, last_candle_ts = infer_market_open_from_db(
            db_path=db_path, asset=asset, interval_sec=interval_sec
        )
        payload = {
            "asset": asset,
            "interval_sec": int(interval_sec),
            "market_open": bool(market_open),
            "open_source": str(open_source),
            "payout": float(ctx.get("payout", payout_fallback)),
            "payout_source": str(ctx.get("payout_source", "fallback")),
            "last_candle_ts": int(last_candle_ts) if last_candle_ts is not None else None,
            "at_utc": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "dependency_available": True,
            "dependency_reason": None,
        }
        write_json(ctx_path, payload)
    except Exception as e:
        print(f"[P30] WARN: market_context cache write failed: {type(e).__name__}: {e}")


def _dependency_fallback(*, asset: str, interval_sec: int, db_path: str, dependency_reason: str) -> int:
    ctx_path = _market_context_path(asset=asset, interval_sec=interval_sec)
    payload = build_dependency_market_context(
        asset=asset,
        interval_sec=interval_sec,
        db_path=db_path,
        payout_fallback=env_float("PAYOUT_FALLBACK", env_float("PAYOUT", 0.8)),
        ctx_path=ctx_path,
        dependency_reason=dependency_reason,
    )
    write_json(ctx_path, payload)
    snap = candle_db_snapshot(db_path, asset, interval_sec)
    report = {
        "ok": bool((snap.get("db_rows") or 0) > 0),
        "mode": "dependency_fallback",
        "asset": asset,
        "interval_sec": int(interval_sec),
        "db_rows": int(snap.get("db_rows") or 0),
        "last_candle_ts": snap.get("last_candle_ts"),
        "dependency_reason": str(dependency_reason),
        "action": "skip_remote_collect" if (snap.get("db_rows") or 0) > 0 else "fail_no_local_data",
        "market_context_path": str(ctx_path),
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 2


def _broker_failure_fallback(*, asset: str, interval_sec: int, db_path: str, dependency_reason: str, broker_options: dict[str, Any]) -> int:
    ctx_path = _market_context_path(asset=asset, interval_sec=interval_sec)
    payload = build_dependency_market_context(
        asset=asset,
        interval_sec=interval_sec,
        db_path=db_path,
        payout_fallback=env_float("PAYOUT_FALLBACK", env_float("PAYOUT", 0.8)),
        ctx_path=ctx_path,
        dependency_reason=dependency_reason,
    )
    snap = candle_db_snapshot(db_path, asset, interval_sec)
    rows = int(snap.get('db_rows') or 0)
    last_ts = snap.get('last_candle_ts')
    now_ts = int(datetime.now(UTC).timestamp())
    age_sec = max(0, now_ts - int(last_ts)) if last_ts is not None else None
    reuse_enabled = bool(broker_options.get('collect_reuse_local_data_on_failure'))
    max_age_sec = int(broker_options.get('collect_reuse_local_max_age_sec') or 3600)
    db_usable = rows > 0 and age_sec is not None and age_sec <= max_age_sec
    action = 'reuse_local_data' if reuse_enabled and db_usable else ('fail_local_data_stale' if rows > 0 else 'fail_no_local_data')
    payload.update({
        'mode': 'broker_failure_fallback',
        'fallback_mode': 'broker_failure_fallback',
        'broker_available': False,
        'db_rows': rows,
        'last_candle_ts': last_ts,
        'last_candle_age_sec': age_sec,
        'db_usable': bool(db_usable),
        'action': action,
        'reuse_local_max_age_sec': max_age_sec,
    })
    write_json(ctx_path, payload)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if action == 'reuse_local_data' else 2


def main():
    repo_root = resolve_repo_root()
    broker_options = _broker_runtime_options(repo_root=repo_root)

    s = load_settings()
    asset = s.data.asset
    interval_sec = int(s.data.interval_sec)
    db_path = s.data.db_path
    max_batch = int(s.data.max_batch)

    lookback = env_int("LOOKBACK_CANDLES", "2000")
    sleep_s = env_float("IQ_SLEEP_S", 0.15)

    dep = iqoption_dependency_status()
    if not bool(dep.get("available", True)):
        raise SystemExit(_dependency_fallback(asset=asset, interval_sec=interval_sec, db_path=db_path, dependency_reason=str(dep.get("reason"))))

    now_ts = int(datetime.now(tz=timezone.utc).timestamp())

    con = open_db(db_path)
    try:
        try:
            client = _build_client(repo_root=repo_root, settings=s, asset=asset, interval_sec=interval_sec)
            client.connect()
            broker_asset = client.resolve_asset_name(asset, require_active_id=True)
        except IQDependencyUnavailable as exc:
            raise SystemExit(_dependency_fallback(asset=asset, interval_sec=interval_sec, db_path=db_path, dependency_reason=str(exc)))
        except Exception as exc:
            if broker_options.get('collect_reuse_local_data_on_failure'):
                raise SystemExit(_broker_failure_fallback(asset=asset, interval_sec=interval_sec, db_path=db_path, dependency_reason=f'{type(exc).__name__}: {exc}', broker_options=broker_options))
            raise

        if broker_asset and broker_asset != asset:
            print(f"[IQ][collect_recent] resolved broker asset: requested_asset={asset} broker_asset={broker_asset}")

        remaining = lookback
        cursor_end = now_ts
        total_seen = 0
        total_upsert = 0

        while remaining > 0:
            n = min(max_batch, remaining)
            try:
                candles = client.get_candles(broker_asset or asset, interval_sec, n, cursor_end)
            except IQDependencyUnavailable as exc:
                raise SystemExit(_dependency_fallback(asset=asset, interval_sec=interval_sec, db_path=db_path, dependency_reason=str(exc)))
            except Exception as exc:
                if broker_options.get('collect_reuse_local_data_on_failure'):
                    raise SystemExit(_broker_failure_fallback(asset=asset, interval_sec=interval_sec, db_path=db_path, dependency_reason=f'{type(exc).__name__}: {exc}', broker_options=broker_options))
                raise RuntimeError(
                    f"collect_recent failed: asset={asset} broker_asset={broker_asset} interval={interval_sec} count={n} end={cursor_end} seen={total_seen} upserted={total_upsert} err={type(exc).__name__}: {exc}"
                ) from exc
            if not candles:
                if total_seen == 0:
                    raise RuntimeError(
                        f"collect_recent returned no candles on first batch: asset={asset} broker_asset={broker_asset} interval={interval_sec} count={n} end={cursor_end}"
                    )
                print(f"[WARN] collect_recent: empty batch after seen={total_seen}; stopping early at end={cursor_end} requested_asset={asset} broker_asset={broker_asset}")
                break

            total_seen += len(candles)
            closed = [c for c in candles if is_closed(c, now_ts, interval_sec)]
            if closed:
                upsert_candles(con, asset, interval_sec, closed)
                total_upsert += len(closed)

            min_ts = min(norm_ts(int(c.get("from", 0) or 0)) for c in candles)
            cursor_end = min_ts - interval_sec
            remaining -= n
            time.sleep(sleep_s)

        maybe_write_market_context(client, asset=asset, interval_sec=interval_sec, db_path=db_path)
        print(f"collect_recent(closed-only): requested_asset={asset} broker_asset={broker_asset} seen~{total_seen} upserted~{total_upsert} lookback={lookback}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
