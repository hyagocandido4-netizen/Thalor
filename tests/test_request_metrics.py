from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from natbin.utils.request_metrics import RequestMetrics, RequestMetricsConfig


class _NowClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value



def test_request_metrics_rolls_over_by_day_and_emits_summary(caplog) -> None:
    clock = _NowClock(datetime(2026, 3, 28, 10, 15, tzinfo=UTC))
    logger = logging.getLogger('tests.request_metrics.rollover')

    with caplog.at_level(logging.INFO, logger=logger.name):
        metrics = RequestMetrics(
            RequestMetricsConfig(enabled=True, timezone='UTC', emit_summary_on_rollover=True),
            logger=logger,
            now_fn=clock,
        )
        metrics.record_success(operation='connect', target='iqoption', latency_s=0.2)
        metrics.record_failure(operation='get_candles', target='iqoption', latency_s=1.5)

        snap_day_1 = metrics.snapshot()
        assert snap_day_1['current']['day'] == '2026-03-28'
        assert snap_day_1['current']['total_requests'] == 2
        assert snap_day_1['current']['total_successes'] == 1
        assert snap_day_1['current']['total_failures'] == 1
        assert snap_day_1['current']['operation_counts'] == {'connect': 1, 'get_candles': 1}
        assert snap_day_1['current']['target_counts'] == {'iqoption': 2}
        assert snap_day_1['current']['avg_latency_ms'] == 850.0
        assert snap_day_1['current']['max_latency_ms'] == 1500.0

        clock.value = datetime(2026, 3, 29, 0, 1, tzinfo=UTC)
        metrics.record_success(operation='connect', target='iqoption', latency_s=0.1)

    payloads = []
    for record in caplog.records:
        try:
            payloads.append(json.loads(record.message))
        except Exception:
            continue

    summary = next(item for item in payloads if item.get('event') == 'request_metrics_summary' and item.get('reason') == 'day_rollover')
    assert summary['day'] == '2026-03-28'
    assert summary['total_requests'] == 2
    assert summary['total_successes'] == 1
    assert summary['total_failures'] == 1
    assert summary['operation_counts'] == {'connect': 1, 'get_candles': 1}

    snap_day_2 = metrics.snapshot()
    assert snap_day_2['current']['day'] == '2026-03-29'
    assert snap_day_2['current']['total_requests'] == 1
    assert snap_day_2['current']['total_successes'] == 1
    assert snap_day_2['current']['total_failures'] == 0



def test_request_metrics_manual_summary_writes_jsonl(tmp_path: Path) -> None:
    log_path = tmp_path / 'logs' / 'request_metrics.jsonl'
    metrics = RequestMetrics(
        RequestMetricsConfig(enabled=True, timezone='UTC', structured_log_path=log_path),
        now_fn=lambda: datetime(2026, 3, 28, 12, 0, tzinfo=UTC),
    )

    metrics.record_success(operation='connect', target='iqoption', latency_s=0.25)
    metrics.record_success(operation='connect', target='iqoption', latency_s=0.5)
    summary = metrics.emit_summary(reason='manual')

    assert summary is not None
    assert summary['day'] == '2026-03-28'
    assert summary['total_requests'] == 2
    assert log_path.exists() is True

    lines = [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    summary_events = [item for item in lines if item.get('event') == 'request_metrics_summary']
    assert len(summary_events) == 1
    assert summary_events[0]['reason'] == 'manual'
    assert summary_events[0]['total_requests'] == 2


def test_request_metrics_emit_request_events_and_periodic_summary(tmp_path: Path) -> None:
    log_path = tmp_path / 'logs' / 'request_metrics.jsonl'
    metrics = RequestMetrics(
        RequestMetricsConfig(
            enabled=True,
            timezone='UTC',
            structured_log_path=log_path,
            emit_request_events=True,
            emit_summary_every_requests=2,
        ),
        now_fn=lambda: datetime(2026, 3, 28, 12, 30, tzinfo=UTC),
    )

    metrics.record_success(operation='connect', target='iqoption:primary', latency_s=0.25, extra={'attempt': 1, 'label': 'iqoption_connect'})
    metrics.record_failure(operation='get_candles', target='iqoption:primary', latency_s=1.0, extra={'attempt': 1, 'label': 'get_candles', 'reason': 'timeout'})

    lines = [json.loads(line) for line in log_path.read_text(encoding='utf-8').splitlines() if line.strip()]
    request_events = [item for item in lines if item.get('event') == 'request_metrics_request']
    periodic_summaries = [item for item in lines if item.get('event') == 'request_metrics_summary' and item.get('reason') == 'periodic']

    assert len(request_events) == 2
    assert request_events[0]['operation'] == 'connect'
    assert request_events[0]['target'] == 'iqoption:primary'
    assert request_events[0]['success'] is True
    assert request_events[0]['attempt'] == 1
    assert request_events[0]['latency_ms'] == 250.0
    assert request_events[1]['operation'] == 'get_candles'
    assert request_events[1]['success'] is False
    assert request_events[1]['reason'] == 'timeout'
    assert len(periodic_summaries) == 1
    assert periodic_summaries[0]['total_requests'] == 2



def test_request_metrics_is_thread_safe() -> None:
    metrics = RequestMetrics(
        RequestMetricsConfig(enabled=True, timezone='UTC'),
        now_fn=lambda: datetime(2026, 3, 28, 13, 0, tzinfo=UTC),
    )

    def _record(_: int) -> None:
        metrics.record_success(operation='get_candles', target='iqoption', latency_s=0.05)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_record, range(128)))

    snapshot = metrics.snapshot()
    assert snapshot['current']['total_requests'] == 128
    assert snapshot['current']['total_successes'] == 128
    assert snapshot['current']['total_failures'] == 0
    assert snapshot['current']['operation_counts'] == {'get_candles': 128}
    assert snapshot['current']['target_counts'] == {'iqoption': 128}
    assert snapshot['current']['avg_latency_ms'] == 50.0
    assert snapshot['current']['max_latency_ms'] == 50.0



def test_request_metrics_config_from_env(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / 'req_metrics.jsonl'
    monkeypatch.setenv('REQUEST_METRICS_ENABLED', '1')
    monkeypatch.setenv('REQUEST_METRICS_TIMEZONE', 'America/Sao_Paulo')
    monkeypatch.setenv('REQUEST_METRICS_LOG_PATH', str(log_path))
    monkeypatch.setenv('REQUEST_METRICS_SUMMARY_LOG_LEVEL', 'WARNING')
    monkeypatch.setenv('REQUEST_METRICS_EMIT_SUMMARY_ON_ROLLOVER', '0')
    monkeypatch.setenv('REQUEST_METRICS_EMIT_REQUEST_EVENTS', '0')
    monkeypatch.setenv('REQUEST_METRICS_EMIT_SUMMARY_EVERY_REQUESTS', '10')

    cfg = RequestMetricsConfig.from_env()

    assert cfg.enabled is True
    assert cfg.timezone == 'America/Sao_Paulo'
    assert cfg.structured_log_path == log_path
    assert cfg.summary_log_level == logging.WARNING
    assert cfg.emit_summary_on_rollover is False
    assert cfg.emit_summary_on_close is True
    assert cfg.emit_request_events is False
    assert cfg.emit_summary_every_requests == 10
