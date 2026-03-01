#!/usr/bin/env python3
"""p15e_fix_asstr_apply.py

Fix rápido para o erro:
  The term 'AsStr' is not recognized ...

Causa:
- O observe_loop_auto.ps1 (patch P15e) passou a usar o helper `AsStr` (pipe)
  mas o script não tinha a função definida.

O que este patch faz:
- Insere uma função `AsStr` (pipeline-safe) *antes* do primeiro uso,
  preservando a estrutura do arquivo (não quebra param()).

Rodar:
  .\.venv\Scripts\python.exe .\scripts\patches\p15e_fix_asstr_apply.py

Depois testar:
  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path


def _now_tag() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _find_repo_root(start: Path) -> Path:
    start = start.resolve()
    for p in [start] + list(start.parents):
        if (p / "scripts").exists() and (p / "src" / "natbin").exists():
            return p
    raise SystemExit("[P15e_fix] Não encontrei o repo root. Rode este script de dentro do repo.")


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + f".bak_{_now_tag()}")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return bak


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def patch_observe_loop_auto(ps1_path: Path) -> bool:
    text = ps1_path.read_text(encoding="utf-8")
    nl = _detect_newline(text)

    if "function AsStr" in text:
        print("[P15e_fix] AsStr já existe. Nada a fazer.")
        return False

    if "| AsStr" not in text:
        print("[P15e_fix] Não achei uso de '| AsStr'. Nada a fazer.")
        return False

    helper = (
        f"# [P15e_fix] helper: safe string conversion (supports pipeline){nl}"
        f"function AsStr {{{nl}"
        f"  param([Parameter(ValueFromPipeline=$true)] $v){nl}"
        f"  process {{{nl}"
        f"    if ($null -eq $v) {{ \"\" }} else {{ [string]$v }}{nl}"
        f"  }}{nl}"
        f"}}{nl}"
        f"# [/P15e_fix]{nl}{nl}"
    )

    # Prefer: inserir antes do bloco P15e (regime-mode)
    marker = "# --- P15e: auto REGIME_MODE"
    if marker in text:
        text = text.replace(marker, helper + marker, 1)
    else:
        # Fallback: inserir no começo da linha do primeiro uso de '| AsStr'
        idx = text.find("| AsStr")
        line_start = text.rfind(nl, 0, idx)
        if line_start < 0:
            line_start = 0
        else:
            line_start = line_start + len(nl)
        text = text[:line_start] + helper + text[line_start:]

    bak = _backup(ps1_path)
    ps1_path.write_text(text, encoding="utf-8")
    print(f"[P15e_fix] OK patched: {ps1_path}")
    print(f"[P15e_fix] Backup: {bak.name}")
    return True


def main() -> None:
    repo = _find_repo_root(Path(__file__).resolve().parent)
    ps1 = repo / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps1.exists():
        raise SystemExit(f"[P15e_fix] observe_loop_auto.ps1 não encontrado: {ps1}")

    changed = patch_observe_loop_auto(ps1)
    if changed:
        print("\n[P15e_fix] Teste agora:")
        print(r"  pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[P15e_fix] ERRO: {e}")
        sys.exit(1)