import time
from dataclasses import dataclass
from typing import Any

from iqoptionapi.stable_api import IQ_Option

from .envutil import env_bool, env_float, env_int


@dataclass
class IQConfig:
    email: str
    password: str
    balance_mode: str = "PRACTICE"


class IQClient:
    def __init__(self, cfg: IQConfig):
        self.cfg = cfg
        self.iq = IQ_Option(cfg.email, cfg.password)

    def _new_api(self) -> None:
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
