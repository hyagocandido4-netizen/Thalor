from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

KEEP_PACKAGE_READMES = {"README_PACKAGE_FINAL_FIX.md"}
ARCHIVE_DIR = Path("docs/package_history/legacy")
MANIFEST_PATH = Path("docs/package_history/manifest.json")
INDEX_PATH = Path("docs/package_history/README.md")


@dataclass(frozen=True)
class ArchiveResult:
    repo_root: str
    moved: list[str]
    kept: list[str]
    archive_dir: str
    manifest_path: str
    index_path: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def archive_legacy_package_readmes(*, repo_root: str | Path = ".") -> ArchiveResult:
    root = Path(repo_root).resolve()
    archive_dir = root / ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []
    kept: list[str] = []
    for path in sorted(root.glob("README_PACKAGE_*.md")):
        if path.name in KEEP_PACKAGE_READMES:
            kept.append(path.name)
            continue
        dest = archive_dir / path.name
        if dest.exists():
            dest.unlink()
        shutil.move(str(path), str(dest))
        moved.append(path.name)

    manifest = {
        "kind": "package_history_archive",
        "repo_root": str(root),
        "archive_dir": str(archive_dir.relative_to(root).as_posix()),
        "moved": moved,
        "kept": kept,
    }
    (root / MANIFEST_PATH).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# Package history archive",
        "",
        "Legacy `README_PACKAGE_*` files were moved out of the repo root to keep",
        "the operational surface clean. The active package note stays at the root:",
        "`README_PACKAGE_FINAL_FIX.md`.",
        "",
        "## Moved files",
        "",
    ]
    if moved:
        lines.extend(f"- `{name}`" for name in moved)
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Kept at repo root",
        "",
    ])
    if kept:
        lines.extend(f"- `{name}`" for name in kept)
    else:
        lines.append("- none")
    (root / INDEX_PATH).write_text("\n".join(lines) + "\n", encoding="utf-8")

    return ArchiveResult(
        repo_root=str(root),
        moved=moved,
        kept=kept,
        archive_dir=str(ARCHIVE_DIR.as_posix()),
        manifest_path=str(MANIFEST_PATH.as_posix()),
        index_path=str(INDEX_PATH.as_posix()),
    )
