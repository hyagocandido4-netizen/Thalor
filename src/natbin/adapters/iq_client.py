import time
import threading
from dataclasses import dataclass
from typing import Any, Mapping

import importlib
import json
import os
import random
import types
from contextlib import contextmanager
from pathlib import Path

from ..config.env import env_bool, env_float, env_int


class IQDependencyUnavailable(RuntimeError):
    """Raised when the optional iqoptionapi package is not available."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = str(reason or 'iqoption_dependency_missing')


_IQ_OPTION_CLASS = None
_IQ_OPTION_IMPORT_ERROR: Exception | None = None


def iqoption_dependency_status(transport_manager=None) -> dict[str, Any]:
    """Return the availability of the optional iqoptionapi dependency.

    The environment toggle ``THALOR_FORCE_IQOPTIONAPI_MISSING=1`` is used by
    deterministic tests/smokes so CI can exercise the fallback path even when
    the package is installed.

    When a transport manager is supplied we also surface missing transport
    dependencies (for example PySocks for SOCKS endpoints) as blockers because
    the provider would be effectively unusable in that configuration.
    """
    global _IQ_OPTION_CLASS, _IQ_OPTION_IMPORT_ERROR

    if env_bool('THALOR_FORCE_IQOPTIONAPI_MISSING', False):
        return {
            'available': False,
            'reason': 'env:THALOR_FORCE_IQOPTIONAPI_MISSING',
        }

    available = False
    reason = None
    if _IQ_OPTION_CLASS is not None:
        available = True
    elif _IQ_OPTION_IMPORT_ERROR is not None:
        reason = f'{type(_IQ_OPTION_IMPORT_ERROR).__name__}: {_IQ_OPTION_IMPORT_ERROR}'
    else:
        try:
            from iqoptionapi.stable_api import IQ_Option as _ImportedIQOption
        except Exception as exc:  # pragma: no cover - depends on local environment
            _IQ_OPTION_IMPORT_ERROR = exc
            reason = f'{type(exc).__name__}: {exc}'
        else:
            _IQ_OPTION_CLASS = _patch_iqoption_class(_ImportedIQOption)
            available = True

    payload = {'available': bool(available), 'reason': reason}
    manager = transport_manager
    if manager is not None:
        try:
            dependency = manager.dependency_status()
        except Exception as exc:  # pragma: no cover - defensive only
            dependency = {
                'available': False,
                'reason': f'{type(exc).__name__}: {exc}',
                'requires_pysocks': False,
            }
        payload['transport_dependency'] = dependency
        if payload['available'] and not bool(dependency.get('available', True)):
            payload['available'] = False
            payload['reason'] = str(dependency.get('reason') or payload.get('reason') or 'network_transport_dependency_missing')
    return payload


def _patch_iqoption_class(iq_cls: Any) -> Any:
    """Apply small safety shims to iqoptionapi when available.

    The upstream client may spawn a background thread that assumes
    ``get_digital_underlying_list_data()["underlying"]`` always exists.
    In practice some OTC/account combinations return malformed payloads and the
    thread crashes noisily with ``KeyError: "underlying"`` even though the
    caller does not rely on digital-open data.

    We patch that private helper once so the thread simply exits when the
    payload is missing or malformed. This keeps diagnostics deterministic and
    does not change trade submission logic.
    """
    try:
        if bool(getattr(iq_cls, '__thalor_digital_open_patched__', False)):
            return iq_cls
    except Exception:
        pass

    attr_name = '_IQ_Option__get_digital_open'
    original = getattr(iq_cls, attr_name, None)
    if callable(original):
        def _safe_get_digital_open(self, *args, **kwargs):
            try:
                payload = self.get_digital_underlying_list_data()
            except Exception:
                return None
            try:
                underlying = payload.get('underlying') if isinstance(payload, Mapping) else None
            except Exception:
                underlying = None
            if not isinstance(underlying, list):
                return None
            try:
                for digital in underlying:
                    if not isinstance(digital, Mapping):
                        continue
                    name = str(digital.get('underlying') or '').strip()
                    if not name:
                        continue
                    schedule = digital.get('schedule') or []
                    bucket = self.OPEN_TIME['digital'][name]
                    bucket['open'] = False
                    for schedule_time in schedule:
                        if not isinstance(schedule_time, Mapping):
                            continue
                        try:
                            start = float(schedule_time.get('open'))
                            end = float(schedule_time.get('close'))
                        except Exception:
                            continue
                        if start < time.time() < end:
                            bucket['open'] = True
                            break
            except Exception:
                return None
            return None
        setattr(iq_cls, attr_name, _safe_get_digital_open)

    try:
        setattr(iq_cls, '__thalor_digital_open_patched__', True)
    except Exception:
        pass
    return iq_cls




def _patch_iqoption_instance(instance: Any) -> Any:
    """Patch a live IQ instance defensively, even when the class was imported earlier.

    This is a second safety belt beyond class-level patching. It prevents the
    upstream background digital-open thread from exploding with KeyError when
    the payload comes malformed or missing. Best-effort only.
    """
    try:
        if bool(getattr(instance, '__thalor_instance_digital_open_patched__', False)):
            return instance
    except Exception:
        pass

    getter = getattr(instance, 'get_digital_underlying_list_data', None)
    if callable(getter):
        def _safe_get_digital_underlying_list_data(self, *args, **kwargs):
            try:
                payload = getter(*args, **kwargs)
            except Exception as exc:
                try:
                    from ..runtime.provider_issue_recorder import record_provider_issue_event
                    record_provider_issue_event(operation='iqoption:get_digital_underlying_list_data', reason=f'{type(exc).__name__}: {exc}', source='iq_client.instance_patch', dedupe_window_sec=120.0)
                except Exception:
                    pass
                return {'underlying': []}
            if not isinstance(payload, Mapping):
                return {'underlying': []}
            underlying = payload.get('underlying')
            if not isinstance(underlying, list):
                try:
                    from ..runtime.provider_issue_recorder import record_provider_issue_event
                    record_provider_issue_event(operation='iqoption:digital_underlying_payload', reason='missing_underlying_list', source='iq_client.instance_patch', dedupe_window_sec=900.0, dedupe_key='iq_digital_underlying_payload_missing')
                except Exception:
                    pass
                sanitized = dict(payload)
                sanitized['underlying'] = []
                return sanitized
            return payload
        try:
            instance.get_digital_underlying_list_data = types.MethodType(_safe_get_digital_underlying_list_data, instance)
        except Exception:
            pass

    def _safe_get_digital_open(self, *args, **kwargs):
        try:
            payload = self.get_digital_underlying_list_data()
            underlying = payload.get('underlying') if isinstance(payload, Mapping) else []
            if not isinstance(underlying, list):
                underlying = []
            for digital in underlying:
                if not isinstance(digital, Mapping):
                    continue
                name = str(digital.get('underlying') or '').strip()
                if not name:
                    continue
                schedule = digital.get('schedule') or []
                bucket = self.OPEN_TIME['digital'][name]
                bucket['open'] = False
                for schedule_time in schedule:
                    if not isinstance(schedule_time, Mapping):
                        continue
                    try:
                        start = float(schedule_time.get('open'))
                        end = float(schedule_time.get('close'))
                    except Exception:
                        continue
                    if start < time.time() < end:
                        bucket['open'] = True
                        break
        except Exception as exc:
            try:
                from ..runtime.provider_issue_recorder import record_provider_issue_event
                record_provider_issue_event(operation='iqoption:__get_digital_open', reason=f'{type(exc).__name__}: {exc}', source='iq_client.instance_patch')
            except Exception:
                pass
        return None

    for attr_name in ('_IQ_Option__get_digital_open', '__get_digital_open'):
        try:
            setattr(instance, attr_name, types.MethodType(_safe_get_digital_open, instance))
        except Exception:
            pass

    try:
        setattr(instance, '__thalor_instance_digital_open_patched__', True)
    except Exception:
        pass
    return instance


def require_iqoption_class():
    status = iqoption_dependency_status()
    if not bool(status.get("available")):
        raise IQDependencyUnavailable(str(status.get("reason") or "iqoption_dependency_missing"))
    return _IQ_OPTION_CLASS


@dataclass
class IQConfig:
    email: str
    password: str
    balance_mode: str = 'PRACTICE'
    transport: dict[str, Any] | None = None
    market_context_timeout_s: float = 2.0
    connect_timeout_s: float = 25.0

def _env_path(key: str, default: str) -> str:
    v = os.environ.get(key)
    return v.strip() if isinstance(v, str) and v.strip() else default


@contextmanager
def _file_lock(lock_path: Path):
    """Cross-platform file lock (best-effort).

    Used to coordinate throttling state between multiple processes.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt  # type: ignore

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl  # type: ignore

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield fh
    finally:
        try:
            if os.name == "nt":
                import msvcrt  # type: ignore

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl  # type: ignore

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass


def _throttle_schedule(*, min_interval_s: float, jitter_s: float, state_file: str, label: str) -> None:
    """Best-effort cross-process throttling for IQ API calls.

    Motivation: when running multi-asset pipelines in parallel, each scope runs in
    its own process and can burst requests simultaneously. This throttle spreads
    call start times to reduce rate-limit risk and smooth I/O.

    Configure via env vars:
      - IQ_THROTTLE_MIN_INTERVAL_S (float, default 0.0)
      - IQ_THROTTLE_JITTER_S       (float, default 0.0)
      - IQ_THROTTLE_STATE_FILE     (path, default 'runs/iq_throttle_state.json')

    IMPORTANT: this is for stability only (not for evasion).
    """
    try:
        mi = float(min_interval_s or 0.0)
        js = float(jitter_s or 0.0)
    except Exception:
        return
    if mi <= 0.0:
        return
    if js < 0.0:
        js = 0.0

    try:
        state_path = Path(state_file)
    except Exception:
        state_path = Path("runs/iq_throttle_state.json")
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    now = time.time()
    start_at = now

    try:
        with _file_lock(lock_path):
            state: dict[str, Any] = {}
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                except Exception:
                    state = {}

            next_utc = float(state.get("next_utc", 0.0) or 0.0)
            start_at = max(now, next_utc)
            if js > 0.0:
                start_at += random.random() * js

            new_next = float(start_at + mi)
            state_out: dict[str, Any] = {
                "next_utc": new_next,
                "updated_utc": now,
                "last_label": str(label),
                "min_interval_s": mi,
                "jitter_s": js,
            }
            try:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(json.dumps(state_out, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                # best-effort: if we can't write, still apply sleep below
                pass
    except Exception:
        return

    sleep_s = float(start_at - now)
    if sleep_s > 0.0:
        time.sleep(sleep_s)

class IQClient:
    def __init__(self, cfg: IQConfig, transport_manager=None, request_metrics=None):
        self.cfg = cfg
        self._guarded_call_cooldowns: dict[str, float] = {}
        self._asset_registry: dict[str, list[dict[str, Any]]] = {}
        self._asset_resolution_cache: dict[str, str] = {}
        self._transport_manager = transport_manager if transport_manager is not None else self._build_transport_manager(getattr(cfg, 'transport', None))
        self._request_metrics = request_metrics
        self._active_transport_binding = None
        self._new_api()

    @staticmethod
    def dependency_status() -> dict[str, Any]:
        return iqoption_dependency_status()

    @staticmethod
    def _build_transport_manager(settings: Any):
        if not settings:
            return None
        try:
            from ..utils.network_transport import NetworkTransportManager

            manager = NetworkTransportManager.from_mapping(settings if isinstance(settings, Mapping) else dict(settings))
        except Exception:
            return None
        return manager if bool(getattr(manager, 'enabled', False)) and bool(getattr(manager, 'ready', False)) else None

    @classmethod
    def from_runtime_config(cls, *, repo_root: str | Path = '.', config_path: str | Path | None = None, asset: str | None = None, interval_sec: int | None = None):
        from ..config.loader import load_resolved_config
        from ..runtime.connectivity import build_runtime_network_transport_manager, build_runtime_request_metrics

        resolved = load_resolved_config(repo_root=repo_root, config_path=config_path, asset=asset, interval_sec=interval_sec)
        broker = resolved.broker
        password = broker.password.get_secret_value() if getattr(broker, 'password', None) is not None else ''
        cfg = IQConfig(
            email=str(getattr(broker, 'email', '') or ''),
            password=str(password or ''),
            balance_mode=str(getattr(broker, 'balance_mode', 'PRACTICE') or 'PRACTICE'),
            transport=None,
            market_context_timeout_s=float(env_float('IQ_MARKET_CONTEXT_TIMEOUT_S', 2.0)),
            connect_timeout_s=float(env_float('IQ_CONNECT_TIMEOUT_S', float(getattr(broker, 'timeout_connect_s', 25) or 25))),
        )
        transport_manager = build_runtime_network_transport_manager(resolved_config=resolved, repo_root=repo_root)
        request_metrics = build_runtime_request_metrics(resolved_config=resolved, repo_root=repo_root)
        return cls(cfg, transport_manager=transport_manager, request_metrics=request_metrics)

    def transport_snapshot(self) -> dict[str, Any]:
        manager = getattr(self, '_transport_manager', None)
        if manager is None:
            return {
                'enabled': False,
                'ready': False,
                'active_binding': None,
                'endpoint_count': 0,
                'endpoints': [],
            }
        snapshot = dict(manager.snapshot())
        active_binding = getattr(self, '_active_transport_binding', None)
        snapshot['active_binding'] = active_binding.as_dict(mask_secret=True) if active_binding is not None else None
        return snapshot

    def request_metrics_snapshot(self) -> dict[str, Any] | None:
        metrics = getattr(self, '_request_metrics', None)
        if metrics is None:
            return None
        try:
            return metrics.snapshot()
        except Exception:
            return None

    def _request_metric_target(self, binding) -> str:
        endpoint = getattr(binding, 'endpoint', None) if binding is not None else None
        if endpoint is not None and getattr(endpoint, 'name', None):
            return f'iqoption:{endpoint.name}'
        return 'iqoption:direct'

    @staticmethod
    def _metrics_operation_name(label: str) -> str:
        text = str(label or '').strip()
        if not text:
            return 'default'
        return text.split(':', 1)[0].strip() or 'default'

    def _request_metric_extra(self, *, binding, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(extra or {})
        endpoint = getattr(binding, 'endpoint', None) if binding is not None else None
        if endpoint is None:
            payload.setdefault('transport_scheme', 'direct')
            payload.setdefault('transport_target', 'direct')
            return payload
        payload.setdefault('transport_scheme', getattr(endpoint, 'scheme', None))
        payload.setdefault('transport_target', getattr(endpoint, 'name', None) or f'{getattr(endpoint, "host", None)}:{getattr(endpoint, "port", None)}')
        payload.setdefault('transport_host', getattr(endpoint, 'host', None))
        payload.setdefault('transport_port', getattr(endpoint, 'port', None))
        payload.setdefault('transport_source', getattr(endpoint, 'source', None))
        payload.setdefault('transport_type', getattr(endpoint, 'transport_type', None))
        return payload

    def _record_request_metric(self, *, binding, operation: str, success: bool, latency_s: float | None = None, extra: Mapping[str, Any] | None = None) -> None:
        metrics = getattr(self, '_request_metrics', None)
        if metrics is None:
            return
        try:
            payload = self._request_metric_extra(binding=binding, extra=extra)
            if success:
                metrics.record_success(operation=operation, target=self._request_metric_target(binding), latency_s=latency_s, extra=payload)
            else:
                metrics.record_failure(operation=operation, target=self._request_metric_target(binding), latency_s=latency_s, extra=payload)
        except Exception:
            pass

    def _connect_attempt_timeout_s(self, binding, explicit_timeout_s: float | None = None) -> float:
        def _coerce(value) -> float | None:
            if value in (None, ''):
                return None
            try:
                parsed = float(value)
            except Exception:
                return None
            return parsed if parsed > 0.0 else None

        for candidate in (
            explicit_timeout_s,
            getattr(self.cfg, 'connect_timeout_s', None),
            env_float('IQ_CONNECT_TIMEOUT_S', 0.0),
        ):
            parsed = _coerce(candidate)
            if parsed is not None:
                return parsed

        endpoint = getattr(binding, 'endpoint', None) if binding is not None else None
        parsed = _coerce(getattr(endpoint, 'connect_timeout_s', None) if endpoint is not None else None)
        if parsed is not None:
            return parsed
        return 0.0

    def _select_transport_binding(self, operation: str, *, allow_fail_open: bool | None = None, required: bool = False):
        manager = getattr(self, '_transport_manager', None)
        if manager is None:
            return None
        active_binding = getattr(self, '_active_transport_binding', None)
        if active_binding is not None and operation not in {'iqoption_connect', 'iqoption_init'}:
            return active_binding
        try:
            return manager.select_binding(operation=operation, allow_fail_open=allow_fail_open)
        except Exception:
            if required and bool(getattr(manager, 'enabled', False)):
                raise
            return getattr(self, '_active_transport_binding', None)

    @contextmanager
    def _apply_transport_binding(self, binding):
        manager = getattr(self, '_transport_manager', None)
        if binding is None or manager is None:
            yield
            return
        with manager.apply_environment(binding):
            yield

    def _record_transport_success(self, binding, operation: str) -> None:
        manager = getattr(self, '_transport_manager', None)
        if binding is None or manager is None:
            return
        try:
            manager.record_success(getattr(binding, 'endpoint', None), operation=operation)
        except Exception:
            pass

    def _record_transport_failure(self, binding, operation: str, error: BaseException | str | None) -> None:
        manager = getattr(self, '_transport_manager', None)
        if binding is None or manager is None:
            return
        try:
            manager.record_failure(getattr(binding, 'endpoint', None), operation=operation, error=error)
        except Exception:
            pass

    def _maybe_throttle(self, label: str) -> None:
        """Best-effort throttling (cross-process) for API calls.

        Controlled by env vars (defaults disable):
          - IQ_THROTTLE_MIN_INTERVAL_S
          - IQ_THROTTLE_JITTER_S
          - IQ_THROTTLE_STATE_FILE
        """
        mi = float(env_float("IQ_THROTTLE_MIN_INTERVAL_S", 0.0) or 0.0)
        if mi <= 0.0:
            return
        js = float(env_float("IQ_THROTTLE_JITTER_S", 0.0) or 0.0)
        state_file = _env_path("IQ_THROTTLE_STATE_FILE", "runs/iq_throttle_state.json")
        _throttle_schedule(min_interval_s=mi, jitter_s=js, state_file=state_file, label=label)

    def _install_transport_runtime_bridge(self, instance: Any, binding) -> None:
        endpoint = getattr(binding, 'endpoint', None) if binding is not None else None
        if endpoint is None:
            return
        try:
            stable_mod = importlib.import_module(instance.__class__.__module__)
            ws_mod = importlib.import_module('iqoptionapi.ws.client')
            api_mod = importlib.import_module('iqoptionapi.api')
        except Exception:
            return
        original_api_cls = getattr(stable_mod, 'IQOptionAPI', None)
        original_ws_cls = getattr(ws_mod, 'WebsocketClient', None)
        original_connect = getattr(instance, 'connect', None)
        if not callable(original_api_cls) or not callable(original_ws_cls) or not callable(original_connect):
            return

        proxy_url = endpoint.proxy_url(mask_secret=False)
        ws_kwargs = dict(getattr(binding, 'websocket_options', {}) or {})

        def _wrapped_connect(this, *args, **kwargs):
            stable_saved = getattr(stable_mod, 'IQOptionAPI', None)
            api_saved = getattr(api_mod, 'IQOptionAPI', None)
            ws_saved = getattr(ws_mod, 'WebsocketClient', None)

            class _TransportAwareIQOptionAPI(original_api_cls):
                def __init__(self, host: str, username: str, password: str, proxies: dict[str, str] | None = None, *a, **kw):
                    effective_proxies = dict(proxies or {})
                    effective_proxies.setdefault('http', proxy_url)
                    effective_proxies.setdefault('https', proxy_url)
                    try:
                        super().__init__(host, username, password, proxies=effective_proxies, *a, **kw)
                    except TypeError:
                        super().__init__(host, username, password, *a, **kw)
                    try:
                        self.proxies = dict(getattr(self, 'proxies', {}) or {})
                        self.proxies.setdefault('http', proxy_url)
                        self.proxies.setdefault('https', proxy_url)
                    except Exception:
                        pass
                    try:
                        session = getattr(self, 'session', None)
                        if session is not None:
                            proxies_map = dict(getattr(session, 'proxies', {}) or {})
                            proxies_map.setdefault('http', proxy_url)
                            proxies_map.setdefault('https', proxy_url)
                            session.proxies = proxies_map
                    except Exception:
                        pass

            class _TransportAwareWebsocketClient(original_ws_cls):
                def __init__(self, api, *a, **kw):
                    super().__init__(api, *a, **kw)
                    try:
                        original_run_forever = self.wss.run_forever

                        def _run_forever(*rf_args, **rf_kwargs):
                            merged = dict(ws_kwargs)
                            merged.update(rf_kwargs)
                            return original_run_forever(*rf_args, **merged)

                        self.wss.run_forever = _run_forever
                    except Exception:
                        pass

            stable_mod.IQOptionAPI = _TransportAwareIQOptionAPI
            api_mod.IQOptionAPI = _TransportAwareIQOptionAPI
            ws_mod.WebsocketClient = _TransportAwareWebsocketClient
            try:
                return original_connect(*args, **kwargs)
            finally:
                if stable_saved is not None:
                    stable_mod.IQOptionAPI = stable_saved
                if api_saved is not None:
                    api_mod.IQOptionAPI = api_saved
                if ws_saved is not None:
                    ws_mod.WebsocketClient = ws_saved

        try:
            instance.connect = types.MethodType(_wrapped_connect, instance)
        except Exception:
            return

    @staticmethod
    def _normalize_connect_error(error: BaseException | str | None) -> RuntimeError | None:
        if error is None:
            return None
        if isinstance(error, json.JSONDecodeError):
            upstream_reason = None
            try:
                gv = importlib.import_module('iqoptionapi.global_value')
                upstream_reason = getattr(gv, 'websocket_error_reason', None) or getattr(gv, 'reason', None)
            except Exception:
                upstream_reason = None
            detail = f'{type(error).__name__}: {error}'
            if upstream_reason not in (None, ''):
                detail = f'non-JSON failure reason: {upstream_reason} ({detail})'
            else:
                detail = f'non-JSON failure reason: {detail}'
            return RuntimeError(detail)
        return None

    def _new_api(self, binding=None) -> None:
        IQ_Option = require_iqoption_class()
        with self._apply_transport_binding(binding):
            self.iq = IQ_Option(self.cfg.email, self.cfg.password)
        if binding is not None:
            self._install_transport_runtime_bridge(self.iq, binding)
        _patch_iqoption_instance(self.iq)
        self._clear_asset_resolution_cache()

    def prime_provider_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'attempted': True,
            'ok': True,
            'digital_underlying_count': 0,
            'reason': None,
        }
        try:
            self.ensure_connection()
            getter = getattr(self.iq, 'get_digital_underlying_list_data', None)
            if callable(getter):
                data = getter() or {}
                underlying = data.get('underlying') if isinstance(data, Mapping) else []
                payload['digital_underlying_count'] = len(list(underlying or []))
        except Exception as exc:
            payload['ok'] = False
            payload['reason'] = f'{type(exc).__name__}: {exc}'
            try:
                from ..runtime.provider_issue_recorder import record_provider_issue_event
                record_provider_issue_event(operation='iqoption:prime_provider_metadata', reason=payload['reason'], source='iq_client')
            except Exception:
                pass
        return payload

    @staticmethod
    def _safe_reason(reason: Any) -> str:
        if reason is None:
            return "unknown"
        try:
            s = str(reason)
        except Exception:
            s = repr(reason)
        s = s.strip()
        return s or "unknown"

    @staticmethod
    def _backoff(base_s: float, attempt: int, max_s: float) -> float:
        base = max(0.05, float(base_s))
        wait = base * (2 ** max(0, int(attempt) - 1))
        return min(float(max_s), wait)

    def _clear_asset_resolution_cache(self) -> None:
        self._asset_registry = {}
        self._asset_resolution_cache = {}

    @staticmethod
    def _asset_text(value: Any) -> str:
        try:
            text = str(value or '')
        except Exception:
            text = repr(value)
        return text.strip().upper()

    @classmethod
    def _asset_key_variants(cls, value: Any) -> list[str]:
        raw = cls._asset_text(value).replace('/', '-').replace('_', '-')
        if not raw:
            return []
        tokens = [token for token in raw.split('-') if token]
        compact = ''.join(tokens)
        keys: list[str] = []

        def _push(candidate: Any) -> None:
            key = ''.join(ch for ch in cls._asset_text(candidate) if ch.isalnum())
            if key and key not in keys:
                keys.append(key)

        _push(raw)
        _push(compact)
        if len(tokens) > 1:
            _push('-'.join(tokens[:-1]))
            _push(''.join(tokens[:-1]))
        for suffix in ('OTC', 'L'):
            if compact.endswith(suffix) and len(compact) > len(suffix):
                _push(compact[:-len(suffix)])
        return keys

    @classmethod
    def _asset_match_score(cls, requested_asset: str, candidate_asset: str) -> int:
        requested_keys = cls._asset_key_variants(requested_asset)
        candidate_keys = cls._asset_key_variants(candidate_asset)
        if not requested_keys or not candidate_keys:
            return 0
        requested_primary = requested_keys[0]
        candidate_primary = candidate_keys[0]
        if candidate_primary == requested_primary:
            return 100
        for idx, key in enumerate(requested_keys[1:], start=1):
            if candidate_primary == key:
                return max(70, 95 - idx)
        for idx, key in enumerate(candidate_keys[1:], start=1):
            if requested_primary == key:
                return max(65, 90 - idx)
        if candidate_primary.startswith(requested_primary) or requested_primary.startswith(candidate_primary):
            return 60
        for key in requested_keys:
            if key in candidate_primary or candidate_primary in key:
                return 50
        return 0

    def _has_live_api(self) -> bool:
        try:
            return getattr(self.iq, 'api', None) is not None
        except Exception:
            return False

    def _get_actives_opcode_map(self) -> dict[str, Any]:
        getter = getattr(self.iq, 'get_all_ACTIVES_OPCODE', None)
        if not callable(getter):
            return {}
        try:
            data = getter() or {}
        except Exception:
            return {}
        out: dict[str, Any] = {}
        if isinstance(data, dict):
            for name, active_id in data.items():
                key = self._asset_text(name)
                if not key:
                    continue
                out[key] = active_id
        return out

    def _register_asset_entry(
        self,
        registry: dict[str, list[dict[str, Any]]],
        *,
        name: Any,
        active_id: Any = None,
        source: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        asset_name = self._asset_text(name)
        if not asset_name:
            return
        entry = {
            'name': asset_name,
            'active_id': active_id,
            'source': str(source),
            'meta': dict(meta or {}),
        }
        for key in self._asset_key_variants(asset_name):
            bucket = registry.setdefault(key, [])
            if all(existing.get('name') != asset_name for existing in bucket):
                bucket.append(entry)

    def _refresh_asset_registry(self, *, force: bool = False) -> dict[str, list[dict[str, Any]]]:
        if self._asset_registry and not force:
            return self._asset_registry

        self.ensure_connection()
        registry: dict[str, list[dict[str, Any]]] = {}

        try:
            updater = getattr(self.iq, 'update_ACTIVES_OPCODE', None)
            if callable(updater):
                self._maybe_throttle('asset_refresh:update_actives_opcode')
                updater()
        except Exception:
            pass

        opcode_map = self._get_actives_opcode_map()
        for name, active_id in opcode_map.items():
            self._register_asset_entry(
                registry,
                name=name,
                active_id=active_id,
                source='actives_opcode',
            )

        try:
            getter = getattr(self.iq, 'get_all_open_time', None)
            if callable(getter):
                self._maybe_throttle('asset_refresh:get_all_open_time')
                open_map = getter() or {}
                if isinstance(open_map, dict):
                    for market_kind, market_assets in open_map.items():
                        if not isinstance(market_assets, dict):
                            continue
                        for name, details in market_assets.items():
                            active_id = opcode_map.get(self._asset_text(name))
                            self._register_asset_entry(
                                registry,
                                name=name,
                                active_id=active_id,
                                source=f'open_time:{market_kind}',
                                meta=details if isinstance(details, dict) else {'value': details},
                            )
        except Exception:
            pass

        self._asset_registry = registry
        return registry

    def resolve_asset_name(self, asset: str, *, require_active_id: bool = False) -> str:
        requested_asset = self._asset_text(asset)
        if not requested_asset:
            return requested_asset

        cached = self._asset_resolution_cache.get(requested_asset)
        if cached:
            return cached

        opcode_map = self._get_actives_opcode_map()
        if requested_asset in opcode_map:
            self._asset_resolution_cache[requested_asset] = requested_asset
            return requested_asset

        registry = self._refresh_asset_registry(force=True)
        opcode_map = self._get_actives_opcode_map()
        if requested_asset in opcode_map:
            self._asset_resolution_cache[requested_asset] = requested_asset
            return requested_asset

        best_name = requested_asset
        best_score = -1
        seen_names: set[str] = set()
        for key in self._asset_key_variants(requested_asset):
            for entry in registry.get(key, []):
                candidate_name = self._asset_text(entry.get('name'))
                if not candidate_name or candidate_name in seen_names:
                    continue
                seen_names.add(candidate_name)
                candidate_active_id = entry.get('active_id')
                if require_active_id and candidate_active_id in (None, ''):
                    continue
                score = self._asset_match_score(requested_asset, candidate_name)
                if candidate_active_id not in (None, ''):
                    score += 5
                if score > best_score:
                    best_score = score
                    best_name = candidate_name

        self._asset_resolution_cache[requested_asset] = best_name
        return best_name

    def _run_with_timeout(self, *, label: str, fn, timeout_s: float):
        timeout = max(0.0, float(timeout_s or 0.0))
        if timeout <= 0.0:
            return fn()

        result: dict[str, Any] = {}
        error: dict[str, BaseException] = {}
        done = threading.Event()

        def _worker() -> None:
            try:
                result['value'] = fn()
            except BaseException as exc:  # pragma: no cover - defensive bridge
                error['exc'] = exc
            finally:
                done.set()

        thread = threading.Thread(target=_worker, name=f'thalor_iq_{label}', daemon=True)
        thread.start()
        if not done.wait(timeout=timeout):
            raise TimeoutError(f'{label} timed out after {timeout:.1f}s')
        if 'exc' in error:
            raise error['exc']
        return result.get('value')

    def connect(self, retries: int | None = None, sleep_s: float | None = None, connect_timeout_s: float | None = None) -> None:
        retries = int(retries if retries is not None else env_int('IQ_CONNECT_RETRIES', 8))
        sleep_s = float(sleep_s if sleep_s is not None else env_float('IQ_CONNECT_SLEEP_S', 2.0))
        sleep_max_s = float(env_float('IQ_CONNECT_SLEEP_MAX_S', max(4.0, sleep_s * 4.0)))
        recreate_on_retry = bool(env_bool('IQ_RECREATE_ON_RETRY', True))

        last_reason = None
        for attempt in range(1, max(1, retries) + 1):
            binding = self._select_transport_binding('iqoption_connect', allow_fail_open=False, required=True)
            if attempt == 1 or recreate_on_retry:
                self._new_api(binding=binding)
            started = time.perf_counter()
            try:
                self._maybe_throttle('connect')
                attempt_timeout_s = self._connect_attempt_timeout_s(binding, explicit_timeout_s=connect_timeout_s)
                with self._apply_transport_binding(binding):
                    ok, reason = self._run_with_timeout(label='iqoption_connect', fn=lambda: self.iq.connect(), timeout_s=attempt_timeout_s)
            except Exception as e:
                normalized = self._normalize_connect_error(e)
                err = normalized if normalized is not None else e
                ok, reason = False, self._safe_reason(err)
                try:
                    from ..runtime.provider_issue_recorder import record_provider_issue_event
                    record_provider_issue_event(operation='iqoption_connect', reason=reason, source='iq_client.connect', dedupe_window_sec=90.0)
                except Exception:
                    pass
            latency_s = max(0.0, time.perf_counter() - started)
            if ok:
                try:
                    with self._apply_transport_binding(binding):
                        self.iq.change_balance(self.cfg.balance_mode)
                except Exception as e:
                    self._record_transport_failure(binding, 'iqoption_change_balance', e)
                    self._record_request_metric(
                        binding=binding,
                        operation='connect',
                        success=False,
                        latency_s=latency_s,
                        extra={
                            'label': 'iqoption_connect',
                            'attempt': attempt,
                            'retries': retries,
                            'stage': 'change_balance',
                            'balance_mode': self.cfg.balance_mode,
                            'reason': f'{type(e).__name__}: {e}',
                        },
                    )
                    raise RuntimeError(
                        f'Conectou mas falhou ao trocar balance_mode={self.cfg.balance_mode}. err={type(e).__name__}: {e}'
                    ) from e
                self._active_transport_binding = binding
                self._record_transport_success(binding, 'iqoption_connect')
                self._record_request_metric(
                    binding=binding,
                    operation='connect',
                    success=True,
                    latency_s=latency_s,
                    extra={
                        'label': 'iqoption_connect',
                        'attempt': attempt,
                        'retries': retries,
                        'balance_mode': self.cfg.balance_mode,
                    },
                )
                self._clear_asset_resolution_cache()
                return
            last_reason = self._safe_reason(reason)
            self._record_transport_failure(binding, 'iqoption_connect', last_reason)
            self._record_request_metric(
                binding=binding,
                operation='connect',
                success=False,
                latency_s=latency_s,
                extra={
                    'label': 'iqoption_connect',
                    'attempt': attempt,
                    'retries': retries,
                    'reason': last_reason,
                    'balance_mode': self.cfg.balance_mode,
                },
            )
            try:
                from ..runtime.provider_issue_recorder import record_provider_issue_event
                record_provider_issue_event(operation='iqoption_connect', reason=last_reason, source='iq_client.connect', dedupe_window_sec=90.0)
            except Exception:
                pass
            self._active_transport_binding = None
            if attempt < retries:
                wait_s = self._backoff(sleep_s, attempt, sleep_max_s)
                print(f'[IQ][connect] attempt {attempt}/{retries} failed: reason={last_reason}; retry in {wait_s:.1f}s')
                time.sleep(wait_s)
        raise RuntimeError(f'Falha ao conectar na IQ Option após {retries} tentativas. reason={last_reason}')

    def ensure_connection(self) -> None:
        try:
            ok = bool(self.iq.check_connect()) and self._has_live_api()
        except Exception:
            ok = False
        if not ok:
            print("[IQ] conexão ausente; tentando reconnect...")
            self.connect(retries=env_int("IQ_RECONNECT_RETRIES", 10), sleep_s=env_float("IQ_RECONNECT_SLEEP_S", 2.0))

    def _call_with_retries(
        self,
        *,
        label: str,
        fn,
        retries_env: str,
        sleep_env: str,
        sleep_max_env: str,
        retries_default: int = 3,
        sleep_default: float = 1.0,
    ):
        retries = int(env_int(retries_env, retries_default))
        sleep_s = float(env_float(sleep_env, sleep_default))
        sleep_max_s = float(env_float(sleep_max_env, max(2.0, sleep_s * 4.0)))
        last_reason = None
        metric_operation = self._metrics_operation_name(label)
        for attempt in range(1, max(1, retries) + 1):
            binding = self._select_transport_binding(f'iqoption:{label}', allow_fail_open=False, required=True)
            started = time.perf_counter()
            try:
                self._maybe_throttle(f"call:{label}")
                if binding is not None:
                    self._active_transport_binding = binding
                with self._apply_transport_binding(binding):
                    self.ensure_connection()
                    result = fn()
                self._record_transport_success(binding, f'iqoption:{label}')
                self._record_request_metric(
                    binding=binding,
                    operation=metric_operation,
                    success=True,
                    latency_s=max(0.0, time.perf_counter() - started),
                    extra={
                        'label': label,
                        'attempt': attempt,
                        'retries': retries,
                    },
                )
                return result
            except Exception as e:
                last_reason = f"{type(e).__name__}: {e}"
                self._record_transport_failure(binding, f'iqoption:{label}', e)
                self._record_request_metric(
                    binding=binding,
                    operation=metric_operation,
                    success=False,
                    latency_s=max(0.0, time.perf_counter() - started),
                    extra={
                        'label': label,
                        'attempt': attempt,
                        'retries': retries,
                        'reason': last_reason,
                    },
                )
                self._active_transport_binding = None
                try:
                    from ..runtime.provider_issue_recorder import record_provider_issue_event
                    record_provider_issue_event(operation=f'iqoption:{label}', reason=last_reason, source='iq_client.call', dedupe_window_sec=90.0)
                except Exception:
                    pass
            if attempt < retries:
                wait_s = self._backoff(sleep_s, attempt, sleep_max_s)
                print(f"[IQ][{label}] attempt {attempt}/{retries} failed: reason={last_reason}; retry in {wait_s:.1f}s")
                refresh_binding = self._select_transport_binding(f'iqoption:{label}', allow_fail_open=False, required=True)
                self._new_api(binding=refresh_binding)
                time.sleep(wait_s)
        raise RuntimeError(f"Falha em {label} após {retries} tentativas. reason={last_reason}")

    def fetch_all_open_time(self):
        return self._call_with_retries(
            label="get_all_open_time",
            fn=lambda: self.iq.get_all_open_time(),
            retries_env="IQ_OPEN_RETRIES",
            sleep_env="IQ_OPEN_SLEEP_S",
            sleep_max_env="IQ_OPEN_SLEEP_MAX_S",
            retries_default=2,
            sleep_default=1.0,
        )

    def fetch_all_profit(self):
        return self._call_with_retries(
            label="get_all_profit",
            fn=lambda: self.iq.get_all_profit(),
            retries_env="IQ_PROFIT_RETRIES",
            sleep_env="IQ_PROFIT_SLEEP_S",
            sleep_max_env="IQ_PROFIT_SLEEP_MAX_S",
            retries_default=2,
            sleep_default=1.0,
        )

    def get_market_context(self, asset: str, interval_sec: int, payout_fallback: float = 0.8) -> dict[str, Any]:
        requested_asset = self._asset_text(asset)
        broker_asset = self.resolve_asset_name(requested_asset, require_active_id=False)
        market_open = True
        open_source = "fallback"
        payout = float(payout_fallback)
        payout_source = "fallback"

        # IMPORTANT:
        # iqoptionapi.get_all_open_time() may spawn background threads that crash noisily
        # for some accounts / OTC paths (e.g. KeyError: 'underlying' from __get_digital_open).
        # To keep the scheduler quiet and deterministic, API-based open checks are DISABLED
        # by default. The scheduler can infer open/closed from the freshness of recently
        # collected candles instead. If needed, this old path can be re-enabled explicitly.
        if env_bool("IQ_MARKET_OPEN_USE_API", False):
            try:
                open_map = self.fetch_all_open_time() or {}
                for kind in ("turbo", "binary", "digital", "forex", "crypto", "cfd"):
                    try:
                        asset_row = open_map.get(kind, {}).get(broker_asset)
                    except Exception:
                        asset_row = None
                    if isinstance(asset_row, dict) and asset_row.get("open") is not None:
                        market_open = bool(asset_row.get("open"))
                        open_source = kind
                        break
            except Exception:
                pass

        # Payout for turbo/binary (most robust / cheap query path)
        try:
            timeout_s = float(getattr(self.cfg, 'market_context_timeout_s', 0.0) or 0.0)
            if timeout_s > 0.0:
                profit_map = self._run_with_timeout(label='get_all_profit', fn=self.fetch_all_profit, timeout_s=timeout_s) or {}
            else:
                profit_map = self.fetch_all_profit() or {}
            asset_profit = profit_map.get(broker_asset) or {}
            for kind in ('turbo', 'binary'):
                v = asset_profit.get(kind)
                if v is None:
                    continue
                fv = float(v)
                if fv > 1.0:
                    fv = fv / 100.0
                if 0.01 <= fv <= 0.99:
                    payout = fv
                    payout_source = kind
                    break
        except Exception:
            pass

        # Optional digital fallback for 5m/15m etc. Disabled by default because it is slower.
        if payout_source == "fallback" and env_bool("IQ_MARKET_DIGITAL_ENABLE", False):
            dur_min = max(1, int(interval_sec // 60))
            try:
                self._maybe_throttle(f"market_context:{broker_asset}:{interval_sec}")
                self.ensure_connection()
                self.iq.subscribe_strike_list(broker_asset, dur_min)
                time.sleep(float(env_float("IQ_DIGITAL_PAYOUT_WAIT_S", 1.2)))
                v = self.iq.get_digital_current_profit(broker_asset, dur_min)
                if v not in (None, False, ""):
                    fv = float(v)
                    if fv > 1.0:
                        fv = fv / 100.0
                    if 0.01 <= fv <= 0.99:
                        payout = fv
                        payout_source = "digital"
            except Exception:
                pass
            finally:
                try:
                    self.iq.unsubscribe_strike_list(broker_asset, dur_min)
                except Exception:
                    pass

        return {
            "asset_requested": requested_asset,
            "asset_resolved": broker_asset,
            "market_open": bool(market_open),
            "open_source": open_source,
            "payout": float(payout),
            "payout_source": payout_source,
        }

    def get_candles(self, asset: str, interval_sec: int, count: int, endtime: int):
        requested_asset = self._asset_text(asset)
        retries = int(env_int("IQ_GET_CANDLES_RETRIES", 3))
        sleep_s = float(env_float("IQ_GET_CANDLES_SLEEP_S", 1.0))
        sleep_max_s = float(env_float("IQ_GET_CANDLES_SLEEP_MAX_S", max(2.0, sleep_s * 4.0)))
        retry_empty = bool(env_bool("IQ_RETRY_EMPTY_BATCH", True))

        last_reason = None
        broker_asset = requested_asset
        for attempt in range(1, max(1, retries) + 1):
            binding = self._select_transport_binding('iqoption:get_candles')
            started = time.perf_counter()
            try:
                with self._apply_transport_binding(binding):
                    self.ensure_connection()
                if attempt == 1:
                    broker_asset = self.resolve_asset_name(requested_asset, require_active_id=True)
                else:
                    broker_asset = self.resolve_asset_name(requested_asset, require_active_id=True)
                self._maybe_throttle(f"candles:{broker_asset}:{interval_sec}")
                with self._apply_transport_binding(binding):
                    candles = self.iq.get_candles(broker_asset, interval_sec, count, endtime)
                if candles or not retry_empty:
                    self._record_transport_success(binding, 'iqoption:get_candles')
                    self._record_request_metric(
                        binding=binding,
                        operation='get_candles',
                        success=True,
                        latency_s=max(0.0, time.perf_counter() - started),
                        extra={
                            'label': 'get_candles',
                            'attempt': attempt,
                            'retries': retries,
                            'requested_asset': requested_asset,
                            'broker_asset': broker_asset,
                            'interval_sec': int(interval_sec),
                            'count': int(count),
                            'endtime': int(endtime),
                            'empty_batch': bool(not candles),
                        },
                    )
                    return candles
                last_reason = "empty_batch"
            except Exception as e:
                last_reason = f"{type(e).__name__}: {e}"
            if last_reason != 'empty_batch':
                self._record_transport_failure(binding, 'iqoption:get_candles', last_reason)
            self._record_request_metric(
                binding=binding,
                operation='get_candles',
                success=False,
                latency_s=max(0.0, time.perf_counter() - started),
                extra={
                    'label': 'get_candles',
                    'attempt': attempt,
                    'retries': retries,
                    'requested_asset': requested_asset,
                    'broker_asset': broker_asset,
                    'interval_sec': int(interval_sec),
                    'count': int(count),
                    'endtime': int(endtime),
                    'reason': last_reason,
                },
            )

            if attempt < retries:
                wait_s = self._backoff(sleep_s, attempt, sleep_max_s)
                print(
                    f"[IQ][get_candles] attempt {attempt}/{retries} failed: requested_asset={requested_asset} broker_asset={broker_asset} interval={interval_sec} count={count} end={endtime} reason={last_reason}; retry in {wait_s:.1f}s"
                )
                self._new_api()
                time.sleep(wait_s)

        raise RuntimeError(
            f"Falha em get_candles após {retries} tentativas. requested_asset={requested_asset} broker_asset={broker_asset} interval={interval_sec} count={count} end={endtime} reason={last_reason}"
        )


    # ------------------- execution bridge helpers (Package M2) -------------------

    def submit_binary_option(self, *, asset: str, amount: float, side: str, duration_min: int):
        side_norm = str(side or '').strip().lower()
        if side_norm not in {'call', 'put'}:
            raise ValueError(f'invalid binary option side: {side}')
        duration = max(1, int(duration_min))
        return self._call_with_retries(
            label=f'buy_option:{asset}:{duration}',
            fn=lambda: self.iq.buy(float(amount), str(asset), side_norm, duration),
            retries_env='IQ_EXEC_BUY_RETRIES',
            sleep_env='IQ_EXEC_BUY_SLEEP_S',
            sleep_max_env='IQ_EXEC_BUY_SLEEP_MAX_S',
            retries_default=2,
            sleep_default=1.0,
        )

    def get_async_order(self, order_id):
        def _fetch():
            try:
                return self.iq.get_async_order(int(order_id))
            except Exception:
                return self.iq.get_async_order(order_id)

        return self._call_with_retries(
            label=f'get_async_order:{order_id}',
            fn=_fetch,
            retries_env='IQ_EXEC_ASYNC_RETRIES',
            sleep_env='IQ_EXEC_ASYNC_SLEEP_S',
            sleep_max_env='IQ_EXEC_ASYNC_SLEEP_MAX_S',
            retries_default=1,
            sleep_default=0.25,
        )

    def get_betinfo_safe(self, order_id):
        def _fetch():
            try:
                return self.iq.get_betinfo(int(order_id))
            except Exception:
                return self.iq.get_betinfo(order_id)

        return self._call_with_retries(
            label=f'get_betinfo:{order_id}',
            fn=_fetch,
            retries_env='IQ_EXEC_BETINFO_RETRIES',
            sleep_env='IQ_EXEC_BETINFO_SLEEP_S',
            sleep_max_env='IQ_EXEC_BETINFO_SLEEP_MAX_S',
            retries_default=2,
            sleep_default=0.5,
        )

    def get_recent_closed_options(self, limit: int = 20):
        lim = max(1, int(limit))
        label = f'get_optioninfo_v2:{lim}'
        cooldown_key = f'history:{lim}'
        now = time.time()
        skip_until = float(self._guarded_call_cooldowns.get(cooldown_key, 0.0) or 0.0)
        if skip_until > now:
            return {
                'msg': {'closed_options': []},
                'skipped': {
                    'reason': 'history_timeout_cooldown',
                    'label': label,
                    'until_epoch': skip_until,
                },
            }

        retries = int(env_int('IQ_EXEC_HISTORY_RETRIES', 1))
        sleep_s = float(env_float('IQ_EXEC_HISTORY_SLEEP_S', 0.5))
        sleep_max_s = float(env_float('IQ_EXEC_HISTORY_SLEEP_MAX_S', max(2.0, sleep_s * 4.0)))
        timeout_s = float(env_float('IQ_EXEC_HISTORY_TIMEOUT_S', 8.0))
        cooldown_s = max(0.0, float(env_float('IQ_EXEC_HISTORY_COOLDOWN_S', 300.0)))

        last_reason = None
        for attempt in range(1, max(1, retries) + 1):
            binding = self._select_transport_binding(f'iqoption:{label}', allow_fail_open=False)
            started = time.perf_counter()
            try:
                self._maybe_throttle(f'call:{label}')
                with self._apply_transport_binding(binding):
                    self.ensure_connection()
                    result = self._run_with_timeout(label=label, fn=lambda: self.iq.get_optioninfo_v2(lim), timeout_s=timeout_s)
                self._record_transport_success(binding, f'iqoption:{label}')
                self._record_request_metric(
                    binding=binding,
                    operation='get_optioninfo_v2',
                    success=True,
                    latency_s=max(0.0, time.perf_counter() - started),
                    extra={
                        'label': label,
                        'attempt': attempt,
                        'retries': retries,
                        'limit': lim,
                        'timeout_sec': timeout_s,
                    },
                )
                return result
            except TimeoutError as e:
                last_reason = f'{type(e).__name__}: {e}'
                self._record_transport_failure(binding, f'iqoption:{label}', e)
                self._record_request_metric(
                    binding=binding,
                    operation='get_optioninfo_v2',
                    success=False,
                    latency_s=max(0.0, time.perf_counter() - started),
                    extra={
                        'label': label,
                        'attempt': attempt,
                        'retries': retries,
                        'limit': lim,
                        'timeout_sec': timeout_s,
                        'reason': last_reason,
                        'guarded_skip': True,
                    },
                )
                if cooldown_s > 0.0:
                    self._guarded_call_cooldowns[cooldown_key] = time.time() + cooldown_s
                print(f'[IQ][{label}] guarded timeout: reason={last_reason}; using empty closed history for now')
                return {
                    'msg': {'closed_options': []},
                    'skipped': {
                        'reason': 'timeout',
                        'label': label,
                        'timeout_sec': timeout_s,
                        'cooldown_sec': cooldown_s,
                    },
                }
            except Exception as e:
                last_reason = f'{type(e).__name__}: {e}'
                self._record_transport_failure(binding, f'iqoption:{label}', e)
                self._record_request_metric(
                    binding=binding,
                    operation='get_optioninfo_v2',
                    success=False,
                    latency_s=max(0.0, time.perf_counter() - started),
                    extra={
                        'label': label,
                        'attempt': attempt,
                        'retries': retries,
                        'limit': lim,
                        'timeout_sec': timeout_s,
                        'reason': last_reason,
                    },
                )
            if attempt < retries:
                wait_s = self._backoff(sleep_s, attempt, sleep_max_s)
                print(f'[IQ][{label}] attempt {attempt}/{retries} failed: reason={last_reason}; retry in {wait_s:.1f}s')
                self._new_api()
                time.sleep(wait_s)
        raise RuntimeError(f'Falha em {label} após {retries} tentativas. reason={last_reason}')

    def get_option_open_by_other_pc(self):
        return self._call_with_retries(
            label='get_option_open_by_other_pc',
            fn=lambda: self.iq.get_option_open_by_other_pc(),
            retries_env='IQ_EXEC_OPEN_OTHER_PC_RETRIES',
            sleep_env='IQ_EXEC_OPEN_OTHER_PC_SLEEP_S',
            sleep_max_env='IQ_EXEC_OPEN_OTHER_PC_SLEEP_MAX_S',
            retries_default=1,
            sleep_default=0.25,
        )

    def list_async_orders(self) -> dict[str, Any]:
        self.ensure_connection()
        try:
            data = getattr(self.iq.api, 'order_async', {}) or {}
        except Exception:
            data = {}
        try:
            return json.loads(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:
            return dict(data)

    def list_socket_opened_orders(self) -> dict[str, Any]:
        self.ensure_connection()
        try:
            data = getattr(self.iq.api, 'socket_option_opened', {}) or {}
        except Exception:
            data = {}
        try:
            return json.loads(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:
            return dict(data)

    def list_socket_closed_orders(self) -> dict[str, Any]:
        self.ensure_connection()
        try:
            data = getattr(self.iq.api, 'socket_option_closed', {}) or {}
        except Exception:
            data = {}
        try:
            return json.loads(json.dumps(data, ensure_ascii=False, default=str))
        except Exception:
            return dict(data)

    def asset_name_from_opcode(self, opcode: Any) -> str | None:
        try:
            if opcode in (None, ''):
                return None
            return self.iq.opcode_to_name(int(opcode))
        except Exception:
            return None
