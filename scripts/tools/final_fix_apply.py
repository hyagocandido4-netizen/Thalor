from __future__ import annotations

import argparse
import json

from natbin.package_history import archive_legacy_package_readmes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Apply final repo cleanup tasks.")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--json", action="store_true")
    ns = p.parse_args(argv)
    result = archive_legacy_package_readmes(repo_root=ns.repo_root)
    if ns.json:
        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"Moved {len(result.moved)} legacy package notes to {result.archive_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
