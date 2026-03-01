#!/usr/bin/env python3
"""
P29 — Repo hygiene: ignora artefatos locais (exports/) gerados pelo export_repo_sanitized.ps1.

Uso:
  python scripts/patches/p29_gitignore_exports_apply.py
"""
from __future__ import annotations

import time
from pathlib import Path

TS = time.strftime("%Y%m%d_%H%M%S")


def backup_path(p: Path) -> Path:
    return p.with_suffix(p.suffix + f".bak_{TS}")


def main() -> None:
    here = Path(__file__).resolve()
    repo = here.parents[2]
    gi = repo / ".gitignore"
    if not gi.exists():
        raise SystemExit("[P29] ERRO: .gitignore não encontrado no repo.")

    raw = gi.read_text(encoding="utf-8", errors="replace").splitlines()
    want = [
        "",
        "# local exports (sanitized zips)",
        "exports/",
        "exports/**/*.zip",
    ]

    # idempotente: só adiciona se não existir exports/ já
    if any(line.strip() == "exports/" for line in raw):
        print("[P29] .gitignore já ignora exports/ (skip).")
        return

    bak = backup_path(gi)
    bak.write_text("\n".join(raw) + "\n", encoding="utf-8", newline="\n")

    out = raw + want
    gi.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print(f"[P29] OK patched {gi} (backup={bak})")
    print("[P29] Próximo passo:")
    print("  - git status (exports/ deve sumir da lista de untracked)")


if __name__ == "__main__":
    main()
