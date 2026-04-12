from __future__ import annotations

import tempfile
from pathlib import Path

from natbin.config.loader import load_thalor_config
from natbin.dashboard_lite import write_dashboard_lite_report
from natbin.package_history import archive_legacy_package_readmes


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    cfg = load_thalor_config(repo_root=repo, config_path=repo / "config" / "multi_asset.yaml")
    assert cfg.multi_asset.enabled is True
    assert cfg.execution.enabled is False
    assert cfg.decision.threshold == 0.02

    with tempfile.TemporaryDirectory(prefix="thalor_final_fix_") as td:
        tmp = Path(td)
        out = write_dashboard_lite_report(repo_root=repo, config_path=repo / "config" / "multi_asset.yaml", out_path=tmp / "dashboard" / "index.html")
        assert out.exists()
        assert "Thalor Lite Dashboard" in out.read_text(encoding="utf-8")

        clean_root = tmp / "cleanup"
        clean_root.mkdir(parents=True, exist_ok=True)
        (clean_root / "README_PACKAGE_H7_APPEND.md").write_text("old\n", encoding="utf-8")
        (clean_root / "README_PACKAGE_FINAL_FIX.md").write_text("keep\n", encoding="utf-8")
        result = archive_legacy_package_readmes(repo_root=clean_root)
        assert "README_PACKAGE_H7_APPEND.md" in result.moved
        assert (clean_root / "docs" / "package_history" / "legacy" / "README_PACKAGE_H7_APPEND.md").exists()

    print("OK final_fix_package_smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
