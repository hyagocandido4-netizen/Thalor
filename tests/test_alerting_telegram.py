from __future__ import annotations

import json
from pathlib import Path

from natbin.alerting.telegram import alerts_status_payload, dispatch_telegram_alert
from natbin.control.plan import build_context


def _write_repo(repo: Path, *, send_enabled: bool = False) -> Path:
    (repo / 'config').mkdir(parents=True, exist_ok=True)
    (repo / 'secrets').mkdir(parents=True, exist_ok=True)
    (repo / 'secrets' / 'bundle.yaml').write_text(
        '\n'.join(
            [
                'broker:',
                '  email: trader@example.com',
                '  password: trader-secret',
                '  balance_mode: PRACTICE',
                'telegram:',
                '  bot_token: 123456:ABCDEF',
                '  chat_id: "999888777"',
                '',
            ]
        ),
        encoding='utf-8',
    )
    cfg = repo / 'config' / 'base.yaml'
    cfg.write_text(
        '\n'.join(
            [
                'version: "2.0"',
                'security:',
                '  deployment_profile: live',
                '  secrets_file: secrets/bundle.yaml',
                '  live_require_external_credentials: true',
                'notifications:',
                '  enabled: true',
                '  telegram:',
                '    enabled: true',
                f'    send_enabled: {str(bool(send_enabled)).lower()}',
                'execution:',
                '  enabled: true',
                '  mode: live',
                '  provider: iqoption',
                'assets:',
                '  - asset: EURUSD-OTC',
                '    interval_sec: 300',
                '    timezone: UTC',
                '',
            ]
        ),
        encoding='utf-8',
    )
    return cfg


def test_dispatch_telegram_alert_queues_when_send_disabled(tmp_path: Path) -> None:
    cfg_path = _write_repo(tmp_path, send_enabled=False)
    ctx = build_context(repo_root=tmp_path, config_path=cfg_path)

    payload = dispatch_telegram_alert(
        repo_root=tmp_path,
        resolved_config=ctx.resolved_config,
        title='Smoke alert',
        lines=['line 1', 'line 2'],
        severity='warn',
        source='pytest',
    )
    assert payload['delivery']['status'] == 'queued'
    outbox = tmp_path / 'runs' / 'alerts' / 'telegram_outbox.jsonl'
    assert outbox.exists()
    lines = [json.loads(line) for line in outbox.read_text(encoding='utf-8').splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]['severity'] == 'warn'
    assert lines[0]['credentials_present'] is True

    status = alerts_status_payload(repo_root=tmp_path, resolved_config=ctx.resolved_config, limit=20)
    tg = status['telegram']
    assert tg['enabled'] is True
    assert tg['send_enabled'] is False
    assert tg['credentials_present'] is True
    assert tg['recent_counts']['queued'] >= 1


def test_dispatch_telegram_alert_sends_when_enabled(tmp_path: Path, monkeypatch) -> None:
    cfg_path = _write_repo(tmp_path, send_enabled=True)
    ctx = build_context(repo_root=tmp_path, config_path=cfg_path)

    def _fake_send(**kwargs):
        return {'http_status': 200, 'response': {'ok': True, 'result': {'message_id': 1}}}

    monkeypatch.setattr('natbin.alerting.telegram._send_telegram_message', _fake_send)

    payload = dispatch_telegram_alert(
        repo_root=tmp_path,
        resolved_config=ctx.resolved_config,
        title='Prod alert',
        lines=['release ok'],
        severity='info',
        source='pytest',
    )
    assert payload['delivery']['status'] == 'sent'
    state = json.loads((tmp_path / 'runs' / 'alerts' / 'telegram_state.json').read_text(encoding='utf-8'))
    assert payload['alert_id'] in state['sent_ids']
