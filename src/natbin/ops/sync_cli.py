from __future__ import annotations

import argparse
import json
from typing import Any

from .sync_state import build_sync_payload


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description='Thalor SYNC-1A lightweight sync entrypoint')
    p.add_argument('command', nargs='?', default='sync')
    p.add_argument('--repo-root', default='.', help='Repository root')
    p.add_argument('--config', default=None, help='Optional config path (kept for CLI parity)')
    p.add_argument('--json', action='store_true')
    p.add_argument('--strict', action='store_true')
    p.add_argument('--freeze-docs', action='store_true', help='Rewrite canonical_state manifests')
    p.add_argument('--write-manifest', action='store_true', help='Compat alias for --freeze-docs')
    p.add_argument('--base-ref', default='origin/main', help='Compat flag accepted by SYNC-1; currently informational only')
    return p


def _print(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    else:
        print(json.dumps(payload, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    ns = _build_parser().parse_args(argv)
    if str(getattr(ns, 'command', 'sync') or 'sync') != 'sync':
        raise SystemExit(f'unsupported lightweight command: {ns.command!r}')
    payload = build_sync_payload(
        repo_root=str(ns.repo_root or '.'),
        config_path=getattr(ns, 'config', None),
        freeze_docs=bool(getattr(ns, 'freeze_docs', False) or getattr(ns, 'write_manifest', False)),
        strict=bool(getattr(ns, 'strict', False)),
        write_artifact=True,
    )
    payload.setdefault('cli_compat', {})
    payload['cli_compat'].update(
        {
            'lightweight_entrypoint': True,
            'requested_base_ref': str(getattr(ns, 'base_ref', 'origin/main') or 'origin/main'),
            'freeze_requested': bool(getattr(ns, 'freeze_docs', False) or getattr(ns, 'write_manifest', False)),
        }
    )
    _print(payload, as_json=bool(getattr(ns, 'json', False)))
    return 0 if bool(payload.get('ok', True)) else 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
