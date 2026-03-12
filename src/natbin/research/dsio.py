# -*- coding: utf-8 -*-
"""Utilitários de IO para avaliações/backtests.

Motivação:
- Evitar drift entre scripts que carregam dataset CSV.
- Garantir: ordenação por ts e drop de linhas sem label (tipicamente a última linha).

Obs:
- Para o observe_loop em produção, normalmente queremos manter a última linha
  (candle atual) para gerar sinal, mesmo sem label. Por isso este helper é
  pensado para backtests/tuning, não necessariamente para o loop ao vivo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

import pandas as pd

PathLike = Union[str, Path]


def read_dataset_csv(
    path: PathLike,
    *,
    label_col: str = "y_open_close",
    sort_ts: bool = True,
    drop_unlabeled: bool = True,
) -> pd.DataFrame:
    """Carrega dataset CSV e aplica normalizações seguras.

    - sort por ts (se existir)
    - drop de rows sem label (se label_col existir), pra evitar treinar/backtestar
      em linha "sem futuro" (ex.: última linha do dataset).
    """
    p = Path(path)
    df = pd.read_csv(p)

    if df is None or len(df) == 0:
        return df

    if sort_ts and "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)

    if drop_unlabeled and (label_col in df.columns):
        df = df[df[label_col].notna()].reset_index(drop=True)

    return df
