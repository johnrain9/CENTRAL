#!/usr/bin/env python3
"""Manage the canonical CENTRAL SQLite task database."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "state" / "central_tasks.db"
DEFAULT_MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
DEFAULT_GENERATED_DIR = REPO_ROOT / "generated"
DEFAULT_TASKS_DIR = REPO_ROOT / "tasks"
DEFAULT_PACKET_PATH = REPO_ROOT / "central_task_system_tasks.md"
PLANNER_STATUSES = {"todo", "in_progress", "blocked", "done"}
RUNTIME_STATUSES = {"queued", "claimed", "running", "pending_review", "failed", "timeout", "canceled", "done"}
ACTIVE_RUNTIME_STATUSES = {"claimed", "running", "pending_review"}
TASK_FILE_NAME_RE = re.compile(r"^CENTRAL-OPS-[0-9]+\\.md$")
SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
KEY_VALUE_RE = re.compile(r"^- `([^`]+)`: (.+)$", re.MULTILINE)
TASK_PACKET_RE = re.compile(r"^## Task (CENTRAL-OPS-[0-9]+): (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str
    sql: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def die(message: str, code: int = 1) -> "None":
    print(message, file=sys.stderr)
    raise SystemExit(code)


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def resolve_db_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_path = os.environ.get("CENTRAL_TASK_DB_PATH")
    if env_path:
        return Path(env_path).expanduser().resolve()
    return DEFAULT_DB_PATH


def resolve_migrations_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return DEFAULT_MIGRATIONS_DIR


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def load_migrations(migrations_dir: Path) -> list[Migration]:
    if not migrations_dir.is_dir():
        die(f"migration directory missing: {migrations_dir}")
    migrations: list[Migration] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        version = path.stem.split("_", 1)[0]
        migrations.append(Migration(version=version, path=path, checksum=checksum, sql=sql))
    if not migrations:
        die(f"no migration files found in {migrations_dir}")
    return migrations


def ensure_migration_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def applied_migrations(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {str(row["version"]): row for row in rows}


def apply_migrations(conn: sqlite3.Connection, migrations: list[Migration]) -> tuple[list[Migration], list[Migration]]:
    ensure_migration_table(conn)
    existing = applied_migrations(conn)
    applied: list[Migration] = []
    skipped: list[Migration] = []
    for migration in migrations:
        recorded = existing.get(migration.version)
        if recorded is not None:
            if recorded["checksum"] != migration.checksum:
                die(
                    "migration checksum mismatch for "
                    f"{migration.path.name}: recorded={recorded['checksum']} current={migration.checksum}"
                )
            skipped.append(migration)
            continue
        with conn:
            conn.executescript(migration.sql)
            conn.execute(
                """
                INSERT INTO schema_migrations (version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (migration.version, migration.path.name, migration.checksum, utc_now()),
            )
        applied.append(migration)
    return applied, skipped


def fetch_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row["name"]) for row in rows]


def require_initialized_db(conn: sqlite3.Connection, db_path: Path) -> None:
    tables = set(fetch_tables(conn))
    required = {"repos", "tasks", "task_execution_settings", "task_dependencies", "task_events"}
    if not required.issubset(tables):
        die(f"database not initialized at {db_path}; run init first")


def parse_json_text(raw: str | None, *, default: Any) -> Any:
    if raw is None:
        return default
    text = raw.strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError) as exc:
            die(f"invalid JSON-ish value: {raw} ({exc})")
    return default


def load_json_document(path_value: str) -> dict[str, Any]:
    if path_value == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path_value).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        die(f"invalid JSON input in {path_value}: {exc}")
    if not isinstance(payload, dict):
        die("JSON input must be an object")
    return payload


def parse_bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    die(f"invalid boolean for {field}: {value!r}")


def parse_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        die(f"invalid integer for {field}: {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError):
        die(f"invalid integer for {field}: {value!r}")


def compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def markdown_summary(text: str, *, fallback: str) -> str:
    stripped = text.strip()
    if not stripped:
        return fallback
    first_line = stripped.splitlines()[0].strip("- ")
    return first_line[:240] if first_line else fallback


def strip_wrapped_backticks(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("`") and cleaned.endswith("`") and len(cleaned) >= 2:
        return cleaned[1:-1]
    return cleaned


def normalize_optional_owner(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "unassigned":
        return None
    return text


def normalize_repo_id(repo_root: str, fallback: str | None = None) -> str:
    root = Path(repo_root)
    name = root.name.strip()
    if name:
        return name
    if fallback:
        return fallback
    die(f"could not derive repo_id from repo root {repo_root!r}")


def now_iso() -> str:
    return utc_now()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def parse_sections(text: str) -> dict[str, str]:
    matches = list(SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[match.group(1).strip()] = text[start:end].strip()
    return sections


def parse_markdown_key_values(section: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for key, value in KEY_VALUE_RE.findall(section):
        pairs[key.strip()] = strip_wrapped_backticks(value)
    return pairs


def render_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return "(no rows)"
    rendered_rows: list[list[str]] = []
    widths = [len(header) for header, _ in columns]
    for row in rows:
        rendered: list[str] = []
        for index, (_, key) in enumerate(columns):
            value = row.get(key)
            if isinstance(value, (dict, list)):
                cell = compact_json(value)
            elif value is None:
                cell = ""
            else:
                cell = str(value)
            widths[index] = max(widths[index], len(cell))
            rendered.append(cell)
        rendered_rows.append(rendered)
    header = "  ".join(header.ljust(widths[index]) for index, (header, _) in enumerate(columns))
    separator = "  ".join("-" * widths[index] for index in range(len(columns)))
    body = [
        "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(rendered))
        for rendered in rendered_rows
    ]
    return "\n".join([header, separator, *body])


def write_output(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def generated_banner(generated_at: str) -> str:
    return f"Generated from CENTRAL DB at {generated_at}. Do not edit manually."


def ensure_repo(
    conn: sqlite3.Connection,
    *,
    repo_id: str,
    repo_root: str,
    display_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO repos (repo_id, display_name, repo_root, is_active, metadata_json, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?, ?)
        ON CONFLICT(repo_id) DO UPDATE SET
            display_name = excluded.display_name,
            repo_root = excluded.repo_root,
            metadata_json = excluded.metadata_json,
            updated_at = excluded.updated_at
        """,
        (
            repo_id,
            display_name or repo_id,
            repo_root,
            compact_json(metadata or {}),
            timestamp,
            timestamp,
        ),
    )


def insert_event(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    event_type: str,
    actor_kind: str,
    actor_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO task_events (task_id, event_type, actor_kind, actor_id, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (task_id, event_type, actor_kind, actor_id, compact_json(payload or {}), now_iso()),
    )


def insert_artifact(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    artifact_kind: str,
    path_or_uri: str,
    label: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO task_artifacts (task_id, artifact_kind, path_or_uri, label, metadata_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (task_id, artifact_kind, path_or_uri, label, compact_json(metadata or {}), now_iso()),
    )


def close_active_assignments(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    assignee_kind: str | None = None,
    assignee_id: str | None = None,
) -> None:
    clauses = ["task_id = ?", "released_at IS NULL"]
    params: list[Any] = [task_id]
    if assignee_kind is not None:
        clauses.append("assignee_kind = ?")
        params.append(assignee_kind)
    if assignee_id is not None:
        clauses.append("assignee_id = ?")
        params.append(assignee_id)
    params.append(now_iso())
    conn.execute(
        f"UPDATE task_assignments SET released_at = ? WHERE {' AND '.join(clauses)}",
        (params[-1], *params[:-1]),
    )


def replace_dependencies(conn: sqlite3.Connection, task_id: str, dependencies: list[str]) -> None:
    conn.execute("DELETE FROM task_dependencies WHERE task_id = ?", (task_id,))
    timestamp = now_iso()
    for depends_on in dependencies:
        conn.execute(
            """
            INSERT INTO task_dependencies (task_id, depends_on_task_id, dependency_kind, created_at)
            VALUES (?, ?, 'hard', ?)
            """,
            (task_id, depends_on, timestamp),
        )


def upsert_execution_settings(conn: sqlite3.Connection, task_id: str, execution: dict[str, Any]) -> None:
    additional_dirs = execution.get("additional_writable_dirs_json")
    if additional_dirs is None:
        additional_dirs = execution.get("additional_writable_dirs", [])
    conn.execute(
        """
        INSERT INTO task_execution_settings (
            task_id,
            task_kind,
            sandbox_mode,
            approval_policy,
            additional_writable_dirs_json,
            timeout_seconds,
            execution_metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            task_kind = excluded.task_kind,
            sandbox_mode = excluded.sandbox_mode,
            approval_policy = excluded.approval_policy,
            additional_writable_dirs_json = excluded.additional_writable_dirs_json,
            timeout_seconds = excluded.timeout_seconds,
            execution_metadata_json = excluded.execution_metadata_json
        """,
        (
            task_id,
            execution["task_kind"],
            execution.get("sandbox_mode"),
            execution.get("approval_policy"),
            compact_json(additional_dirs),
            execution["timeout_seconds"],
            compact_json(execution.get("execution_metadata_json", execution.get("metadata", {}))),
        ),
    )


def fetch_task_row(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()


def fetch_execution_row(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM task_execution_settings WHERE task_id = ?", (task_id,)).fetchone()


def fetch_active_lease(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM task_active_leases WHERE task_id = ?", (task_id,)).fetchone()


def load_dependencies(conn: sqlite3.Connection, task_ids: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    task_ids = list(task_ids)
    if not task_ids:
        return {}
    placeholders = ", ".join("?" for _ in task_ids)
    rows = conn.execute(
        f"""
        SELECT d.task_id, d.depends_on_task_id, d.dependency_kind, t.planner_status AS depends_on_status, t.title AS depends_on_title
        FROM task_dependencies d
        JOIN tasks t ON t.task_id = d.depends_on_task_id
        WHERE d.task_id IN ({placeholders})
        ORDER BY d.task_id, d.depends_on_task_id
        """,
        tuple(task_ids),
    ).fetchall()
    mapping: dict[str, list[dict[str, Any]]] = {task_id: [] for task_id in task_ids}
    for row in rows:
        mapping.setdefault(str(row["task_id"]), []).append(
            {
                "depends_on_task_id": row["depends_on_task_id"],
                "dependency_kind": row["dependency_kind"],
                "depends_on_status": row["depends_on_status"],
                "depends_on_title": row["depends_on_title"],
            }
        )
    return mapping


def fetch_latest_events(conn: sqlite3.Connection, task_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT event_id, event_type, actor_kind, actor_id, payload_json, created_at
        FROM task_events
        WHERE task_id = ?
        ORDER BY event_id DESC
        LIMIT ?
        """,
        (task_id, limit),
    ).fetchall()
    return [
        {
            "event_id": row["event_id"],
            "event_type": row["event_type"],
            "actor_kind": row["actor_kind"],
            "actor_id": row["actor_id"],
            "payload": parse_json_text(row["payload_json"], default={}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def fetch_artifacts(conn: sqlite3.Connection, task_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT artifact_id, artifact_kind, path_or_uri, label, metadata_json, created_at
        FROM task_artifacts
        WHERE task_id = ?
        ORDER BY artifact_id ASC
        """,
        (task_id,),
    ).fetchall()
    return [
        {
            "artifact_id": row["artifact_id"],
            "artifact_kind": row["artifact_kind"],
            "path_or_uri": row["path_or_uri"],
            "label": row["label"],
            "metadata": parse_json_text(row["metadata_json"], default={}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def fetch_task_snapshots(
    conn: sqlite3.Connection,
    *,
    task_id: str | None = None,
    repo_id: str | None = None,
    planner_status: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["t.archived_at IS NULL"]
    params: list[Any] = []
    if task_id is not None:
        clauses.append("t.task_id = ?")
        params.append(task_id)
    if repo_id is not None:
        clauses.append("t.target_repo_id = ?")
        params.append(repo_id)
    if planner_status is not None:
        clauses.append("t.planner_status = ?")
        params.append(planner_status)
    query = f"""
        SELECT
            t.*,
            r.display_name AS repo_display_name,
            r.repo_root,
            r.is_active AS repo_is_active,
            r.metadata_json AS repo_metadata_json,
            es.task_kind,
            es.sandbox_mode,
            es.approval_policy,
            es.additional_writable_dirs_json,
            es.timeout_seconds,
            es.execution_metadata_json,
            rs.runtime_status,
            rs.queue_name,
            rs.claimed_by,
            rs.claimed_at,
            rs.started_at,
            rs.finished_at,
            rs.pending_review_at,
            rs.last_runtime_error,
            rs.retry_count,
            rs.last_transition_at,
            rs.runtime_metadata_json,
            al.lease_owner_kind,
            al.lease_owner_id,
            al.assignment_state AS lease_assignment_state,
            al.lease_acquired_at,
            al.lease_expires_at,
            al.last_heartbeat_at,
            al.execution_run_id,
            al.lease_metadata_json
        FROM tasks t
        JOIN repos r ON r.repo_id = t.target_repo_id
        LEFT JOIN task_execution_settings es ON es.task_id = t.task_id
        LEFT JOIN task_runtime_state rs ON rs.task_id = t.task_id
        LEFT JOIN task_active_leases al ON al.task_id = t.task_id
        WHERE {' AND '.join(clauses)}
        ORDER BY t.priority ASC, t.task_id ASC
    """
    rows = conn.execute(query, tuple(params)).fetchall()
    dependency_map = load_dependencies(conn, [str(row["task_id"]) for row in rows])
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        dependencies = dependency_map.get(str(row["task_id"]), [])
        blocker_ids = [item["depends_on_task_id"] for item in dependencies if item["dependency_kind"] == "hard" and item["depends_on_status"] != "done"]
        metadata = parse_json_text(row["metadata_json"], default={})
        execution = None
        if row["task_kind"] is not None:
            execution = {
                "task_kind": row["task_kind"],
                "sandbox_mode": row["sandbox_mode"],
                "approval_policy": row["approval_policy"],
                "additional_writable_dirs": parse_json_text(row["additional_writable_dirs_json"], default=[]),
                "timeout_seconds": row["timeout_seconds"],
                "metadata": parse_json_text(row["execution_metadata_json"], default={}),
            }
        runtime = None
        if row["runtime_status"] is not None:
            runtime = {
                "runtime_status": row["runtime_status"],
                "queue_name": row["queue_name"],
                "claimed_by": row["claimed_by"],
                "claimed_at": row["claimed_at"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "pending_review_at": row["pending_review_at"],
                "last_runtime_error": row["last_runtime_error"],
                "retry_count": row["retry_count"],
                "last_transition_at": row["last_transition_at"],
                "metadata": parse_json_text(row["runtime_metadata_json"], default={}),
            }
        lease = None
        if row["lease_owner_kind"] is not None:
            lease = {
                "lease_owner_kind": row["lease_owner_kind"],
                "lease_owner_id": row["lease_owner_id"],
                "assignment_state": row["lease_assignment_state"],
                "lease_acquired_at": row["lease_acquired_at"],
                "lease_expires_at": row["lease_expires_at"],
                "last_heartbeat_at": row["last_heartbeat_at"],
                "execution_run_id": row["execution_run_id"],
                "metadata": parse_json_text(row["lease_metadata_json"], default={}),
            }
        snapshots.append(
            {
                "task_id": row["task_id"],
                "title": row["title"],
                "summary": row["summary"],
                "objective_md": row["objective_md"],
                "context_md": row["context_md"],
                "scope_md": row["scope_md"],
                "deliverables_md": row["deliverables_md"],
                "acceptance_md": row["acceptance_md"],
                "testing_md": row["testing_md"],
                "dispatch_md": row["dispatch_md"],
                "closeout_md": row["closeout_md"],
                "reconciliation_md": row["reconciliation_md"],
                "planner_status": row["planner_status"],
                "version": row["version"],
                "priority": row["priority"],
                "task_type": row["task_type"],
                "planner_owner": row["planner_owner"],
                "worker_owner": row["worker_owner"],
                "target_repo_id": row["target_repo_id"],
                "target_repo_display_name": row["repo_display_name"],
                "target_repo_root": row["repo_root"],
                "target_repo_is_active": bool(row["repo_is_active"]),
                "approval_required": bool(row["approval_required"]),
                "source_kind": row["source_kind"],
                "archived_at": row["archived_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "closed_at": row["closed_at"],
                "metadata": metadata,
                "repo_metadata": parse_json_text(row["repo_metadata_json"], default={}),
                "execution": execution,
                "runtime": runtime,
                "lease": lease,
                "dependencies": dependencies,
                "blocking_dependency_ids": blocker_ids,
                "dependency_blocked": bool(blocker_ids),
            }
        )
    return snapshots


def task_is_eligible(snapshot: dict[str, Any]) -> bool:
    if snapshot["planner_status"] not in {"todo", "in_progress"}:
        return False
    if not snapshot["target_repo_is_active"]:
        return False
    if snapshot["dependency_blocked"]:
        return False
    if snapshot["lease"] is not None:
        return False
    runtime = snapshot["runtime"]
    if runtime is None:
        return True
    return runtime["runtime_status"] in {"queued", "failed", "timeout", "canceled"}


def order_eligible_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [snapshot for snapshot in snapshots if task_is_eligible(snapshot)]
    buckets: dict[int, dict[str, deque[dict[str, Any]]]] = defaultdict(lambda: defaultdict(deque))
    for snapshot in eligible:
        buckets[int(snapshot["priority"])][snapshot["target_repo_id"]].append(snapshot)
    ordered: list[dict[str, Any]] = []
    for priority in sorted(buckets.keys()):
        repo_map = buckets[priority]
        repo_ids = sorted(repo_map.keys())
        while any(repo_map[repo_id] for repo_id in repo_ids):
            for repo_id in repo_ids:
                if repo_map[repo_id]:
                    ordered.append(repo_map[repo_id].popleft())
    return ordered


def validate_task_payload(payload: dict[str, Any], *, for_update: bool) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    text_fields = [
        "task_id",
        "title",
        "summary",
        "objective_md",
        "context_md",
        "scope_md",
        "deliverables_md",
        "acceptance_md",
        "testing_md",
        "dispatch_md",
        "closeout_md",
        "reconciliation_md",
        "planner_status",
        "task_type",
        "planner_owner",
        "worker_owner",
        "target_repo_id",
        "target_repo_root",
        "target_repo_display_name",
        "source_kind",
    ]
    for field in text_fields:
        if field in payload:
            value = payload[field]
            if value is None and field == "worker_owner":
                normalized[field] = None
                continue
            if value is None:
                die(f"missing value for {field}")
            normalized[field] = str(value)
    if "planner_status" in normalized and normalized["planner_status"] not in PLANNER_STATUSES:
        die(f"invalid planner_status: {normalized['planner_status']}")
    for field in ["priority"]:
        if field in payload:
            normalized[field] = parse_int(payload[field], field=field)
    if "approval_required" in payload:
        normalized["approval_required"] = parse_bool(payload["approval_required"], field="approval_required")
    if "metadata" in payload:
        if not isinstance(payload["metadata"], dict):
            die("metadata must be an object")
        normalized["metadata"] = payload["metadata"]
    elif "metadata_json" in payload:
        if not isinstance(payload["metadata_json"], dict):
            die("metadata_json must be an object")
        normalized["metadata"] = payload["metadata_json"]
    if "dependencies" in payload:
        dependencies = payload["dependencies"]
        if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
            die("dependencies must be a list of task IDs")
        normalized["dependencies"] = dependencies
    if "execution" in payload:
        execution = payload["execution"]
        if not isinstance(execution, dict):
            die("execution must be an object")
        if not for_update:
            for required in ["task_kind", "timeout_seconds"]:
                if required not in execution:
                    die(f"missing execution.{required}")
        normalized_execution: dict[str, Any] = {}
        for field in ["task_kind", "sandbox_mode", "approval_policy"]:
            if field in execution and execution[field] is not None:
                normalized_execution[field] = str(execution[field])
        if "timeout_seconds" in execution:
            normalized_execution["timeout_seconds"] = parse_int(execution["timeout_seconds"], field="execution.timeout_seconds")
        if "additional_writable_dirs" in execution:
            dirs = execution["additional_writable_dirs"]
            if not isinstance(dirs, list) or not all(isinstance(item, str) for item in dirs):
                die("execution.additional_writable_dirs must be a list of strings")
            normalized_execution["additional_writable_dirs"] = dirs
        if "metadata" in execution:
            if not isinstance(execution["metadata"], dict):
                die("execution.metadata must be an object")
            normalized_execution["metadata"] = execution["metadata"]
        elif "execution_metadata_json" in execution:
            if not isinstance(execution["execution_metadata_json"], dict):
                die("execution.execution_metadata_json must be an object")
            normalized_execution["execution_metadata_json"] = execution["execution_metadata_json"]
        normalized["execution"] = normalized_execution
    if not for_update:
        required_fields = [
            "task_id",
            "title",
            "summary",
            "objective_md",
            "context_md",
            "scope_md",
            "deliverables_md",
            "acceptance_md",
            "testing_md",
            "dispatch_md",
            "closeout_md",
            "reconciliation_md",
            "planner_status",
            "priority",
            "task_type",
            "planner_owner",
            "target_repo_id",
            "target_repo_root",
        ]
        for field in required_fields:
            if field not in normalized:
                die(f"missing required field: {field}")
        if "approval_required" not in normalized:
            normalized["approval_required"] = False
        if "source_kind" not in normalized:
            normalized["source_kind"] = "planner"
        if "metadata" not in normalized:
            normalized["metadata"] = {}
        if "dependencies" not in normalized:
            normalized["dependencies"] = []
        if "execution" not in normalized:
            die("missing required field: execution")
    return normalized


def merge_task_metadata(existing_raw: str, incoming: dict[str, Any] | None) -> dict[str, Any]:
    current = parse_json_text(existing_raw, default={})
    if incoming:
        current.update(incoming)
    return current


def create_task(conn: sqlite3.Connection, payload: dict[str, Any], *, actor_kind: str, actor_id: str) -> dict[str, Any]:
    normalized = validate_task_payload(payload, for_update=False)
    timestamp = now_iso()
    ensure_repo(
        conn,
        repo_id=normalized["target_repo_id"],
        repo_root=normalized["target_repo_root"],
        display_name=normalized.get("target_repo_display_name") or normalized["target_repo_id"],
    )
    conn.execute(
        """
        INSERT INTO tasks (
            task_id,
            title,
            summary,
            objective_md,
            context_md,
            scope_md,
            deliverables_md,
            acceptance_md,
            testing_md,
            dispatch_md,
            closeout_md,
            reconciliation_md,
            planner_status,
            version,
            priority,
            task_type,
            planner_owner,
            worker_owner,
            target_repo_id,
            approval_required,
            source_kind,
            archived_at,
            created_at,
            updated_at,
            closed_at,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
        """,
        (
            normalized["task_id"],
            normalized["title"],
            normalized["summary"],
            normalized["objective_md"],
            normalized["context_md"],
            normalized["scope_md"],
            normalized["deliverables_md"],
            normalized["acceptance_md"],
            normalized["testing_md"],
            normalized["dispatch_md"],
            normalized["closeout_md"],
            normalized["reconciliation_md"],
            normalized["planner_status"],
            normalized["priority"],
            normalized["task_type"],
            normalized["planner_owner"],
            normalize_optional_owner(normalized.get("worker_owner")),
            normalized["target_repo_id"],
            1 if normalized["approval_required"] else 0,
            normalized["source_kind"],
            timestamp,
            timestamp,
            timestamp if normalized["planner_status"] == "done" else None,
            compact_json(normalized["metadata"]),
        ),
    )
    upsert_execution_settings(conn, normalized["task_id"], normalized["execution"])
    replace_dependencies(conn, normalized["task_id"], normalized["dependencies"])
    insert_event(
        conn,
        task_id=normalized["task_id"],
        event_type="planner.task_created",
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload={
            "planner_status": normalized["planner_status"],
            "priority": normalized["priority"],
            "target_repo_id": normalized["target_repo_id"],
            "dependencies": normalized["dependencies"],
        },
    )
    return fetch_task_snapshots(conn, task_id=normalized["task_id"])[0]


def update_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    expected_version: int,
    payload: dict[str, Any],
    actor_kind: str,
    actor_id: str,
    allow_active_lease: bool,
) -> dict[str, Any]:
    normalized = validate_task_payload(payload, for_update=True)
    current_row = fetch_task_row(conn, task_id)
    if current_row is None:
        die(f"task not found: {task_id}")
    current_version = int(current_row["version"])
    if current_version != expected_version:
        die(f"version mismatch for {task_id}: expected {expected_version}, current {current_version}")
    current_lease = fetch_active_lease(conn, task_id)
    if current_lease is not None and not allow_active_lease:
        die(f"task {task_id} has an active lease; retry with --allow-active-lease only for explicit override workflows")

    merged: dict[str, Any] = {key: current_row[key] for key in current_row.keys()}
    if "worker_owner" in normalized:
        normalized["worker_owner"] = normalize_optional_owner(normalized.get("worker_owner"))
    for field in [
        "title",
        "summary",
        "objective_md",
        "context_md",
        "scope_md",
        "deliverables_md",
        "acceptance_md",
        "testing_md",
        "dispatch_md",
        "closeout_md",
        "reconciliation_md",
        "planner_status",
        "priority",
        "task_type",
        "planner_owner",
        "worker_owner",
        "target_repo_id",
        "source_kind",
    ]:
        if field in normalized:
            merged[field] = normalized[field]
    if "approval_required" in normalized:
        merged["approval_required"] = 1 if normalized["approval_required"] else 0
    merged_metadata = merge_task_metadata(current_row["metadata_json"], normalized.get("metadata"))
    merged["metadata_json"] = compact_json(merged_metadata)
    merged["updated_at"] = now_iso()
    next_version = current_version + 1
    merged["version"] = next_version
    if normalized.get("planner_status") == "done":
        merged["closed_at"] = merged["updated_at"]
    elif "planner_status" in normalized and normalized["planner_status"] != "done":
        merged["closed_at"] = None

    if "target_repo_id" in normalized:
        repo_root = normalized.get("target_repo_root") or str(current_row["target_repo_id"])
        if "target_repo_root" not in normalized:
            repo_existing = conn.execute(
                "SELECT repo_root, display_name FROM repos WHERE repo_id = ?",
                (normalized["target_repo_id"],),
            ).fetchone()
            if repo_existing is not None:
                repo_root = str(repo_existing["repo_root"])
        ensure_repo(
            conn,
            repo_id=normalized["target_repo_id"],
            repo_root=repo_root,
            display_name=normalized.get("target_repo_display_name") or normalized["target_repo_id"],
        )
    elif "target_repo_root" in normalized:
        ensure_repo(
            conn,
            repo_id=str(current_row["target_repo_id"]),
            repo_root=normalized["target_repo_root"],
            display_name=normalized.get("target_repo_display_name") or str(current_row["target_repo_id"]),
        )

    cursor = conn.execute(
        """
        UPDATE tasks
        SET title = ?,
            summary = ?,
            objective_md = ?,
            context_md = ?,
            scope_md = ?,
            deliverables_md = ?,
            acceptance_md = ?,
            testing_md = ?,
            dispatch_md = ?,
            closeout_md = ?,
            reconciliation_md = ?,
            planner_status = ?,
            version = ?,
            priority = ?,
            task_type = ?,
            planner_owner = ?,
            worker_owner = ?,
            target_repo_id = ?,
            approval_required = ?,
            source_kind = ?,
            updated_at = ?,
            closed_at = ?,
            metadata_json = ?
        WHERE task_id = ? AND version = ?
        """,
        (
            merged["title"],
            merged["summary"],
            merged["objective_md"],
            merged["context_md"],
            merged["scope_md"],
            merged["deliverables_md"],
            merged["acceptance_md"],
            merged["testing_md"],
            merged["dispatch_md"],
            merged["closeout_md"],
            merged["reconciliation_md"],
            merged["planner_status"],
            merged["version"],
            merged["priority"],
            merged["task_type"],
            merged["planner_owner"],
            merged["worker_owner"],
            merged["target_repo_id"],
            merged["approval_required"],
            merged["source_kind"],
            merged["updated_at"],
            merged["closed_at"],
            merged["metadata_json"],
            task_id,
            current_version,
        ),
    )
    if cursor.rowcount == 0:
        die(f"failed to update {task_id}; version changed concurrently")

    current_execution_row = fetch_execution_row(conn, task_id)
    if "execution" in normalized:
        merged_execution = {
            "task_kind": current_execution_row["task_kind"] if current_execution_row is not None else normalized["execution"].get("task_kind"),
            "sandbox_mode": current_execution_row["sandbox_mode"] if current_execution_row is not None else None,
            "approval_policy": current_execution_row["approval_policy"] if current_execution_row is not None else None,
            "additional_writable_dirs": parse_json_text(current_execution_row["additional_writable_dirs_json"], default=[])
            if current_execution_row is not None
            else [],
            "timeout_seconds": current_execution_row["timeout_seconds"] if current_execution_row is not None else normalized["execution"].get("timeout_seconds"),
            "execution_metadata_json": parse_json_text(current_execution_row["execution_metadata_json"], default={})
            if current_execution_row is not None
            else {},
        }
        for key, value in normalized["execution"].items():
            if key == "metadata":
                merged_execution["execution_metadata_json"] = value
            else:
                merged_execution[key] = value
        if merged_execution.get("task_kind") is None or merged_execution.get("timeout_seconds") is None:
            die(f"execution settings incomplete for {task_id}")
        upsert_execution_settings(conn, task_id, merged_execution)

    if "dependencies" in normalized:
        replace_dependencies(conn, task_id, normalized["dependencies"])

    insert_event(
        conn,
        task_id=task_id,
        event_type="planner.task_updated",
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload={
            "expected_version": expected_version,
            "new_version": next_version,
            "fields": sorted(normalized.keys()),
            "allow_active_lease": allow_active_lease,
        },
    )
    return fetch_task_snapshots(conn, task_id=task_id)[0]


def reconcile_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    expected_version: int,
    outcome: str,
    summary: str,
    notes: str | None,
    tests: str | None,
    artifacts: list[str],
    actor_kind: str,
    actor_id: str,
) -> dict[str, Any]:
    if outcome not in {"done", "blocked"}:
        die(f"invalid reconcile outcome: {outcome}")
    current_row = fetch_task_row(conn, task_id)
    if current_row is None:
        die(f"task not found: {task_id}")
    if int(current_row["version"]) != expected_version:
        die(f"version mismatch for {task_id}: expected {expected_version}, current {current_row['version']}")
    metadata = merge_task_metadata(current_row["metadata_json"], None)
    metadata["closeout"] = {
        "outcome": outcome,
        "summary": summary,
        "notes": notes,
        "tests": tests,
        "reconciled_at": now_iso(),
        "actor_id": actor_id,
    }
    if outcome == "blocked":
        metadata["blocker_summary"] = summary
    updated_at = now_iso()
    next_version = int(current_row["version"]) + 1
    conn.execute(
        """
        UPDATE tasks
        SET planner_status = ?,
            version = ?,
            worker_owner = NULL,
            updated_at = ?,
            closed_at = ?,
            metadata_json = ?
        WHERE task_id = ? AND version = ?
        """,
        (
            outcome,
            next_version,
            updated_at,
            updated_at if outcome == "done" else None,
            compact_json(metadata),
            task_id,
            expected_version,
        ),
    )
    for artifact in artifacts:
        insert_artifact(
            conn,
            task_id=task_id,
            artifact_kind="planner_closeout",
            path_or_uri=artifact,
            label=Path(artifact).name,
            metadata={"reconciled_by": actor_id, "outcome": outcome},
        )
    insert_event(
        conn,
        task_id=task_id,
        event_type="planner.task_reconciled",
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload={"outcome": outcome, "summary": summary, "tests": tests, "artifacts": artifacts},
    )
    return fetch_task_snapshots(conn, task_id=task_id)[0]


def summarize_portfolio(conn: sqlite3.Connection) -> dict[str, Any]:
    generated_at = now_iso()
    snapshots = fetch_task_snapshots(conn)
    eligible = order_eligible_snapshots(snapshots)
    planner_counts: dict[str, int] = {status: 0 for status in sorted(PLANNER_STATUSES)}
    runtime_counts: dict[str, int] = {status: 0 for status in sorted(RUNTIME_STATUSES)}
    per_repo: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "eligible": 0, "blocked": 0, "pending_review": 0, "running": 0})
    blocked_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        planner_counts[snapshot["planner_status"]] = planner_counts.get(snapshot["planner_status"], 0) + 1
        runtime_status = snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else None
        if runtime_status:
            runtime_counts[runtime_status] = runtime_counts.get(runtime_status, 0) + 1
        repo_summary = per_repo[snapshot["target_repo_id"]]
        repo_summary["total"] += 1
        if task_is_eligible(snapshot):
            repo_summary["eligible"] += 1
        if snapshot["planner_status"] == "blocked":
            repo_summary["blocked"] += 1
            blocked_rows.append(snapshot)
        if runtime_status == "pending_review":
            repo_summary["pending_review"] += 1
            review_rows.append(snapshot)
        if runtime_status == "running":
            repo_summary["running"] += 1
    return {
        "generated_at": generated_at,
        "planner_counts": planner_counts,
        "runtime_counts": runtime_counts,
        "top_eligible": [
            {
                "task_id": snapshot["task_id"],
                "title": snapshot["title"],
                "priority": snapshot["priority"],
                "target_repo_id": snapshot["target_repo_id"],
            }
            for snapshot in eligible[:10]
        ],
        "blocked_count": len(blocked_rows),
        "oldest_blocked_at": min((row["updated_at"] for row in blocked_rows), default=None),
        "pending_review_count": len(review_rows),
        "oldest_pending_review_at": min(
            (
                row["runtime"].get("pending_review_at") or row["runtime"].get("last_transition_at")
                for row in review_rows
                if row["runtime"] is not None
            ),
            default=None,
        ),
        "per_repo": [
            {
                "repo_id": repo_id,
                **counts,
            }
            for repo_id, counts in sorted(per_repo.items())
        ],
    }


def format_summary_text(summary: dict[str, Any]) -> str:
    lines = [generated_banner(summary["generated_at"]), "", "Planner counts:"]
    for status, count in summary["planner_counts"].items():
        lines.append(f"- {status}: {count}")
    lines.append("")
    lines.append("Runtime counts:")
    for status, count in summary["runtime_counts"].items():
        lines.append(f"- {status}: {count}")
    lines.append("")
    lines.append(f"Blocked: {summary['blocked_count']} (oldest {summary['oldest_blocked_at'] or 'n/a'})")
    lines.append(
        f"Pending review: {summary['pending_review_count']} (oldest {summary['oldest_pending_review_at'] or 'n/a'})"
    )
    lines.append("")
    lines.append("Top eligible:")
    for item in summary["top_eligible"]:
        lines.append(f"- {item['task_id']} | p{item['priority']} | {item['target_repo_id']} | {item['title']}")
    lines.append("")
    lines.append("Per repo:")
    if summary["per_repo"]:
        lines.append(render_table(summary["per_repo"], [("repo", "repo_id"), ("total", "total"), ("eligible", "eligible"), ("blocked", "blocked"), ("review", "pending_review"), ("running", "running")]))
    else:
        lines.append("(no repos)")
    return "\n".join(lines)


def format_eligible_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot in order_eligible_snapshots(snapshots):
        rows.append(
            {
                "task_id": snapshot["task_id"],
                "priority": snapshot["priority"],
                "repo": snapshot["target_repo_id"],
                "planner_status": snapshot["planner_status"],
                "runtime_status": snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else "",
                "planner_owner": snapshot["planner_owner"],
                "worker_owner": snapshot["worker_owner"] or "",
                "title": snapshot["title"],
            }
        )
    return rows


def format_blocked_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if snapshot["planner_status"] != "blocked":
            continue
        metadata = snapshot["metadata"]
        blocker = metadata.get("blocker_summary")
        if not blocker and isinstance(metadata.get("closeout"), dict):
            blocker = metadata["closeout"].get("summary")
        rows.append(
            {
                "task_id": snapshot["task_id"],
                "repo": snapshot["target_repo_id"],
                "planner_owner": snapshot["planner_owner"],
                "blocked_at": snapshot["updated_at"],
                "blocker": blocker or snapshot["summary"],
                "title": snapshot["title"],
            }
        )
    return rows


def format_assignments_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if not snapshot["planner_owner"] and not snapshot["worker_owner"] and snapshot["lease"] is None:
            continue
        rows.append(
            {
                "task_id": snapshot["task_id"],
                "repo": snapshot["target_repo_id"],
                "planner_owner": snapshot["planner_owner"],
                "worker_owner": snapshot["worker_owner"] or "",
                "lease_owner": snapshot["lease"]["lease_owner_id"] if snapshot["lease"] else "",
                "lease_expires_at": snapshot["lease"]["lease_expires_at"] if snapshot["lease"] else "",
                "runtime_status": snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else "",
                "title": snapshot["title"],
            }
        )
    return rows


def format_review_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        runtime = snapshot["runtime"]
        if runtime is None or runtime["runtime_status"] not in {"pending_review", "failed", "timeout"}:
            continue
        rows.append(
            {
                "task_id": snapshot["task_id"],
                "repo": snapshot["target_repo_id"],
                "runtime_status": runtime["runtime_status"],
                "planner_status": snapshot["planner_status"],
                "age_at": runtime.get("pending_review_at") or runtime.get("last_transition_at") or "",
                "claimed_by": runtime.get("claimed_by") or "",
                "last_error": runtime.get("last_runtime_error") or "",
                "title": snapshot["title"],
            }
        )
    return rows


def render_task_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": snapshot["task_id"],
        "title": snapshot["title"],
        "summary": snapshot["summary"],
        "priority": snapshot["priority"],
        "planner_status": snapshot["planner_status"],
        "runtime_status": snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else None,
        "target_repo_id": snapshot["target_repo_id"],
        "target_repo_root": snapshot["target_repo_root"],
        "planner_owner": snapshot["planner_owner"],
        "worker_owner": snapshot["worker_owner"],
        "objective_md": snapshot["objective_md"],
        "context_md": snapshot["context_md"],
        "scope_md": snapshot["scope_md"],
        "deliverables_md": snapshot["deliverables_md"],
        "acceptance_md": snapshot["acceptance_md"],
        "testing_md": snapshot["testing_md"],
        "dispatch_md": snapshot["dispatch_md"],
        "closeout_md": snapshot["closeout_md"],
        "reconciliation_md": snapshot["reconciliation_md"],
        "dependencies": snapshot["dependencies"],
        "execution": snapshot["execution"],
        "runtime": snapshot["runtime"],
        "lease": snapshot["lease"],
        "metadata": snapshot["metadata"],
        "created_at": snapshot["created_at"],
        "updated_at": snapshot["updated_at"],
        "closed_at": snapshot["closed_at"],
    }


def render_task_card_markdown(snapshot: dict[str, Any], *, generated_at: str) -> str:
    dependencies = snapshot["dependencies"]
    runtime_status = snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else "none"
    lines = [
        f"# {snapshot['task_id']} {snapshot['title']}",
        "",
        generated_banner(generated_at),
        "",
        "## Metadata",
        f"- Task ID: `{snapshot['task_id']}`",
        f"- Planner Status: `{snapshot['planner_status']}`",
        f"- Runtime Status: `{runtime_status}`",
        f"- Priority: `{snapshot['priority']}`",
        f"- Target Repo: `{snapshot['target_repo_id']}` ({snapshot['target_repo_root']})",
        f"- Planner Owner: `{snapshot['planner_owner']}`",
        f"- Worker Owner: `{snapshot['worker_owner'] or 'unassigned'}`",
        "",
        "## Objective",
        snapshot["objective_md"],
        "",
        "## Context",
        snapshot["context_md"],
        "",
        "## Scope",
        snapshot["scope_md"],
        "",
        "## Deliverables",
        snapshot["deliverables_md"],
        "",
        "## Acceptance",
        snapshot["acceptance_md"],
        "",
        "## Testing",
        snapshot["testing_md"],
        "",
        "## Dispatch",
        snapshot["dispatch_md"],
        "",
        "## Dependencies",
    ]
    if dependencies:
        for dependency in dependencies:
            lines.append(
                f"- `{dependency['depends_on_task_id']}` ({dependency['depends_on_status']}) - {dependency['depends_on_title']}"
            )
    else:
        lines.append("- none")
    lines.extend(["", "## Reconciliation", snapshot["reconciliation_md"], ""])
    return "\n".join(lines)


def render_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# CENTRAL Portfolio Summary",
        "",
        generated_banner(summary["generated_at"]),
        "",
        "## Planner Counts",
    ]
    for status, count in summary["planner_counts"].items():
        lines.append(f"- `{status}`: {count}")
    lines.extend(["", "## Runtime Counts"])
    for status, count in summary["runtime_counts"].items():
        lines.append(f"- `{status}`: {count}")
    lines.extend([
        "",
        "## Queue Health",
        f"- Blocked count: {summary['blocked_count']}",
        f"- Oldest blocked: {summary['oldest_blocked_at'] or 'n/a'}",
        f"- Pending review count: {summary['pending_review_count']}",
        f"- Oldest pending review: {summary['oldest_pending_review_at'] or 'n/a'}",
        "",
        "## Top Eligible",
    ])
    for item in summary["top_eligible"]:
        lines.append(f"- `{item['task_id']}` | p{item['priority']} | {item['target_repo_id']} | {item['title']}")
    lines.extend(["", "## Per Repo"])
    if summary["per_repo"]:
        for repo in summary["per_repo"]:
            lines.append(
                f"- `{repo['repo_id']}`: total={repo['total']}, eligible={repo['eligible']}, blocked={repo['blocked']}, review={repo['pending_review']}, running={repo['running']}"
            )
    else:
        lines.append("- no repos")
    lines.append("")
    return "\n".join(lines)


def begin_immediate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def runtime_claim(
    conn: sqlite3.Connection,
    *,
    worker_id: str,
    queue_name: str,
    lease_seconds: int,
    task_id: str | None,
    actor_id: str,
) -> dict[str, Any]:
    begin_immediate(conn)
    snapshots = fetch_task_snapshots(conn, task_id=task_id) if task_id else fetch_task_snapshots(conn)
    ordered = order_eligible_snapshots(snapshots)
    if task_id is not None:
        ordered = [snapshot for snapshot in ordered if snapshot["task_id"] == task_id]
    if not ordered:
        conn.rollback()
        die("no eligible task available to claim")
    snapshot = ordered[0]
    claimed_at = now_iso()
    lease_expires_at = datetime.fromisoformat(claimed_at.replace("Z", "+00:00"))
    lease_expires_at = lease_expires_at.timestamp() + lease_seconds
    lease_expires_iso = datetime.fromtimestamp(lease_expires_at, tz=timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        """
        INSERT INTO task_runtime_state (
            task_id,
            runtime_status,
            queue_name,
            claimed_by,
            claimed_at,
            started_at,
            finished_at,
            pending_review_at,
            last_runtime_error,
            retry_count,
            last_transition_at,
            runtime_metadata_json
        )
        VALUES (?, 'claimed', ?, ?, ?, NULL, NULL, NULL, NULL, COALESCE((SELECT retry_count FROM task_runtime_state WHERE task_id = ?), 0), ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            runtime_status = 'claimed',
            queue_name = excluded.queue_name,
            claimed_by = excluded.claimed_by,
            claimed_at = excluded.claimed_at,
            last_transition_at = excluded.last_transition_at,
            runtime_metadata_json = excluded.runtime_metadata_json
        """,
        (
            snapshot["task_id"],
            queue_name,
            worker_id,
            claimed_at,
            snapshot["task_id"],
            claimed_at,
            compact_json({"claim_source": actor_id}),
        ),
    )
    conn.execute(
        """
        INSERT INTO task_active_leases (
            task_id,
            lease_owner_kind,
            lease_owner_id,
            assignment_state,
            lease_acquired_at,
            lease_expires_at,
            last_heartbeat_at,
            execution_run_id,
            lease_metadata_json
        )
        VALUES (?, 'worker', ?, 'claimed', ?, ?, ?, ?, ?)
        """,
        (
            snapshot["task_id"],
            worker_id,
            claimed_at,
            lease_expires_iso,
            claimed_at,
            f"{snapshot['task_id']}-{int(datetime.now(timezone.utc).timestamp())}",
            compact_json({"queue_name": queue_name, "lease_seconds": lease_seconds}),
        ),
    )
    conn.execute(
        """
        INSERT INTO task_assignments (task_id, assignee_kind, assignee_id, assignment_state, assigned_at, released_at, notes)
        VALUES (?, 'worker', ?, 'claimed', ?, NULL, ?)
        """,
        (
            snapshot["task_id"],
            worker_id,
            claimed_at,
            f"queue={queue_name}",
        ),
    )
    insert_event(
        conn,
        task_id=snapshot["task_id"],
        event_type="runtime.task_claimed",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={"worker_id": worker_id, "queue_name": queue_name, "lease_expires_at": lease_expires_iso},
    )
    conn.commit()
    return fetch_task_snapshots(conn, task_id=snapshot["task_id"])[0]


def runtime_heartbeat(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    worker_id: str,
    lease_seconds: int,
    actor_id: str,
) -> dict[str, Any]:
    begin_immediate(conn)
    lease = fetch_active_lease(conn, task_id)
    if lease is None:
        conn.rollback()
        die(f"no active lease for {task_id}")
    if str(lease["lease_owner_id"]) != worker_id:
        conn.rollback()
        die(f"lease owner mismatch for {task_id}: expected {lease['lease_owner_id']}, got {worker_id}")
    heartbeat_at = now_iso()
    lease_expires_at = datetime.fromisoformat(heartbeat_at.replace("Z", "+00:00"))
    lease_expires_at = lease_expires_at.timestamp() + lease_seconds
    lease_expires_iso = datetime.fromtimestamp(lease_expires_at, tz=timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        """
        UPDATE task_active_leases
        SET lease_expires_at = ?,
            last_heartbeat_at = ?
        WHERE task_id = ? AND lease_owner_id = ?
        """,
        (lease_expires_iso, heartbeat_at, task_id, worker_id),
    )
    insert_event(
        conn,
        task_id=task_id,
        event_type="runtime.heartbeat",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={"worker_id": worker_id, "lease_expires_at": lease_expires_iso},
    )
    conn.commit()
    return fetch_task_snapshots(conn, task_id=task_id)[0]


def runtime_transition(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    status: str,
    worker_id: str | None,
    error_text: str | None,
    notes: str | None,
    artifacts: list[str],
    actor_id: str,
) -> dict[str, Any]:
    if status not in RUNTIME_STATUSES:
        die(f"invalid runtime status: {status}")
    begin_immediate(conn)
    runtime_row = conn.execute("SELECT * FROM task_runtime_state WHERE task_id = ?", (task_id,)).fetchone()
    if runtime_row is None:
        conn.rollback()
        die(f"runtime state missing for {task_id}")
    lease = fetch_active_lease(conn, task_id)
    if lease is not None and worker_id is not None and str(lease["lease_owner_id"]) != worker_id:
        conn.rollback()
        die(f"lease owner mismatch for {task_id}: expected {lease['lease_owner_id']}, got {worker_id}")
    transition_at = now_iso()
    current = row_to_dict(runtime_row) or {}
    queue_name = current.get("queue_name")
    claimed_by = current.get("claimed_by")
    claimed_at = current.get("claimed_at")
    started_at = current.get("started_at")
    finished_at = current.get("finished_at")
    pending_review_at = current.get("pending_review_at")
    retry_count = int(current.get("retry_count") or 0)
    if status == "running":
        started_at = started_at or transition_at
    if status in {"pending_review", "failed", "timeout", "canceled", "done"}:
        finished_at = transition_at
    if status == "pending_review":
        pending_review_at = transition_at
    elif status != current.get("runtime_status"):
        pending_review_at = None if status not in {"pending_review"} else pending_review_at
    if status in {"failed", "timeout"}:
        retry_count += 1
    conn.execute(
        """
        UPDATE task_runtime_state
        SET runtime_status = ?,
            queue_name = ?,
            claimed_by = ?,
            claimed_at = ?,
            started_at = ?,
            finished_at = ?,
            pending_review_at = ?,
            last_runtime_error = ?,
            retry_count = ?,
            last_transition_at = ?,
            runtime_metadata_json = ?
        WHERE task_id = ?
        """,
        (
            status,
            queue_name,
            claimed_by,
            claimed_at,
            started_at,
            finished_at,
            pending_review_at,
            error_text,
            retry_count,
            transition_at,
            compact_json({"notes": notes} if notes else {}),
            task_id,
        ),
    )
    if status in {"pending_review", "failed", "timeout", "canceled", "done"}:
        if lease is not None:
            close_active_assignments(conn, task_id=task_id, assignee_kind="worker", assignee_id=str(lease["lease_owner_id"]))
            conn.execute("DELETE FROM task_active_leases WHERE task_id = ?", (task_id,))
    for artifact in artifacts:
        insert_artifact(
            conn,
            task_id=task_id,
            artifact_kind="runtime_artifact",
            path_or_uri=artifact,
            label=Path(artifact).name,
            metadata={"runtime_status": status},
        )
    insert_event(
        conn,
        task_id=task_id,
        event_type="runtime.status_transition",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={"status": status, "worker_id": worker_id, "error": error_text, "notes": notes, "artifacts": artifacts},
    )
    conn.commit()
    return fetch_task_snapshots(conn, task_id=task_id)[0]


def runtime_recover_stale(conn: sqlite3.Connection, *, limit: int, actor_id: str) -> dict[str, Any]:
    begin_immediate(conn)
    now_value = now_iso()
    rows = conn.execute(
        """
        SELECT task_id, lease_owner_id, lease_expires_at
        FROM task_active_leases
        WHERE lease_expires_at < ?
        ORDER BY lease_expires_at ASC
        LIMIT ?
        """,
        (now_value, limit),
    ).fetchall()
    recovered: list[dict[str, Any]] = []
    for row in rows:
        task_id = str(row["task_id"])
        worker_id = str(row["lease_owner_id"])
        conn.execute(
            """
            UPDATE task_runtime_state
            SET runtime_status = 'queued',
                claimed_by = NULL,
                claimed_at = NULL,
                last_transition_at = ?,
                last_runtime_error = ?,
                retry_count = retry_count + 1,
                runtime_metadata_json = ?
            WHERE task_id = ?
            """,
            (
                now_value,
                "stale lease recovered",
                compact_json({"recovered_at": now_value, "prior_worker_id": worker_id}),
                task_id,
            ),
        )
        close_active_assignments(conn, task_id=task_id, assignee_kind="worker", assignee_id=worker_id)
        conn.execute("DELETE FROM task_active_leases WHERE task_id = ?", (task_id,))
        insert_event(
            conn,
            task_id=task_id,
            event_type="runtime.stale_lease_recovered",
            actor_kind="runtime",
            actor_id=actor_id,
            payload={"worker_id": worker_id, "expired_at": row["lease_expires_at"]},
        )
        recovered.append({"task_id": task_id, "worker_id": worker_id, "expired_at": row["lease_expires_at"]})
    conn.commit()
    return {"recovered_count": len(recovered), "recovered": recovered, "recovered_at": now_value}


def parse_task_markdown(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        die(f"invalid bootstrap task file header: {path}")
    heading = lines[0][2:].strip()
    heading_parts = heading.split(" ", 1)
    task_id = heading_parts[0].strip()
    title = heading_parts[1].strip() if len(heading_parts) > 1 else task_id
    sections = parse_sections(text)
    metadata = parse_markdown_key_values(sections.get("Task Metadata", ""))
    execution = parse_markdown_key_values(sections.get("Execution Settings", ""))
    target_repo_root = metadata.get("Target Repo", str(REPO_ROOT))
    target_repo_id = normalize_repo_id(target_repo_root, fallback=metadata.get("Task ID", task_id))
    additional_dirs = parse_json_text(execution.get("Additional Writable Dirs"), default=[])
    payload = {
        "task_id": metadata.get("Task ID", task_id),
        "title": title,
        "summary": markdown_summary(sections.get("Objective", ""), fallback=title),
        "objective_md": sections.get("Objective", "Imported from bootstrap markdown."),
        "context_md": sections.get("Context", "Imported from bootstrap markdown."),
        "scope_md": sections.get("Scope Boundaries", "Imported from bootstrap markdown."),
        "deliverables_md": sections.get("Deliverables", "Imported from bootstrap markdown."),
        "acceptance_md": sections.get("Acceptance", "Imported from bootstrap markdown."),
        "testing_md": sections.get("Testing", "Imported from bootstrap markdown."),
        "dispatch_md": sections.get("Dispatch Contract", "Imported from bootstrap markdown."),
        "closeout_md": sections.get("Closeout Contract", "Imported from bootstrap markdown."),
        "reconciliation_md": sections.get("Repo Reconciliation", "Imported from bootstrap markdown."),
        "planner_status": metadata.get("Status", "todo"),
        "priority": parse_int(execution.get("Priority", 100), field=f"{task_id}.Priority"),
        "task_type": metadata.get("Task Type", "planning"),
        "planner_owner": metadata.get("Planner Owner", "planner/coordinator"),
        "worker_owner": normalize_optional_owner(metadata.get("Worker Owner")),
        "target_repo_id": target_repo_id,
        "target_repo_root": target_repo_root,
        "target_repo_display_name": target_repo_id,
        "approval_required": parse_bool(execution.get("Approval Required", "false"), field=f"{task_id}.Approval Required"),
        "source_kind": "bootstrap_markdown",
        "metadata": {
            "bootstrap_source_path": str(path),
            "bootstrap_summary_record": metadata.get("Summary Record"),
            "bootstrap_source_of_truth_note": metadata.get("Source Of Truth"),
            "bootstrap_import_kind": "task_file",
        },
        "execution": {
            "task_kind": execution.get("Task Kind", "mutating"),
            "sandbox_mode": execution.get("Sandbox Mode"),
            "approval_policy": execution.get("Approval Policy"),
            "additional_writable_dirs": additional_dirs if isinstance(additional_dirs, list) else [],
            "timeout_seconds": parse_int(execution.get("Timeout Seconds", 1800), field=f"{task_id}.Timeout Seconds"),
            "metadata": {},
        },
        "dependencies": re.findall(r"`([^`]+)`", sections.get("Dependencies", "")),
    }
    return payload


def parse_packet_tasks(path: Path) -> dict[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    matches = list(TASK_PACKET_RE.finditer(text))
    payloads: dict[str, dict[str, Any]] = {}
    for index, match in enumerate(matches):
        task_id = match.group(1)
        title = match.group(2).strip()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[start:end]
        sections = parse_sections(block)
        repo_section = sections.get("Repo", "")
        repo_match = re.search(r"Primary repo: `([^`]+)`", repo_section)
        repo_root = repo_match.group(1) if repo_match else str(REPO_ROOT)
        repo_id = normalize_repo_id(repo_root, fallback="CENTRAL")
        status_section = sections.get("Status", "")
        status_match = re.search(r"`([^`]+)`", status_section)
        status = status_match.group(1) if status_match else "todo"
        objective_md = sections.get("Objective", "Imported from bootstrap packet only.")
        payloads[task_id] = {
            "task_id": task_id,
            "title": title,
            "summary": markdown_summary(objective_md, fallback=title),
            "objective_md": objective_md,
            "context_md": sections.get("Context", "Imported from bootstrap packet only."),
            "scope_md": "Imported from bootstrap packet only; no dedicated markdown task file existed.",
            "deliverables_md": sections.get("Deliverables", "Imported from bootstrap packet only."),
            "acceptance_md": sections.get("Acceptance Criteria", "Imported from bootstrap packet only."),
            "testing_md": sections.get("Testing", "Imported from bootstrap packet only."),
            "dispatch_md": f"Dispatch from CENTRAL using repo=CENTRAL do task {task_id}.",
            "closeout_md": f"Imported from bootstrap packet only for {task_id}.",
            "reconciliation_md": "Imported from bootstrap packet only; DB-canonical model supersedes markdown.",
            "planner_status": status,
            "priority": 100,
            "task_type": "planning",
            "planner_owner": "planner/coordinator",
            "worker_owner": None,
            "target_repo_id": repo_id,
            "target_repo_root": repo_root,
            "target_repo_display_name": repo_id,
            "approval_required": False,
            "source_kind": "bootstrap_packet",
            "metadata": {
                "bootstrap_source_path": str(path),
                "bootstrap_import_kind": "packet_only",
                "bootstrap_notes_md": sections.get("Notes", ""),
            },
            "execution": {
                "task_kind": "mutating",
                "sandbox_mode": "workspace-write",
                "approval_policy": "never",
                "additional_writable_dirs": [],
                "timeout_seconds": 1800,
                "metadata": {"imported_from_packet_only": True},
            },
            "dependencies": [],
        }
    return payloads


def migrate_bootstrap(
    conn: sqlite3.Connection,
    *,
    tasks_dir: Path,
    packet_path: Path,
    actor_id: str,
    update_existing: bool,
) -> dict[str, Any]:
    payloads: dict[str, dict[str, Any]] = {}
    if tasks_dir.is_dir():
        for path in sorted(tasks_dir.iterdir()):
            if path.is_file() and TASK_FILE_NAME_RE.match(path.name):
                payloads[path.stem] = parse_task_markdown(path)
    if packet_path.exists():
        for task_id, payload in parse_packet_tasks(packet_path).items():
            payloads.setdefault(task_id, payload)
    imported: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    begin_immediate(conn)
    for task_id in sorted(payloads.keys()):
        payload = payloads[task_id]
        existing = fetch_task_row(conn, task_id)
        if existing is not None and not update_existing:
            skipped.append(task_id)
            continue
        if existing is not None and update_existing:
            current = fetch_task_snapshots(conn, task_id=task_id)[0]
            patch_payload = dict(payload)
            patch_payload.pop("task_id", None)
            update_task(
                conn,
                task_id=task_id,
                expected_version=int(current["version"]),
                payload=patch_payload,
                actor_kind="migration",
                actor_id=actor_id,
                allow_active_lease=True,
            )
            insert_event(
                conn,
                task_id=task_id,
                event_type="migration.bootstrap_updated",
                actor_kind="migration",
                actor_id=actor_id,
                payload={"source": payload["metadata"].get("bootstrap_source_path")},
            )
            updated.append(task_id)
            continue
        create_task(conn, payload, actor_kind="migration", actor_id=actor_id)
        insert_event(
            conn,
            task_id=task_id,
            event_type="migration.bootstrap_imported",
            actor_kind="migration",
            actor_id=actor_id,
            payload={"source": payload["metadata"].get("bootstrap_source_path"), "import_kind": payload["metadata"].get("bootstrap_import_kind")},
        )
        insert_artifact(
            conn,
            task_id=task_id,
            artifact_kind="bootstrap_source",
            path_or_uri=payload["metadata"].get("bootstrap_source_path", ""),
            label=payload["metadata"].get("bootstrap_import_kind"),
            metadata={"imported_by": actor_id},
        )
        imported.append(task_id)
    conn.commit()
    return {
        "imported_count": len(imported),
        "updated_count": len(updated),
        "skipped_count": len(skipped),
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "tasks_dir": str(tasks_dir),
        "packet_path": str(packet_path),
    }


def print_or_json(payload: Any, *, as_json: bool, formatter: Any | None = None) -> int:
    if as_json:
        print(json_dumps(payload))
        return 0
    if formatter is not None:
        print(formatter(payload))
    elif isinstance(payload, str):
        print(payload)
    else:
        print(payload)
    return 0


def command_init(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db_path)
    migrations_dir = resolve_migrations_dir(args.migrations_dir)
    migrations = load_migrations(migrations_dir)
    conn = connect(db_path)
    try:
        applied, skipped = apply_migrations(conn, migrations)
        payload = {
            "db_path": str(db_path),
            "migrations_dir": str(migrations_dir),
            "applied_count": len(applied),
            "already_applied_count": len(skipped),
            "latest_version": migrations[-1].version,
            "tables": fetch_tables(conn),
        }
    finally:
        conn.close()
    return print_or_json(payload, as_json=args.json, formatter=lambda data: "\n".join([
        f"DB path:         {data['db_path']}",
        f"Migrations dir:  {data['migrations_dir']}",
        f"Applied now:     {data['applied_count']}",
        f"Already applied: {data['already_applied_count']}",
        f"Latest version:  {data['latest_version']}",
        "Tables:",
        *[f"- {table}" for table in data['tables']],
    ]))


def command_status(args: argparse.Namespace) -> int:
    db_path = resolve_db_path(args.db_path)
    migrations_dir = resolve_migrations_dir(args.migrations_dir)
    migrations = load_migrations(migrations_dir)
    exists = db_path.exists()
    payload: dict[str, Any] = {
        "db_path": str(db_path),
        "migrations_dir": str(migrations_dir),
        "exists": exists,
        "available_migrations": [migration.path.name for migration in migrations],
    }
    if exists:
        conn = connect(db_path)
        try:
            ensure_migration_table(conn)
            applied = applied_migrations(conn)
            payload["applied_migrations"] = [row["name"] for row in applied.values()]
            payload["pending_migrations"] = [migration.path.name for migration in migrations if migration.version not in applied]
            payload["tables"] = fetch_tables(conn)
        finally:
            conn.close()
    else:
        payload["applied_migrations"] = []
        payload["pending_migrations"] = [migration.path.name for migration in migrations]
        payload["tables"] = []
    return print_or_json(payload, as_json=args.json, formatter=lambda data: "\n".join([
        f"DB path:           {data['db_path']}",
        f"Exists:            {data['exists']}",
        f"Available:         {len(data['available_migrations'])} migration(s)",
        f"Applied:           {len(data['applied_migrations'])} migration(s)",
        f"Pending:           {len(data['pending_migrations'])} migration(s)",
        *( ["Tables:"] + [f"- {table}" for table in data['tables']] if data['tables'] else [] ),
    ]))


def open_initialized_connection(db_path_arg: str | None) -> tuple[sqlite3.Connection, Path]:
    db_path = resolve_db_path(db_path_arg)
    conn = connect(db_path)
    require_initialized_db(conn, db_path)
    return conn, db_path


def command_repo_upsert(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        with conn:
            ensure_repo(
                conn,
                repo_id=args.repo_id,
                repo_root=args.repo_root,
                display_name=args.display_name or args.repo_id,
                metadata=parse_json_text(args.metadata_json, default={}),
            )
        payload = row_to_dict(conn.execute("SELECT * FROM repos WHERE repo_id = ?", (args.repo_id,)).fetchone())
    finally:
        conn.close()
    return print_or_json(payload, as_json=args.json, formatter=lambda row: render_table([row], [("repo_id", "repo_id"), ("display_name", "display_name"), ("repo_root", "repo_root"), ("active", "is_active")]))


def command_task_create(args: argparse.Namespace) -> int:
    payload = load_json_document(args.input)
    conn, _ = open_initialized_connection(args.db_path)
    try:
        with conn:
            snapshot = create_task(conn, payload, actor_kind="planner", actor_id=args.actor_id)
    finally:
        conn.close()
    return print_or_json(render_task_card(snapshot), as_json=args.json, formatter=json_dumps)


def command_task_update(args: argparse.Namespace) -> int:
    payload = load_json_document(args.input)
    conn, _ = open_initialized_connection(args.db_path)
    try:
        with conn:
            snapshot = update_task(
                conn,
                task_id=args.task_id,
                expected_version=args.expected_version,
                payload=payload,
                actor_kind="planner",
                actor_id=args.actor_id,
                allow_active_lease=args.allow_active_lease,
            )
    finally:
        conn.close()
    return print_or_json(render_task_card(snapshot), as_json=args.json, formatter=json_dumps)


def command_task_reconcile(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        with conn:
            snapshot = reconcile_task(
                conn,
                task_id=args.task_id,
                expected_version=args.expected_version,
                outcome=args.outcome,
                summary=args.summary,
                notes=args.notes,
                tests=args.tests,
                artifacts=args.artifact,
                actor_kind="planner",
                actor_id=args.actor_id,
            )
    finally:
        conn.close()
    return print_or_json(render_task_card(snapshot), as_json=args.json, formatter=json_dumps)


def command_task_show(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshots = fetch_task_snapshots(conn, task_id=args.task_id)
        if not snapshots:
            die(f"task not found: {args.task_id}")
        payload = render_task_card(snapshots[0])
        payload["events"] = fetch_latest_events(conn, args.task_id, limit=10)
        payload["artifacts"] = fetch_artifacts(conn, args.task_id)
    finally:
        conn.close()
    return print_or_json(payload, as_json=args.json, formatter=json_dumps)


def command_task_list(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshots = fetch_task_snapshots(conn, repo_id=args.repo_id, planner_status=args.planner_status)
        rows = [
            {
                "task_id": snapshot["task_id"],
                "priority": snapshot["priority"],
                "planner_status": snapshot["planner_status"],
                "runtime_status": snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else "",
                "repo": snapshot["target_repo_id"],
                "planner_owner": snapshot["planner_owner"],
                "worker_owner": snapshot["worker_owner"] or "",
                "version": snapshot["version"],
                "title": snapshot["title"],
            }
            for snapshot in snapshots
        ]
    finally:
        conn.close()
    return print_or_json(rows, as_json=args.json, formatter=lambda data: render_table(data, [("task_id", "task_id"), ("p", "priority"), ("planner", "planner_status"), ("runtime", "runtime_status"), ("repo", "repo"), ("version", "version"), ("title", "title")]))


def command_view_summary(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        summary = summarize_portfolio(conn)
    finally:
        conn.close()
    return print_or_json(summary, as_json=args.json, formatter=format_summary_text)


def command_view_eligible(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshots = fetch_task_snapshots(conn, repo_id=args.repo_id)
        rows = format_eligible_rows(snapshots)
    finally:
        conn.close()
    return print_or_json(rows, as_json=args.json, formatter=lambda data: "\n".join([generated_banner(now_iso()), "", render_table(data, [("task_id", "task_id"), ("p", "priority"), ("repo", "repo"), ("planner", "planner_status"), ("runtime", "runtime_status"), ("owner", "planner_owner"), ("title", "title")])]))


def command_view_blocked(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        rows = format_blocked_rows(fetch_task_snapshots(conn))
    finally:
        conn.close()
    return print_or_json(rows, as_json=args.json, formatter=lambda data: "\n".join([generated_banner(now_iso()), "", render_table(data, [("task_id", "task_id"), ("repo", "repo"), ("planner_owner", "planner_owner"), ("blocked_at", "blocked_at"), ("blocker", "blocker")])]))


def command_view_repo(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshots = fetch_task_snapshots(conn, repo_id=args.repo_id)
        rows = [
            {
                "task_id": snapshot["task_id"],
                "priority": snapshot["priority"],
                "planner_status": snapshot["planner_status"],
                "runtime_status": snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else "",
                "dependency_blocked": "yes" if snapshot["dependency_blocked"] else "",
                "planner_owner": snapshot["planner_owner"],
                "worker_owner": snapshot["worker_owner"] or "",
                "lease_owner": snapshot["lease"]["lease_owner_id"] if snapshot["lease"] else "",
                "title": snapshot["title"],
            }
            for snapshot in snapshots
        ]
    finally:
        conn.close()
    return print_or_json(rows, as_json=args.json, formatter=lambda data: "\n".join([generated_banner(now_iso()), "", render_table(data, [("task_id", "task_id"), ("p", "priority"), ("planner", "planner_status"), ("runtime", "runtime_status"), ("dep_blocked", "dependency_blocked"), ("planner_owner", "planner_owner"), ("lease_owner", "lease_owner"), ("title", "title")])]))


def command_view_assignments(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        rows = format_assignments_rows(fetch_task_snapshots(conn))
    finally:
        conn.close()
    return print_or_json(rows, as_json=args.json, formatter=lambda data: "\n".join([generated_banner(now_iso()), "", render_table(data, [("task_id", "task_id"), ("repo", "repo"), ("planner_owner", "planner_owner"), ("worker_owner", "worker_owner"), ("lease_owner", "lease_owner"), ("lease_expires_at", "lease_expires_at"), ("runtime", "runtime_status")])]))


def command_view_review(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        rows = format_review_rows(fetch_task_snapshots(conn))
    finally:
        conn.close()
    return print_or_json(rows, as_json=args.json, formatter=lambda data: "\n".join([generated_banner(now_iso()), "", render_table(data, [("task_id", "task_id"), ("repo", "repo"), ("runtime", "runtime_status"), ("planner", "planner_status"), ("age_at", "age_at"), ("claimed_by", "claimed_by"), ("last_error", "last_error")])]))


def command_view_task_card(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshots = fetch_task_snapshots(conn, task_id=args.task_id)
        if not snapshots:
            die(f"task not found: {args.task_id}")
        payload = render_task_card(snapshots[0])
    finally:
        conn.close()
    return print_or_json(payload, as_json=args.json, formatter=json_dumps)


def command_export_summary_md(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        summary = summarize_portfolio(conn)
    finally:
        conn.close()
    output_path = Path(args.output).expanduser().resolve() if args.output else (DEFAULT_GENERATED_DIR / "portfolio_summary.md")
    write_output(output_path, render_summary_markdown(summary))
    payload = {"output_path": str(output_path), "generated_at": summary["generated_at"]}
    return print_or_json(payload, as_json=args.json, formatter=json_dumps)


def command_export_task_card_md(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshots = fetch_task_snapshots(conn, task_id=args.task_id)
        if not snapshots:
            die(f"task not found: {args.task_id}")
        snapshot = snapshots[0]
    finally:
        conn.close()
    generated_at = now_iso()
    output_path = Path(args.output).expanduser().resolve() if args.output else (DEFAULT_GENERATED_DIR / "task_cards" / f"{args.task_id}.md")
    write_output(output_path, render_task_card_markdown(snapshot, generated_at=generated_at))
    payload = {"output_path": str(output_path), "generated_at": generated_at, "task_id": args.task_id}
    return print_or_json(payload, as_json=args.json, formatter=json_dumps)


def command_runtime_eligible(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        rows = format_eligible_rows(fetch_task_snapshots(conn, repo_id=args.repo_id))
    finally:
        conn.close()
    if args.limit is not None:
        rows = rows[: args.limit]
    return print_or_json(rows, as_json=args.json, formatter=lambda data: "\n".join([generated_banner(now_iso()), "", render_table(data, [("task_id", "task_id"), ("p", "priority"), ("repo", "repo"), ("planner", "planner_status"), ("runtime", "runtime_status"), ("owner", "planner_owner"), ("title", "title")])]))


def command_runtime_claim(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshot = runtime_claim(
            conn,
            worker_id=args.worker_id,
            queue_name=args.queue_name,
            lease_seconds=args.lease_seconds,
            task_id=args.task_id,
            actor_id=args.actor_id,
        )
    finally:
        conn.close()
    return print_or_json(render_task_card(snapshot), as_json=args.json, formatter=json_dumps)


def command_runtime_heartbeat(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshot = runtime_heartbeat(
            conn,
            task_id=args.task_id,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
            actor_id=args.actor_id,
        )
    finally:
        conn.close()
    return print_or_json(render_task_card(snapshot), as_json=args.json, formatter=json_dumps)


def command_runtime_transition(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshot = runtime_transition(
            conn,
            task_id=args.task_id,
            status=args.status,
            worker_id=args.worker_id,
            error_text=args.error_text,
            notes=args.notes,
            artifacts=args.artifact,
            actor_id=args.actor_id,
        )
    finally:
        conn.close()
    return print_or_json(render_task_card(snapshot), as_json=args.json, formatter=json_dumps)


def command_runtime_recover_stale(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        payload = runtime_recover_stale(conn, limit=args.limit, actor_id=args.actor_id)
    finally:
        conn.close()
    return print_or_json(payload, as_json=args.json, formatter=json_dumps)


def command_migrate_bootstrap(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        payload = migrate_bootstrap(
            conn,
            tasks_dir=Path(args.tasks_dir).expanduser().resolve(),
            packet_path=Path(args.packet_path).expanduser().resolve(),
            actor_id=args.actor_id,
            update_existing=args.update_existing,
        )
    finally:
        conn.close()
    return print_or_json(payload, as_json=args.json, formatter=json_dumps)


def add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db-path", help="SQLite DB path. Defaults to CENTRAL_TASK_DB_PATH or CENTRAL/state/central_tasks.db")


def add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Print structured output.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the canonical CENTRAL SQLite task database.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or upgrade the CENTRAL task DB by applying explicit migrations.")
    add_db_argument(init_parser)
    init_parser.add_argument("--migrations-dir", help="Directory containing SQL migration files.")
    add_json_argument(init_parser)
    init_parser.set_defaults(func=command_init)

    status_parser = subparsers.add_parser("status", help="Show DB existence, applied migrations, and table inventory.")
    add_db_argument(status_parser)
    status_parser.add_argument("--migrations-dir", help="Directory containing SQL migration files.")
    add_json_argument(status_parser)
    status_parser.set_defaults(func=command_status)

    repo_upsert_parser = subparsers.add_parser("repo-upsert", help="Create or update a repo row in the CENTRAL DB.")
    add_db_argument(repo_upsert_parser)
    repo_upsert_parser.add_argument("--repo-id", required=True)
    repo_upsert_parser.add_argument("--repo-root", required=True)
    repo_upsert_parser.add_argument("--display-name")
    repo_upsert_parser.add_argument("--metadata-json", help="JSON metadata object.")
    add_json_argument(repo_upsert_parser)
    repo_upsert_parser.set_defaults(func=command_repo_upsert)

    task_create_parser = subparsers.add_parser("task-create", help="Create a planner-owned task from JSON input.")
    add_db_argument(task_create_parser)
    task_create_parser.add_argument("--input", required=True, help="Path to JSON payload, or - for stdin.")
    task_create_parser.add_argument("--actor-id", default="planner/coordinator")
    add_json_argument(task_create_parser)
    task_create_parser.set_defaults(func=command_task_create)

    task_update_parser = subparsers.add_parser("task-update", help="Update a planner-owned task from JSON input.")
    add_db_argument(task_update_parser)
    task_update_parser.add_argument("--task-id", required=True)
    task_update_parser.add_argument("--expected-version", required=True, type=int)
    task_update_parser.add_argument("--input", required=True, help="Path to JSON patch payload, or - for stdin.")
    task_update_parser.add_argument("--actor-id", default="planner/coordinator")
    task_update_parser.add_argument("--allow-active-lease", action="store_true")
    add_json_argument(task_update_parser)
    task_update_parser.set_defaults(func=command_task_update)

    task_reconcile_parser = subparsers.add_parser("task-reconcile", help="Reconcile planner closeout for done or blocked outcomes.")
    add_db_argument(task_reconcile_parser)
    task_reconcile_parser.add_argument("--task-id", required=True)
    task_reconcile_parser.add_argument("--expected-version", required=True, type=int)
    task_reconcile_parser.add_argument("--outcome", required=True, choices=["done", "blocked"])
    task_reconcile_parser.add_argument("--summary", required=True)
    task_reconcile_parser.add_argument("--notes")
    task_reconcile_parser.add_argument("--tests")
    task_reconcile_parser.add_argument("--artifact", action="append", default=[])
    task_reconcile_parser.add_argument("--actor-id", default="planner/coordinator")
    add_json_argument(task_reconcile_parser)
    task_reconcile_parser.set_defaults(func=command_task_reconcile)

    task_show_parser = subparsers.add_parser("task-show", help="Show a task card plus recent events and artifacts.")
    add_db_argument(task_show_parser)
    task_show_parser.add_argument("--task-id", required=True)
    add_json_argument(task_show_parser)
    task_show_parser.set_defaults(func=command_task_show)

    task_list_parser = subparsers.add_parser("task-list", help="List tasks from the CENTRAL DB.")
    add_db_argument(task_list_parser)
    task_list_parser.add_argument("--repo-id")
    task_list_parser.add_argument("--planner-status", choices=sorted(PLANNER_STATUSES))
    add_json_argument(task_list_parser)
    task_list_parser.set_defaults(func=command_task_list)

    view_summary_parser = subparsers.add_parser("view-summary", help="Show portfolio summary generated from DB state.")
    add_db_argument(view_summary_parser)
    add_json_argument(view_summary_parser)
    view_summary_parser.set_defaults(func=command_view_summary)

    view_eligible_parser = subparsers.add_parser("view-eligible", help="Show eligible dispatch work from DB state.")
    add_db_argument(view_eligible_parser)
    view_eligible_parser.add_argument("--repo-id")
    add_json_argument(view_eligible_parser)
    view_eligible_parser.set_defaults(func=command_view_eligible)

    view_blocked_parser = subparsers.add_parser("view-blocked", help="Show blocked planner work from DB state.")
    add_db_argument(view_blocked_parser)
    add_json_argument(view_blocked_parser)
    view_blocked_parser.set_defaults(func=command_view_blocked)

    view_repo_parser = subparsers.add_parser("view-repo", help="Show per-repo queue state from DB state.")
    add_db_argument(view_repo_parser)
    view_repo_parser.add_argument("--repo-id", required=True)
    add_json_argument(view_repo_parser)
    view_repo_parser.set_defaults(func=command_view_repo)

    view_assignments_parser = subparsers.add_parser("view-assignments", help="Show planner assignments and active leases.")
    add_db_argument(view_assignments_parser)
    add_json_argument(view_assignments_parser)
    view_assignments_parser.set_defaults(func=command_view_assignments)

    view_review_parser = subparsers.add_parser("view-review", help="Show pending review, failed, and timeout runtime work.")
    add_db_argument(view_review_parser)
    add_json_argument(view_review_parser)
    view_review_parser.set_defaults(func=command_view_review)

    view_task_parser = subparsers.add_parser("view-task-card", help="Show a task detail card from DB state.")
    add_db_argument(view_task_parser)
    view_task_parser.add_argument("--task-id", required=True)
    add_json_argument(view_task_parser)
    view_task_parser.set_defaults(func=command_view_task_card)

    export_summary_parser = subparsers.add_parser("export-summary-md", help="Write a non-canonical markdown portfolio summary.")
    add_db_argument(export_summary_parser)
    export_summary_parser.add_argument("--output", help="Output path. Defaults to CENTRAL/generated/portfolio_summary.md")
    add_json_argument(export_summary_parser)
    export_summary_parser.set_defaults(func=command_export_summary_md)

    export_task_parser = subparsers.add_parser("export-task-card-md", help="Write a non-canonical markdown task card export.")
    add_db_argument(export_task_parser)
    export_task_parser.add_argument("--task-id", required=True)
    export_task_parser.add_argument("--output", help="Output path. Defaults to CENTRAL/generated/task_cards/<task_id>.md")
    add_json_argument(export_task_parser)
    export_task_parser.set_defaults(func=command_export_task_card_md)

    runtime_eligible_parser = subparsers.add_parser("runtime-eligible", help="Show runtime-claimable tasks from DB state.")
    add_db_argument(runtime_eligible_parser)
    runtime_eligible_parser.add_argument("--repo-id")
    runtime_eligible_parser.add_argument("--limit", type=int)
    add_json_argument(runtime_eligible_parser)
    runtime_eligible_parser.set_defaults(func=command_runtime_eligible)

    runtime_claim_parser = subparsers.add_parser("runtime-claim", help="Atomically claim eligible work and create a lease.")
    add_db_argument(runtime_claim_parser)
    runtime_claim_parser.add_argument("--worker-id", required=True)
    runtime_claim_parser.add_argument("--queue-name", default="default")
    runtime_claim_parser.add_argument("--lease-seconds", type=int, default=900)
    runtime_claim_parser.add_argument("--task-id")
    runtime_claim_parser.add_argument("--actor-id", default="dispatcher")
    add_json_argument(runtime_claim_parser)
    runtime_claim_parser.set_defaults(func=command_runtime_claim)

    runtime_heartbeat_parser = subparsers.add_parser("runtime-heartbeat", help="Renew an active lease heartbeat.")
    add_db_argument(runtime_heartbeat_parser)
    runtime_heartbeat_parser.add_argument("--task-id", required=True)
    runtime_heartbeat_parser.add_argument("--worker-id", required=True)
    runtime_heartbeat_parser.add_argument("--lease-seconds", type=int, default=900)
    runtime_heartbeat_parser.add_argument("--actor-id", default="dispatcher")
    add_json_argument(runtime_heartbeat_parser)
    runtime_heartbeat_parser.set_defaults(func=command_runtime_heartbeat)

    runtime_transition_parser = subparsers.add_parser("runtime-transition", help="Record a runtime status transition.")
    add_db_argument(runtime_transition_parser)
    runtime_transition_parser.add_argument("--task-id", required=True)
    runtime_transition_parser.add_argument("--status", required=True, choices=sorted(RUNTIME_STATUSES))
    runtime_transition_parser.add_argument("--worker-id")
    runtime_transition_parser.add_argument("--error-text")
    runtime_transition_parser.add_argument("--notes")
    runtime_transition_parser.add_argument("--artifact", action="append", default=[])
    runtime_transition_parser.add_argument("--actor-id", default="dispatcher")
    add_json_argument(runtime_transition_parser)
    runtime_transition_parser.set_defaults(func=command_runtime_transition)

    runtime_recover_parser = subparsers.add_parser("runtime-recover-stale", help="Recover expired leases into reclaimable queued state.")
    add_db_argument(runtime_recover_parser)
    runtime_recover_parser.add_argument("--limit", type=int, default=50)
    runtime_recover_parser.add_argument("--actor-id", default="dispatcher")
    add_json_argument(runtime_recover_parser)
    runtime_recover_parser.set_defaults(func=command_runtime_recover_stale)

    migrate_parser = subparsers.add_parser("migrate-bootstrap", help="Import bootstrap markdown task records into the CENTRAL DB.")
    add_db_argument(migrate_parser)
    migrate_parser.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    migrate_parser.add_argument("--packet-path", default=str(DEFAULT_PACKET_PATH))
    migrate_parser.add_argument("--actor-id", default="migration/bootstrap")
    migrate_parser.add_argument("--update-existing", action="store_true")
    add_json_argument(migrate_parser)
    migrate_parser.set_defaults(func=command_migrate_bootstrap)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
