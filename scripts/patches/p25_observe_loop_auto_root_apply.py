#!/usr/bin/env python3
"""P25 - Harden observe_loop_auto.ps1 against wrong working directory

Problem:
  observe_loop_auto.ps1 calls relative paths like:
    .\\.venv\\Scripts\\python.exe
    .\\scripts\\scheduler\\observe_loop.ps1

  This works only if you *run from repo root*.
  In Task Scheduler / services, the working directory is often different (e.g. C:\\Windows\\System32),
  causing the loop to fail immediately.

Fix:
  After $ErrorActionPreference = 'Stop', force Set-Location to the repo root computed from $PSScriptRoot.

Run:
  python .\\scripts\\patches\\p25_observe_loop_auto_root_apply.py
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path


TARGET_REL = Path("scripts/scheduler/observe_loop_auto.ps1")


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
    raise SystemExit("[P25] ERRO: não encontrei a raiz do repo.")


def _backup(path: Path) -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_{ts}")
    bak.write_bytes(path.read_bytes())
    return bak


def patch(root: Path) -> None:
    target = root / TARGET_REL
    if not target.exists():
        raise SystemExit(f"[P25] ERRO: arquivo não encontrado: {target}")

    txt = target.read_text(encoding="utf-8", errors="replace")

    if "Set-Location $repoRoot" in txt or "Set-Location $root" in txt:
        print("[P25] observe_loop_auto.ps1 já parece fixar working dir (skip).")
        return

    lines = txt.splitlines(True)

    insert_after = None
    for i, ln in enumerate(lines):
        if "$ErrorActionPreference" in ln and "Stop" in ln:
            insert_after = i
            break

    if insert_after is None:
        raise SystemExit("[P25] ERRO: não achei a linha $ErrorActionPreference = 'Stop'.")

    insert = [
        "\n",
        "# [P25] garante execução a partir da raiz do repo (evita falhas no Task Scheduler)\n",
        "$repoRoot = Resolve-Path (Join-Path $PSScriptRoot \"..\\..\")\n",
        "Set-Location $repoRoot\n",
        "\n",
    ]

    bak = _backup(target)
    new_txt = "".join(lines[: insert_after + 1] + insert + lines[insert_after + 1 :])
    target.write_text(new_txt, encoding="utf-8")
    print(f"[P25] OK {target} (backup={bak})")


def main() -> None:
    root = _find_repo_root(Path(__file__).resolve().parent)
    patch(root)
    print("[P25] Teste sugerido:")
    print("  - rode de QUALQUER pasta: pwsh -ExecutionPolicy Bypass -File <repo>\\scripts\\scheduler\\observe_loop_auto.ps1 -Once")


if __name__ == "__main__":
    main()
