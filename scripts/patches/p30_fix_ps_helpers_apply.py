from __future__ import annotations

import re
from pathlib import Path
from datetime import datetime


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    target = repo / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not target.exists():
        raise SystemExit(f"[P30] alvo não encontrado: {target}")

    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(True)

    # Anchor do bloco P15e (sempre existe nas versões recentes)
    p15e_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*#\s*---\s*P15e:", line):
            p15e_idx = i
            break
    if p15e_idx is None:
        raise SystemExit("[P30] não achei o anchor '# --- P15e:' no observe_loop_auto.ps1")

    # Vamos substituir "tudo que parecer helpers" ANTES do P15e:
    # prioridade: marker [P15e_fix], senão function AsStr, senão param(ValueFromPipeline) quebrado.
    start_idx = None
    patterns = [
        r"^\s*#\s*\[P15e_fix\]",  # marker oficial
        r"^\s*function\s+AsStr\b",  # helper antigo
        r"^\s*param\(\[Parameter\(ValueFromPipeline=\$true\)\]\s*\$v\)\s*$",  # helper quebrado
    ]
    for pat in patterns:
        for i in range(0, p15e_idx):
            if re.match(pat, lines[i], flags=re.IGNORECASE):
                start_idx = i
                break
        if start_idx is not None:
            break

    if start_idx is None:
        # se não achou nada, só injeta antes do P15e (não remove nada)
        start_idx = p15e_idx

    helper_block = (
        "# [P15e_fix] helpers: safe env parsing for PowerShell (pipeline-friendly; accepts \"0,07\")\n"
        "function AsStr {\n"
        "  param([Parameter(ValueFromPipeline=$true)] $v)\n"
        "  process { if ($null -eq $v) { \"\" } else { [string]$v } }\n"
        "}\n"
        "\n"
        "function AsInt {\n"
        "  param([Parameter(ValueFromPipeline=$true)] $v)\n"
        "  process {\n"
        "    if ($null -eq $v -or \"$v\" -eq \"\") { return 0 }\n"
        "    $s = [string]$v\n"
        "    $s = $s.Replace(\",\", \".\")\n"
        "    try { return [int][double]::Parse($s, [System.Globalization.CultureInfo]::InvariantCulture) } catch { return 0 }\n"
        "  }\n"
        "}\n"
        "\n"
        "function AsFloat {\n"
        "  param([Parameter(ValueFromPipeline=$true)] $v)\n"
        "  process {\n"
        "    if ($null -eq $v -or \"$v\" -eq \"\") { return 0.0 }\n"
        "    $s = [string]$v\n"
        "    $s = $s.Replace(\",\", \".\")\n"
        "    try { return [double]::Parse($s, [System.Globalization.CultureInfo]::InvariantCulture) } catch { return 0.0 }\n"
        "  }\n"
        "}\n"
        "# [/P15e_fix]\n"
        "\n"
    )
    helper_lines = helper_block.splitlines(True)

    # Substitui a região start_idx .. p15e_idx-1 inteira por um bloco limpo (remove lixo quebrado)
    new_lines = lines[:start_idx] + helper_lines + lines[p15e_idx:]

    backup = target.with_suffix(target.suffix + f".bak_{_ts()}")
    backup.write_text(text, encoding="utf-8")
    target.write_text("".join(new_lines), encoding="utf-8")

    print(f"[P30] OK patched: {target}")
    print(f"[P30] backup: {backup}")
    print("[P30] Esperado após o patch:")
    print("  - selfcheck_repo.py -> ALL OK")
    print("  - observe_loop_auto.ps1 -Once não pode mais cair em 'param not recognized'")


if __name__ == "__main__":
    main()