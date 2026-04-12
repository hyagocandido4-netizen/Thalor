from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_src() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    src = repo_root / 'src'
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


_bootstrap_src()

from natbin.ops.diag_suite import main  # noqa: E402


if __name__ == '__main__':
    raise SystemExit(main())
