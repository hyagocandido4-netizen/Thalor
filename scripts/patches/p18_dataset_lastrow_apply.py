#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P18 — Fix 1-candle lag (keep last row unlabeled) + make backtests robust

CRÍTICO:
- y_open_close depende do próximo candle (shift(-1)).
- Logo, o ÚLTIMO candle sempre tem y_open_close=NaN.
- O dataset antigo removia esse último candle => observe ficava 1 candle atrasado.

Este patch faz:
1) src/natbin/dataset2.py
   - build_dataset(): mantém o último candle (último ts) mesmo sem label.
     (mantém APENAS o último; outros NaN por gaps/sessão continuam fora)
   - build_dataset_incremental(): expected_last_ts vira db_max (dataset inclui último ts)

2) Scripts de backtest/tuning que montam X/y:
   - inserem df = df[df["y_open_close"].notna()].copy() em lugar seguro
   - garantem sort por ts antes de montar features

Segurança:
- backups .bak_<timestamp>
- py_compile nos arquivos alterados

Run (no root do repo):
  .\\.venv\\Scripts\\python.exe .\\scripts\\patches\\p18_dataset_lastrow_apply.py
"""
from __future__ import annotations

import py_compile
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class PatchResult:
    path: Path
    changed: bool
    backup_path: Optional[Path]
    note: str = ""


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def find_repo_root() -> Path:
    cwd = Path.cwd().resolve()
    if (cwd / "src" / "natbin").exists():
        return cwd
    here = Path(__file__).resolve()
    for p in [here.parent] + list(here.parents):
        if (p / "src" / "natbin").exists():
            return p
    raise SystemExit("P18: não encontrei src/natbin. Rode a partir do root do repo.")


def backup_file(path: Path) -> Path:
    b = path.with_suffix(path.suffix + f".bak_{stamp()}")
    shutil.copy2(path, b)
    return b


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def patch_dataset2(text: str) -> Tuple[str, bool, str]:
    """
    Patch dataset2.py across versions:
    - replace strict dropna that removes last row
    - expected_last = db_max in incremental builder
    """
    changed = False
    notes: List[str] = []

    if "NOTE(P18): y_open_close depende do próximo candle" in text:
        # already patched
        pass
    else:
        # Version A: df_out = df.dropna(subset=[y_open_close, *feature_cols]).copy()
        pat_a = re.compile(
            r"^(?P<indent>\s*)df_out\s*=\s*df\.dropna\(subset=\[y_open_close,\s*\*feature_cols\]\)\.copy\(\)\s*$",
            flags=re.MULTILINE,
        )
        m_a = pat_a.search(text)
        if m_a:
            ind = m_a.group("indent")
            block = (
                f"{ind}# NOTE(P18): y_open_close depende do próximo candle (shift(-1)). O ÚLTIMO candle\n"
                f"{ind}# sempre fica com label NaN. Mantemos esse último candle no dataset para o online.\n"
                f"{ind}# Mantemos APENAS o último candle sem label; os demais NaN (ex.: gaps/sessão) continuam fora.\n"
                f"{ind}df_feat = df.dropna(subset=feature_cols).copy()\n"
                f"{ind}if y_open_close in df_feat.columns:\n"
                f"{ind}    last_ts = df_feat[\"ts\"].max()\n"
                f"{ind}    keep_mask = (df_feat[\"ts\"] == last_ts) | df_feat[y_open_close].notna()\n"
                f"{ind}    df_out = df_feat.loc[keep_mask].copy()\n"
                f"{ind}else:\n"
                f"{ind}    df_out = df_feat"
            )
            text = text[:m_a.start()] + block + text[m_a.end():]
            changed = True
            notes.append("build_dataset keep-last-row (vA)")

        # Version B: out = out.dropna(subset=["y_open_close"] + feature_cols).reset_index(drop=True)
        pat_b = re.compile(
            r"^(?P<indent>\s*)out\s*=\s*out\.dropna\(subset=\[\"y_open_close\"\]\s*\+\s*feature_cols\)\.reset_index\(drop=True\)\s*$",
            flags=re.MULTILINE,
        )
        m_b = pat_b.search(text)
        if m_b:
            ind = m_b.group("indent")
            block = (
                f"{ind}# NOTE(P18): y_open_close depende do próximo candle (shift(-1)). O ÚLTIMO candle\n"
                f"{ind}# sempre fica com label NaN. Mantemos esse último candle no dataset para o online.\n"
                f"{ind}# Mantemos APENAS o último candle sem label; os demais NaN (ex.: gaps/sessão) continuam fora.\n"
                f"{ind}out = out.dropna(subset=feature_cols).reset_index(drop=True)\n"
                f"{ind}if len(out) > 0:\n"
                f"{ind}    last_ts = out[\"ts\"].max()\n"
                f"{ind}    out = out.loc[(out[\"ts\"] == last_ts) | out[\"y_open_close\"].notna()].reset_index(drop=True)"
            )
            text = text[:m_b.start()] + block + text[m_b.end():]
            changed = True
            notes.append("build_dataset keep-last-row (vB)")

    # incremental builder expected_last = db_max - step  -> db_max
    pat_exp = re.compile(r"^(?P<indent>\s*)expected_last\s*=\s*db_max\s*-\s*step.*$", flags=re.MULTILINE)
    m2 = pat_exp.search(text)
    if m2:
        ind = m2.group("indent")
        repl = f"{ind}expected_last = db_max  # P18: dataset inclui o candle mais recente (label NaN) para o online"
        text = text[:m2.start()] + repl + text[m2.end():]
        changed = True
        notes.append("incremental expected_last=db_max")

    return text, changed, "; ".join(notes)


def patch_consumer_df_loading(text: str) -> Tuple[str, bool, str]:
    """
    Make consumer scripts robust:
    - ensure df sorted by ts after loading
    - ensure df filtered to labeled rows BEFORE building X/y (avoids astype(int) on NaN)
    """
    if 'df["y_open_close"].notna()' in text or "df['y_open_close'].notna()" in text:
        return text, False, "already has y_open_close filter"

    lines = text.splitlines(keepends=True)

    # Find first df = pd.read_csv(...)
    read_idx = None
    read_indent = ""
    read_has_sort = False
    read_pat = re.compile(r"^(?P<indent>\s*)df\s*=\s*pd\.read_csv\(")
    for i, line in enumerate(lines):
        m = read_pat.match(line)
        if m:
            read_idx = i
            read_indent = m.group("indent")
            read_has_sort = "sort_values" in line and ("\"ts\"" in line or "'ts'" in line)
            break
    if read_idx is None:
        return text, False, "no df=pd.read_csv match"

    def indent_len(s: str) -> int:
        return len(s.replace("\t", "    "))

    base_indent_len = indent_len(read_indent)

    # Detect empty-check block immediately after read_csv line (common pattern)
    j = read_idx + 1
    while j < len(lines) and lines[j].strip() == "":
        j += 1

    if_line_idx = None
    if j < len(lines) and re.match(rf"^{re.escape(read_indent)}if\s+(len\(df\)\s*==\s*0|df\.empty)\s*:\s*$", lines[j]):
        if_line_idx = j

    block_end = j
    if if_line_idx is not None:
        k = if_line_idx + 1
        while k < len(lines):
            if lines[k].strip() == "":
                k += 1
                continue
            cur_indent = re.match(r"^(\s*)", lines[k]).group(1)
            if indent_len(cur_indent) <= base_indent_len:
                break
            k += 1
        block_end = k

    # Find existing df sort line after block_end (within next 40 lines)
    sort_idx = None
    sort_pat = re.compile(rf"^{re.escape(read_indent)}df\s*=\s*df\.sort_values\(\s*[\"']ts[\"']\s*\).*")
    if read_has_sort:
        sort_idx = read_idx
    else:
        for i in range(block_end, min(len(lines), block_end + 40)):
            if sort_pat.match(lines[i]):
                sort_idx = i
                break

    changed = False
    notes: List[str] = []

    # If no sort, insert it at block_end
    if sort_idx is None:
        sort_line = f'{read_indent}df = df.sort_values("ts").reset_index(drop=True)\n'
        lines.insert(block_end, sort_line)
        sort_idx = block_end
        block_end += 1
        changed = True
        notes.append("insert sort_values(ts)")

    # Insert filter right after sort_idx (or after read line if read line already sorts)
    filter_line = f'{read_indent}df = df[df["y_open_close"].notna()].copy()\n'
    insert_at = sort_idx + 1
    lines.insert(insert_at, filter_line)
    changed = True
    notes.append("insert y_open_close notna filter")

    return "".join(lines), changed, "; ".join(notes)


def apply_patch(path: Path, patch_fn) -> PatchResult:
    orig = read_text(path)
    new, changed, note = patch_fn(orig)
    if not changed:
        return PatchResult(path=path, changed=False, backup_path=None, note=note)
    b = backup_file(path)
    write_text(path, new)
    return PatchResult(path=path, changed=True, backup_path=b, note=note)


def main() -> None:
    root = find_repo_root()

    results: List[PatchResult] = []
    to_compile: List[Path] = []

    # dataset2
    dataset2 = root / "src" / "natbin" / "dataset2.py"
    if not dataset2.exists():
        raise SystemExit(f"P18: não achei {dataset2}")
    results.append(apply_patch(dataset2, patch_dataset2))
    to_compile.append(dataset2)

    consumer_rel = [
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
    for rel in consumer_rel:
        p = root / rel
        if not p.exists():
            continue
        results.append(apply_patch(p, patch_consumer_df_loading))
        to_compile.append(p)

    # compile
    errors: List[str] = []
    for p in to_compile:
        try:
            py_compile.compile(str(p), doraise=True)
        except Exception as e:
            errors.append(f"{p}: {e}")

    changed = [r for r in results if r.changed]
    print(f"[P18] files changed: {len(changed)}/{len(results)}")
    for r in changed:
        print(f"[P18] OK {r.path} (backup={r.backup_path})")
        if r.note:
            print(f"      note: {r.note}")

    if errors:
        print("[P18] ERROR: py_compile falhou:")
        for e in errors:
            print("  -", e)
        raise SystemExit(2)

    print("[P18] OK.")
    print("[P18] Smoke-tests sugeridos:")
    print("  - Regerar dataset (P11): confira que o último ts do CSV == último ts da tabela candles")
    print("  - Rodar observe uma vez: ele deve usar o último candle (sem lag)")
    print("  - Rodar paper_pnl_backtest: deve ignorar a última linha sem label (sem crash)")


if __name__ == "__main__":
    main()
