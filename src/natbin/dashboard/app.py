from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DashArgs:
    repo_root: str = "."
    config: str = "config/multi_asset.yaml"
    refresh_sec: float = 3.0
    max_events: int = 200
    max_signals: int = 200


def _parse_dash_args(argv: list[str]) -> DashArgs:
    # Keep this parser lenient: Streamlit may add its own args.
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--repo-root", default=".")
    p.add_argument("--config", default="config/multi_asset.yaml")
    p.add_argument("--refresh-sec", type=float, default=3.0)
    p.add_argument("--max-events", type=int, default=200)
    p.add_argument("--max-signals", type=int, default=200)
    ns, _unknown = p.parse_known_args(argv)
    try:
        refresh = float(ns.refresh_sec)
    except Exception:
        refresh = 0.0
    if refresh < 0:
        refresh = 0.0
    return DashArgs(
        repo_root=str(ns.repo_root),
        config=str(ns.config),
        refresh_sec=refresh,
        max_events=max(10, int(ns.max_events or 200)),
        max_signals=max(10, int(ns.max_signals or 200)),
    )


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _tail_jsonl(path: Path, n: int) -> list[dict[str, Any]]:
    if n <= 0:
        return []
    if not path.exists():
        return []
    out: deque[dict[str, Any]] = deque(maxlen=n)
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    # Keep raw line if JSON is malformed.
                    out.append({"_raw": line})
    except Exception:
        return []
    return list(out)


def _sqlite_connect_ro(db_path: Path) -> sqlite3.Connection:
    # Prefer read-only mode (avoids locks).
    # On Windows, `immutable=1` is safe only if file never changes; avoid it.
    uri = f"file:{db_path.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=1.0)


def _sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [r[0] for r in cur.fetchall()]


def _sqlite_table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    # row = (cid, name, type, notnull, dflt_value, pk)
    return [r[1] for r in cur.fetchall()]


def _sqlite_fetch_recent(
    conn: sqlite3.Connection, table: str, *, limit: int = 200
) -> list[dict[str, Any]]:
    cols = _sqlite_table_columns(conn, table)
    if not cols:
        return []
    order_col = None
    for cand in ("ts", "observed_at_utc", "created_at_utc", "id"):
        if cand in cols:
            order_col = cand
            break

    if order_col:
        q = f'SELECT * FROM "{table}" ORDER BY "{order_col}" DESC LIMIT ?'
        rows = conn.execute(q, (int(limit),)).fetchall()
    else:
        q = f'SELECT * FROM "{table}" LIMIT ?'
        rows = conn.execute(q, (int(limit),)).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({cols[i]: r[i] for i in range(min(len(cols), len(r)))})
    return out


def run() -> None:
    # Heavy deps only inside the Streamlit runtime.
    import streamlit as st
    import streamlit.components.v1 as components

    try:
        import pandas as pd
    except Exception:
        pd = None  # type: ignore

    # Parse defaults (args passed via: streamlit run app.py -- --repo-root ...).
    args = _parse_dash_args(sys.argv[1:])

    st.set_page_config(page_title="Thalor Dashboard", layout="wide")
    st.title("Thalor — Dashboard Local")

    # Sidebar
    st.sidebar.header("Configuração")
    repo_root = st.sidebar.text_input("repo_root", value=str(args.repo_root))
    config_path = st.sidebar.text_input("config", value=str(args.config))
    refresh_sec = st.sidebar.number_input(
        "auto-refresh (segundos; 0 = desligado)",
        min_value=0.0,
        max_value=120.0,
        value=float(args.refresh_sec),
        step=1.0,
    )
    max_signals = int(
        st.sidebar.number_input("max sinais (tabela)", min_value=10, max_value=5000, value=int(args.max_signals), step=10)
    )
    max_events = int(
        st.sidebar.number_input("max eventos execução (jsonl)", min_value=10, max_value=5000, value=int(args.max_events), step=10)
    )
    auto_refresh = st.sidebar.checkbox("auto refresh", value=(refresh_sec > 0.0))
    _ = st.sidebar.button("Refresh agora")

    repo = Path(repo_root).expanduser().resolve()
    cfg = (repo / config_path).resolve() if not Path(config_path).is_absolute() else Path(config_path).resolve()

    st.caption(f"Repo: `{repo}`  |  Config: `{cfg}`")

    if auto_refresh and refresh_sec > 0:
        # Simple client-side reload without extra deps.
        ms = int(float(refresh_sec) * 1000)
        components.html(
            f"<script>setTimeout(function(){{window.location.reload();}}, {ms});</script>",
            height=0,
        )

    # --- Control plane (health / precheck) ---
    st.subheader("Control plane")

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("**Health**")
        try:
            from natbin.control.commands import health_payload

            health = health_payload(repo_root=str(repo), config_path=str(cfg))
            st.json(health, expanded=False)
        except Exception as e:
            st.warning(f"health_payload falhou: {e}")

    with col_b:
        st.markdown("**Precheck**")
        try:
            from natbin.control.commands import precheck_payload

            pre = precheck_payload(repo_root=str(repo), config_path=str(cfg))
            st.json(pre, expanded=False)
        except Exception as e:
            st.warning(f"precheck_payload falhou: {e}")

    # --- Portfolio status / last cycle ---
    st.subheader("Portfolio (multi-asset)")

    status: dict[str, Any] | None = None
    try:
        from natbin.control.commands import portfolio_status_payload

        status = portfolio_status_payload(repo_root=str(repo), config_path=str(cfg))
    except Exception as e:
        st.error(f"portfolio_status_payload falhou: {e}")

    if status:
        # High-level summary
        ma = status.get("multi_asset") or {}
        latest_cycle = status.get("latest_cycle") or {}
        latest_alloc = status.get("latest_allocation") or status.get("latest_allocation") or {}

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("multi_asset.enabled", str(ma.get("enabled")))
        c2.metric("max_parallel_assets", str(ma.get("max_parallel_assets")))
        c3.metric("stagger_sec", str(ma.get("stagger_sec")))
        c4.metric("portfolio_topk_total", str(ma.get("portfolio_topk_total")))

        st.markdown("**Último ciclo**")
        if latest_cycle:
            st.json(
                {
                    "cycle_id": latest_cycle.get("cycle_id"),
                    "started_at_utc": latest_cycle.get("started_at_utc"),
                    "finished_at_utc": latest_cycle.get("finished_at_utc"),
                    "ok": latest_cycle.get("ok"),
                    "message": latest_cycle.get("message"),
                    "errors": latest_cycle.get("errors"),
                    "gates": latest_cycle.get("gates"),
                },
                expanded=False,
            )
        else:
            st.info("Nenhum ciclo encontrado (ainda não existe runs/portfolio_cycle_latest.json).")

        # Scopes table
        scopes = status.get("scopes") or []
        if scopes:
            st.markdown("**Scopes**")
            rows = []
            for s in scopes:
                scope = s.get("scope") or {}
                dp = s.get("data_paths") or {}
                rp = s.get("runtime_paths") or {}
                rows.append(
                    {
                        "scope_tag": scope.get("scope_tag"),
                        "asset": scope.get("asset"),
                        "interval_sec": scope.get("interval_sec"),
                        "db_path": dp.get("db_path"),
                        "dataset_path": dp.get("dataset_path"),
                        "signals_db_path": rp.get("signals_db_path"),
                        "state_db_path": rp.get("state_db_path"),
                    }
                )
            if pd is not None:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            else:
                st.json(rows, expanded=False)

        # Allocation summary
        if latest_alloc:
            st.markdown("**Última alocação**")
            st.json(
                {
                    "allocation_id": latest_alloc.get("allocation_id"),
                    "at_utc": latest_alloc.get("at_utc"),
                    "max_select": latest_alloc.get("max_select"),
                    "selected": latest_alloc.get("selected"),
                    "suppressed": latest_alloc.get("suppressed"),
                },
                expanded=False,
            )

    # --- Per-scope: decision + signals ---
    st.subheader("Signals / Decisions por scope")

    scope_tags: list[str] = []
    runtime_paths_by_tag: dict[str, dict[str, Any]] = {}
    if status and status.get("scopes"):
        for s in status["scopes"]:
            scope = s.get("scope") or {}
            tag = scope.get("scope_tag")
            if not tag:
                continue
            scope_tags.append(tag)
            runtime_paths_by_tag[tag] = s.get("runtime_paths") or {}

    chosen_tag = st.selectbox("scope_tag", options=scope_tags if scope_tags else ["(none)"])

    if chosen_tag and chosen_tag != "(none)":
        rp = runtime_paths_by_tag.get(chosen_tag, {})
        signals_db = rp.get("signals_db_path")
        state_db = rp.get("state_db_path")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Decision (latest)**")
            dec_path = repo / "runs" / "decisions" / f"decision_latest_{chosen_tag}.json"
            dec = _read_json(dec_path)
            if dec is None:
                st.info(f"Não encontrei {dec_path}")
            else:
                # Small summary first
                raw = dec.get("raw") if isinstance(dec, dict) else None
                summary = {
                    "asset": dec.get("asset") if isinstance(dec, dict) else None,
                    "interval_sec": dec.get("interval_sec") if isinstance(dec, dict) else None,
                    "dt_local": dec.get("dt_local") if isinstance(dec, dict) else None,
                    "ts": dec.get("ts") if isinstance(dec, dict) else None,
                    "action": dec.get("action") if isinstance(dec, dict) else None,
                    "reason": dec.get("reason") if isinstance(dec, dict) else None,
                    "blockers": dec.get("blockers") if isinstance(dec, dict) else None,
                    "proba_up": (raw or {}).get("proba_up") if isinstance(raw, dict) else dec.get("proba_up"),
                    "conf": (raw or {}).get("conf") if isinstance(raw, dict) else dec.get("conf"),
                    "ev": (raw or {}).get("ev") if isinstance(raw, dict) else dec.get("ev"),
                    "gate_mode": (raw or {}).get("gate_mode") if isinstance(raw, dict) else dec.get("gate_mode"),
                    "regime_ok": (raw or {}).get("regime_ok") if isinstance(raw, dict) else dec.get("regime_ok"),
                }
                st.json(summary, expanded=False)
                with st.expander("raw decision json"):
                    st.json(dec)

        with c2:
            st.markdown("**Signals (recent)**")
            if not signals_db:
                st.info("signals_db_path não disponível.")
            else:
                sdb = Path(str(signals_db))
                try:
                    conn = _sqlite_connect_ro(sdb)
                    try:
                        tables = _sqlite_tables(conn)
                        table = "signals_v2" if "signals_v2" in tables else (tables[0] if tables else None)
                        if not table:
                            st.info(f"Nenhuma tabela em {sdb}")
                        else:
                            rows = _sqlite_fetch_recent(conn, table, limit=max_signals)
                            if not rows:
                                st.info("Sem linhas.")
                            else:
                                if pd is not None:
                                    df = pd.DataFrame(rows)
                                    st.dataframe(df, use_container_width=True, hide_index=True)
                                    # Quick charts if columns exist
                                    chart_cols = [c for c in ("proba_up", "conf", "score") if c in df.columns]
                                    if chart_cols and "ts" in df.columns:
                                        df2 = df.sort_values("ts")
                                        st.line_chart(df2.set_index("ts")[chart_cols])
                                else:
                                    st.json(rows[:50], expanded=False)
                    finally:
                        conn.close()
                except Exception as e:
                    st.warning(f"Falha ao ler sqlite {sdb}: {e}")

            if state_db:
                st.caption(f"state_db_path: `{state_db}`")

    # --- Execution events (jsonl) ---
    st.subheader("Execution events (JSONL)")

    jsonl_path = repo / "runs" / "logs" / "execution_events.jsonl"
    events = _tail_jsonl(jsonl_path, max_events)
    if not events:
        st.info(f"Sem eventos em {jsonl_path} (ainda).")
    else:
        # Normalize to table when possible.
        if pd is not None:
            st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
        else:
            st.json(events, expanded=False)


if __name__ == "__main__":
    run()
