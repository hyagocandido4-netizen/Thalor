from __future__ import annotations

import argparse
import html
import json
import threading
from datetime import UTC, datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config.loader import load_thalor_config
from .config.paths import resolve_config_path, resolve_repo_root
from .control.commands import (
    doctor_payload,
    health_payload,
    portfolio_status_payload,
    precheck_payload,
    quota_payload,
    release_payload,
    security_payload,
    status_payload,
)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if isinstance(value, set):
        return sorted(value)
    return str(value)


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=_json_default)


def _normalize_scalar(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, (int, str)):
        return str(value)
    return _dump_json(value)


def _section_state(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return ("unknown", "muted")
    sev = str(payload.get("severity") or "").strip().lower()
    if sev in {"error", "warn", "ok", "info"}:
        label = sev.upper() if sev != "ok" else "OK"
        css = {
            "error": "danger",
            "warn": "warn",
            "ok": "ok",
            "info": "muted",
        }[sev]
        return label, css
    ok = payload.get("ok")
    if ok is True:
        return ("OK", "ok")
    if ok is False:
        return ("ERROR", "danger")
    return ("UNKNOWN", "muted")


def build_dashboard_lite_snapshot(*, repo_root: str | Path = ".", config_path: str | Path | None = None) -> dict[str, Any]:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    cfg = load_thalor_config(config_path=cfg_path, repo_root=root)

    sections: dict[str, Any] = {}
    calls = {
        "health": health_payload,
        "precheck": precheck_payload,
        "security": security_payload,
        "doctor": doctor_payload,
        "quota": quota_payload,
        "release": release_payload,
        "status": status_payload,
        "portfolio": portfolio_status_payload,
    }
    for name, fn in calls.items():
        try:
            sections[name] = fn(repo_root=str(root), config_path=str(cfg_path))
        except Exception as exc:
            sections[name] = {
                "kind": name,
                "ok": False,
                "severity": "error",
                "error": str(exc),
            }

    return {
        "kind": "dashboard_lite",
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "repo_root": str(root),
        "config_path": str(cfg_path),
        "profile": str(cfg.runtime.profile),
        "assets": [
            {
                "asset": str(item.asset),
                "interval_sec": int(item.interval_sec),
                "enabled": bool(item.enabled),
                "cluster_key": str(item.cluster_key),
                "topk_k": int(item.topk_k),
            }
            for item in cfg.assets
        ],
        "sections": sections,
    }


def render_dashboard_lite_html(snapshot: dict[str, Any], *, refresh_sec: float = 15.0) -> str:
    sections = dict(snapshot.get("sections") or {})
    cards: list[str] = []
    for name in ["health", "precheck", "security", "doctor", "quota", "release", "status", "portfolio"]:
        payload = sections.get(name) or {}
        label, css = _section_state(payload)
        summary_bits: list[str] = []
        if isinstance(payload, dict):
            for key in ("message", "profile", "ready_for_live", "ready_for_practice", "portfolio_enabled"):
                if key in payload:
                    summary_bits.append(f"<div class='mini'>{html.escape(key)}: {html.escape(_normalize_scalar(payload.get(key)))}</div>")
        cards.append(
            f"<section class='card'>"
            f"<div class='card-head'><span>{html.escape(name.upper())}</span><span class='pill pill-{css}'>{html.escape(label)}</span></div>"
            f"{''.join(summary_bits) if summary_bits else "<div class='mini'>no summary</div>"}"
            f"</section>"
        )

    asset_rows = []
    for item in list(snapshot.get("assets") or []):
        asset_rows.append(
            "<tr>"
            f"<td>{html.escape(_normalize_scalar(item.get('asset')))}</td>"
            f"<td>{html.escape(_normalize_scalar(item.get('interval_sec')))}</td>"
            f"<td>{html.escape(_normalize_scalar(item.get('cluster_key')))}</td>"
            f"<td>{html.escape(_normalize_scalar(item.get('topk_k')))}</td>"
            f"<td>{html.escape(_normalize_scalar(item.get('enabled')))}</td>"
            "</tr>"
        )

    details = []
    for name, payload in sections.items():
        details.append(
            "<details class='detail'>"
            f"<summary>{html.escape(name)}</summary>"
            f"<pre>{html.escape(_dump_json(payload))}</pre>"
            "</details>"
        )

    refresh_meta = ""
    try:
        refresh_value = float(refresh_sec)
    except Exception:
        refresh_value = 0.0
    if refresh_value > 0.0:
        refresh_meta = f"<meta http-equiv='refresh' content='{max(1, int(refresh_value))}'>"

    return f"""<!doctype html>
<html lang='pt-br'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
{refresh_meta}
<title>Thalor Lite Dashboard</title>
<style>
:root {{
  color-scheme: dark;
  --bg: #081018;
  --panel: #111b27;
  --panel2: #0d1520;
  --line: #1f3144;
  --text: #d6e4f0;
  --muted: #7f95aa;
  --ok: #35d49a;
  --warn: #f0c24f;
  --danger: #ff6f6f;
  --accent: #55c7ff;
}}
body {{ background: radial-gradient(circle at top, #122238, var(--bg) 50%); color: var(--text); font-family: Arial, sans-serif; margin:0; }}
.wrapper {{ max-width: 1280px; margin: 0 auto; padding: 24px; }}
h1 {{ margin: 0 0 6px 0; }}
.sub {{ color: var(--muted); margin-bottom: 20px; }}
.grid {{ display:grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 14px; margin-bottom: 24px; }}
.card {{ background: linear-gradient(180deg, var(--panel), var(--panel2)); border:1px solid var(--line); border-radius: 16px; padding: 16px; box-shadow: 0 8px 24px rgba(0,0,0,.24); }}
.card-head {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:10px; font-weight:bold; }}
.pill {{ border-radius:999px; padding:4px 10px; font-size:12px; font-weight:bold; }}
.pill-ok {{ background: rgba(53,212,154,.12); color: var(--ok); border:1px solid rgba(53,212,154,.25); }}
.pill-warn {{ background: rgba(240,194,79,.12); color: var(--warn); border:1px solid rgba(240,194,79,.25); }}
.pill-danger {{ background: rgba(255,111,111,.12); color: var(--danger); border:1px solid rgba(255,111,111,.25); }}
.pill-muted {{ background: rgba(127,149,170,.12); color: var(--muted); border:1px solid rgba(127,149,170,.25); }}
.mini {{ color: var(--muted); font-size: 13px; margin: 4px 0; }}
.section {{ background: linear-gradient(180deg, rgba(17,27,39,.92), rgba(13,21,32,.92)); border:1px solid var(--line); border-radius: 16px; padding: 18px; margin-bottom: 20px; }}
.table-wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse: collapse; }}
th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); font-size:14px; }}
th {{ color: var(--accent); font-weight:600; }}
details.detail {{ margin-top: 10px; border:1px solid var(--line); border-radius:12px; padding:10px 12px; background: rgba(0,0,0,.16); }}
details.detail summary {{ cursor:pointer; color: var(--accent); }}
pre {{ white-space: pre-wrap; word-break: break-word; color: #d5deea; font-size: 12px; }}
</style>
</head>
<body>
<div class='wrapper'>
  <h1>Thalor Lite Dashboard</h1>
  <div class='sub'>Profile: <strong>{html.escape(_normalize_scalar(snapshot.get('profile')))}</strong> · Generated at {html.escape(_normalize_scalar(snapshot.get('generated_at_utc')))}</div>
  <div class='grid'>
    {''.join(cards)}
  </div>
  <section class='section'>
    <h2>Assets</h2>
    <div class='table-wrap'>
      <table>
        <thead><tr><th>Asset</th><th>Interval</th><th>Cluster</th><th>TopK</th><th>Enabled</th></tr></thead>
        <tbody>{''.join(asset_rows) if asset_rows else '<tr><td colspan="5">no assets</td></tr>'}</tbody>
      </table>
    </div>
  </section>
  <section class='section'>
    <h2>Raw sections</h2>
    {''.join(details)}
  </section>
</div>
</body>
</html>
"""


def write_dashboard_lite_report(*, repo_root: str | Path = ".", config_path: str | Path | None = None, out_path: str | Path | None = None, refresh_sec: float = 15.0) -> Path:
    root = resolve_repo_root(repo_root=repo_root, config_path=config_path)
    cfg_path = resolve_config_path(repo_root=root, config_path=config_path)
    output = Path(out_path) if out_path is not None else root / "runs" / "reports" / "dashboard_lite" / "index.html"
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot = build_dashboard_lite_snapshot(repo_root=root, config_path=cfg_path)
    output.write_text(render_dashboard_lite_html(snapshot, refresh_sec=refresh_sec), encoding="utf-8")
    return output


def _serve_directory(directory: Path, *, port: int) -> int:
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    server = ThreadingHTTPServer(("0.0.0.0", int(port)), handler)
    try:
        server.serve_forever(poll_interval=0.5)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate and optionally serve the Thalor Lite Dashboard.")
    p.add_argument("--repo-root", default=".")
    p.add_argument("--config", default="config/multi_asset.yaml")
    p.add_argument("--out", default="runs/reports/dashboard_lite/index.html")
    p.add_argument("--refresh-sec", type=float, default=15.0)
    p.add_argument("--serve", action="store_true")
    p.add_argument("--port", type=int, default=8501)
    p.add_argument("--loop-sec", type=float, default=15.0)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = _parse_args(argv)
    root = resolve_repo_root(repo_root=ns.repo_root, config_path=ns.config)
    cfg_path = resolve_config_path(repo_root=root, config_path=ns.config)
    out_path = write_dashboard_lite_report(repo_root=root, config_path=cfg_path, out_path=ns.out, refresh_sec=ns.refresh_sec)
    print(out_path)
    if not ns.serve:
        return 0

    stop_event = threading.Event()

    def _refresh_loop() -> None:
        while not stop_event.wait(max(1.0, float(ns.loop_sec))):
            try:
                write_dashboard_lite_report(repo_root=root, config_path=cfg_path, out_path=out_path, refresh_sec=ns.refresh_sec)
            except Exception:
                pass

    worker = threading.Thread(target=_refresh_loop, name="dashboard-lite-refresh", daemon=True)
    worker.start()
    try:
        return _serve_directory(out_path.parent, port=int(ns.port))
    finally:
        stop_event.set()
        worker.join(timeout=1.0)


if __name__ == "__main__":
    raise SystemExit(main())
