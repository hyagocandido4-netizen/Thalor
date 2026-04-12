from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from natbin.ops import intelligence_maintenance as mod
from natbin.usecases.observer.signal_store import write_sqlite_signal


class DummyCfg:
    class MA:
        enabled = True
        partition_data_paths = True
        data_db_template = 'data/market_{scope_tag}.sqlite3'
        dataset_path_template = 'data/datasets/{scope_tag}/dataset.csv'

    class DATA:
        db_path = 'data/market.sqlite3'
        dataset_path = 'data/dataset.csv'

    multi_asset = MA()
    data = DATA()


def _scope(asset: str = 'EURUSD-OTC', interval_sec: int = 300):
    return SimpleNamespace(asset=asset, interval_sec=interval_sec, scope_tag=f'{asset}_{interval_sec}s', timezone='America/Sao_Paulo')


def _surface_item(scope_tag: str, *, pack_available: bool, eval_available: bool, warnings: list[str] | None = None) -> dict[str, object]:
    return {
        'scope_tag': scope_tag,
        'asset': scope_tag.split('_', 1)[0],
        'interval_sec': 300,
        'enabled': True,
        'severity': 'warn' if warnings else 'ok',
        'pack_available': pack_available,
        'eval_available': eval_available,
        'warnings': list(warnings or []),
    }


def _audit_item(scope_tag: str, *, cp_meta_missing: bool = False, stale: bool = False, missing: bool = False) -> dict[str, object]:
    return {
        'scope': {'scope_tag': scope_tag},
        'cp_meta_missing': cp_meta_missing,
        'stale': stale,
        'missing': missing,
    }


def test_intelligence_maintenance_refreshes_needed_scope(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    cfg = repo / 'config.yaml'
    cfg.write_text('x: 1', encoding='utf-8')
    scope = _scope()
    scope_tag = scope.scope_tag
    db_path = repo / 'data' / f'market_{scope_tag}.sqlite3'
    ds_path = repo / 'data' / 'datasets' / scope_tag / 'dataset.csv'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    ds_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b'')
    ds_path.write_text('ts,y_open_close\n1,1\n', encoding='utf-8')

    monkeypatch.setattr(mod, 'load_selected_scopes', lambda **kwargs: (repo, cfg, DummyCfg(), [scope]))
    monkeypatch.setattr(
        mod,
        'resolve_scope_paths',
        lambda **kwargs: {'data': SimpleNamespace(db_path=db_path, dataset_path=ds_path), 'runtime': None},
    )

    surface_calls = {'n': 0}
    def fake_surface(**kwargs):
        surface_calls['n'] += 1
        if surface_calls['n'] == 1:
            return {'items': [_surface_item(scope_tag, pack_available=False, eval_available=False, warnings=['pack_artifact'])]}
        return {'items': [_surface_item(scope_tag, pack_available=True, eval_available=True, warnings=[])]}
    monkeypatch.setattr(mod, 'build_portfolio_intelligence_payload', fake_surface)

    audit_calls = {'n': 0}
    def fake_audit(**kwargs):
        audit_calls['n'] += 1
        if audit_calls['n'] == 1:
            return {'scope_results': [_audit_item(scope_tag, cp_meta_missing=True)]}
        return {'scope_results': [_audit_item(scope_tag, cp_meta_missing=False)]}
    monkeypatch.setattr(mod, 'build_signal_artifact_audit_payload', fake_audit)

    refresh_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        mod,
        'refresh_config_intelligence',
        lambda **kwargs: refresh_calls.append(kwargs) or {
            'ok': True,
            'items': [{'scope_tag': scope_tag, 'ok': True}],
            'materialized_portfolio': {'ok': True},
        },
    )

    runtime_calls: list[list[str]] = []
    monkeypatch.setattr(
        mod,
        '_run_runtime_app_json',
        lambda **kwargs: runtime_calls.append(list(kwargs['subargs'])) or {'ok': True, 'returncode': 0, 'timed_out': False, 'command': kwargs['subargs']},
    )

    payload = mod.build_portfolio_intelligence_maintenance_payload(repo_root=repo, config_path=cfg, all_scopes=True)

    assert payload['ok'] is True
    assert payload['selected_scope_tags'] == [scope_tag]
    assert refresh_calls and refresh_calls[0]['asset'] == scope.asset
    assert any(call[:2] == ['asset', 'candidate'] for call in runtime_calls)
    assert [step['name'] for step in payload['steps']] == ['asset_candidate', 'intelligence_refresh']


def test_intelligence_maintenance_prepares_when_dataset_missing(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    cfg = repo / 'config.yaml'
    cfg.write_text('x: 1', encoding='utf-8')
    scope = _scope('GBPUSD-OTC')
    scope_tag = scope.scope_tag
    db_path = repo / 'data' / f'market_{scope_tag}.sqlite3'
    ds_path = repo / 'data' / 'datasets' / scope_tag / 'dataset.csv'

    monkeypatch.setattr(mod, 'load_selected_scopes', lambda **kwargs: (repo, cfg, DummyCfg(), [scope]))
    monkeypatch.setattr(
        mod,
        'resolve_scope_paths',
        lambda **kwargs: {'data': SimpleNamespace(db_path=db_path, dataset_path=ds_path), 'runtime': None},
    )
    monkeypatch.setattr(mod, 'build_portfolio_intelligence_payload', lambda **kwargs: {'items': [_surface_item(scope_tag, pack_available=False, eval_available=False)]})
    monkeypatch.setattr(mod, 'build_signal_artifact_audit_payload', lambda **kwargs: {'scope_results': [_audit_item(scope_tag, cp_meta_missing=False)]})
    monkeypatch.setattr(mod, 'refresh_config_intelligence', lambda **kwargs: {'ok': True, 'items': [{'scope_tag': scope_tag, 'ok': True}], 'materialized_portfolio': {'ok': True}})

    runtime_calls: list[list[str]] = []
    monkeypatch.setattr(
        mod,
        '_run_runtime_app_json',
        lambda **kwargs: runtime_calls.append(list(kwargs['subargs'])) or {'ok': True, 'returncode': 0, 'timed_out': False, 'command': kwargs['subargs']},
    )

    payload = mod.build_portfolio_intelligence_maintenance_payload(repo_root=repo, config_path=cfg, all_scopes=True)
    names = [step['name'] for step in payload['steps']]
    assert names == ['asset_prepare', 'asset_candidate', 'intelligence_refresh']
    assert runtime_calls[0][:2] == ['asset', 'prepare']
    assert runtime_calls[1][:2] == ['asset', 'candidate']


def test_intelligence_maintenance_only_cp_meta_filters_scopes(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    cfg = repo / 'config.yaml'
    cfg.write_text('x: 1', encoding='utf-8')
    scope_a = _scope('EURUSD-OTC')
    scope_b = _scope('USDCAD-OTC')

    monkeypatch.setattr(mod, 'load_selected_scopes', lambda **kwargs: (repo, cfg, DummyCfg(), [scope_a, scope_b]))
    monkeypatch.setattr(mod, 'resolve_scope_paths', lambda **kwargs: {'data': SimpleNamespace(db_path=repo / 'db.sqlite3', dataset_path=repo / 'ds.csv'), 'runtime': None})
    monkeypatch.setattr(
        mod,
        'build_portfolio_intelligence_payload',
        lambda **kwargs: {
            'items': [
                _surface_item(scope_a.scope_tag, pack_available=True, eval_available=True),
                _surface_item(scope_b.scope_tag, pack_available=True, eval_available=True),
            ]
        },
    )
    monkeypatch.setattr(
        mod,
        'build_signal_artifact_audit_payload',
        lambda **kwargs: {
            'scope_results': [
                _audit_item(scope_a.scope_tag, cp_meta_missing=False),
                _audit_item(scope_b.scope_tag, cp_meta_missing=True),
            ]
        },
    )
    monkeypatch.setattr(mod, 'refresh_config_intelligence', lambda **kwargs: {'ok': True, 'items': [], 'materialized_portfolio': {'ok': True}})
    monkeypatch.setattr(mod, '_run_runtime_app_json', lambda **kwargs: {'ok': True, 'returncode': 0, 'timed_out': False, 'command': kwargs['subargs']})

    payload = mod.build_portfolio_intelligence_maintenance_payload(repo_root=repo, config_path=cfg, all_scopes=True, only_cp_meta=True)
    assert payload['selected_scope_tags'] == [scope_b.scope_tag]


def test_write_sqlite_signal_mirrors_to_root_db(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    scoped_db = tmp_path / 'runs' / 'signals' / 'EURUSD-OTC_300s' / 'live_signals.sqlite3'
    monkeypatch.setenv('THALOR_SIGNALS_DB_PATH', str(scoped_db))
    monkeypatch.delenv('THALOR_MIRROR_SIGNALS_TO_ROOT', raising=False)

    row = {
        'dt_local': '2026-04-08 21:55:00',
        'day': '2026-04-08',
        'asset': 'EURUSD-OTC',
        'interval_sec': 300,
        'ts': 1775609700,
        'action': 'HOLD',
        'reason': 'regime_block',
        'blockers': 'below_ev_threshold',
        'conf': 0.55,
        'score': 0.0,
        'proba_up': 0.55,
        'threshold': 0.02,
        'k': 1,
        'rank_in_day': -1,
        'executed_today': 0,
        'budget_left': 1,
        'thresh_on': 'ev',
        'gate_mode': 'cp_meta_iso',
        'regime_ok': 0,
        'payout': 0.8,
        'ev': -1.0,
    }
    write_sqlite_signal(row)

    root_db = tmp_path / 'runs' / 'live_signals.sqlite3'
    assert scoped_db.exists()
    assert root_db.exists()

    def _count(path: Path) -> int:
        con = sqlite3.connect(path)
        try:
            return int(con.execute('SELECT COUNT(*) FROM signals_v2').fetchone()[0])
        finally:
            con.close()

    assert _count(scoped_db) == 1
    assert _count(root_db) == 1



def test_runtime_app_json_marks_missing_payload_as_failure(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path
    class Completed:
        returncode = 0
        stdout = ''
        stderr = ''
    monkeypatch.setattr(mod.subprocess, 'run', lambda *args, **kwargs: Completed())
    payload = mod._run_runtime_app_json(repo_root=repo, config_path=None, subargs=['status'])
    assert payload['ok'] is False
    assert payload['missing_payload'] is True


def test_control_app_module_status_cli_prints_json() -> None:
    import json as _json
    import os
    import subprocess as _subprocess
    import sys as _sys
    env = os.environ.copy()
    env['PYTHONPATH'] = f".:src" + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    repo = Path(__file__).resolve().parents[1]
    proc = _subprocess.run(
        [_sys.executable, '-m', 'natbin.control.app', 'status', '--json'],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    payload = _json.loads(proc.stdout)
    assert isinstance(payload, dict)
    assert isinstance(payload.get('config'), dict)
    assert isinstance(payload.get('scope'), dict)



def test_control_app_main_accepts_prefixed_intelligence_refresh(monkeypatch, capsys, tmp_path: Path) -> None:
    from natbin.control import app as control_app

    monkeypatch.setattr(
        control_app,
        'intelligence_refresh_payload',
        lambda **kwargs: {'ok': True, 'kind': 'intelligence_refresh_ok', 'scope_tag': 'EURUSD-OTC_300s'},
    )

    rc = control_app.main([
        '--repo-root', str(tmp_path),
        '--config', 'config.yaml',
        'intelligence-refresh',
        '--asset', 'EURUSD-OTC',
        '--interval-sec', '300',
        '--json',
    ])
    captured = capsys.readouterr()
    assert rc == 0
    payload = __import__('json').loads(captured.out)
    assert payload['kind'] == 'intelligence_refresh_ok'
