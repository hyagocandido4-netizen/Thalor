from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
import py_compile


def find_repo_root() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        Path.cwd(),
        here.parents[2],  # scripts/patches -> scripts -> repo root
        here.parents[1],
        here.parents[0],
    ]
    for root in candidates:
        if (root / "src" / "natbin" / "observe_signal_topk_perday.py").exists():
            return root
    raise SystemExit(
        "Não achei src/natbin/observe_signal_topk_perday.py. "
        "Rode este script dentro do repo (C:\\Users\\hyago\\Documents\\bot)."
    )


def backup_file(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


def fix_thr_nameerror(text: str) -> tuple[str, int]:
    """
    Corrige o bug:
      thr_lo = float(os.getenv("COVREG_THR_LO", str(thr)))
    onde 'thr' não existe -> NameError no import do módulo.

    Substitui por default seguro "0.10".
    """
    n = 0

    bad_line = 'thr_lo = float(os.getenv("COVREG_THR_LO", str(thr)))'
    if bad_line in text:
        text = text.replace(
            bad_line,
            'thr_lo = float(os.getenv("COVREG_THR_LO", "0.10"))',
        )
        n += 1

    # Também cobre variações do mesmo padrão (com aspas simples/duplas e espaços)
    pat = re.compile(
        r'os\.getenv\(\s*["\']COVREG_THR_LO["\']\s*,\s*str\(\s*thr\s*\)\s*\)'
    )
    text2, n2 = pat.subn('os.getenv("COVREG_THR_LO", "0.10")', text)
    text = text2
    n += n2

    return text, n


def inject_p8b_cpreg(text: str) -> tuple[str, bool]:
    """
    P8b = CPREG (alpha schedule + slot-aware):
      - CPREG_ENABLE=1 ativa
      - Alpha varia com o progresso do dia (warmup -> ramp -> end)
      - Slot 2+ aplica multiplicador (mais conservador)

    Implementação: injeta um bloco imediatamente ANTES do primeiro compute_scores(...),
    com indentação igual ao callsite.
    """
    if "P8b: CPREG" in text:
        return text, False

    lines = text.splitlines(True)

    # Primeiro call de compute_scores(
    idx = None
    for i, line in enumerate(lines):
        if "compute_scores(" in line and not line.lstrip().startswith("#"):
            idx = i
            break

    if idx is None:
        raise SystemExit(
            "Não encontrei 'compute_scores(' em observe_signal_topk_perday.py.\n"
            "Cole aqui um trecho do arquivo perto da parte que calcula score/gate."
        )

    indent = re.match(r"^(\s*)", lines[idx]).group(1)

    block = [
        f"{indent}# --- P8b: CPREG (alpha schedule + slot-aware) ---\n",
        f"{indent}import os as _os\n",
        f"{indent}from datetime import datetime as _dt\n",
        f"{indent}if _os.getenv('CPREG_ENABLE', '0').strip() == '1':\n",
        f"{indent}    _gm = locals().get('gate_mode_eff', '')\n",
        f"{indent}    if isinstance(_gm, str) and _gm.strip().lower() == 'cp':\n",
        f"{indent}        _now = _dt.now()\n",
        f"{indent}        _sec = (_now.hour * 3600) + (_now.minute * 60) + _now.second\n",
        f"{indent}        _frac = _sec / 86400.0\n",
        f"{indent}        _slot = int(locals().get('executed_today', 0)) + 1\n",
        f"{indent}        _a0 = float(_os.getenv('CPREG_ALPHA_START', '0.06'))\n",
        f"{indent}        _a1 = float(_os.getenv('CPREG_ALPHA_END',   '0.09'))\n",
        f"{indent}        _w  = float(_os.getenv('CPREG_WARMUP_FRAC',  '0.50'))\n",
        f"{indent}        _e  = float(_os.getenv('CPREG_RAMP_END_FRAC','0.90'))\n",
        f"{indent}        _m2 = float(_os.getenv('CPREG_SLOT2_MULT',   '0.85'))\n",
        f"{indent}        if _frac <= _w:\n",
        f"{indent}            _a = _a0\n",
        f"{indent}        elif _frac >= _e:\n",
        f"{indent}            _a = _a1\n",
        f"{indent}        else:\n",
        f"{indent}            _u = (_frac - _w) / max(1e-9, (_e - _w))\n",
        f"{indent}            _a = _a0 + (_a1 - _a0) * _u\n",
        f"{indent}        if _slot >= 2:\n",
        f"{indent}            _a = _a * _m2\n",
        f"{indent}        _a = max(0.001, min(0.50, _a))\n",
        # sem f-string aqui (para não quebrar o patcher)
        f"{indent}        _os.environ['CP_ALPHA'] = '%.4f' % (_a,)\n",
        f"{indent}# --- /P8b ---\n",
    ]

    new_lines = lines[:idx] + block + lines[idx:]
    return "".join(new_lines), True


def main() -> None:
    root = find_repo_root()
    obs = root / "src" / "natbin" / "observe_signal_topk_perday.py"

    print(f"[P8b] Repo: {root}")
    print(f"[P8b] File: {obs}")

    bkp = backup_file(obs)
    print(f"[P8b] Backup: {bkp}")

    text = obs.read_text(encoding="utf-8")

    text, nfix = fix_thr_nameerror(text)
    print(f"[P8b] FIX NameError(thr): {nfix} ocorrência(s)")

    text, injected = inject_p8b_cpreg(text)
    print(f"[P8b] CPREG injected: {injected}")

    obs.write_text(text, encoding="utf-8")

    # valida sintaxe do arquivo alvo
    py_compile.compile(str(obs), doraise=True)
    print("[P8b] OK: py_compile passou (observe_signal_topk_perday.py válido).")


if __name__ == "__main__":
    main()