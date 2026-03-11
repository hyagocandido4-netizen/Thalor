from __future__ import annotations

from pathlib import Path
import zipfile

from natbin.release_hygiene import build_release_report
from natbin.ops.release_hygiene import create_release_bundle, should_include_path


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")




def _write_m7_required(repo: Path) -> None:
    _write(repo / "docker-compose.prod.yml", "services: {}\n")
    _write(repo / "docs" / "ALERTING_M7.md", "# alerting\n")
    _write(repo / "docs" / "PRODUCTION_CHECKLIST_M7.md", "# checklist\n")
    _write(repo / "docs" / "DIAGRAMS_M7.md", "# diagrams\n")
    _write(repo / "docs" / "INCIDENT_RUNBOOKS_M71.md", "# incidents\n")
    _write(repo / "docs" / "LIVE_OPS_HARDENING_M71.md", "# live ops\n")
    _write(repo / "README_PACKAGE_M7_1_APPEND.md", "# m71\n")
    _write(repo / "src" / "natbin" / "incidents" / "reporting.py", "# placeholder\n")
    _write(repo / "scripts" / "tools" / "incident_ops_smoke.py", "# placeholder\n")

def test_should_include_path_rules() -> None:
    assert should_include_path("README.md")
    assert should_include_path("src/natbin/runtime_app.py")
    assert should_include_path(".env.example")
    assert not should_include_path(".env")
    assert not should_include_path(".env.local")
    assert not should_include_path(".git/config")
    assert not should_include_path(".venv/Lib/site-packages/foo.py")
    assert not should_include_path("data/market.sqlite3")
    assert not should_include_path("runs/logs/runtime.log")
    assert not should_include_path("runs_smoke/status.json")
    assert not should_include_path("configs/variants/local.yaml")
    assert not should_include_path("src/natbin.egg-info/SOURCES.txt")
    assert not should_include_path("foo/bar/__pycache__/a.pyc")
    assert not should_include_path("docs/notes.swp")
    assert not should_include_path("secrets/broker.yaml")
    assert not should_include_path("config/broker_secrets.yaml")


def test_create_release_bundle_excludes_runtime_and_secrets(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    _write(repo / "README.md", "hello")
    _write(repo / ".env.example", "IQ_EMAIL=")
    _write(repo / "requirements.txt", "pytest")
    _write(repo / "pyproject.toml", "[build-system]\nrequires=[]\n")
    _write(repo / "setup.cfg", "[metadata]\nname=natbin\n")
    _write(repo / "src" / "natbin" / "runtime_app.py", "print('ok')\n")
    _write(repo / "scripts" / "tools" / "release_bundle.py", "print('ok')\n")
    _write_m7_required(repo)
    _write(repo / ".env", "secret")
    _write(repo / ".git" / "config", "[core]")
    _write(repo / ".venv" / "pyvenv.cfg", "home=python")
    _write(repo / "data" / "market.sqlite3", "db")
    _write(repo / "runs" / "runtime.log", "log")
    _write(repo / "configs" / "variants" / "local.yaml", "asset: EURUSD-OTC")
    _write(repo / "src" / "natbin.egg-info" / "SOURCES.txt", "src")
    _write(repo / "docs" / "keep.md", "keep me")
    _write(repo / "secrets" / "broker.yaml", "broker:\n  password: secret")
    _write(repo / "config" / "broker_secrets.yaml", "broker:\n  password: secret")

    out = tmp_path / "bundle.zip"
    report = create_release_bundle(repo, out_path=out)
    assert report.ok
    assert out.exists()

    with zipfile.ZipFile(out, "r") as zf:
        names = set(zf.namelist())

    assert "README.md" in names
    assert "docs/keep.md" in names
    assert ".env.example" in names
    assert ".env" not in names
    assert ".git/config" not in names
    assert ".venv/pyvenv.cfg" not in names
    assert "data/market.sqlite3" not in names
    assert "runs/runtime.log" not in names
    assert "configs/variants/local.yaml" not in names
    assert "src/natbin.egg-info/SOURCES.txt" not in names
    assert "secrets/broker.yaml" not in names
    assert "config/broker_secrets.yaml" not in names


def test_build_release_report_warns_about_local_runtime_state(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    _write(repo / "README.md", "hello")
    _write(repo / ".env.example", "IQ_EMAIL=")
    _write(repo / "requirements.txt", "pytest")
    _write(repo / "pyproject.toml", "[build-system]\nrequires=[]\n")
    _write(repo / "setup.cfg", "[metadata]\nname=natbin\n")
    _write(repo / "src" / "natbin" / "runtime_app.py", "print('ok')\n")
    _write(repo / "scripts" / "tools" / "release_bundle.py", "print('ok')\n")
    _write_m7_required(repo)
    _write(repo / ".env", "secret")
    _write(repo / "runs" / "heartbeat.json", "{}")
    _write(repo / ".pytest_cache" / "README.md", "cache")

    report = build_release_report(repo)
    assert report.ok
    assert report.missing_required_files == []
    assert any(".env" in item for item in report.warnings)
    assert any("runs/" in item for item in report.warnings)
    assert ".pytest_cache" in report.safe_prune_candidates
