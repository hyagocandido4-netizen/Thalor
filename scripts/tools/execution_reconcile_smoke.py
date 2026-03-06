#!/usr/bin/env python
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def _ok(msg: str) -> None:
    print(f'[execution-reconcile][OK] {msg}')


def _fail(msg: str) -> None:
    print(f'[execution-reconcile][FAIL] {msg}')
    raise SystemExit(2)


def _write_cfg(repo: Path, *, submit_behavior: str = 'timeout', settlement: str = 'open') -> Path:
    (repo / 'config').mkdir(parents=True, exist_ok=True)
    cfg = repo / 'config' / 'base.yaml'
    cfg.write_text(
        '\n'.join([
            'version: "2.0"',
            'assets:',
            '  - asset: EURUSD-OTC',
            '    interval_sec: 300',
            '    timezone: UTC',
            'execution:',
            '  enabled: true',
            '  mode: paper',
            '  provider: fake',
            '  account_mode: PRACTICE',
            '  stake:',
            '    amount: 2.0',
            '    currency: BRL',
            '  fake:',
            f'    submit_behavior: {submit_behavior}',
            f'    settlement: {settlement}',
            '    settle_after_sec: 0',
            '    create_order_on_timeout: true',
            '  reconcile:',
            '    history_lookback_sec: 3600',
            '    not_found_grace_sec: 1',
            '    settle_grace_sec: 1',
        ]),
        encoding='utf-8',
    )
    return cfg


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / 'src'
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from natbin.control.plan import build_context
    from natbin.runtime.execution import adapter_from_context, intent_from_signal_row, submit_intent
    from natbin.runtime.reconciliation import reconcile_scope
    from natbin.state.execution_repo import ExecutionRepository

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        cfg = _write_cfg(repo)
        ctx = build_context(repo_root=repo, config_path=cfg)
        execution_repo = ExecutionRepository(repo / 'runs' / 'runtime_execution.sqlite3')
        adapter = adapter_from_context(ctx, repo_root=repo)
        row = {'day': '2026-03-05', 'ts': 1772668800, 'action': 'CALL', 'conf': 0.6, 'score': 0.7}
        intent = intent_from_signal_row(row=row, ctx=ctx)
        intent, created = execution_repo.ensure_intent(intent)
        if not created:
            _fail('expected fresh intent creation')
        submitted, attempt = submit_intent(repo_root=repo, ctx=ctx, repo=execution_repo, adapter=adapter, intent=intent)
        if submitted.intent_state != 'submitted_unknown':
            _fail(f'expected submitted_unknown after fake timeout, got {submitted.intent_state}')
        result, detail = reconcile_scope(repo_root=repo, ctx=ctx, adapter=adapter)
        refreshed = execution_repo.get_intent(submitted.intent_id)
        if refreshed is None or refreshed.intent_state != 'accepted_open':
            _fail(f'reconcile did not converge timeout->accepted_open, result={result.as_dict()} detail={detail} intent={refreshed.as_dict() if refreshed else None}')
        _ok('timeout to accepted_open reconcile ok')

        # Orphan detection.
        dummy_req = attempt.request_json  # keep object referenced so mypy stays calm
        from natbin.runtime.execution_models import SubmitOrderRequest
        foreign = SubmitOrderRequest(
            intent_id='foreign_intent',
            client_order_key='foreign-client-key',
            broker_name='fake',
            account_mode='PRACTICE',
            scope_tag='EURUSD-OTC_300s',
            asset='EURUSD-OTC',
            interval_sec=300,
            side='PUT',
            amount=2.0,
            currency='BRL',
            signal_ts=1772669100,
            expiry_ts=1772669400,
            entry_deadline_utc='2026-03-05T00:10:02+00:00',
            metadata={},
        )
        adapter.submit_order(foreign)
        result2, detail2 = reconcile_scope(repo_root=repo, ctx=ctx, adapter=adapter)
        if result2.new_orphans <= 0:
            _fail(f'expected orphan detection, got {result2.as_dict()} detail={detail2}')
        _ok('orphan detection ok')

    print('[execution-reconcile] ALL OK')


if __name__ == '__main__':
    main()
