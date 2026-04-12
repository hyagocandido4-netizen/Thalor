from __future__ import annotations

from scripts.tools import portfolio_cp_meta_maintenance as mod


def test_debt_scope_tags_prefers_repair_scope_tags_for_missing_artifacts() -> None:
    closure = {
        'repair_scope_tags': ['EURUSD-OTC_300s', 'GBPUSD-OTC_300s'],
        'closure_debts': [],
    }
    audit = {
        'scope_results': [
            {'scope': {'scope_tag': 'EURUSD-OTC_300s'}, 'exists': False, 'missing': True, 'stale': False, 'cp_meta_missing': False},
            {'scope': {'scope_tag': 'GBPUSD-OTC_300s'}, 'exists': False, 'missing': True, 'stale': False, 'cp_meta_missing': False},
        ]
    }
    assert mod._debt_scope_tags(closure, audit) == ['EURUSD-OTC_300s', 'GBPUSD-OTC_300s']


def test_repair_scope_set_tracks_missing_artifacts() -> None:
    audit = {
        'scope_results': [
            {'scope': {'scope_tag': 'EURUSD-OTC_300s'}, 'exists': False, 'missing': True, 'stale': False, 'cp_meta_missing': False},
            {'scope': {'scope_tag': 'GBPUSD-OTC_300s'}, 'exists': True, 'missing': False, 'stale': False, 'cp_meta_missing': False},
        ]
    }
    assert mod._repair_scope_set(audit, ['EURUSD-OTC_300s', 'GBPUSD-OTC_300s']) == {'EURUSD-OTC_300s'}
