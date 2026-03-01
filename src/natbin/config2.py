from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass(frozen=True)
class DataConfig:
    asset: str
    interval_sec: int
    db_path: str
    timezone: str
    max_batch: int = 1000


@dataclass(frozen=True)
class Phase2Config:
    dataset_path: str = "data/dataset_phase2.csv"
    runs_dir: str = "runs"
    n_splits: int = 6
    threshold_min: float = 0.60
    threshold_max: float = 0.80
    threshold_step: float = 0.01


@dataclass(frozen=True)
class Config:
    data: DataConfig
    phase2: Phase2Config


def load_config(path: str = "config.yaml") -> Config:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    d = cfg["data"]

    data = DataConfig(
        asset=str(d["asset"]).strip(),
        interval_sec=int(d["interval_sec"]),
        db_path=str(d["db_path"]).strip(),
        timezone=str(d.get("timezone", "America/Sao_Paulo")).strip(),
        max_batch=int(d.get("max_batch", 1000)),
    )

    p = cfg.get("phase2", {}) or {}
    phase2 = Phase2Config(
        dataset_path=str(p.get("dataset_path", "data/dataset_phase2.csv")),
        runs_dir=str(p.get("runs_dir", "runs")),
        n_splits=int(p.get("n_splits", 6)),
        threshold_min=float(p.get("threshold_min", 0.60)),
        threshold_max=float(p.get("threshold_max", 0.80)),
        threshold_step=float(p.get("threshold_step", 0.01)),
    )

    return Config(data=data, phase2=phase2)