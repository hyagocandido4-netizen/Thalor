from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class PatchResult:
    changed: bool
    backup_path: Path | None = None


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _backup_file(path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak_{ts}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def patch_observe_loop_auto(ps1_path: Path) -> PatchResult:
    if not ps1_path.exists():
        raise FileNotFoundError(f"observe_loop_auto.ps1 não encontrado em: {ps1_path}")

    text = ps1_path.read_text(encoding="utf-8")
    nl = _detect_newline(text)

    changed = False

    # 1) Inject VOL_* floors/ceilings right after the "computing params" log line
    if "P15e: VOL floors/ceilings" not in text:
        m = re.search(r"(?m)^.*\[P12\] auto volume: computing params\.\.\..*(?:\r?\n)", text)
        if not m:
            raise RuntimeError(
                "Não achei o marcador '[P12] auto volume: computing params...' em observe_loop_auto.ps1. "
                "Me mande as ~120 linhas do topo do arquivo para eu ajustar o patch."
            )

        vol_block = (
            f"# --- P15e: VOL floors/ceilings for K>=3 + meta_iso ---{nl}"
            f"$env:VOL_ENFORCE_P14 = \"1\"{nl}"
            f"$env:VOL_THR_MIN = \"0.02\"{nl}"
            f"$env:VOL_BOOTSTRAP_THR_FLOOR = \"0.02\"{nl}"
            f"$env:VOL_BOOTSTRAP_STUCK_THR_FLOOR = \"0.02\"{nl}"
            f"$env:VOL_SAFE_THR_MIN = \"0.02\"{nl}"
            f"{nl}"
            f"$env:VOL_ALPHA_MAX = \"0.12\"{nl}"
            f"$env:VOL_BOOTSTRAP_ALPHA_END_CEIL = \"0.12\"{nl}"
            f"$env:VOL_SAFE_ALPHA_MAX = \"0.12\"{nl}"
            f"# --- /P15e ---{nl}"
        )

        text = text[: m.end()] + vol_block + text[m.end() :]
        changed = True

    # 2) Inject dynamic REGIME_MODE toggle after "$rec = $obj.recommended"
    if "P15e: auto REGIME_MODE" not in text:
        m2 = re.search(r"(?m)^.*\$rec\s*=\s*\$obj\.recommended.*(?:\r?\n)", text)
        if not m2:
            raise RuntimeError(
                "Não achei a linha '$rec = $obj.recommended' em observe_loop_auto.ps1. "
                "Me mande o trecho em volta do parse do JSON (procure por ConvertFrom-Json)."
            )

        regime_block = (
            f"# --- P15e: auto REGIME_MODE during bootstrap (avoid starvation) ---{nl}"
            f"# Se quiser forçar manualmente, set: $env:REGIME_MODE_LOCK=\"1\" e $env:REGIME_MODE=\"hard|soft|off\"{nl}"
            f"if ((($env:REGIME_MODE_LOCK | AsStr) -ne \"1\")) {{{nl}"
            f"  $dec = ($obj.decision | AsStr){nl}"
            f"  $tpd = 0.0{nl}"
            f"  try {{ $tpd = [double]($rec.observed_trades_per_day | AsStr) }} catch {{ $tpd = 0.0 }}{nl}"
            f"  $tt = 0{nl}"
            f"  try {{ $tt = [int]($obj.window.trades_today | AsStr) }} catch {{ $tt = 0 }}{nl}"
            f"  if (($dec -like \"*bootstrap*\") -and ($tpd -lt 0.20) -and ($tt -eq 0)) {{{nl}"
            f"    $env:REGIME_MODE = \"soft\"{nl}"
            f"    Write-Host \"[P15e] REGIME_MODE=soft (bootstrap low-trades)\" -ForegroundColor Yellow{nl}"
            f"  }} else {{{nl}"
            f"    $env:REGIME_MODE = \"hard\"{nl}"
            f"  }}{nl}"
            f"}}{nl}"
            f"# --- /P15e ---{nl}"
        )

        text = text[: m2.end()] + regime_block + text[m2.end() :]
        changed = True

    if not changed:
        return PatchResult(changed=False)

    backup = _backup_file(ps1_path)
    ps1_path.write_text(text, encoding="utf-8")
    return PatchResult(changed=True, backup_path=backup)


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    ps1 = root / "scripts" / "scheduler" / "observe_loop_auto.ps1"

    res = patch_observe_loop_auto(ps1)

    if res.changed:
        print(f"[P15e] OK patched: {ps1}")
        print(f"[P15e] Backup: {res.backup_path}")
        print("\n[P15e] Teste sugerido:")
        print("  pwsh -ExecutionPolicy Bypass -File .\\scripts\\scheduler\\observe_loop_auto.ps1 -Once")
        print(
            "\nEsperado: o P12 começa a baixar THRESHOLD de 0.10 -> 0.09 -> 0.08 ... até 0.02 "
            "(se continuar sem trades).\n"
            "E, se estiver em bootstrap sem trades, você deve ver um log '[P15e] REGIME_MODE=soft ...' "
            "e não mais 'reason=regime_block'."
        )
    else:
        print("[P15e] Nada para fazer (já aplicado).")


if __name__ == "__main__":
    main()