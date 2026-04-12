from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def parse_json_events(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    events: list[dict[str, Any]] = []
    idx = 0
    length = len(text)
    while idx < length:
        next_obj = text.find('{', idx)
        next_arr = text.find('[', idx)
        starts = [value for value in (next_obj, next_arr) if value >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            obj, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            idx = start + 1
            continue
        if isinstance(obj, (dict, list)):
            item: dict[str, Any] = {
                'start': start,
                'end': start + end,
                'type': 'dict' if isinstance(obj, dict) else 'list',
                'payload': obj,
            }
            if isinstance(obj, dict):
                item['kind'] = obj.get('kind')
                item['ok'] = obj.get('ok')
                item['severity'] = obj.get('severity')
            events.append(item)
        idx = start + end
    return events


def _event_rank(event: dict[str, Any]) -> tuple[int, int]:
    payload = event.get('payload')
    if isinstance(payload, dict) and str(payload.get('kind') or '').strip():
        return (3, int(event.get('end') or 0))
    if isinstance(payload, dict) and any(key in payload for key in ('ok', 'severity', 'ready_for_long_practice', 'ready_for_practice', 'ready_for_cycle', 'stability_state')):
        return (2, int(event.get('end') or 0))
    return (1, int(event.get('end') or 0))


def _select_last_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not events:
        return None
    ranked = sorted(enumerate(events), key=lambda pair: (_event_rank(pair[1]), pair[0]))
    return ranked[-1][1]


def select_last_payload(text: str) -> dict[str, Any] | list[Any] | None:
    events = parse_json_events(text)
    event = _select_last_event(events) or (events[-1] if events else None)
    if not isinstance(event, dict):
        return None
    payload = event.get('payload')
    if isinstance(payload, (dict, list)):
        return payload
    return None


def select_last_dict(text: str) -> dict[str, Any] | None:
    payload = select_last_payload(text)
    return payload if isinstance(payload, dict) else None


def write_json_summary(*, base_dir: Path, stdout_text: str) -> dict[str, Any]:
    base_dir.mkdir(parents=True, exist_ok=True)
    events = parse_json_events(stdout_text)
    summary: dict[str, Any] = {
        'json_event_count': len(events),
        'events': [
            {
                'index': i,
                'start': event['start'],
                'end': event['end'],
                'type': event['type'],
                'kind': event.get('kind'),
                'ok': event.get('ok'),
                'severity': event.get('severity'),
            }
            for i, event in enumerate(events)
        ],
        'last_json_kind': None,
        'last_json_ok': None,
        'last_json_severity': None,
    }
    if events:
        with (base_dir / 'json_events.jsonl').open('w', encoding='utf-8') as fh:
            for i, event in enumerate(events):
                fh.write(json.dumps({
                    'index': i,
                    'kind': event.get('kind'),
                    'ok': event.get('ok'),
                    'severity': event.get('severity'),
                    'payload': event['payload'],
                }, ensure_ascii=False) + '\n')
        last = _select_last_event(events) or events[-1]
        payload = last['payload']
        (base_dir / 'last_json.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        summary['last_json_kind'] = last.get('kind')
        summary['last_json_ok'] = last.get('ok')
        summary['last_json_severity'] = last.get('severity')
        if isinstance(payload, dict):
            for key in ('ready_for_long_practice', 'ready_for_practice', 'ready_for_cycle', 'ready_for_live', 'ready_for_real', 'stability_state'):
                if key in payload:
                    summary[key] = payload.get(key)
    (base_dir / 'parsed_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return summary


__all__ = ['parse_json_events', 'select_last_payload', 'select_last_dict', 'write_json_summary']
