from __future__ import annotations

"""Release hygiene helpers.

Package M1 focuses on one operational problem: sharing/deploying the repo
without accidentally bundling secrets, virtualenvs, git metadata, local
runtime databases, or transient caches.

The helpers in this module provide a canonical, cross-platform implementation
for:

* inspecting the repo and producing a hygiene report
* exporting a clean ZIP bundle ready to be shared/extracted at repo root
* validating that the generated archive contains only allowed files

The bundle intentionally defaults to *rootless* archive entries
(``README.md`` instead of ``Thalor/README.md``) so the ZIP can be extracted
directly into an existing checkout.
"""

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Iterable
import zipfile


TOOL_VERSION = 1

ROOT_EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "ENV",
    "data",
    "runs",
    "secrets",
    "exports",
    "backups",
    "build",
    "dist",
    "htmlcov",
    "artifacts",
    "test_battery",
    "diag_zips",
}

ROOT_EXCLUDED_PREFIXES = (
    "runs_",
    "temp_",
    "tmp_",
    "backup_",
    "artifact_",
    "cache_",
)

ANYWHERE_EXCLUDED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".cache",
    ".tox",
    ".nox",
}

EXCLUDED_RELATIVE_PREFIXES = (
    "configs/variants/",
)

EXCLUDED_RELATIVE_GLOBS = (
    "config/*secret.yaml",
    "config/*secret.yml",
    "config/*secrets.yaml",
    "config/*secrets.yml",
)

EXCLUDED_FILE_GLOBS = (
    ".env",
    ".env.*",
    "*.sqlite3",
    "*.sqlite3-wal",
    "*.sqlite3-shm",
    "*.db",
    "*.joblib",
    "*.pkl",
    "*.pickle",
    "*.npy",
    "*.npz",
    "*.log",
    "*.tmp",
    "*.swp",
    "*.bak_*",
    "*.orig",
    "*.rej",
    "*.whl",
    "coverage.xml",
    "diag_bundle_*.zip",
)

REQUIRED_RELEASE_FILES = (
    "README.md",
    ".env.example",
    "requirements.txt",
    "pyproject.toml",
    "setup.cfg",
    "docker-compose.prod.yml",
    "docs/ALERTING_M7.md",
    "docs/PRODUCTION_CHECKLIST_M7.md",
    "docs/DIAGRAMS_M7.md",
    "docs/INCIDENT_RUNBOOKS_M71.md",
    "docs/LIVE_OPS_HARDENING_M71.md",
    "README_PACKAGE_M7_1_APPEND.md",
    "src/natbin/runtime_app.py",
    "src/natbin/incidents/reporting.py",
    "scripts/tools/release_bundle.py",
    "scripts/tools/incident_ops_smoke.py",
)

SAFE_PRUNE_GLOBS = (
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".cache",
    "build",
    "dist",
    "htmlcov",
    "exports",
    "backups",
    "artifacts",
    "runs_smoke*",
    "temp_*",
    "tmp_*",
    "*.swp",
    "*.tmp",
    "*.egg-info",
    "test_battery",
    "diag_zips",
    "coverage.xml",
    "diag_bundle_*.zip",
)


@dataclass(frozen=True)
class ReleaseReport:
    ok: bool
    repo_root: str
    archive_path: str | None
    archive_sha256: str | None
    included_files: int
    included_bytes: int
    missing_required_files: list[str]
    required_files_verified: list[str]
    warnings: list[str]
    safe_prune_candidates: list[str]
    excluded_summary: dict[str, int]
    sample_entries: list[str]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _as_posix(rel_path: str | Path) -> str:
    raw = str(rel_path).replace("\\", "/").strip("/")
    if raw in {".", ""}:
        return ""
    return raw


def _is_egg_info_dir(name: str) -> bool:
    return str(name).endswith(".egg-info")


def _match_reason(rel_path: str, *, is_dir: bool) -> str | None:
    rel = _as_posix(rel_path)
    if not rel:
        return None

    # configs/variants is intentionally local-only
    for prefix in EXCLUDED_RELATIVE_PREFIXES:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            return "local_variant_config"
    for pattern in EXCLUDED_RELATIVE_GLOBS:
        if fnmatch.fnmatch(rel, pattern):
            return "external_secret_file"

    pp = PurePosixPath(rel)
    parts = pp.parts
    first = parts[0] if parts else ""

    if is_dir:
        name = parts[-1]
        if len(parts) == 1:
            if name in ROOT_EXCLUDED_DIRS:
                return "top_level_runtime_dir"
            if any(name.startswith(prefix) for prefix in ROOT_EXCLUDED_PREFIXES):
                return "top_level_temp_dir"
        if name in ANYWHERE_EXCLUDED_DIRS:
            return "cache_dir"
        if _is_egg_info_dir(name):
            return "egg_info_dir"
        if len(parts) > 1:
            if first in ROOT_EXCLUDED_DIRS:
                return "top_level_runtime_dir"
            if any(first.startswith(prefix) for prefix in ROOT_EXCLUDED_PREFIXES):
                return "top_level_temp_dir"
        return None

    if len(parts) > 1:
        if first in ROOT_EXCLUDED_DIRS:
            return "top_level_runtime_dir"
        if any(first.startswith(prefix) for prefix in ROOT_EXCLUDED_PREFIXES):
            return "top_level_temp_dir"

    parent_parts = parts[:-1]
    for parent in parent_parts:
        if parent in ANYWHERE_EXCLUDED_DIRS:
            return "cache_dir"
        if _is_egg_info_dir(parent):
            return "egg_info_dir"

    name = pp.name
    if name == ".env.example":
        return None
    for pattern in EXCLUDED_FILE_GLOBS:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern):
            if pattern.startswith(".env"):
                return "secret_env"
            if pattern.endswith(".sqlite3") or "sqlite3" in pattern or pattern == "*.db":
                return "db_artifact"
            if pattern in {"*.joblib", "*.pkl", "*.pickle", "*.npy", "*.npz"}:
                return "model_artifact"
            if pattern == "*.log":
                return "log_artifact"
            if pattern in {"*.swp", "*.tmp"}:
                return "editor_temp"
            if pattern in {"*.bak_*", "*.orig", "*.rej"}:
                return "patch_backup"
            if pattern == "*.whl":
                return "wheel_artifact"
            return "excluded_file"

    return None


def should_include_path(rel_path: str | Path, *, is_dir: bool = False) -> bool:
    return _match_reason(rel_path, is_dir=is_dir) is None


def _iter_repo_files(repo_root: Path) -> tuple[list[Path], Counter[str], list[str]]:
    repo_root = Path(repo_root).resolve()
    included: list[Path] = []
    excluded_summary: Counter[str] = Counter()
    samples: list[str] = []

    for dirpath, dirnames, filenames in os.walk(repo_root, topdown=True):
        current = Path(dirpath)
        rel_dir = current.relative_to(repo_root)
        rel_dir_posix = "" if rel_dir == Path(".") else rel_dir.as_posix()

        kept_dirs: list[str] = []
        for dirname in sorted(dirnames):
            rel_child = dirname if not rel_dir_posix else f"{rel_dir_posix}/{dirname}"
            reason = _match_reason(rel_child, is_dir=True)
            if reason:
                excluded_summary[reason] += 1
                if len(samples) < 20:
                    samples.append(rel_child)
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            rel_file = filename if not rel_dir_posix else f"{rel_dir_posix}/{filename}"
            reason = _match_reason(rel_file, is_dir=False)
            if reason:
                excluded_summary[reason] += 1
                if len(samples) < 20:
                    samples.append(rel_file)
                continue
            included.append(repo_root / rel_file)

    included.sort(key=lambda p: p.relative_to(repo_root).as_posix())
    return included, excluded_summary, samples


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _compute_archive_sha256(path: Path) -> str:
    return _file_sha256(path)


def _default_archive_path(repo_root: Path) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return repo_root / "exports" / f"thalor_release_clean_{ts}.zip"


def _normalize_root_prefix(prefix: str | None) -> str:
    raw = _as_posix(prefix or "")
    if not raw:
        return ""
    return raw.strip("/")


def _build_warnings(repo_root: Path) -> list[str]:
    warnings: list[str] = []
    mapping = {
        ".env": "arquivo secreto local presente (.env) — o bundle exclui esse arquivo",
        ".git": "metadados git locais presentes (.git) — o bundle exclui essa pasta",
        ".venv": "virtualenv local presente (.venv) — o bundle exclui essa pasta",
        "data": "artefatos locais presentes em data/ — o bundle exclui essa pasta",
        "runs": "artefatos locais presentes em runs/ — o bundle exclui essa pasta",
        "runs_smoke": "artefatos de smoke presentes em runs_smoke/ — o bundle exclui essa pasta",
        "runs_smoke_daemon_fs": "artefatos de smoke presentes em runs_smoke_daemon_fs/ — o bundle exclui essa pasta",
        "test_battery": "artefatos históricos presentes em test_battery/ — o bundle exclui essa pasta",
        "diag_zips": "bundles de diagnóstico presentes em diag_zips/ — o bundle exclui essa pasta",
        ".pytest_cache": "cache local presente (.pytest_cache) — o bundle exclui essa pasta",
        "configs/variants": "configs locais em configs/variants/ — o bundle exclui essa pasta",
        "secrets": "secret bundles locais presentes em secrets/ — o bundle exclui essa pasta",
        "config/broker_secrets.yaml": "bundle local de credenciais presente em config/broker_secrets.yaml — o bundle exclui esse arquivo",
        "src/natbin.egg-info": "metadata gerada de empacotamento presente em src/natbin.egg-info/ — o bundle exclui essa pasta",
        "coverage.xml": "relatório local coverage.xml presente — o bundle exclui esse arquivo",
    }
    for rel, msg in mapping.items():
        if (repo_root / rel).exists():
            warnings.append(msg)
    for path in sorted(repo_root.glob('diag_bundle_*.zip')):
        warnings.append(f'bundle de diagnóstico local presente em {path.name} — o bundle exclui esse arquivo')
    return warnings


def find_safe_prune_candidates(repo_root: str | Path = ".") -> list[str]:
    root = Path(repo_root).resolve()
    hits: list[str] = []

    # top-level dirs/files
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        name = child.name
        rel = child.relative_to(root).as_posix()
        if child.is_dir():
            if _is_egg_info_dir(name):
                hits.append(rel)
                continue
            if any(fnmatch.fnmatch(name, pat) for pat in SAFE_PRUNE_GLOBS):
                hits.append(rel)
                continue
        else:
            if any(fnmatch.fnmatch(name, pat) for pat in SAFE_PRUNE_GLOBS):
                hits.append(rel)

    # nested egg-info directories and editor temps (skip heavy runtime roots)
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        current = Path(dirpath)
        rel_dir = current.relative_to(root)
        rel_dir_posix = "" if rel_dir == Path(".") else rel_dir.as_posix()

        kept_dirs: list[str] = []
        for dirname in dirnames:
            rel_child = dirname if not rel_dir_posix else f"{rel_dir_posix}/{dirname}"
            if _match_reason(rel_child, is_dir=True):
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for dirname in dirnames:
            if _is_egg_info_dir(dirname):
                rel = dirname if not rel_dir_posix else f"{rel_dir_posix}/{dirname}"
                if rel not in hits:
                    hits.append(rel)

        for filename in filenames:
            if not (fnmatch.fnmatch(filename, "*.swp") or fnmatch.fnmatch(filename, "*.tmp")):
                continue
            rel = filename if not rel_dir_posix else f"{rel_dir_posix}/{filename}"
            if rel not in hits:
                hits.append(rel)

    return sorted(dict.fromkeys(hits))


def build_release_report(repo_root: str | Path = ".") -> ReleaseReport:
    root = Path(repo_root).resolve()
    included, excluded_summary, samples = _iter_repo_files(root)
    included_bytes = sum(p.stat().st_size for p in included)
    included_rel = [p.relative_to(root).as_posix() for p in included]
    missing_required = [p for p in REQUIRED_RELEASE_FILES if p not in included_rel]
    warnings = _build_warnings(root)
    safe_prune = find_safe_prune_candidates(root)
    return ReleaseReport(
        ok=not missing_required,
        repo_root=str(root),
        archive_path=None,
        archive_sha256=None,
        included_files=len(included),
        included_bytes=included_bytes,
        missing_required_files=missing_required,
        required_files_verified=[p for p in REQUIRED_RELEASE_FILES if p in included_rel],
        warnings=warnings,
        safe_prune_candidates=safe_prune,
        excluded_summary=dict(sorted(excluded_summary.items())),
        sample_entries=included_rel[:25],
    )


def _validate_archive_entries(entries: Iterable[str], *, root_prefix: str = "") -> tuple[list[str], list[str]]:
    invalid: list[str] = []
    prefix = _normalize_root_prefix(root_prefix)
    seen = sorted({_as_posix(name) for name in entries if _as_posix(name)})
    logical_seen: list[str] = []

    for name in seen:
        logical_name = name
        if prefix:
            prefix_tag = f"{prefix}/"
            if logical_name == prefix:
                continue
            if logical_name.startswith(prefix_tag):
                logical_name = logical_name[len(prefix_tag):]
        logical_seen.append(logical_name)
        if not should_include_path(logical_name, is_dir=False):
            invalid.append(name)

    missing_required = [p for p in REQUIRED_RELEASE_FILES if p not in logical_seen]
    return invalid, missing_required


def create_release_bundle(
    repo_root: str | Path = ".",
    *,
    out_path: str | Path | None = None,
    root_prefix: str | None = "",
) -> ReleaseReport:
    root = Path(repo_root).resolve()
    report = build_release_report(root)
    if report.missing_required_files:
        return report

    target = Path(out_path).resolve() if out_path else _default_archive_path(root).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    prefix = _normalize_root_prefix(root_prefix)

    included, excluded_summary, _samples = _iter_repo_files(root)

    with tempfile.NamedTemporaryFile(prefix="thalor_release_", suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for file_path in included:
                rel = file_path.relative_to(root).as_posix()
                arcname = f"{prefix}/{rel}" if prefix else rel
                zf.write(file_path, arcname=arcname)

        with zipfile.ZipFile(tmp_path, mode="r") as zf:
            invalid_entries, missing_required = _validate_archive_entries(zf.namelist(), root_prefix=prefix)
            if invalid_entries or missing_required:
                return ReleaseReport(
                    ok=False,
                    repo_root=str(root),
                    archive_path=str(target),
                    archive_sha256=None,
                    included_files=len(included),
                    included_bytes=sum(p.stat().st_size for p in included),
                    missing_required_files=missing_required,
                    required_files_verified=[p for p in REQUIRED_RELEASE_FILES if p not in missing_required],
                    warnings=_build_warnings(root),
                    safe_prune_candidates=find_safe_prune_candidates(root),
                    excluded_summary=dict(sorted(excluded_summary.items())),
                    sample_entries=invalid_entries[:25] if invalid_entries else [],
                )

        if target.exists():
            target.unlink()
        tmp_path.replace(target)

        archive_sha256 = _compute_archive_sha256(target)
        return ReleaseReport(
            ok=True,
            repo_root=str(root),
            archive_path=str(target),
            archive_sha256=archive_sha256,
            included_files=len(included),
            included_bytes=sum(p.stat().st_size for p in included),
            missing_required_files=[],
            required_files_verified=list(REQUIRED_RELEASE_FILES),
            warnings=_build_warnings(root),
            safe_prune_candidates=find_safe_prune_candidates(root),
            excluded_summary=dict(sorted(excluded_summary.items())),
            sample_entries=[p.relative_to(root).as_posix() for p in included[:25]],
        )
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _print_report(report: ReleaseReport, *, as_json: bool) -> None:
    payload = {
        "tool": "natbin.release_hygiene",
        "tool_version": TOOL_VERSION,
        "generated_at_utc": _now_utc(),
        **report.as_dict(),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"[release_hygiene] ok={payload['ok']}")
    if payload["archive_path"]:
        print(f"[release_hygiene] archive={payload['archive_path']}")
    if payload["archive_sha256"]:
        print(f"[release_hygiene] archive_sha256={payload['archive_sha256']}")
    print(f"[release_hygiene] included_files={payload['included_files']}")
    print(f"[release_hygiene] included_bytes={payload['included_bytes']}")
    if payload["missing_required_files"]:
        print("[release_hygiene] missing_required=" + ", ".join(payload["missing_required_files"]))
    if payload["warnings"]:
        for item in payload["warnings"]:
            print(f"[release_hygiene][WARN] {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a clean repo ZIP bundle (Package M1).")
    parser.add_argument("--repo-root", default=".", help="repo root (default: current directory)")
    parser.add_argument("--out", default="", help="target ZIP path (default: exports/thalor_release_clean_<ts>.zip)")
    parser.add_argument("--root-prefix", default="", help="optional prefix inside the zip (default: rootless)")
    parser.add_argument("--dry-run", action="store_true", help="inspect only; do not create the ZIP")
    parser.add_argument("--json", action="store_true", help="print report as JSON")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if args.dry_run:
        report = build_release_report(repo_root)
        _print_report(report, as_json=bool(args.json))
        return 0 if report.ok else 2

    report = create_release_bundle(
        repo_root=repo_root,
        out_path=(args.out or None),
        root_prefix=args.root_prefix,
    )
    _print_report(report, as_json=bool(args.json))
    return 0 if report.ok else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
