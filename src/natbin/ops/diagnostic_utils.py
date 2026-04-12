from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config.paths import resolve_config_path, resolve_repo_root
from ..portfolio.paths import resolve_scope_data_paths, resolve_scope_runtime_paths
from ..portfolio.runner import load_scopes


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def check(name: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {'name': name, 'status': status, 'message': message}
    if extra:
        item.update(extra)
    return item


def severity_from_checks(checks: list[dict[str, Any]]) -> str:
    if any(str(item.get('status')) == 'error' for item in checks):
        return 'error'
    if any(str(item.get('status')) == 'warn' for item in checks):
        return 'warn'
    return 'ok'


def parse_iso(raw: Any) -> datetime | None:
    if raw in (None, ''):
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def age_sec_from_iso(raw: Any, *, now: datetime | None = None) -> float | None:
    stamp = parse_iso(raw)
    if stamp is None:
        return None
    return max(0.0, ((now or now_utc()) - stamp).total_seconds())


_ARTIFACT_TIMESTAMP_KEYS: tuple[str, ...] = (
    'at_utc',
    'checked_at_utc',
    'generated_at_utc',
    'updated_at_utc',
    'evaluated_at_utc',
    'finished_at_utc',
    'started_at_utc',
)


def artifact_timestamp(payload: Any, *, preferred_keys: tuple[str, ...] | None = None) -> tuple[str | None, datetime | None]:
    if not isinstance(payload, dict):
        return None, None
    keys = tuple(preferred_keys or _ARTIFACT_TIMESTAMP_KEYS)
    for key in keys:
        stamp = parse_iso(payload.get(key))
        if stamp is not None:
            return key, stamp
    return None, None


def artifact_freshness(
    payload: Any,
    *,
    max_age_sec: int,
    now: datetime | None = None,
    preferred_keys: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    field, stamp = artifact_timestamp(payload, preferred_keys=preferred_keys)
    age_sec = None if stamp is None else max(0.0, ((now or now_utc()) - stamp).total_seconds())
    fresh = bool(age_sec is not None and age_sec <= max(1, int(max_age_sec)))
    stale = bool(isinstance(payload, dict) and age_sec is not None and not fresh)
    return {
        'present': isinstance(payload, dict),
        'timestamp_field': field,
        'at_utc': None if stamp is None else stamp.isoformat(timespec='seconds'),
        'age_sec': age_sec,
        'max_age_sec': int(max_age_sec),
        'fresh': fresh,
        'stale': stale,
    }


def resolve_path(repo_root: str | Path, raw: str | Path | None) -> Path | None:
    if raw in (None, ''):
        return None
    path = Path(str(raw).strip())
    if path.is_absolute():
        return path.resolve()
    return (Path(repo_root).resolve() / path).resolve()


def dedupe_actions(actions: list[str]) -> list[str]:
    out: list[str] = []
    for action in actions:
        text = str(action or '').strip()
        if text and text not in out:
            out.append(text)
    return out


def load_selected_scopes(
    *,
    repo_root: str | Path = '.',
    config_path: str | Path | None = None,
    asset: str | None = None,
    interval_sec: int | None = None,
    all_scopes: bool = False,
) -> tuple[Path, Path, Any, list[Any]]:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    scopes, cfg = load_scopes(repo_root=root, config_path=cfg_path)
    if all_scopes:
        selected = list(scopes)
    else:
        selected = list(scopes)
        if asset is not None:
            selected = [s for s in selected if str(getattr(s, 'asset', '')) == str(asset)]
        if interval_sec is not None:
            selected = [s for s in selected if int(getattr(s, 'interval_sec', 0)) == int(interval_sec)]
        if not selected and scopes:
            selected = [scopes[0]]
    return Path(root).resolve(), Path(cfg_path).resolve(), cfg, selected


def resolve_scope_paths(*, repo_root: str | Path, cfg: Any, scope: Any) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    partition = bool(getattr(cfg.multi_asset, 'partition_data_paths', True)) and bool(getattr(cfg.multi_asset, 'enabled', False))
    db_tpl = str(getattr(cfg.multi_asset, 'data_db_template', 'data/market_{scope_tag}.sqlite3'))
    ds_tpl = str(getattr(cfg.multi_asset, 'dataset_path_template', 'data/datasets/{scope_tag}/dataset.csv'))
    data_paths = resolve_scope_data_paths(
        root,
        asset=str(scope.asset),
        interval_sec=int(scope.interval_sec),
        partition_enable=partition,
        db_template=db_tpl,
        dataset_template=ds_tpl,
        default_db_path=getattr(cfg.data, 'db_path', 'data/market_otc.sqlite3'),
        default_dataset_path=getattr(cfg.data, 'dataset_path', 'data/dataset_phase2.csv'),
    )
    runtime_paths = resolve_scope_runtime_paths(
        root,
        scope_tag=str(scope.scope_tag),
        partition_enable=bool(getattr(getattr(cfg, 'multi_asset', None), 'enabled', False)),
    )
    return {'data': data_paths, 'runtime': runtime_paths}
