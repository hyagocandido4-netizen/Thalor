from __future__ import annotations

import sys
from pathlib import Path

# Compat wrapper: previous packages used the user-facing name
# "portfolio-canary-signal-scan", while the stable standalone tool
# is implemented in portfolio_canary_signal_proof.py.
ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / 'scripts' / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from portfolio_canary_signal_proof import main  # type: ignore  # noqa: E402


if __name__ == '__main__':
    raise SystemExit(main())
