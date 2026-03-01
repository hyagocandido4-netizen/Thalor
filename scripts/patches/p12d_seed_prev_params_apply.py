from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def repo_root() -> Path:
    cwd = Path.cwd().resolve()
    for p in [cwd] + list(cwd.parents):
        if (p / ".git").exists():
            return p
    raise SystemExit("Não encontrei .git. Rode dentro de C:\\Users\\hyago\\Documents\\bot")


def backup(p: Path) -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)


def main() -> None:
    root = repo_root()
    ps1 = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps1.exists():
        raise SystemExit(f"Não encontrei: {ps1}")

    txt = ps1.read_text(encoding="utf-8", errors="replace")
    marker = "# P12d: seed previous params from runs/auto_params.json"
    if marker in txt:
        print("[P12d] Já aplicado (marker encontrado).")
        return

    needle = 'Write-Host "[P12] auto volume: computing params..." -ForegroundColor Cyan'
    if needle not in txt:
        raise SystemExit("Não encontrei o ponto de injeção (linha do Write-Host). Me mande o conteúdo do observe_loop_auto.ps1.")

    seed = r'''
# P12d: seed previous params from runs/auto_params.json
# (Assim o auto_volume consegue continuar de 0.09 -> 0.08 -> 0.07 entre execuções do pwsh)
$statePath = ".\runs\auto_params.json"
if (Test-Path $statePath) {
  try {
    $prev = Get-Content $statePath -Raw | ConvertFrom-Json
    $pr = $prev.recommended
    if ($pr -ne $null) {
      if ($pr.threshold -ne $null) { $env:THRESHOLD = [string]$pr.threshold }
      if ($pr.cpreg_alpha_start -ne $null) { $env:CPREG_ALPHA_START = [string]$pr.cpreg_alpha_start }
      if ($pr.cpreg_alpha_end -ne $null) { $env:CPREG_ALPHA_END = [string]$pr.cpreg_alpha_end }
      if ($pr.cpreg_slot2_mult -ne $null) { $env:CPREG_SLOT2_MULT = [string]$pr.cpreg_slot2_mult }
    }
  } catch {
    # se der erro, segue com defaults
  }
}
'''

    backup(ps1)
    txt2 = txt.replace(needle, seed + "\n" + needle)
    ps1.write_text(txt2, encoding="utf-8")
    print(f"[P12d] OK: patched {ps1}")


if __name__ == "__main__":
    main()