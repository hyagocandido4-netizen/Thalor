#!/usr/bin/env python3
"""P23 - Repo hygiene (gitignore + cleanup helpers)

What this patch does:
  1) Extends .gitignore to ignore patch backups (*.bak_*) and SQLite sidecar files
     (-wal / -shm / -journal), plus dataset meta files (data/*.meta.json).
  2) Adds an optional cleanup script: scripts/tools/cleanup_backups.ps1

Why:
  - Patch scripts (Pxx) create *.bak_YYYYMMDD_* backups. Without ignoring them,
    `git status` gets noisy and it's easy to accidentally commit backups.
  - SQLite produces -wal / -shm files that are not matched by *.sqlite3.
  - Dataset incremental writes data/*.meta.json.

Safe/idempotent:
  - Only appends a clearly delimited block to .gitignore if not present.
  - Will not overwrite an existing cleanup script.

Run:
  python .\scripts\patches\p23_repo_hygiene_apply.py
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path


P23_MARKER_BEGIN = "# --- natbin repo hygiene (P23) ---"
P23_MARKER_END = "# --- end natbin repo hygiene (P23) ---"


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(12):
        if (cur / "src" / "natbin").is_dir() and (cur / "pyproject.toml").exists():
            return cur
        if (cur / "src" / "natbin").is_dir() and (cur / ".gitignore").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise SystemExit("[P23] ERRO: não consegui localizar a raiz do repo (esperava src/natbin + pyproject.toml).")


def _backup(path: Path) -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_{ts}")
    bak.write_bytes(path.read_bytes())
    return bak


def patch_gitignore(root: Path) -> None:
    gi = root / ".gitignore"
    if not gi.exists():
        raise SystemExit(f"[P23] ERRO: .gitignore não encontrado em {root}")

    text = gi.read_text(encoding="utf-8", errors="replace")
    if P23_MARKER_BEGIN in text:
        print("[P23] .gitignore já contém bloco P23 (skip).")
        return

    block = "\n".join(
        [
            "",
            P23_MARKER_BEGIN,
            "# Patch backups (gerados por scripts/patches)",
            "*.bak_*",
            "*.bak",
            "*.bak.*",
            "*.orig",
            "*.rej",
            "",
            "# SQLite sidecars (WAL/SHM/JOURNAL)",
            "*.sqlite3-wal",
            "*.sqlite3-shm",
            "*.sqlite3-journal",
            "",
            "# Dataset incremental metadata", 
            "data/*.meta.json",
            "data/*.meta.yaml",
            "data/*.meta.yml",
            "",
            "# Config backups", 
            "config.yaml.bak_*",
            "configs/*.bak_*",
            "configs/**/*.bak_*",
            P23_MARKER_END,
            "",
        ]
    )

    bak = _backup(gi)
    gi.write_text(text.rstrip() + block, encoding="utf-8")
    print(f"[P23] OK .gitignore atualizado (backup={bak})")


def ensure_cleanup_script(root: Path) -> None:
    target = root / "scripts" / "tools" / "cleanup_backups.ps1"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        print(f"[P23] cleanup_backups.ps1 já existe (skip): {target}")
        return

    target.write_text(
        """# scripts/tools/cleanup_backups.ps1
#
# Remove arquivos de backup criados automaticamente por scripts/patches
# Ex.: *.bak_YYYYMMDD_HHMMSS
#
# Uso:
#   pwsh -ExecutionPolicy Bypass -File .\\scripts\\tools\\cleanup_backups.ps1
#
# Segurança:
#  - NÃO remove nada em data/ ou runs/.
#  - Só remove padrões conhecidos de backup.

$ErrorActionPreference = 'Stop'

# scripts/tools -> repo root (2 níveis acima)
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\\..")

$patterns = @(
  '*.bak_*',
  '*.orig',
  '*.rej'
)

$deleted = 0

# 1) limpa arquivos de backup na RAIZ do repo (não-recursivo)
foreach ($pat in $patterns) {
  Get-ChildItem -Path $repoRoot -File -Filter $pat -ErrorAction SilentlyContinue | ForEach-Object {
    try {
      Remove-Item -LiteralPath $_.FullName -Force
      $deleted += 1
    } catch {
      Write-Warning "falha ao remover: $($_.FullName) -> $($_.Exception.Message)"
    }
  }
}

# 2) limpa recursivamente apenas em src/ e scripts/
$targets = @(
  Join-Path $repoRoot 'src',
  Join-Path $repoRoot 'scripts'
)

foreach ($base in $targets) {
  if (-not (Test-Path $base)) { continue }
  foreach ($pat in $patterns) {
    Get-ChildItem -Path $base -Recurse -File -Filter $pat -ErrorAction SilentlyContinue | ForEach-Object {
      try {
        Remove-Item -LiteralPath $_.FullName -Force
        $deleted += 1
      } catch {
        Write-Warning "falha ao remover: $($_.FullName) -> $($_.Exception.Message)"
      }
    }
  }
}

Write-Host "[cleanup_backups] deleted=$deleted"
""",
        encoding="utf-8",
    )
    print(f"[P23] OK escreveu {target}")


def main() -> None:
    here = Path(__file__).resolve()
    root = _find_repo_root(here.parent)
    patch_gitignore(root)
    ensure_cleanup_script(root)
    print("[P23] OK.")
    print("[P23] Smoke-test sugerido:")
    print("  - git status (não deve listar *.bak_*)")
    print("  - (opcional) rode scripts/tools/cleanup_backups.ps1 para limpar backups antigos")


if __name__ == "__main__":
    main()
