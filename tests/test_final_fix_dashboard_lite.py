from __future__ import annotations

from pathlib import Path

from natbin.dashboard_lite import render_dashboard_lite_html, write_dashboard_lite_report
from natbin.package_history import archive_legacy_package_readmes


def test_render_dashboard_lite_html_contains_cards_and_details() -> None:
    html = render_dashboard_lite_html(
        {
            "profile": "multi",
            "generated_at_utc": "2026-03-26T00:00:00+00:00",
            "assets": [{"asset": "EURUSD-OTC", "interval_sec": 300, "cluster_key": "fx", "topk_k": 3, "enabled": True}],
            "sections": {
                "health": {"severity": "ok", "message": "healthy"},
                "portfolio": {"severity": "warn", "message": "waiting data"},
            },
        },
        refresh_sec=5.0,
    )

    assert "Thalor Lite Dashboard" in html
    assert "EURUSD-OTC" in html
    assert "healthy" in html
    assert "portfolio" in html.lower()
    assert "http-equiv='refresh'" in html


def test_write_dashboard_lite_report_writes_file(tmp_path: Path, monkeypatch) -> None:
    import natbin.dashboard_lite as dl

    monkeypatch.setattr(
        dl,
        "build_dashboard_lite_snapshot",
        lambda **_: {
            "profile": "default",
            "generated_at_utc": "2026-03-26T00:00:00+00:00",
            "assets": [],
            "sections": {},
        },
    )

    out = write_dashboard_lite_report(repo_root=tmp_path, config_path=tmp_path / "config.yaml", out_path=tmp_path / "out" / "index.html")
    assert out.exists()
    assert "Thalor Lite Dashboard" in out.read_text(encoding="utf-8")


def test_archive_legacy_package_readmes_moves_old_files(tmp_path: Path) -> None:
    (tmp_path / "README_PACKAGE_H7_APPEND.md").write_text("old\n", encoding="utf-8")
    (tmp_path / "README_PACKAGE_FINAL_FIX.md").write_text("keep\n", encoding="utf-8")

    result = archive_legacy_package_readmes(repo_root=tmp_path)

    assert "README_PACKAGE_H7_APPEND.md" in result.moved
    assert "README_PACKAGE_FINAL_FIX.md" in result.kept
    assert not (tmp_path / "README_PACKAGE_H7_APPEND.md").exists()
    assert (tmp_path / "docs" / "package_history" / "legacy" / "README_PACKAGE_H7_APPEND.md").exists()
