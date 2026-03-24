import time
import threading
from dataclasses import dataclass
from typing import Any

import json
import os
import random
from contextlib import contextmanager
from pathlib import Path

from ..config.env import env_bool, env_float, env_int


class IQDependencyUnavailable(RuntimeError):
    """Raised when the optional iqoptionapi package is not available."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = str(reason or "iqoption_dependency_missing")


_IQ_OPTION_CLASS = None
_IQ_OPTION_IMPORT_ERROR: Exception | None = None


def iqoption_dependency_status() -> dict[str, Any]:
    """Return the availability of the optional iqoptionapi dependency.

    The environment toggle ``THALOR_FORCE_IQOPTIONAPI_MISSING=1`` is used by
    deterministic tests/smokes so CI can exercise the fallback path even when
    the package is installed.
    """
    global _IQ_OPTION_CLASS, _IQ_OPTION_IMPORT_ERROR

    if env_bool("THALOR_FORCE_IQOPTIONAPI_MISSING", False):
        return {
            "available": False,
            "reason": "env:THALOR_FORCE_IQOPTIONAPI_MISSING",
        }

    if _IQ_OPTION_CLASS is not None:
        return {"available": True, "reason": None}

    if _IQ_OPTION_IMPORT_ERROR is not None:
        return {
            "available": False,
            "reason": f"{type(_IQ_OPTION_IMPORT_ERROR).__name__}: {_IQ_OPTION_IMPORT_ERROR}",
        }

    try:
        from iqoptionapi.stable_api import IQ_Option as _ImportedIQOption
    except Exception as exc:  # pragma: no cover - depends on local environment
        _IQ_OPTION_IMPORT_ERROR = exc
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    _IQ_OPTION_CLASS = _ImportedIQOption
    return {"available": True, "reason": None}


def require_iqoption_class():
    status = iqoption_dependency_status()
    if not bool(status.get("available")):
        raise IQDependencyUnavailable(str(status.get("reason") or "iqoption_dependency_missing"))
    return _IQ_OPTION_CLASS


@dataclass
class IQConfig:
    email: str
    password: str
    balance_mode: str = "PRACTICE"

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
    def __init__(self, cfg: IQConfig):
        self.cfg = cfg
        self._guarded_call_cooldowns: dict[str, float] = {}
        IQ_Option = require_iqoption_class()
        self.iq = IQ_Option(cfg.email, cfg.password)

    @staticmethod
    def dependency_status() -> dict[str, Any]:
        return iqoption_dependency_status()

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

    def _new_api(self) -> None:
        IQ_Option = require_iqoption_class()
        self.iq = IQ_Option(self.cfg.email, self.cfg.password)

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

    def connect(self, retries: int | None = None, sleep_s: float | None = None) -> None:
        retries = int(retries if retries is not None else env_int("IQ_CONNECT_RETRIES", 8))
        sleep_s = float(sleep_s if sleep_s is not None else env_float("IQ_CONNECT_SLEEP_S", 2.0))
        sleep_max_s = float(env_float("IQ_CONNECT_SLEEP_MAX_S", max(4.0, sleep_s * 4.0)))
        recreate_on_retry = bool(env_bool("IQ_RECREATE_ON_RETRY", True))

        last_reason = None
        for attempt in range(1, max(1, retries) + 1):
            if attempt > 1 and recreate_on_retry:
                self._new_api()
            try:
                self._maybe_throttle("connect")
                ok, reason = self.iq.connect()
            except Exception as e:
                ok, reason = False, f"{type(e).__name__}: {e}"
            if ok:
                try:
                    self.iq.change_balance(self.cfg.balance_mode)
                except Exception as e:
                    raise RuntimeError(
                        f"Conectou mas falhou ao trocar balance_mode={self.cfg.balance_mode}. err={type(e).__name__}: {e}"
                    ) from e
                return
            last_reason = self._safe_reason(reason)
            if attempt < retries:
                wait_s = self._backoff(sleep_s, attempt, sleep_max_s)
                print(f"[IQ][connect] attempt {attempt}/{retries} failed: reason={last_reason}; retry in {wait_s:.1f}s")
                time.sleep(wait_s)
        raise RuntimeError(f"Falha ao conectar na IQ Option após {retries} tentativas. reason={last_reason}")

    def ensure_connection(self) -> None:
        try:
            ok = bool(self.iq.check_connect())
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
        for attempt in range(1, max(1, retries) + 1):
            try:
                self._maybe_throttle(f"call:{label}")
                self.ensure_connection()
                return fn()
            except Exception as e:
                last_reason = f"{type(e).__name__}: {e}"
            if attempt < retries:
                wait_s = self._backoff(sleep_s, attempt, sleep_max_s)
                print(f"[IQ][{label}] attempt {attempt}/{retries} failed: reason={last_reason}; retry in {wait_s:.1f}s")
                self._new_api()
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
                for kind in ("turbo", "binary", "digital"):
                    try:
                        v = open_map.get(kind, {}).get(asset, {}).get("open")
                    except Exception:
                        v = None
                    if v is not None:
                        market_open = bool(v)
                        open_source = kind
                        break
            except Exception:
                pass

        # Payout for turbo/binary (most robust / cheap query path)
        try:
            profit_map = self.fetch_all_profit() or {}
            asset_profit = profit_map.get(asset) or {}
            for kind in ("turbo", "binary"):
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
                self._maybe_throttle(f"market_context:{asset}:{interval_sec}")
                self.ensure_connection()
                self.iq.subscribe_strike_list(asset, dur_min)
                time.sleep(float(env_float("IQ_DIGITAL_PAYOUT_WAIT_S", 1.2)))
                v = self.iq.get_digital_current_profit(asset, dur_min)
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
                    self.iq.unsubscribe_strike_list(asset, dur_min)
                except Exception:
                    pass

        return {
            "market_open": bool(market_open),
            "open_source": open_source,
            "payout": float(payout),
            "payout_source": payout_source,
        }

    def get_candles(self, asset: str, interval_sec: int, count: int, endtime: int):
        retries = int(env_int("IQ_GET_CANDLES_RETRIES", 3))
        sleep_s = float(env_float("IQ_GET_CANDLES_SLEEP_S", 1.0))
        sleep_max_s = float(env_float("IQ_GET_CANDLES_SLEEP_MAX_S", max(2.0, sleep_s * 4.0)))
        retry_empty = bool(env_bool("IQ_RETRY_EMPTY_BATCH", True))

        last_reason = None
        for attempt in range(1, max(1, retries) + 1):
            try:
                self._maybe_throttle(f"candles:{asset}:{interval_sec}")
                self.ensure_connection()
                candles = self.iq.get_candles(asset, interval_sec, count, endtime)
                if candles or not retry_empty:
                    return candles
                last_reason = "empty_batch"
            except Exception as e:
                last_reason = f"{type(e).__name__}: {e}"

            if attempt < retries:
                wait_s = self._backoff(sleep_s, attempt, sleep_max_s)
                print(
                    f"[IQ][get_candles] attempt {attempt}/{retries} failed: asset={asset} interval={interval_sec} count={count} end={endtime} reason={last_reason}; retry in {wait_s:.1f}s"
                )
                self._new_api()
                time.sleep(wait_s)

        raise RuntimeError(
            f"Falha em get_candles após {retries} tentativas. asset={asset} interval={interval_sec} count={count} end={endtime} reason={last_reason}"
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
            try:
                self._maybe_throttle(f'call:{label}')
                self.ensure_connection()
                return self._run_with_timeout(label=label, fn=lambda: self.iq.get_optioninfo_v2(lim), timeout_s=timeout_s)
            except TimeoutError as e:
                last_reason = f'{type(e).__name__}: {e}'
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
