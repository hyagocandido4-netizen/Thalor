from __future__ import annotations

import re
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
import py_compile

MARK_CPEG = "# --- P8b: CPREG (alpha schedule + slot-aware) ---"
MARK_THR_ENV = "# --- P8b: THRESHOLD env override ---"


def find_repo_root() -> Path:
    start = Path.cwd().resolve()
    for p in [start] + list(start.parents):
        if (p / ".git").exists():
            return p
        if (p / "pyproject.toml").exists() and (p / "src").exists():
            return p
    raise SystemExit("Não encontrei a raiz do repo (.git). Rode dentro de C:\\Users\\hyago\\Documents\\bot")


def backup_file(p: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    b = p.with_suffix(p.suffix + f".bak_{ts}")
    shutil.copy2(p, b)
    return b


def git_ref_exists(root: Path, ref: str) -> bool:
    r = subprocess.run(["git", "rev-parse", "--verify", ref], cwd=root, capture_output=True, text=True)
    return r.returncode == 0


def git_restore_file(root: Path, ref: str, relpath: str) -> None:
    # tenta git restore (git novo)
    r = subprocess.run(["git", "restore", "--source", ref, "--", relpath], cwd=root, capture_output=True, text=True)
    if r.returncode == 0:
        return
    # fallback: git checkout (git antigo)
    r2 = subprocess.run(["git", "checkout", ref, "--", relpath], cwd=root, capture_output=True, text=True)
    if r2.returncode != 0:
        raise SystemExit(
            "Falhou restaurar o arquivo via git.\n"
            f"restore stderr:\n{r.stderr}\n"
            f"checkout stderr:\n{r2.stderr}\n"
        )


def patch_allow_cp_gate_mode(text: str) -> tuple[str, int]:
    n = 0
    # se tiver: if gate_mode not in ("meta", "iso", "conf"):
    pat1 = re.compile(r'if\s+gate_mode\s+not\s+in\s*\(\s*"meta"\s*,\s*"iso"\s*,\s*"conf"\s*\)\s*:')
    text, k = pat1.subn('if gate_mode not in ("meta", "iso", "conf", "cp"):', text)
    n += k

    # variação com aspas simples
    pat2 = re.compile(r"if\s+gate_mode\s+not\s+in\s*\(\s*'meta'\s*,\s*'iso'\s*,\s*'conf'\s*\)\s*:")
    text, k = pat2.subn("if gate_mode not in ('meta', 'iso', 'conf', 'cp'):", text)
    n += k
    return text, n


def inject_threshold_env_override(text: str) -> tuple[str, bool]:
    """
    Permite override por env var:
      $env:THRESHOLD="0.10"
    sem precisar editar config.yaml.
    """
    if MARK_THR_ENV in text:
        return text, False

    # procura a linha onde thr é definido a partir do config best
    m = re.search(r'^(\s*)thr\s*=\s*float\(\s*best\.get\(\s*["\']threshold["\']', text, flags=re.M)
    if not m:
        # se não achar, não injeta (não quebra)
        return text, False

    indent = m.group(1)
    # injeta logo depois da linha do thr (no mesmo bloco)
    line_end = text.find("\n", m.start())
    if line_end == -1:
        return text, False
    insert_pos = line_end + 1

    block = "\n".join(
        [
            f"{indent}{MARK_THR_ENV}",
            f"{indent}thr_env = os.getenv('THRESHOLD', '').strip()",
            f"{indent}if thr_env:",
            f"{indent}    try:",
            f"{indent}        thr = float(thr_env)",
            f"{indent}    except Exception:",
            f"{indent}        pass",
            f"{indent}# --- /P8b ---",
            "",
        ]
    ) + "\n"

    return text[:insert_pos] + block + text[insert_pos:], True


def inject_cpreg_before_compute_scores(text: str) -> tuple[str, bool]:
    """
    Injeta CPREG dentro do main(), imediatamente antes de compute_scores(...)
    """
    if MARK_CPEG in text:
        return text, False

    m = re.search(
        r'^(\s*)proba\s*,\s*conf\s*,\s*score\s*,\s*gate_used\s*=\s*compute_scores\s*\(',
        text,
        flags=re.M,
    )
    if not m:
        raise SystemExit("Não encontrei 'proba, conf, score, gate_used = compute_scores(' para inserir o CPREG.")

    indent = m.group(1)
    insert_pos = m.start()

    block = "\n".join(
        [
            f"{indent}{MARK_CPEG}",
            f"{indent}# Ajusta CP_ALPHA dinamicamente ANTES do compute_scores (só quando gate_mode == 'cp')",
            f"{indent}if os.getenv('CPREG_ENABLE', '0').strip() == '1' and gate_mode == 'cp':",
            f"{indent}    # Usa o horário do último candle (last_dt) na timezone local para ser determinístico",
            f"{indent}    _sec = (last_dt.hour * 3600) + (last_dt.minute * 60) + last_dt.second",
            f"{indent}    _frac = _sec / 86400.0",
            f"{indent}    _slot = executed_today_count(asset, day) + 1",
            f"{indent}    _a0 = float(os.getenv('CPREG_ALPHA_START', '0.06'))",
            f"{indent}    _a1 = float(os.getenv('CPREG_ALPHA_END',   '0.09'))",
            f"{indent}    _w  = float(os.getenv('CPREG_WARMUP_FRAC',  '0.50'))",
            f"{indent}    _e  = float(os.getenv('CPREG_RAMP_END_FRAC','0.90'))",
            f"{indent}    _m2 = float(os.getenv('CPREG_SLOT2_MULT',   '0.85'))",
            f"{indent}    if _frac <= _w:",
            f"{indent}        _a = _a0",
            f"{indent}    elif _frac >= _e:",
            f"{indent}        _a = _a1",
            f"{indent}    else:",
            f"{indent}        _u = (_frac - _w) / max(1e-9, (_e - _w))",
            f"{indent}        _a = _a0 + (_a1 - _a0) * _u",
            f"{indent}    if _slot >= 2:",
            f"{indent}        _a = _a * _m2",
            f"{indent}    _a = max(0.001, min(0.50, _a))",
            f"{indent}    os.environ['CP_ALPHA'] = '%.4f' % (_a,)",
            f"{indent}# --- /P8b ---",
            "",
        ]
    ) + "\n"

    return text[:insert_pos] + block + text[insert_pos:], True


def main() -> None:
    root = find_repo_root()
    rel = "src/natbin/observe_signal_topk_perday.py"
    path = root / rel

    if not path.exists():
        raise SystemExit(f"Arquivo alvo não existe: {path}")

    print(f"[P8b] repo: {root}")
    print(f"[P8b] alvo: {path}")

    bkp = backup_file(path)
    print(f"[P8b] backup: {bkp}")

    # restaura limpo do origin/main (ou HEAD se não existir)
    ref = "origin/main" if git_ref_exists(root, "origin/main") else "HEAD"
    print(f"[P8b] restaurando arquivo limpo de: {ref}")
    git_restore_file(root, ref, rel)

    text = path.read_text(encoding="utf-8")

    text, n = patch_allow_cp_gate_mode(text)
    print(f"[P8b] patch allow cp gate_mode: {n}")

    text, thr_inj = inject_threshold_env_override(text)
    print(f"[P8b] THRESHOLD env override injected: {thr_inj}")

    text, cpreg_inj = inject_cpreg_before_compute_scores(text)
    print(f"[P8b] CPREG injected: {cpreg_inj}")

    path.write_text(text, encoding="utf-8")

    py_compile.compile(str(path), doraise=True)
    print("[P8b] OK: py_compile passou. Observe loop não deve mais quebrar por NameError.")


if __name__ == "__main__":
    main()