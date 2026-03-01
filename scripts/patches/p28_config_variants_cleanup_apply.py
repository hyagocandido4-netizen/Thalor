#!/usr/bin/env python
"""P28 - Clean root config sprawl (move config_*.yaml into configs/variants) + ignore them by default.

Goal:
  Reduce repo-root clutter and avoid accidental commits of local config snapshots.

What it does:
  1) Moves repo-root files matching config_*.yaml / config_*.yml into configs/variants/
     (preserves filenames; adds timestamp suffix if collision).
  2) Ensures .gitignore ignores:
        - root-level config_*.yaml / config_*.yml
        - configs/variants/ (treated as local-only by default)
  3) Writes configs/README.md describing the convention.

If you DO want to version a variant config:
  - move it from configs/variants/ to configs/ (or remove the ignore rule).

Safe to re-run: creates backups and is idempotent.
"""

from __future__ import annotations

import datetime as _dt
import shutil
from pathlib import Path


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _backup(path: Path) -> Path:
    bak = path.with_suffix(path.suffix + f".bak_{_ts()}")
    shutil.copy2(path, bak)
    return bak


def _repo_root_from_here() -> Path:
    here = Path(__file__).resolve()
    root = here.parents[2]
    if not (root / "src" / "natbin").exists():
        raise SystemExit(f"[P28] ERROR: repo root not found from {here}")
    return root


def _ensure_gitignore_rules(path: Path) -> bool:
    rules = [
        "# Local config snapshots (keep repo root clean)",
        "/config_*.yaml",
        "/config_*.yml",
        "# Local-only config variants (not versioned by default)",
        "configs/variants/",
    ]

    if not path.exists():
        path.write_text("\n".join(rules) + "\n", encoding="utf-8")
        return True

    txt = path.read_text(encoding="utf-8")
    changed = False
    for r in rules:
        if r not in txt:
            txt = txt.rstrip() + "\n" + r + "\n"
            changed = True

    if changed:
        _backup(path)
        path.write_text(txt, encoding="utf-8")
    return changed


CONFIGS_README = """# Configs

Conventions used in this repo:

- `config.yaml` (repo root) is the *main* config used by `observe_loop` and most CLI runs.
- Preset configs that are meant to be versioned live under `configs/`.
  Example: `configs/wr_meta_hgb_3x20.yaml`.

Local experimentation snapshots should NOT live in the repo root.

## Local-only variants

If you generate local variants (e.g. `config_META.yaml`, `config_k2.yaml`), place them in:

- `configs/variants/`

By default, `configs/variants/` is gitignored so you don't accidentally commit local configs.

If you decide a variant config is useful long-term:

- Move it to `configs/` (or remove the ignore rule for that file).

## Sharing a sanitized zip

Use:

- `scripts/tools/export_repo_sanitized.ps1`

"""


def main() -> None:
    root = _repo_root_from_here()

    # 1) Move root config_*.yaml/yml
    variants_dir = root / "configs" / "variants"
    variants_dir.mkdir(parents=True, exist_ok=True)

    moved = []
    for pat in ("config_*.yaml", "config_*.yml"):
        for p in root.glob(pat):
            if not p.is_file():
                continue
            dest = variants_dir / p.name
            if dest.exists():
                dest = variants_dir / f"{p.stem}_DUP_{_ts()}{p.suffix}"
            shutil.move(str(p), str(dest))
            moved.append((p.name, str(dest.relative_to(root))))

    # 2) Update .gitignore
    gi = root / ".gitignore"
    gi_changed = _ensure_gitignore_rules(gi)

    # 3) configs README
    readme = root / "configs" / "README.md"
    if readme.exists():
        _backup(readme)
    readme.write_text(CONFIGS_README, encoding="utf-8")

    print(f"[P28] moved {len(moved)} root config_* files into configs/variants/")
    for src, dst in moved:
        print(f"  - {src} -> {dst}")
    print(f"[P28] .gitignore updated: {gi_changed}")
    print(f"[P28] wrote {readme}")

    print("[P28] Próximo passo sugerido:")
    print("  - git status (config_* deve sumir do root e configs/variants fica ignorado)")


if __name__ == "__main__":
    main()
