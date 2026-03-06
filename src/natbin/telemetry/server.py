from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .metrics import MetricRegistry, REGISTRY, render_prometheus_text


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec='seconds')


def _parse_bind(bind: str) -> tuple[str, int]:
    raw = str(bind or '').strip()
    if not raw:
        return '127.0.0.1', 9108
    if ':' not in raw:
        return raw, 9108
    host, port_s = raw.rsplit(':', 1)
    try:
        port = int(port_s)
    except Exception:
        port = 9108
    return host.strip() or '127.0.0.1', max(1, min(65535, int(port)))


@dataclass
class TelemetryState:
    """In-memory health snapshot surfaced by /readyz and /healthz."""

    started_at_utc: str = field(default_factory=_utc_now_iso)
    last_update_utc: str | None = None

    # Overall process readiness (best-effort; depends on the embedding loop)
    ready: bool = False
    ready_reason: str = 'starting'

    # Last observed runtime cycle
    last_cycle_id: str | None = None
    last_cycle_ok: bool | None = None
    last_cycle_message: str | None = None

    # Global gates
    kill_switch_active: bool = False
    drain_mode_active: bool = False

    # Per-scope hints (portfolio mode)
    scopes: dict[str, dict[str, Any]] = field(default_factory=dict)

    def update(self, **fields: Any) -> None:
        self.last_update_utc = _utc_now_iso()
        for k, v in fields.items():
            if hasattr(self, k):
                setattr(self, k, v)
            else:
                # best-effort extensibility
                self.scopes.setdefault('_extra', {})[str(k)] = v

    def scope_update(self, scope_tag: str, **fields: Any) -> None:
        self.last_update_utc = _utc_now_iso()
        entry = dict(self.scopes.get(scope_tag) or {})
        entry.update({str(k): v for k, v in fields.items()})
        self.scopes[str(scope_tag)] = entry

    def snapshot(self) -> dict[str, Any]:
        return {
            **asdict(self),
            'at_utc': _utc_now_iso(),
        }


class _Handler(BaseHTTPRequestHandler):
    registry: MetricRegistry
    state: TelemetryState

    def _send(self, code: int, body: bytes, *, content_type: str) -> None:
        self.send_response(int(code))
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def log_message(self, format: str, *args):  # noqa: A003 - BaseHTTPRequestHandler API
        # Silence default HTTP server logging; runtime already has its own logs.
        return

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        path = (self.path or '/').split('?', 1)[0]
        if path == '/metrics':
            text = render_prometheus_text(self.registry)
            self._send(200, text.encode('utf-8'), content_type='text/plain; version=0.0.4; charset=utf-8')
            return
        if path == '/livez':
            payload = {
                'status': 'ok',
                'started_at_utc': self.state.started_at_utc,
                'uptime_sec': int(max(0.0, time.time() - _iso_to_epoch(self.state.started_at_utc))),
            }
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self._send(200, body, content_type='application/json; charset=utf-8')
            return
        if path == '/readyz':
            ok = bool(self.state.ready)
            payload = {
                'ready': ok,
                'reason': self.state.ready_reason,
                'last_update_utc': self.state.last_update_utc,
                'kill_switch_active': bool(self.state.kill_switch_active),
                'drain_mode_active': bool(self.state.drain_mode_active),
            }
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self._send(200 if ok else 503, body, content_type='application/json; charset=utf-8')
            return
        if path == '/healthz':
            body = json.dumps(self.state.snapshot(), ensure_ascii=False).encode('utf-8')
            self._send(200, body, content_type='application/json; charset=utf-8')
            return
        self._send(404, b'not_found', content_type='text/plain; charset=utf-8')


def _iso_to_epoch(iso: str | None) -> float:
    if not iso:
        return time.time()
    try:
        return datetime.fromisoformat(str(iso)).replace(tzinfo=UTC).timestamp()
    except Exception:
        return time.time()


class TelemetryServer:
    def __init__(
        self,
        *,
        bind: str,
        registry: MetricRegistry | None = None,
        state: TelemetryState | None = None,
    ) -> None:
        self.bind = str(bind or '127.0.0.1:9108')
        self.registry = registry or REGISTRY
        self.state = state or TelemetryState()
        self._thread: threading.Thread | None = None
        self._httpd: ThreadingHTTPServer | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        host, port = _parse_bind(self.bind)

        # Build a handler class bound to this instance.
        registry = self.registry
        state = self.state

        class Handler(_Handler):
            registry = registry
            state = state

        httpd = ThreadingHTTPServer((host, port), Handler)
        self._httpd = httpd

        def _run() -> None:
            try:
                httpd.serve_forever(poll_interval=0.5)
            except Exception:
                return

        t = threading.Thread(target=_run, name='thalor_telemetry_http', daemon=True)
        t.start()
        self._thread = t

    def stop(self) -> None:
        httpd = self._httpd
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        self._httpd = None
        self._thread = None
