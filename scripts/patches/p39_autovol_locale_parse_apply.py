"""
P39 - auto_volume locale-safe parsing + selfcheck coverage

Why:
- On pt-BR Windows, PowerShell commonly converts numbers to strings using comma decimal
  (e.g. "0,07"). Python's float("0,07") fails.
- observe_loop_auto.ps1 sets env vars from JSON using [string] casting -> culture-aware -> comma.
- auto_volume.py previously used float(v) directly when reading env -> it would silently fall back
  to defaults, making autovol unstable / "forget" previous params.

This patch:
1) Makes auto_volume._f/_i tolerant to comma decimals and blank strings.
2) Extends scripts/tools/selfcheck_repo.py to:
   - run the envutil import-completeness check (was defined but never called)
   - validate auto_volume parses "0,07" as 0.07
   - (fix) skip envutil.py in that check (since it defines env_* itself)

Safe: no trading logic changes; only parsing + CI/selfcheck coverage.

Usage:
  Save as: scripts/patches/p39_autovol_locale_parse_apply.py
  Run:
    .\.venv\Scripts\python.exe .\scripts\patches\p39_autovol_locale_parse_apply.py
  Then:
    .\.venv\Scripts\python.exe .\scripts\tools\selfcheck_repo.py
    pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once
"""

from __future__ import annotations

import datetime as _dt
import py_compile
import re
from pathlib import Path


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup(p: Path) -> Path:
    bak = p.with_suffix(p.suffix + f".bak_{_ts()}")
    bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return bak


def _patch_auto_volume(text: str) -> tuple[str, bool]:
    """
    Patch src/natbin/auto_volume.py:
      - _f: parse comma decimals
      - _i: tolerate "2000,0" etc
    """
    changed = False

    # fast-idempotency: if _f already replaces comma, skip
    if "def _f" in text and 'replace(",", ".")' in text:
        # already patched
        pass
    else:
        # Prefer exact-match replace for robustness
        old_f = (
            "def _f(v: Any, default: float) -> float:\n"
            "    try:\n"
            "        return float(v)\n"
            "    except Exception:\n"
            "        return float(default)\n"
        )
        new_f = (
            "def _f(v: Any, default: float) -> float:\n"
            "    \"\"\"Parse float values that may come from PowerShell/ENV.\n\n"
            "    In pt-BR locales PowerShell commonly serializes decimals with comma\n"
            "    (e.g. '0,07'). Python's float() does NOT accept that, so we normalize.\n"
            "    \"\"\"\n"
            "    try:\n"
            "        if isinstance(v, str):\n"
            "            s = v.strip()\n"
            "            if s == \"\":\n"
            "                raise ValueError(\"empty\")\n"
            "            s = s.replace(\",\", \".\")\n"
            "            return float(s)\n"
            "        return float(v)\n"
            "    except Exception:\n"
            "        return float(default)\n"
        )

        if old_f in text:
            text = text.replace(old_f, new_f)
            changed = True
        else:
            # fallback regex (be conservative)
            text2, n = re.subn(
                r"def _f\([^\n]*\):\n(?:\s+.*\n){0,30}?\s*except Exception:\n\s*return float\(default\)\n",
                new_f + "\n",
                text,
                count=1,
            )
            if n:
                text = text2
                changed = True

    # _i patch
    if "def _i" in text and "int(float(" in text:
        pass
    else:
        old_i = (
            "def _i(v: Any, default: int) -> int:\n"
            "    try:\n"
            "        return int(v)\n"
            "    except Exception:\n"
            "        return int(default)\n"
        )
        new_i = (
            "def _i(v: Any, default: int) -> int:\n"
            "    \"\"\"Parse int values that may come from PowerShell/ENV.\"\"\"\n"
            "    try:\n"
            "        if isinstance(v, str):\n"
            "            s = v.strip()\n"
            "            if s == \"\":\n"
            "                raise ValueError(\"empty\")\n"
            "            # Some values might look like '2000,0' due to locale; be tolerant.\n"
            "            s = s.replace(\",\", \".\")\n"
            "            return int(float(s))\n"
            "        return int(v)\n"
            "    except Exception:\n"
            "        return int(default)\n"
        )
        if old_i in text:
            text = text.replace(old_i, new_i)
            changed = True
        else:
            text2, n = re.subn(
                r"def _i\([^\n]*\):\n(?:\s+.*\n){0,30}?\s*except Exception:\n\s*return int\(default\)\n",
                new_i + "\n",
                text,
                count=1,
            )
            if n:
                text = text2
                changed = True

    return text, changed


def _patch_selfcheck(text: str) -> tuple[str, bool]:
    """
    Patch scripts/tools/selfcheck_repo.py:
      - Call _check_envutil_imports(root) (it existed but was never invoked)
      - Add a strict comma-decimal parse test for auto_volume._f
      - Fix _check_envutil_imports to skip envutil.py itself
    """
    changed = False

    # 1) ensure envutil.py is skipped in the envutil imports scan
    if 'if py.name == "envutil.py"' not in text:
        lines = text.splitlines()
        out = []
        inserted = False
        for l in lines:
            out.append(l)
            if (not inserted) and l.strip() == 'for py in src.rglob("*.py"):':
                out.append('        if py.name == "envutil.py":')
                out.append("            continue")
                inserted = True
                changed = True
        text = "\n".join(out)

    # 2) call _check_envutil_imports(root) inside main (before ALL OK)
    if "_check_envutil_imports(root)" not in text:
        lines = text.splitlines()
        out = []
        inserted = False
        for l in lines:
            if (not inserted) and l.strip() == 'print("[selfcheck] ALL OK")':
                block = [
                    "",
                    "    # envutil import completeness (ensures env_* used are imported)",
                    "    try:",
                    "        _check_envutil_imports(root)",
                    '        _ok("envutil imports ok")',
                    "    except SystemExit:",
                    "        raise",
                    "    except Exception as e:",
                    '        _fail(f\"envutil imports check failed: {e}\")',
                    "",
                    "    # pt-BR decimal comma safety (auto_volume)",
                    "    try:",
                    "        from natbin import auto_volume as _av",
                    '        v = _av._f(\"0,07\", 0.0)',
                    "        if abs(v - 0.07) > 1e-9:",
                    '            _fail(f\"auto_volume._f does not parse comma decimals: got {v}\")',
                    '        _ok(\"auto_volume locale float parse ok\")',
                    "    except SystemExit:",
                    "        raise",
                    "    except Exception as e:",
                    '        _fail(f\"auto_volume locale parse check failed: {e}\")',
                    "",
                ]
                out.extend(block)
                inserted = True
                changed = True
            out.append(l)
        text = "\n".join(out)

    return text, changed


def main() -> None:
    repo = Path(__file__).resolve().parents[2]
    auto_volume_py = repo / "src" / "natbin" / "auto_volume.py"
    selfcheck_py = repo / "scripts" / "tools" / "selfcheck_repo.py"

    touched = []

    # auto_volume
    if auto_volume_py.exists():
        txt = auto_volume_py.read_text(encoding="utf-8")
        new_txt, ch = _patch_auto_volume(txt)
        if ch:
            bak = _backup(auto_volume_py)
            auto_volume_py.write_text(new_txt, encoding="utf-8")
            touched.append(("auto_volume.py", str(bak)))
    else:
        raise SystemExit(f"[P39][FAIL] missing file: {auto_volume_py}")

    # selfcheck_repo
    if selfcheck_py.exists():
        txt = selfcheck_py.read_text(encoding="utf-8")
        new_txt, ch = _patch_selfcheck(txt)
        if ch:
            bak = _backup(selfcheck_py)
            selfcheck_py.write_text(new_txt, encoding="utf-8")
            touched.append(("selfcheck_repo.py", str(bak)))
    else:
        raise SystemExit(f"[P39][FAIL] missing file: {selfcheck_py}")

    # compile
    py_compile.compile(str(auto_volume_py), doraise=True)
    py_compile.compile(str(selfcheck_py), doraise=True)

    print("[P39] OK.")
    if touched:
        for name, bak in touched:
            print(f"[P39] patched: {name} (backup={bak})")
    else:
        print("[P39] no changes needed (already applied)")

    print("[P39] Smoke-tests sugeridos:")
    print(r"  - .\.venv\Scripts\python.exe .\scripts\tools\selfcheck_repo.py")
    print(r"  - pwsh -ExecutionPolicy Bypass -File .\scripts\scheduler\observe_loop_auto.ps1 -Once")


if __name__ == "__main__":
    main()