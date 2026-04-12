from __future__ import annotations

from natbin.control.commands import intelligence_payload, practice_payload, portfolio_status_payload
from natbin.dashboard.app import _normalize_rows_for_dataframe
from natbin.intelligence.paths import (
    anti_overfit_data_summary_path,
    anti_overfit_tuning_path,
    intelligence_ops_state_path,
    retrain_plan_path,
)
from natbin.state.control_repo import write_repo_control_artifact


def test_recovery_exports_exist(tmp_path):
    assert callable(intelligence_payload)
    assert callable(practice_payload)
    assert callable(portfolio_status_payload)
    rows = _normalize_rows_for_dataframe([{'payload': {'a': 1}}])
    assert isinstance(rows, list) and rows[0]['payload']
    assert retrain_plan_path(repo_root=tmp_path, scope_tag='EURUSD-OTC_300s').name == 'retrain_plan.json'
    assert intelligence_ops_state_path(repo_root=tmp_path, scope_tag='EURUSD-OTC_300s').name == 'intelligence_ops_state.json'
    assert anti_overfit_data_summary_path(repo_root=tmp_path, scope_tag='EURUSD-OTC_300s').name == 'anti_overfit_data_summary.json'
    assert anti_overfit_tuning_path(repo_root=tmp_path, scope_tag='EURUSD-OTC_300s').name == 'anti_overfit_tuning.json'
    out = write_repo_control_artifact(repo_root=tmp_path, name='sync', payload={'ok': True})
    assert out.exists()
