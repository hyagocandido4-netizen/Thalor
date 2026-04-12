from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .structured_log import append_jsonl


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def json_default(value: Any) -> Any:
    if hasattr(value, 'model_dump'):
        try:
            return value.model_dump(mode='python')
        except Exception:
            pass
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(str(item) for item in value)
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')


def json_dumps(payload: dict[str, Any], *, pretty: bool = True) -> str:
    if pretty:
        return json.dumps(payload, ensure_ascii=False, indent=2, default=json_default)
    return json.dumps(payload, ensure_ascii=False, default=json_default)


def print_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    text = json_dumps(payload, pretty=as_json)
    print(text)


def write_json(path: str | Path, payload: dict[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json_dumps(payload, pretty=True) + '\n', encoding='utf-8')
    return target


def write_repo_artifact(repo_root: str | Path, name: str, payload: dict[str, Any]) -> Path:
    repo = Path(repo_root).resolve()
    target = repo / 'runs' / 'control' / '_repo' / f'{name}.json'
    return write_json(target, payload)


def write_scope_artifact(repo_root: str | Path, scope_tag: str, name: str, payload: dict[str, Any]) -> Path:
    repo = Path(repo_root).resolve()
    target = repo / 'runs' / 'control' / str(scope_tag) / f'{name}.json'
    return write_json(target, payload)


def maybe_append_log(path: str | Path | None, payload: dict[str, Any]) -> None:
    if path in (None, ''):
        return
    try:
        append_jsonl(path, payload, add_at_utc=False)
    except Exception:
        # Logging must never fail the diagnostic command.
        pass


def build_logger(name: str, *, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger(name)
    level = logging.DEBUG if verbose else logging.INFO
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:
            handler.setLevel(level)
    return logger


def log_event(logger: logging.Logger | None, event: str, **fields: Any) -> None:
    if logger is None:
        return
    payload = {'at_utc': utc_now_iso(), 'event': str(event)}
    payload.update(fields)
    try:
        logger.info(json_dumps(payload, pretty=False))
    except Exception:
        logger.info('%s %s', event, fields)


def add_repo_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--repo-root', default='.', help='Raiz do repositório')
    parser.add_argument('--config', default=None, help='Caminho do profile YAML')


def add_scope_args(parser: argparse.ArgumentParser, *, all_scopes: bool = True) -> None:
    parser.add_argument('--asset', default=None, help='Filtrar por asset')
    parser.add_argument('--interval-sec', type=int, default=None, help='Filtrar por intervalo')
    if all_scopes:
        parser.add_argument('--all-scopes', action='store_true', help='Executa em todos os scopes do profile')


def add_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--json', action='store_true', help='Pretty JSON na saída')
    parser.add_argument('--verbose', action='store_true', help='Logs adicionais no stderr')
    parser.add_argument('--dry-run', action='store_true', help='Não grava artifacts próprios e não executa probes ativos')
    parser.add_argument('--output', default=None, help='Escreve o payload final em um arquivo JSON')
    parser.add_argument('--log-jsonl-path', default=None, help='Arquivo JSONL opcional para telemetria do comando')


def exit_code_from_payload(payload: dict[str, Any]) -> int:
    severity = str(payload.get('severity') or ('error' if not bool(payload.get('ok', True)) else 'ok'))
    return 0 if severity not in {'error', 'fatal'} else 2


def exception_payload(kind: str, exc: BaseException) -> dict[str, Any]:
    return {
        'kind': kind,
        'at_utc': utc_now_iso(),
        'ok': False,
        'severity': 'error',
        'message': f'{type(exc).__name__}: {exc}',
    }
