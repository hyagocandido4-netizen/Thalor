from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from natbin.brokers import FakeBrokerAdapter, IQOptionAdapter
from natbin.runtime.broker_surface import (
    adapter_from_context,
    execution_cfg,
    execution_enabled,
    reconcile_cfg,
    scope_from_context,
)



def _ctx(*, execution: dict, broker: dict | None = None):
    return SimpleNamespace(
        resolved_config={
            'execution': execution,
            'broker': broker or {},
        },
        config=SimpleNamespace(asset='EURUSD-OTC', interval_sec=300),
        scope=SimpleNamespace(scope_tag='EURUSD-OTC_300s'),
    )



def test_broker_surface_builds_fake_adapter_and_scope(tmp_path: Path) -> None:
    ctx = _ctx(
        execution={
            'enabled': True,
            'mode': 'paper',
            'provider': 'fake',
            'account_mode': 'PRACTICE',
            'fake': {
                'submit_behavior': 'ack',
                'settlement': 'open',
                'heartbeat_ok': True,
            },
        }
    )

    adapter = adapter_from_context(ctx, repo_root=tmp_path)
    scope = scope_from_context(ctx)

    assert isinstance(adapter, FakeBrokerAdapter)
    assert execution_enabled(ctx) is True
    assert execution_cfg(ctx)['provider'] == 'fake'
    assert scope.asset == 'EURUSD-OTC'
    assert scope.interval_sec == 300
    assert scope.scope_tag == 'EURUSD-OTC_300s'
    assert scope.account_mode == 'PRACTICE'



def test_broker_surface_builds_iqoption_adapter_with_reconcile_window(tmp_path: Path) -> None:
    ctx = _ctx(
        execution={
            'enabled': True,
            'mode': 'live',
            'provider': 'iqoption',
            'account_mode': 'REAL',
            'reconcile': {
                'history_lookback_sec': 7200,
                'settle_grace_sec': 45,
            },
        },
        broker={'balance_mode': 'REAL'},
    )

    adapter = adapter_from_context(ctx, repo_root=tmp_path)

    assert isinstance(adapter, IQOptionAdapter)
    assert reconcile_cfg(ctx)['history_lookback_sec'] == 7200
    assert adapter.history_limit == 24
    assert adapter.settle_grace_sec == 45
    assert adapter.account_mode == 'REAL'
