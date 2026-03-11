from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from natbin.control.plan import build_context
from natbin.security.broker_guard import evaluate_submit_guard, note_submit_attempt


def _make_repo(tmp_path: Path) -> tuple[Path, object]:
    repo = tmp_path / 'repo'
    (repo / 'config').mkdir(parents=True, exist_ok=True)
    (repo / 'config' / 'base.yaml').write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                '  account_mode: PRACTICE',
                'security:',
                '  deployment_profile: live',
                '  guard:',
                '    enabled: true',
                '    live_only: true',
                '    min_submit_spacing_sec: 30',
                '    max_submit_per_minute: 1',
                '    time_filter_enable: true',
                '    allowed_start_local: "09:00"',
                '    allowed_end_local: "17:00"',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    ctx = build_context(repo_root=repo, config_path=repo / 'config' / 'base.yaml')
    return repo, ctx


def test_broker_guard_enforces_time_spacing_and_rate_limit(tmp_path: Path) -> None:
    repo, ctx = _make_repo(tmp_path)
    t0 = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)

    first = evaluate_submit_guard(repo_root=repo, ctx=ctx, now_utc=t0)
    assert first.allowed is True

    note_submit_attempt(repo_root=repo, ctx=ctx, transport_status='ack', now_utc=t0)
    spacing = evaluate_submit_guard(repo_root=repo, ctx=ctx, now_utc=t0 + timedelta(seconds=5))
    assert spacing.allowed is False
    assert spacing.reason == 'security_submit_spacing'

    note_submit_attempt(repo_root=repo, ctx=ctx, transport_status='ack', now_utc=t0 + timedelta(seconds=31))
    rate = evaluate_submit_guard(repo_root=repo, ctx=ctx, now_utc=t0 + timedelta(seconds=62))
    assert rate.allowed is False
    assert rate.reason == 'security_submit_rate_limit'

    closed = evaluate_submit_guard(repo_root=repo, ctx=ctx, now_utc=datetime(2026, 3, 10, 20, 0, tzinfo=UTC))
    assert closed.allowed is False
    assert closed.reason == 'security_time_filter_closed'
