#!/usr/bin/env python3
"""P24 - Repo self-check script + CI wiring

Adds:
  - scripts/tools/selfcheck_repo.py : lightweight runtime checks that catch
    common breakages (missing exports / wrong signature / missing AsStr).

Patches:
  - .github/workflows/ci.yml : runs the selfcheck after compileall.

Idempotent:
  - Won't overwrite an existing selfcheck file.
  - Won't duplicate CI step if already present.

Run:
  python .\scripts\patches\p24_repo_selfcheck_apply.py
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path


SELFCHK_REL = Path("scripts/tools/selfcheck_repo.py")


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(12):
        if (cur / "src" / "natbin").is_dir() and (cur / "pyproject.toml").exists():
            return cur
        if (cur / "src" / "natbin").is_dir() and (cur / ".github").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    raise SystemExit("[P24] ERRO: não encontrei a raiz do repo.")


def _backup(path: Path) -> Path:
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak_{ts}")
    bak.write_bytes(path.read_bytes())
    return bak


def ensure_selfcheck(root: Path) -> None:
    target = root / SELFCHK_REL
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        print(f"[P24] selfcheck já existe (skip): {target}")
        return

    content = r'''#!/usr/bin/env python3
"""Repo self-check (fast, deterministic).

Goal: fail fast on the most common/most painful breakages:
  - observe importing gate_meta symbols that no longer exist
  - gate_meta API drift (train_base_cal_iso_meta kwargs)
  - PowerShell helper function referenced but not defined (AsStr)

This script must be:
  - fast (< 2s)
  - no network
  - no dependency on data/ or runs/

CI note:
  - We do NOT assume the package is installed. We inject <repo>/src into sys.path.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path


def _fail(msg: str) -> None:
    print(f"[selfcheck][FAIL] {msg}")
    raise SystemExit(2)


def _ok(msg: str) -> None:
    print(f"[selfcheck][OK] {msg}")


def _ensure_src_on_path(repo_root: Path) -> None:
    src = repo_root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def check_gate_meta_api() -> None:
    try:
        import natbin.gate_meta as gm
    except Exception as e:
        _fail(f"import natbin.gate_meta falhou: {e}")

    for name in ["compute_scores", "train_base_cal_iso_meta", "GATE_VERSION", "META_FEATURES"]:
        if not hasattr(gm, name):
            _fail(f"natbin.gate_meta não exporta {name}")

    # Signature guard: we already broke this once.
    fn = gm.train_base_cal_iso_meta
    sig = inspect.signature(fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

    if not has_var_kw:
        # Accept either naming convention.
        if ("meta_model" not in sig.parameters) and ("meta_model_type" not in sig.parameters):
            _fail(
                "train_base_cal_iso_meta não aceita meta_model/meta_model_type nem **kwargs; "
                "observe pode quebrar." 
            )

    _ok("gate_meta API ok")


def check_observe_imports() -> None:
    try:
        import natbin.observe_signal_topk_perday as _obs  # noqa: F401
    except Exception as e:
        _fail(f"import natbin.observe_signal_topk_perday falhou: {e}")

    _ok("observe_signal_topk_perday import ok")


def check_ps_helpers(repo_root: Path) -> None:
    ps = repo_root / "scripts" / "scheduler" / "observe_loop_auto.ps1"
    if not ps.exists():
        _ok("observe_loop_auto.ps1 não encontrado (skip)")
        return

    txt = ps.read_text(encoding="utf-8", errors="replace")

    # Only fail if AsStr is referenced and not defined.
    if "AsStr" in txt and "function AsStr" not in txt:
        _fail("observe_loop_auto.ps1 referencia 'AsStr' mas não define function AsStr")

    _ok("observe_loop_auto.ps1 helpers ok")


def main() -> None:
    # scripts/tools/selfcheck_repo.py -> repo root: parents[2]
    repo_root = Path(__file__).resolve().parents[2]
    _ensure_src_on_path(repo_root)

    check_gate_meta_api()
    check_observe_imports()
    check_ps_helpers(repo_root)

    print("[selfcheck] ALL OK")


if __name__ == "__main__":
    main()
'''

    target.write_text(content, encoding="utf-8")
    print(f"[P24] OK escreveu {target}")


def patch_ci(root: Path) -> None:
    ci = root / ".github" / "workflows" / "ci.yml"
    if not ci.exists():
        print("[P24] CI workflow não encontrado (skip).")
        return

    text = ci.read_text(encoding="utf-8", errors="replace")
    if "selfcheck_repo.py" in text:
        print("[P24] CI já contém selfcheck (skip).")
        return

    # Insert after compileall step if possible.
    needle = "Sanity: compile all modules"
    idx = text.find(needle)

    if idx != -1:
        after = text.find("- name:", idx + 1)
        insertion_point = after if after != -1 else len(text)
    else:
        insertion_point = len(text)

    step = "\n      - name: Repo selfcheck\n        run: python scripts/tools/selfcheck_repo.py\n"

    bak = _backup(ci)
    ci.write_text(text[:insertion_point] + step + text[insertion_point:], encoding="utf-8")
    print(f"[P24] OK patch CI: {ci} (backup={bak})")


def main() -> None:
    root = _find_repo_root(Path(__file__).resolve().parent)
    ensure_selfcheck(root)
    patch_ci(root)
    print("[P24] OK.")
    print("[P24] Teste sugerido:")
    print("  - python scripts/tools/selfcheck_repo.py")


if __name__ == "__main__":
    main()
