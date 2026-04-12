from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.tools.portfolio_canary_signal_proof import _select_governed_scopes


def test_select_governed_scopes_rotates_after_best_scope(tmp_path: Path) -> None:
    repo = tmp_path
    scopes = ['best', 's1', 's2', 's3', 's4']
    first, meta1 = _select_governed_scopes(scopes, repo=repo, budget=3, scope_order='best_first_round_robin')
    second, meta2 = _select_governed_scopes(scopes, repo=repo, budget=3, scope_order='best_first_round_robin')
    assert first[0] == 'best' and second[0] == 'best'
    assert first != second
    assert meta1['skipped_scope_count'] == 2
    assert meta2['scope_cursor_after'] != meta1['scope_cursor_after']
