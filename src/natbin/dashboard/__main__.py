from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    p = argparse.ArgumentParser(
        prog="python -m natbin.dashboard",
        description="Launch the local Thalor dashboard (Streamlit).",
    )
    p.add_argument("--repo-root", default=".", help="Repo root (default: .)")
    p.add_argument(
        "--config",
        default="config/multi_asset.yaml",
        help="Config path relative to repo root (default: config/multi_asset.yaml)",
    )
    p.add_argument("--port", type=int, default=8501, help="Streamlit server port (default: 8501)")
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="Run Streamlit in headless mode (do not try to open browser).",
    )
    args, unknown = p.parse_known_args(argv)
    return args, unknown


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args, unknown = _parse_args(argv)

    try:
        import streamlit  # noqa: F401
    except Exception:
        print(
            "ERROR: streamlit is not installed.\n"
            "Install it in your venv:\n"
            "  pip install streamlit\n",
            file=sys.stderr,
        )
        return 2

    # Import only to locate the file. app.py must not import streamlit at module import time.
    from . import app as dashboard_app

    app_path = Path(dashboard_app.__file__).resolve()

    cmd: list[str] = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "--server.port",
        str(int(args.port)),
    ]
    if args.no_browser:
        cmd += ["--server.headless", "true"]

    cmd += [
        str(app_path),
        "--",
        "--repo-root",
        str(args.repo_root),
        "--config",
        str(args.config),
    ]
    cmd += unknown

    # Let Streamlit own stdout/stderr (useful for troubleshooting).
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
