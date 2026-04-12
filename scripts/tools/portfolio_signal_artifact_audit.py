from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from natbin.ops.signal_artifact_audit import build_signal_artifact_audit_payload  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description='Audita artifacts de decisão do canary em todos os scopes, sem submeter ordens nem tocar no provider.')
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--config', default=None)
    ap.add_argument('--asset', default=None)
    ap.add_argument('--interval-sec', type=int, default=None)
    ap.add_argument('--all-scopes', action='store_true')
    ap.add_argument('--decision-max-age-sec', type=int, default=3600)
    ap.add_argument('--json', action='store_true')
    ns = ap.parse_args(argv)
    payload = build_signal_artifact_audit_payload(
        repo_root=ns.repo_root,
        config_path=ns.config,
        asset=ns.asset,
        interval_sec=ns.interval_sec,
        all_scopes=bool(ns.all_scopes),
        decision_max_age_sec=int(ns.decision_max_age_sec or 3600),
        write_artifact=True,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2 if ns.json else None))
    return 0 if bool(payload.get('ok')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
