#!/usr/bin/env python
"""P34 - Centralizar leitura do dataset CSV (ordenação por ts + drop de última linha sem label)

Motivação:
- A base tem vários scripts (paper_*, tune_*) que fazem `pd.read_csv(...)` com
  pequenas variações. Isso gera drift e bugs (ex.: última linha sem label).
- Este patch cria um helper único e troca os scripts para usarem ele.

O que este patch faz:
  1) Cria `src/natbin/dsio.py` com `read_dataset_csv()`
  2) Atualiza scripts que fazem `df = pd.read_csv(...)` para:
        df = read_dataset_csv(..., label_col="y_open_close")
     (de forma conservadora; não mexe em observe_loop)
  3) Roda `py_compile` nos arquivos alterados

Como rodar:
  .\.venv\Scripts\python.exe .\scripts\patches\p34_eval_dataset_io_apply.py
"""

from __future__ import annotations

import re
import sys
import py_compile
from pathlib import Path
from typing import List, Tuple


TARGETS = [
    "src/natbin/paper_backtest.py",
    "src/natbin/paper_backtest_v2.py",
    "src/natbin/paper_backtest_v3.py",
    "src/natbin/paper_pnl_backtest.py",
    "src/natbin/paper_multiwindow_v3.py",
    "src/natbin/paper_topk_multiwindow.py",
    "src/natbin/paper_topk_perday_multiwindow.py",
    "src/natbin/paper_tune_v2.py",
    "src/natbin/train_walkforward.py",
    "src/natbin/tune_multiwindow_topk.py",
]

DSIO_REL = "src/natbin/dsio.py"


def find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(16):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    # fallback: tenta o cwd
    cwd = Path.cwd().resolve()
    if (cwd / ".git").exists():
        return cwd
    return start.resolve()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def ensure_dsio(repo: Path) -> Path:
    dsio_path = repo / DSIO_REL
    code = """# -*- coding: utf-8 -*-
\"\"\"Utilitários de IO para avaliações/backtests.

Motivação:
- Evitar drift entre scripts que carregam dataset CSV.
- Garantir: ordenação por ts e drop de linhas sem label (tipicamente a última linha).

Obs:
- Para o observe_loop em produção, normalmente queremos manter a última linha
  (candle atual) para gerar sinal, mesmo sem label. Por isso este helper é
  pensado para backtests/tuning, não necessariamente para o loop ao vivo.
\"\"\"

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
    \"\"\"Carrega dataset CSV e aplica normalizações seguras.

    - sort por ts (se existir)
    - drop de rows sem label (se label_col existir), pra evitar treinar/backtestar
      em linha "sem futuro" (ex.: última linha do dataset).
    \"\"\"
    p = Path(path)
    df = pd.read_csv(p)

    if df is None or len(df) == 0:
        return df

    if sort_ts and "ts" in df.columns:
        df = df.sort_values("ts").reset_index(drop=True)

    if drop_unlabeled and (label_col in df.columns):
        df = df[df[label_col].notna()].reset_index(drop=True)

    return df
"""

    if dsio_path.exists():
        old = dsio_path.read_text(encoding="utf-8", errors="replace")
        if old.strip() == code.strip():
            return dsio_path

    write_text(dsio_path, code)
    return dsio_path


def patch_imports(lines: List[str]) -> Tuple[List[str], bool]:
    joined = "\n".join(lines)
    if re.search(r"^\s*from\s+\.dsio\s+import\s+read_dataset_csv\s*$", joined, re.M):
        return lines, False

    out: List[str] = []
    inserted = False

    for line in lines:
        out.append(line)
        if (not inserted) and re.match(r"^\s*import\s+pandas\s+as\s+pd\s*$", line):
            out.append("from .dsio import read_dataset_csv")
            inserted = True

    if inserted:
        return out, True

    # fallback: insere após o bloco inicial de imports
    out2: List[str] = []
    inserted2 = False
    for line in out:
        if (not inserted2) and (line.startswith("import ") or line.startswith("from ") or (not line.strip())):
            out2.append(line)
            continue
        if not inserted2:
            out2.append("from .dsio import read_dataset_csv")
            inserted2 = True
        out2.append(line)

    return out2, inserted2


def patch_read_csv(lines: List[str]) -> Tuple[List[str], int]:
    out: List[str] = []
    changed = 0

    pat = re.compile(r"^(?P<indent>\s*)df\s*=\s*pd\.read_csv\((?P<arg>[^)]*)\)(?P<tail>.*)$")

    for line in lines:
        m = pat.match(line)
        if not m:
            out.append(line)
            continue

        # já usa helper?
        if "read_dataset_csv" in line:
            out.append(line)
            continue

        indent = m.group("indent")
        arg = m.group("arg").strip()

        out.append(f"{indent}df = read_dataset_csv({arg}, label_col=\"y_open_close\")")
        changed += 1

    return out, changed


def patch_file(path: Path) -> Tuple[bool, str]:
    txt = path.read_text(encoding="utf-8", errors="replace")
    lines = txt.splitlines()

    lines2, imp_changed = patch_imports(lines)
    lines3, read_changed = patch_read_csv(lines2)

    if not (imp_changed or read_changed):
        return False, "no changes"

    path.write_text("\n".join(lines3) + "\n", encoding="utf-8", newline="\n")
    return True, f"imports={'yes' if imp_changed else 'no'} readcsv={read_changed}"


def main() -> None:
    repo = find_repo_root(Path(__file__))
    if not (repo / "src" / "natbin").exists():
        print(f"[P34] ERRO: repo root não parece correto: {repo}")
        sys.exit(2)

    changed_files: List[Path] = []

    dsio_path = ensure_dsio(repo)
    changed_files.append(dsio_path)
    print(f"[P34] OK wrote {dsio_path}")

    for rel in TARGETS:
        p = repo / rel
        if not p.exists():
            print(f"[P34] skip missing: {rel}")
            continue
        ok, note = patch_file(p)
        if ok:
            changed_files.append(p)
            print(f"[P34] OK patched: {rel} ({note})")
        else:
            print(f"[P34] OK untouched: {rel} ({note})")

    # compile-check
    for p in changed_files:
        py_compile.compile(str(p), doraise=True)

    print(f"[P34] files checked: {len(changed_files)}")
    print("[P34] Smoke-tests sugeridos:")
    print("  1) .\\.venv\\Scripts\\python.exe .\\scripts\\tools\\selfcheck_repo.py")
    print("  2) .\\.venv\\Scripts\\python.exe -m natbin.paper_pnl_backtest --k 2 --holdout-days 60 --payout 0.8 --gate-mode cp --meta-model hgb --thresh-on ev --retrain-every-days 20 --threshold 0.03")
    print("  3) .\\.venv\\Scripts\\python.exe -m natbin.tune_multiwindow_topk --k 1 --windows 2 --window-days 60 --gate-mode cp --meta-model hgb --thresh-on ev --min-total-trades 80 --min-trades-per-window 25")
    print("[P34] Done.")


if __name__ == "__main__":
    main()
