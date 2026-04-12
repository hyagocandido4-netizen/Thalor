from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ..runtime.failsafe import CircuitBreakerSnapshot
from ..runtime.scope import build_scope
from ..runtime.perf import load_json_cached, write_text_if_changed


CONTROL_DIR_REL = Path('runs') / 'control'
REPO_CONTROL_DIR_REL = CONTROL_DIR_REL / '_repo'


def control_db_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else Path('runs/runtime_control.sqlite3')


def _ensure_column(con: sqlite3.Connection, *, table: str, name: str, ddl_type: str, default_sql: str | None = None) -> None:
    columns = {str(row[1]) for row in con.execute(f'PRAGMA table_info({table})').fetchall()}
    if name in columns:
        return
    sql = f'ALTER TABLE {table} ADD COLUMN {name} {ddl_type}'
    if default_sql is not None:
        sql += f' DEFAULT {default_sql}'
    con.execute(sql)


def ensure_runtime_control_db(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS circuit_breakers (
            asset TEXT NOT NULL,
            interval_sec INTEGER NOT NULL,
            state TEXT NOT NULL,
            failures INTEGER NOT NULL DEFAULT 0,
            last_failure_utc TEXT NULL,
            opened_until_utc TEXT NULL,
            half_open_trials_used INTEGER NOT NULL DEFAULT 0,
            reason TEXT NULL,
            primary_cause TEXT NULL,
            failure_domain TEXT NULL,
            failure_step TEXT NULL,
            last_transport_error TEXT NULL,
            last_transport_failure_utc TEXT NULL,
            half_open_trial_in_flight INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (asset, interval_sec)
        )
        """
    )
    for name, ddl_type, default_sql in (
        ('primary_cause', 'TEXT', None),
        ('failure_domain', 'TEXT', None),
        ('failure_step', 'TEXT', None),
        ('last_transport_error', 'TEXT', None),
        ('last_transport_failure_utc', 'TEXT', None),
        ('half_open_trial_in_flight', 'INTEGER NOT NULL', '0'),
    ):
        _ensure_column(con, table='circuit_breakers', name=name, ddl_type=ddl_type, default_sql=default_sql)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS cycle_health (
            asset TEXT NOT NULL,
            interval_sec INTEGER NOT NULL,
            last_success_utc TEXT NULL,
            last_failure_utc TEXT NULL,
            last_failure_reason TEXT NULL,
            PRIMARY KEY (asset, interval_sec)
        )
        """
    )
    con.commit()


class RuntimeControlRepository:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = control_db_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path))
        ensure_runtime_control_db(con)
        return con

    def load_breaker(self, asset: str, interval_sec: int) -> CircuitBreakerSnapshot:
        con = self._connect()
        try:
            row = con.execute(
                """
                SELECT asset, interval_sec, state, failures, last_failure_utc, opened_until_utc,
                       half_open_trials_used, reason, primary_cause, failure_domain, failure_step,
                       last_transport_error, last_transport_failure_utc, half_open_trial_in_flight
                FROM circuit_breakers
                WHERE asset=? AND interval_sec=?
                """,
                (asset, int(interval_sec)),
            ).fetchone()
            if not row:
                return CircuitBreakerSnapshot(asset=asset, interval_sec=int(interval_sec))
            return CircuitBreakerSnapshot.from_mapping(
                {
                    'asset': row[0],
                    'interval_sec': int(row[1]),
                    'state': row[2],
                    'failures': int(row[3]),
                    'last_failure_utc': datetime.fromisoformat(row[4]) if row[4] else None,
                    'opened_until_utc': datetime.fromisoformat(row[5]) if row[5] else None,
                    'half_open_trials_used': int(row[6]),
                    'reason': row[7],
                    'primary_cause': row[8],
                    'failure_domain': row[9],
                    'failure_step': row[10],
                    'last_transport_error': row[11],
                    'last_transport_failure_utc': datetime.fromisoformat(row[12]) if row[12] else None,
                    'half_open_trial_in_flight': bool(int(row[13] or 0)),
                }
            )
        finally:
            con.close()

    def save_breaker(self, snap: CircuitBreakerSnapshot) -> None:
        con = self._connect()
        try:
            con.execute(
                """
                INSERT INTO circuit_breakers (
                    asset, interval_sec, state, failures, last_failure_utc, opened_until_utc,
                    half_open_trials_used, reason, primary_cause, failure_domain, failure_step,
                    last_transport_error, last_transport_failure_utc, half_open_trial_in_flight
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset, interval_sec) DO UPDATE SET
                    state=excluded.state,
                    failures=excluded.failures,
                    last_failure_utc=excluded.last_failure_utc,
                    opened_until_utc=excluded.opened_until_utc,
                    half_open_trials_used=excluded.half_open_trials_used,
                    reason=excluded.reason,
                    primary_cause=excluded.primary_cause,
                    failure_domain=excluded.failure_domain,
                    failure_step=excluded.failure_step,
                    last_transport_error=excluded.last_transport_error,
                    last_transport_failure_utc=excluded.last_transport_failure_utc,
                    half_open_trial_in_flight=excluded.half_open_trial_in_flight
                """,
                (
                    snap.asset,
                    int(snap.interval_sec),
                    snap.state,
                    int(snap.failures),
                    snap.last_failure_utc.isoformat() if snap.last_failure_utc else None,
                    snap.opened_until_utc.isoformat() if snap.opened_until_utc else None,
                    int(snap.half_open_trials_used),
                    snap.reason,
                    snap.primary_cause,
                    snap.failure_domain,
                    snap.failure_step,
                    snap.last_transport_error,
                    snap.last_transport_failure_utc.isoformat() if snap.last_transport_failure_utc else None,
                    1 if snap.half_open_trial_in_flight else 0,
                ),
            )
            con.commit()
        finally:
            con.close()



def repo_control_dir(*, repo_root: str | Path) -> Path:
    root = Path(repo_root).resolve()
    return root / REPO_CONTROL_DIR_REL


def repo_control_artifact_paths(*, repo_root: str | Path) -> dict[str, str]:
    base = repo_control_dir(repo_root=repo_root)
    return {
        'repo_control_dir': str(base),
        'sync': str(base / 'sync.json'),
        'backup': str(base / 'backup.json'),
        'healthcheck': str(base / 'healthcheck.json'),
        'provider_probe': str(base / 'provider_probe.json'),
        'production_gate': str(base / 'production_gate.json'),
        'config_provenance': str(base / 'config_provenance.json'),
        'support_bundle': str(base / 'support_bundle.json'),
        'runtime_artifact_audit': str(base / 'runtime_artifact_audit.json'),
        'guardrail_audit': str(base / 'guardrail_audit.json'),
        'dependency_audit': str(base / 'dependency_audit.json'),
        'state_db_audit': str(base / 'state_db_audit.json'),
        'workspace_hygiene': str(base / 'workspace_hygiene.json'),
        'repo_sync': str(base / 'repo_sync.json'),
    }


def write_repo_control_artifact(*, repo_root: str | Path, name: str, payload: dict[str, Any]) -> Path:
    path_raw = repo_control_artifact_paths(repo_root=repo_root).get(name)
    if path_raw is None:
        raise KeyError(f'unknown repo control artifact: {name}')
    path = Path(path_raw)
    body = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False, default=str)
    write_text_if_changed(path, body, encoding='utf-8')
    return path


def read_repo_control_artifact(*, repo_root: str | Path, name: str) -> dict[str, Any] | None:
    path_raw = repo_control_artifact_paths(repo_root=repo_root).get(name)
    if path_raw is None:
        return None
    obj = load_json_cached(path_raw)
    return obj if isinstance(obj, dict) else None


def control_scope_dir(*, repo_root: str | Path, asset: str, interval_sec: int) -> Path:
    root = Path(repo_root).resolve()
    scope = build_scope(asset, interval_sec)
    return root / CONTROL_DIR_REL / scope.scope_tag


def control_artifact_paths(*, repo_root: str | Path, asset: str, interval_sec: int) -> dict[str, str]:
    base = control_scope_dir(repo_root=repo_root, asset=asset, interval_sec=interval_sec)
    return {
        'control_dir': str(base),
        'plan': str(base / 'plan.json'),
        'quota': str(base / 'quota.json'),
        'precheck': str(base / 'precheck.json'),
        'health': str(base / 'health.json'),
        'loop_status': str(base / 'loop_status.json'),
        'effective_config': str(base / 'effective_config.json'),
        'connectivity': str(base / 'connectivity.json'),
        'execution': str(base / 'execution.json'),
        'execution_hardening': str(base / 'execution_hardening.json'),
        'orders': str(base / 'orders.json'),
        'reconcile': str(base / 'reconcile.json'),
        'guard': str(base / 'guard.json'),
        'protection': str(base / 'protection.json'),
        'lifecycle': str(base / 'lifecycle.json'),
        'security': str(base / 'security.json'),
        'intelligence': str(base / 'intelligence.json'),
        'practice': str(base / 'practice.json'),
        'practice_bootstrap': str(base / 'practice_bootstrap.json'),
        'practice_round': str(base / 'practice_round.json'),
        'retrain': str(base / 'retrain.json'),
        'release': str(base / 'release.json'),
        'doctor': str(base / 'doctor.json'),
        'provider_probe': str(base / 'provider_probe.json'),
        'production_gate': str(base / 'production_gate.json'),
        'config_provenance': str(base / 'config_provenance.json'),
        'support_bundle': str(base / 'support_bundle.json'),
        'runtime_artifact_audit': str(base / 'runtime_artifact_audit.json'),
        'guardrail_audit': str(base / 'guardrail_audit.json'),
        'dependency_audit': str(base / 'dependency_audit.json'),
        'state_db_audit': str(base / 'state_db_audit.json'),
        'retention': str(base / 'retention.json'),
        'alerts': str(base / 'alerts.json'),
        'incidents': str(base / 'incidents.json'),
        'breaker': str(base / 'breaker.json'),
        'triage': str(base / 'triage.json'),
    }


def write_control_artifact(
    *,
    repo_root: str | Path,
    asset: str,
    interval_sec: int,
    name: str,
    payload: dict[str, Any],
) -> Path:
    paths = control_artifact_paths(repo_root=repo_root, asset=asset, interval_sec=interval_sec)
    path_raw = paths.get(name)
    if path_raw is None:
        raise KeyError(f'unknown control artifact: {name}')
    path = Path(path_raw)
    body = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False, default=str)
    write_text_if_changed(path, body, encoding='utf-8')
    return path


def read_control_artifact(
    *,
    repo_root: str | Path,
    asset: str,
    interval_sec: int,
    name: str,
) -> dict[str, Any] | None:
    path_raw = control_artifact_paths(repo_root=repo_root, asset=asset, interval_sec=interval_sec).get(name)
    if path_raw is None:
        return None
    obj = load_json_cached(path_raw)
    return obj if isinstance(obj, dict) else None
