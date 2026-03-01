from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass(frozen=True)
class IQCreds:
    email: str
    password: str
    balance_mode: str = "PRACTICE"  # PRACTICE/REAL


@dataclass(frozen=True)
class DataCfg:
    asset: str
    interval_sec: int
    db_path: str
    max_batch: int
    timezone: str


@dataclass(frozen=True)
class Settings:
    iq: IQCreds
    data: DataCfg


def load_settings(config_path: str = "config.yaml") -> Settings:
    load_dotenv()

    email = os.getenv("IQ_EMAIL", "").strip()
    password = os.getenv("IQ_PASSWORD", "").strip()
    balance_mode = os.getenv("IQ_BALANCE_MODE", "PRACTICE").strip().upper()

    if not email or not password:
        raise RuntimeError("Faltou IQ_EMAIL/IQ_PASSWORD. Crie um arquivo .env (use .env.example como modelo).")

    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    d = cfg["data"]

    data = DataCfg(
        asset=str(d["asset"]).strip(),
        interval_sec=int(d["interval_sec"]),
        db_path=str(d["db_path"]).strip(),
        max_batch=int(d.get("max_batch", 1000)),
        timezone=str(d.get("timezone", "America/Sao_Paulo")).strip(),
    )

    return Settings(
        iq=IQCreds(email=email, password=password, balance_mode=balance_mode),
        data=data,
    )