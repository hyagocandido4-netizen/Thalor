from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from natbin.ops.practice_preflight import build_practice_preflight_payload
from natbin.ops.production_doctor import build_production_doctor_payload
from natbin.ops.safe_refresh import maybe_heal_control_freshness


class _DummyConfig(SimpleNamespace):
    config_path: str
    asset: str
    interval_sec: int
    dataset_path: str | None = None


class _DummyScope(SimpleNamespace):
    scope_tag: str


class _DummyCtx(SimpleNamespace):
    repo_root: str
    config: _DummyConfig
    scope: _DummyScope
    resolved_config: dict
    scoped_paths: dict


def test_maybe_heal_control_freshness_dry_run_reports_planned(tmp_path: Path) -> None:
    payload = maybe_heal_control_freshness(
        repo_root=tmp_path,
        config_path=tmp_path / 'config.yaml',
        asset='EURUSD-OTC',
        interval_sec=300,
        freshness_limit_sec=1200,
        enabled=True,
        dry_run=True,
    )
    assert payload['status'] == 'planned'
    assert payload['safe'] is True
    assert payload['potentially_submits'] is False
    assert payload['attempted'] is False
    assert payload['guard']['drain_mode_enforced'] is True


def test_practice_preflight_runs_safe_control_freshness_repair_first(monkeypatch, tmp_path: Path) -> None:
    ctx = _DummyCtx(
        repo_root=str(tmp_path),
        config=_DummyConfig(config_path=str(tmp_path / 'config.yaml'), asset='EURUSD-OTC', interval_sec=300),
        scope=_DummyScope(scope_tag='EURUSD-OTC_300s'),
        resolved_config={},
        scoped_paths={},
    )
    freshness_calls: list[dict] = []

    monkeypatch.setattr('natbin.ops.practice_preflight.build_context', lambda **_: ctx)
    monkeypatch.setattr('natbin.ops.practice_preflight.collect_sensitive_values', lambda _cfg: [])
    monkeypatch.setattr('natbin.ops.practice_preflight.sanitize_payload', lambda payload, sensitive_values=None: payload)
    monkeypatch.setattr('natbin.ops.practice_preflight.write_scope_artifact', lambda *args, **kwargs: None)
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.maybe_heal_market_context',
        lambda **kwargs: {
            'name': 'market_context',
            'enabled': True,
            'attempted': False,
            'status': 'skip',
            'message': 'market_context_fresh',
            'safe': True,
            'potentially_submits': False,
        },
    )
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.maybe_heal_control_freshness',
        lambda **kwargs: freshness_calls.append(kwargs) or {
            'name': 'control_freshness',
            'enabled': True,
            'attempted': True,
            'status': 'ok',
            'message': 'control_freshness_refreshed',
            'safe': True,
            'potentially_submits': False,
        },
    )
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.build_diag_suite_payload',
        lambda **kwargs: {
            'ok': True,
            'severity': 'ok',
            'actions': [],
            'results': {
                'practice': {
                    'ready_for_practice': True,
                    'checks': [
                        {'name': 'production_doctor', 'status': 'ok', 'message': 'ok'},
                        {'name': 'runtime_soak', 'status': 'ok', 'message': 'ok'},
                    ],
                }
            },
            'ready_for_practice': True,
        },
    )
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.build_transport_smoke_payload',
        lambda **kwargs: {'ok': True, 'severity': 'ok', 'actions': [], 'scope_results': [{'actions': []}]},
    )
    monkeypatch.setattr(
        'natbin.ops.practice_preflight.build_module_smoke_payload',
        lambda **kwargs: {'ok': True, 'severity': 'ok', 'actions': []},
    )

    payload = build_practice_preflight_payload(repo_root=tmp_path, config_path=tmp_path / 'config.yaml', dry_run=False)
    assert freshness_calls, 'safe control_freshness repair should be attempted by default'
    repair_names = [item['name'] for item in payload['repairs']]
    assert 'market_context' in repair_names
    assert 'control_freshness' in repair_names
    assert payload['ready_for_long_practice'] is True


def test_guardrail_audit_treats_market_closed_as_operational_no_trade(monkeypatch, tmp_path: Path) -> None:
    from natbin.ops.guardrail_audit import _scope_payload

    ctx = _DummyCtx(
        repo_root=str(tmp_path),
        config=_DummyConfig(config_path=str(tmp_path / 'config.yaml'), asset='EURUSD-OTC', interval_sec=300),
        scope=_DummyScope(scope_tag='EURUSD-OTC_300s'),
        resolved_config={'execution': {'account_mode': 'PRACTICE'}, 'broker': {'balance_mode': 'PRACTICE'}},
        scoped_paths={},
    )

    class _DummyFailsafe:
        def is_kill_switch_active(self, env):
            return False, None

        def is_drain_mode_active(self, env):
            return False, None

        def evaluate_circuit(self, breaker, now):
            return SimpleNamespace(state='closed', reason=None, opened_until_utc=None)

    monkeypatch.setattr('natbin.ops.guardrail_audit.build_context', lambda **_: ctx)
    monkeypatch.setattr('natbin.ops.guardrail_audit._failsafe_from_context', lambda ctx, repo: _DummyFailsafe())
    monkeypatch.setattr('natbin.ops.guardrail_audit.RuntimeControlRepository', lambda *args, **kwargs: SimpleNamespace(load_breaker=lambda *a, **k: None))
    monkeypatch.setattr(
        'natbin.ops.guardrail_audit._evaluate_precheck',
        lambda **kwargs: {'blocked': True, 'reason': 'market_closed', 'next_wake_utc': '2026-04-02T13:00:00+00:00'},
    )
    monkeypatch.setattr(
        'natbin.ops.guardrail_audit.evaluate_execution_hardening',
        lambda **kwargs: SimpleNamespace(as_dict=lambda: {'allowed': True, 'reason': None, 'live_real_mode': False, 'details': {}}),
    )
    monkeypatch.setattr('natbin.ops.guardrail_audit.write_control_artifact', lambda **kwargs: None)

    payload = _scope_payload(repo=tmp_path, cfg_path=tmp_path / 'config.yaml', asset='EURUSD-OTC', interval_sec=300)
    checks = {item['name']: item for item in payload['checks']}
    assert checks['precheck']['status'] == 'ok'
    assert checks['precheck']['operational_no_trade'] is True
    assert payload['severity'] == 'ok'


def test_production_doctor_treats_fresh_closed_market_as_ok(tmp_path: Path, monkeypatch) -> None:
    try:
        from tests.test_production_doctor import _seed_runtime_surface
    except ModuleNotFoundError:
        import importlib.util

        helper_path = Path(__file__).with_name('test_production_doctor.py')
        spec = importlib.util.spec_from_file_location('_test_production_doctor_helper', helper_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _seed_runtime_surface = module._seed_runtime_surface

    cfg = _seed_runtime_surface(tmp_path, execution_live=False)
    ctx = __import__('natbin.control.plan', fromlist=['build_context']).build_context(repo_root=tmp_path, config_path=cfg, dump_snapshot=False)
    market_path = Path(ctx.scoped_paths['market_context'])
    payload = json.loads(market_path.read_text(encoding='utf-8'))
    payload['market_open'] = False
    payload['open_source'] = 'db_stale'
    payload['at_utc'] = datetime.now(UTC).isoformat(timespec='seconds')
    market_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    doctor = build_production_doctor_payload(repo_root=tmp_path, config_path=cfg)
    checks = {item['name']: item for item in doctor['checks']}
    assert checks['market_context']['status'] == 'ok'
    assert checks['market_context']['no_trade_window'] is True
