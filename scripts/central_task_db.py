#!/usr/bin/env python3
"""Manage the canonical CENTRAL SQLite task database."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import sqlite3
import sys
import uuid
try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False
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
DEFAULT_DURABILITY_DIR = REPO_ROOT / "durability" / "central_db"
PLANNER_STATUSES = {"todo", "in_progress", "awaiting_audit", "failed", "done"}
RUNTIME_STATUSES = {"queued", "claimed", "running", "pending_review", "failed", "timeout", "canceled", "done"}
ACTIVE_RUNTIME_STATUSES = {"claimed", "running", "pending_review"}
TERMINAL_RUNTIME_STATUSES = {"pending_review", "failed", "timeout", "canceled", "done"}
AUTO_RECONCILE_PLANNER_STATUSES = {"todo", "in_progress"}
TASK_ID_RESERVATION_STATUSES = {"active", "completed", "expired"}
DEFAULT_TASK_ID_SERIES = "CENTRAL-OPS"
DEFAULT_TASK_ID_RESERVATION_HOURS = 48
MAX_TASK_ID_RESERVATION_COUNT = 10
TASK_FILE_NAME_RE = re.compile(r"^CENTRAL-OPS-[0-9]+\\.md$")
TASK_ID_RE = re.compile(r"^(?P<series>[A-Z0-9]+(?:-[A-Z0-9]+)*)-(?P<number>[0-9]+)$")
TASK_ID_SERIES_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
REPO_LOOKUP_TOKEN_RE = re.compile(r"[^a-z0-9]+")
SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
KEY_VALUE_RE = re.compile(r"^- `([^`]+)`: (.+)$", re.MULTILINE)
TASK_PACKET_RE = re.compile(r"^## Task (CENTRAL-OPS-[0-9]+): (.+)$", re.MULTILINE)
SNAPSHOT_DB_FILENAME = "central_tasks.db"
SNAPSHOT_MANIFEST_FILENAME = "manifest.json"
SNAPSHOT_POINTER_FILENAME = "latest.json"


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


def resolve_durability_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    return DEFAULT_DURABILITY_DIR


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        die(f"database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
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
    required = {
        "repos",
        "repo_aliases",
        "tasks",
        "task_execution_settings",
        "task_dependencies",
        "task_events",
        "task_id_reservations",
        "task_id_reservation_events",
    }
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


def load_batch_document(path_value: str) -> dict[str, Any]:
    """Load a YAML or JSON batch file.

    Accepts two shapes:
    - A list of task items (shorthand).
    - An object with optional ``series``, ``repo``, ``defaults`` keys and a
      required ``tasks`` list.

    Returns a normalised dict with keys: series, repo, defaults, tasks.
    """
    if path_value == "-":
        raw = sys.stdin.read()
        source_fmt = "json"
    else:
        p = Path(path_value)
        raw = p.read_text(encoding="utf-8")
        source_fmt = "yaml" if p.suffix.lower() in {".yaml", ".yml"} else "json"

    parsed: Any = None
    if source_fmt == "yaml":
        if not _YAML_AVAILABLE:
            die("PyYAML is required for YAML batch files: pip install pyyaml")
        try:
            parsed = _yaml.safe_load(raw)
        except Exception as exc:
            die(f"invalid YAML in {path_value}: {exc}")
    else:
        # Try JSON first; fall back to YAML if suffix mismatch.
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            if _YAML_AVAILABLE:
                try:
                    parsed = _yaml.safe_load(raw)
                except Exception as exc:
                    die(f"invalid JSON/YAML in {path_value}: {exc}")
            else:
                die(f"invalid JSON in {path_value}")

    if isinstance(parsed, list):
        return {"series": None, "repo": None, "defaults": {}, "tasks": parsed}
    if isinstance(parsed, dict):
        tasks = parsed.get("tasks")
        if tasks is None:
            die("batch document must have a 'tasks' list or be a bare list")
        if not isinstance(tasks, list):
            die("batch 'tasks' must be a list")
        return {
            "series": parsed.get("series"),
            "repo": parsed.get("repo"),
            "defaults": parsed.get("defaults") or {},
            "tasks": tasks,
        }
    die(f"batch document must be a list or object, got {type(parsed).__name__}")


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


def normalize_repo_lookup_key(value: str) -> str:
    return REPO_LOOKUP_TOKEN_RE.sub("", value.strip().casefold())


def normalize_repo_root_key(value: str) -> str:
    return str(Path(value).expanduser()).rstrip("/\\").casefold()


def normalize_repo_aliases(aliases: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        cleaned = str(alias).strip()
        if not cleaned:
            die("repo aliases cannot be blank")
        if not normalize_repo_lookup_key(cleaned):
            die(f"repo alias must contain letters or digits: {alias!r}")
        if cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def shell_join(parts: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts if part)


def build_repo_onboarding_command(
    *,
    repo_id: str | None,
    repo_root: str | None,
    display_name: str | None = None,
    aliases: Iterable[str] | None = None,
    command_name: str = "repo-onboard",
) -> str:
    resolved_repo_id = (repo_id or "").strip()
    resolved_repo_root = (repo_root or "").strip()
    if not resolved_repo_id and resolved_repo_root:
        resolved_repo_id = normalize_repo_id(resolved_repo_root, fallback="REPO_ID")
    if not resolved_repo_id:
        resolved_repo_id = "REPO_ID"
    if not resolved_repo_root:
        resolved_repo_root = "/abs/path/to/repo"
    resolved_display_name = (display_name or resolved_repo_id).strip() or resolved_repo_id
    command = [
        "python3",
        str(SCRIPT_PATH),
        command_name,
        "--repo-id",
        resolved_repo_id,
        "--repo-root",
        resolved_repo_root,
    ]
    if resolved_display_name and resolved_display_name != resolved_repo_id:
        command.extend(["--display-name", resolved_display_name])
    for alias in normalize_repo_aliases(aliases or []):
        command.extend(["--alias", alias])
    return shell_join(command)


def normalize_task_id_series(series: str | None) -> str:
    candidate = (series or DEFAULT_TASK_ID_SERIES).strip().upper()
    if not candidate or not TASK_ID_SERIES_RE.match(candidate):
        die(f"invalid task ID series: {series!r}")
    return candidate


def parse_task_id(task_id: str) -> tuple[str, int]:
    canonical = task_id.strip().upper()
    # Strip well-known suffixes (e.g. -AUDIT) before matching
    for suffix in ("-AUDIT",):
        if canonical.endswith(suffix):
            canonical = canonical[: -len(suffix)]
            break
    match = TASK_ID_RE.match(canonical)
    if match is None:
        die(f"invalid task_id: {task_id!r}")
    return match.group("series"), int(match.group("number"))


def make_task_id(series: str, number: int) -> str:
    if number <= 0:
        die(f"task numbers must be positive: {number}")
    return f"{normalize_task_id_series(series)}-{number}"


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


def write_json_document(path: Path, payload: Any) -> None:
    write_output(path, json_dumps(payload) + "\n")


def generated_banner(generated_at: str) -> str:
    return f"Generated from CENTRAL DB at {generated_at}. Do not edit manually."


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def stable_sha256(payload: Any) -> str:
    return hashlib.sha256(compact_json(payload).encode("utf-8")).hexdigest()


def snapshots_root(durability_dir: Path) -> Path:
    return durability_dir / "snapshots"


def latest_snapshot_pointer_path(durability_dir: Path) -> Path:
    return durability_dir / SNAPSHOT_POINTER_FILENAME


def generate_snapshot_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def backup_connection_to_path(source_conn: sqlite3.Connection, target_db_path: Path) -> None:
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    if target_db_path.exists():
        target_db_path.unlink()
    target_conn = sqlite3.connect(str(target_db_path))
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()


def copy_sqlite_database(source_db_path: Path, target_db_path: Path) -> None:
    source_conn = connect_read_only(source_db_path)
    try:
        backup_connection_to_path(source_conn, target_db_path)
    finally:
        source_conn.close()


def render_snapshot_rows(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for manifest in manifests:
        rows.append(
            {
                "snapshot_id": manifest["snapshot_id"],
                "created_at": manifest["created_at"],
                "task_count": manifest["task_count"],
                "event_count": manifest["event_count"],
                "planner_digest": manifest["planner_state_digest"][:12],
                "runtime_digest": manifest["runtime_state_digest"][:12],
                "db_mb": f"{manifest['db_bytes'] / (1024 * 1024):.2f}",
                "note": manifest.get("note") or "",
            }
        )
    return rows


def build_snapshot_manifest(
    conn: sqlite3.Connection,
    *,
    snapshot_id: str,
    source_db_path: Path,
    snapshot_db_path: Path,
    actor_id: str,
    note: str | None,
) -> dict[str, Any]:
    require_initialized_db(conn, snapshot_db_path)
    applied = applied_migrations(conn)
    tasks = conn.execute(
        """
        SELECT task_id, version, planner_status, priority, updated_at, target_repo_id
        FROM tasks
        WHERE archived_at IS NULL
        ORDER BY task_id ASC
        """
    ).fetchall()
    runtime_rows = conn.execute(
        """
        SELECT task_id, runtime_status, queue_name, claimed_by, last_transition_at, retry_count
        FROM task_runtime_state
        ORDER BY task_id ASC
        """
    ).fetchall()
    repo_ids = [
        str(row["repo_id"])
        for row in conn.execute("SELECT repo_id FROM repos ORDER BY repo_id ASC").fetchall()
    ]
    task_rows = [
        {
            "task_id": str(row["task_id"]),
            "version": int(row["version"]),
            "planner_status": str(row["planner_status"]),
            "priority": int(row["priority"]),
            "updated_at": str(row["updated_at"]),
            "target_repo_id": str(row["target_repo_id"]),
        }
        for row in tasks
    ]
    runtime_payload = [
        {
            "task_id": str(row["task_id"]),
            "runtime_status": str(row["runtime_status"]),
            "queue_name": row["queue_name"],
            "claimed_by": row["claimed_by"],
            "last_transition_at": str(row["last_transition_at"]),
            "retry_count": int(row["retry_count"]),
        }
        for row in runtime_rows
    ]
    planner_status_counts: dict[str, int] = defaultdict(int)
    for row in task_rows:
        planner_status_counts[row["planner_status"]] += 1
    db_sha256 = file_sha256(snapshot_db_path)
    return {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "created_at": now_iso(),
        "actor_id": actor_id,
        "note": note,
        "source_db_path": str(source_db_path),
        "db_filename": SNAPSHOT_DB_FILENAME,
        "db_bytes": snapshot_db_path.stat().st_size,
        "db_sha256": db_sha256,
        "task_count": len(task_rows),
        "event_count": int(conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]),
        "artifact_count": int(conn.execute("SELECT COUNT(*) FROM task_artifacts").fetchone()[0]),
        "repo_ids": repo_ids,
        "planner_status_counts": dict(sorted(planner_status_counts.items())),
        "applied_migrations": [row["name"] for row in applied.values()],
        "planner_state_digest": stable_sha256(task_rows),
        "runtime_state_digest": stable_sha256(runtime_payload),
        "tasks": task_rows,
    }


def load_latest_snapshot_pointer(durability_dir: Path) -> dict[str, Any]:
    pointer_path = latest_snapshot_pointer_path(durability_dir)
    if not pointer_path.exists():
        die(f"latest snapshot pointer missing: {pointer_path}")
    raw = json.loads(pointer_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "snapshot_id" not in raw:
        die(f"invalid latest snapshot pointer: {pointer_path}")
    return raw


def resolve_snapshot_manifest(durability_dir: Path, snapshot_id: str | None) -> tuple[dict[str, Any], Path]:
    selected_id = snapshot_id
    if selected_id is None:
        selected_id = str(load_latest_snapshot_pointer(durability_dir)["snapshot_id"])
    manifest_path = snapshots_root(durability_dir) / selected_id / SNAPSHOT_MANIFEST_FILENAME
    if not manifest_path.exists():
        die(f"snapshot manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        die(f"invalid snapshot manifest: {manifest_path}")
    return manifest, manifest_path


def list_snapshot_manifests(durability_dir: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    root = snapshots_root(durability_dir)
    if not root.exists():
        return manifests
    for manifest_path in sorted(root.glob(f"*/{SNAPSHOT_MANIFEST_FILENAME}"), reverse=True):
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            manifests.append(raw)
    return manifests


def ensure_repo(
    conn: sqlite3.Connection,
    *,
    repo_id: str,
    repo_root: str,
    display_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    timestamp = now_iso()
    existing = conn.execute(
        "SELECT display_name, metadata_json FROM repos WHERE repo_id = ?",
        (repo_id,),
    ).fetchone()
    resolved_display_name = (
        display_name
        or (str(existing["display_name"]) if existing is not None and existing["display_name"] is not None else repo_id)
    )
    resolved_metadata = (
        metadata
        if metadata is not None
        else parse_json_text(str(existing["metadata_json"]), default={})
        if existing is not None
        else {}
    )
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
            resolved_display_name,
            repo_root,
            compact_json(resolved_metadata),
            timestamp,
            timestamp,
        ),
    )


def replace_repo_aliases(conn: sqlite3.Connection, *, repo_id: str, aliases: Iterable[str]) -> list[str]:
    normalized_aliases = normalize_repo_aliases(aliases)
    timestamp = now_iso()
    conn.execute("DELETE FROM repo_aliases WHERE repo_id = ?", (repo_id,))
    for alias in normalized_aliases:
        conn.execute(
            """
            INSERT INTO repo_aliases (repo_id, alias, normalized_alias, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (repo_id, alias, normalize_repo_lookup_key(alias), timestamp, timestamp),
        )
    return normalized_aliases


def load_repo_aliases_map(
    conn: sqlite3.Connection,
    *,
    repo_ids: Iterable[str] | None = None,
) -> dict[str, list[str]]:
    clauses: list[str] = []
    params: list[Any] = []
    if repo_ids is not None:
        repo_ids = list(repo_ids)
        if not repo_ids:
            return {}
        placeholders = ", ".join("?" for _ in repo_ids)
        clauses.append(f"repo_id IN ({placeholders})")
        params.extend(repo_ids)
    query = "SELECT repo_id, alias FROM repo_aliases"
    if clauses:
        query += f" WHERE {' AND '.join(clauses)}"
    query += " ORDER BY repo_id ASC, alias ASC"
    rows = conn.execute(query, tuple(params)).fetchall()
    aliases: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        aliases[str(row["repo_id"])].append(str(row["alias"]))
    return dict(aliases)


def build_repo_payload(
    row: sqlite3.Row,
    *,
    aliases: list[str] | None = None,
    lookup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "repo_id": str(row["repo_id"]),
        "display_name": str(row["display_name"]),
        "repo_root": str(row["repo_root"]),
        "is_active": bool(row["is_active"]),
        "metadata": parse_json_text(row["metadata_json"], default={}),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "aliases": aliases or [],
    }
    if lookup is not None:
        payload["lookup"] = lookup
    return payload


def fetch_repo_payload(conn: sqlite3.Connection, repo_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM repos WHERE repo_id = ?", (repo_id,)).fetchone()
    if row is None:
        return None
    aliases = load_repo_aliases_map(conn, repo_ids=[repo_id]).get(repo_id, [])
    return build_repo_payload(row, aliases=aliases)


def fetch_repo_registry(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM repos ORDER BY repo_id ASC").fetchall()
    alias_map = load_repo_aliases_map(conn, repo_ids=[str(row["repo_id"]) for row in rows])
    return [
        build_repo_payload(row, aliases=alias_map.get(str(row["repo_id"]), []))
        for row in rows
    ]


def iter_repo_lookup_candidates(repo: dict[str, Any]) -> Iterable[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    raw_values = [
        ("repo_id", str(repo["repo_id"])),
        ("display_name", str(repo["display_name"])),
        ("repo_root", str(repo["repo_root"])),
    ]
    root_name = Path(str(repo["repo_root"])).name.strip()
    if root_name:
        raw_values.append(("repo_root_basename", root_name))
    raw_values.extend(("alias", alias) for alias in repo.get("aliases", []))
    for kind, value in raw_values:
        cleaned = str(value).strip()
        key = (kind, cleaned)
        if not cleaned or key in seen:
            continue
        seen.add(key)
        yield kind, cleaned


def format_repo_reference_matches(matches: dict[str, set[str]]) -> str:
    return ", ".join(
        f"{repo_id} ({'/'.join(sorted(kinds))})"
        for repo_id, kinds in sorted(matches.items())
    )


def resolve_repo_reference(
    conn: sqlite3.Connection,
    reference: str,
    *,
    field: str = "repo",
    allow_missing: bool = False,
) -> dict[str, Any] | None:
    query = reference.strip()
    if not query:
        die(f"{field} reference cannot be blank")
    registry = fetch_repo_registry(conn)
    if not registry:
        if allow_missing:
            return None
        die(f"no repos are registered; cannot resolve {field} reference {reference!r}")

    query_key = normalize_repo_lookup_key(query)
    query_root_key = normalize_repo_root_key(query) if "/" in query or "\\" in query else None
    exact_matches: dict[str, set[str]] = defaultdict(set)
    normalized_matches: dict[str, set[str]] = defaultdict(set)
    for repo in registry:
        repo_id = str(repo["repo_id"])
        for kind, candidate in iter_repo_lookup_candidates(repo):
            if kind == "repo_root":
                if candidate == query or (query_root_key is not None and normalize_repo_root_key(candidate) == query_root_key):
                    exact_matches[repo_id].add(kind)
                continue
            if candidate == query:
                exact_matches[repo_id].add(kind)
            if query_key and normalize_repo_lookup_key(candidate) == query_key:
                normalized_matches[repo_id].add(kind)

    for match_quality, matches in (("exact", exact_matches), ("normalized", normalized_matches)):
        if not matches:
            continue
        if len(matches) > 1:
            die(
                f"ambiguous {field} reference {reference!r}: "
                f"{format_repo_reference_matches(matches)}. Use a canonical repo_id."
            )
        repo_id, kinds = next(iter(matches.items()))
        repo = next(item for item in registry if str(item["repo_id"]) == repo_id)
        repo["lookup"] = {
            "reference": reference,
            "match_quality": match_quality,
            "matched_by": sorted(kinds),
        }
        return repo

    if allow_missing:
        return None
    known_repo_ids = ", ".join(str(repo["repo_id"]) for repo in registry)
    die(f"unknown {field} reference {reference!r}; known repo_ids: {known_repo_ids}")


def resolve_repo_filter(conn: sqlite3.Connection, repo_reference: str | None) -> str | None:
    if repo_reference is None:
        return None
    resolved = resolve_repo_reference(conn, repo_reference, field="repo")
    return None if resolved is None else str(resolved["repo_id"])


def registered_repo_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT repo_id FROM repos ORDER BY repo_id ASC").fetchall()
    return [str(row["repo_id"]) for row in rows]


def known_repo_ids_summary(conn: sqlite3.Connection) -> str:
    repo_ids = registered_repo_ids(conn)
    return ", ".join(repo_ids) if repo_ids else "(none)"


def die_repo_onboarding_required(
    conn: sqlite3.Connection,
    *,
    operation: str,
    repo_id: str | None,
    repo_root: str | None,
    reason: str,
    aliases: Iterable[str] | None = None,
) -> "None":
    command = build_repo_onboarding_command(
        repo_id=repo_id,
        repo_root=repo_root,
        aliases=aliases,
    )
    verify_reference = repo_id or repo_root or "REPO_ID"
    lines = [
        f"repo onboarding required before {operation}.",
        reason,
        f"Register the repo first: {command}",
        f"Verify the canonical identity: {shell_join(['python3', str(SCRIPT_PATH), 'repo-resolve', '--repo', verify_reference])}",
        f"Known repo_ids: {known_repo_ids_summary(conn)}",
    ]
    die("\n".join(lines))


def resolve_task_repo_target(conn: sqlite3.Connection, normalized: dict[str, Any]) -> dict[str, Any] | None:
    target_repo_root = normalized.get("target_repo_root")
    target_repo_id = normalized.get("target_repo_id")
    resolved_by_root = None
    resolved_by_id = None
    if target_repo_root is not None:
        resolved_by_root = resolve_repo_reference(
            conn,
            str(target_repo_root),
            field="target_repo_root",
            allow_missing=True,
        )
    if target_repo_id is not None:
        resolved_by_id = resolve_repo_reference(
            conn,
            str(target_repo_id),
            field="target_repo_id",
            allow_missing=True,
        )
    if resolved_by_root and resolved_by_id and resolved_by_root["repo_id"] != resolved_by_id["repo_id"]:
        die(
            "conflicting repo target references: "
            f"target_repo_id={target_repo_id!r} resolved to {resolved_by_id['repo_id']} "
            f"but target_repo_root={target_repo_root!r} resolved to {resolved_by_root['repo_id']}"
        )
    operation = "planner task creation/update"
    if target_repo_id is not None and resolved_by_id is None and resolved_by_root is not None:
        die_repo_onboarding_required(
            conn,
            operation=operation,
            repo_id=str(resolved_by_root["repo_id"]),
            repo_root=str(resolved_by_root["repo_root"]),
            aliases=[str(target_repo_id)],
            reason=(
                f"target_repo_id {target_repo_id!r} is not registered for canonical repo "
                f"{resolved_by_root['repo_id']!r} at {resolved_by_root['repo_root']!r}. "
                "Use the canonical repo_id or add the alias explicitly."
            ),
        )
    if target_repo_root is not None and resolved_by_root is None and resolved_by_id is not None:
        die_repo_onboarding_required(
            conn,
            operation=operation,
            repo_id=str(resolved_by_id["repo_id"]),
            repo_root=str(resolved_by_id["repo_root"]),
            reason=(
                f"target_repo_root {target_repo_root!r} is not the canonical repo_root for "
                f"{resolved_by_id['repo_id']!r}. Registered repo_root: {resolved_by_id['repo_root']!r}. "
                "Update the registry first if the repo moved."
            ),
        )
    resolved = resolved_by_root or resolved_by_id
    if resolved is None:
        die_repo_onboarding_required(
            conn,
            operation=operation,
            repo_id=str(target_repo_id) if target_repo_id is not None else None,
            repo_root=str(target_repo_root) if target_repo_root is not None else None,
            reason=(
                f"target repo is not registered: target_repo_id={target_repo_id!r}, "
                f"target_repo_root={target_repo_root!r}."
            ),
        )
    normalized["target_repo_id"] = str(resolved["repo_id"])
    normalized["target_repo_root"] = str(resolved["repo_root"])
    normalized["target_repo_display_name"] = str(resolved["display_name"])
    return resolved


def render_repo_rows(rows: list[dict[str, Any]]) -> str:
    return render_table(
        [
            {
                "repo_id": row["repo_id"],
                "display_name": row["display_name"],
                "repo_root": row["repo_root"],
                "active": "yes" if row["is_active"] else "no",
                "aliases": ", ".join(row.get("aliases", [])),
            }
            for row in rows
        ],
        [
            ("repo_id", "repo_id"),
            ("display_name", "display_name"),
            ("repo_root", "repo_root"),
            ("active", "active"),
            ("aliases", "aliases"),
        ],
    )


def render_repo_detail(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    metadata_text = json.dumps(metadata, indent=2, sort_keys=True) if metadata else "(none)"
    return "\n".join(
        [
            f"repo_id: {row['repo_id']}",
            f"display_name: {row['display_name']}",
            f"repo_root: {row['repo_root']}",
            f"active: {'yes' if row['is_active'] else 'no'}",
            f"aliases: {', '.join(row.get('aliases', [])) or '(none)'}",
            "metadata:",
            "\n  ".join(metadata_text.splitlines()),
            f"created_at: {row['created_at']}",
            f"updated_at: {row['updated_at']}",
        ]
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


def insert_task_id_reservation_event(
    conn: sqlite3.Connection,
    *,
    reservation_id: str,
    event_type: str,
    actor_id: str,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO task_id_reservation_events (reservation_id, event_type, actor_id, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (reservation_id, event_type, actor_id, compact_json(payload or {}), now_iso()),
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


def parse_iso8601(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_task_numbers_for_series(conn: sqlite3.Connection, series: str) -> set[int]:
    normalized_series = normalize_task_id_series(series)
    rows = conn.execute(
        "SELECT task_id FROM tasks WHERE task_id LIKE ?",
        (f"{normalized_series}-%",),
    ).fetchall()
    numbers: set[int] = set()
    for row in rows:
        task_series, task_number = parse_task_id(str(row["task_id"]))
        if task_series == normalized_series:
            numbers.add(task_number)
    return numbers


def fetch_task_id_reservation_events(
    conn: sqlite3.Connection,
    reservation_ids: Iterable[str],
    *,
    limit_per_reservation: int = 10,
) -> dict[str, list[dict[str, Any]]]:
    reservation_ids = [item for item in reservation_ids if item]
    if not reservation_ids:
        return {}
    placeholders = ", ".join("?" for _ in reservation_ids)
    rows = conn.execute(
        f"""
        SELECT event_id, reservation_id, event_type, actor_id, payload_json, created_at
        FROM task_id_reservation_events
        WHERE reservation_id IN ({placeholders})
        ORDER BY event_id DESC
        """,
        tuple(reservation_ids),
    ).fetchall()
    events_by_reservation: dict[str, list[dict[str, Any]]] = {reservation_id: [] for reservation_id in reservation_ids}
    for row in rows:
        reservation_id = str(row["reservation_id"])
        bucket = events_by_reservation.setdefault(reservation_id, [])
        if len(bucket) >= limit_per_reservation:
            continue
        bucket.append(
            {
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "actor_id": row["actor_id"],
                "payload": parse_json_text(row["payload_json"], default={}),
                "created_at": row["created_at"],
            }
        )
    return events_by_reservation


def fetch_task_id_reservation_rows(
    conn: sqlite3.Connection,
    *,
    series: str | None = None,
    status: str | None = None,
    reservation_id: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    params: list[Any] = []
    if reservation_id is not None:
        clauses.append("reservation_id = ?")
        params.append(reservation_id)
    if series is not None:
        clauses.append("series = ?")
        params.append(normalize_task_id_series(series))
    if status is not None:
        if status not in TASK_ID_RESERVATION_STATUSES:
            die(f"invalid reservation status: {status}")
        clauses.append("status = ?")
        params.append(status)
    query = """
        SELECT *
        FROM task_id_reservations
    """
    if clauses:
        query += f" WHERE {' AND '.join(clauses)}"
    query += " ORDER BY created_at DESC, reservation_id DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    return conn.execute(query, tuple(params)).fetchall()


def build_task_id_reservation_payload(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    include_events: bool = False,
    events: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    series = str(row["series"])
    start_number = int(row["start_number"])
    end_number = int(row["end_number"])
    task_numbers = fetch_task_numbers_for_series(conn, series)
    reserved_numbers = list(range(start_number, end_number + 1))
    existing_numbers = sorted(number for number in reserved_numbers if number in task_numbers)
    open_numbers = sorted(number for number in reserved_numbers if number not in task_numbers)
    payload = {
        "reservation_id": row["reservation_id"],
        "series": series,
        "range_start": start_number,
        "range_end": end_number,
        "range_label": f"{series}-{start_number}..{series}-{end_number}",
        "count": len(reserved_numbers),
        "task_ids": [make_task_id(series, number) for number in reserved_numbers],
        "existing_task_ids": [make_task_id(series, number) for number in existing_numbers],
        "open_task_ids": [make_task_id(series, number) for number in open_numbers],
        "filled_count": len(existing_numbers),
        "open_count": len(open_numbers),
        "status": row["status"],
        "reserved_by": row["reserved_by"],
        "reserved_for": row["reserved_for"],
        "note": row["note"],
        "metadata": parse_json_text(row["metadata_json"], default={}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "expires_at": row["expires_at"],
        "resolved_at": row["resolved_at"],
    }
    if include_events:
        payload["events"] = (events or {}).get(str(row["reservation_id"]), [])
    return payload


def reconcile_task_id_reservations(
    conn: sqlite3.Connection,
    *,
    series: str | None = None,
    actor_id: str,
) -> list[dict[str, Any]]:
    rows = fetch_task_id_reservation_rows(conn, series=series, status="active")
    if not rows:
        return []
    now_value = now_iso()
    now_dt = parse_iso8601(now_value)
    task_numbers_by_series: dict[str, set[int]] = {}
    updates: list[dict[str, Any]] = []
    for row in rows:
        row_series = str(row["series"])
        if row_series not in task_numbers_by_series:
            task_numbers_by_series[row_series] = fetch_task_numbers_for_series(conn, row_series)
        task_numbers = task_numbers_by_series[row_series]
        reserved_numbers = list(range(int(row["start_number"]), int(row["end_number"]) + 1))
        existing_task_ids = [make_task_id(row_series, number) for number in reserved_numbers if number in task_numbers]
        missing_task_ids = [make_task_id(row_series, number) for number in reserved_numbers if number not in task_numbers]
        new_status: str | None = None
        event_type: str | None = None
        if not missing_task_ids:
            new_status = "completed"
            event_type = "planner.task_id_reservation_completed"
        elif parse_iso8601(str(row["expires_at"])) <= now_dt:
            new_status = "expired"
            event_type = "planner.task_id_reservation_expired"
        if new_status is None or new_status == str(row["status"]):
            continue
        conn.execute(
            """
            UPDATE task_id_reservations
            SET status = ?, updated_at = ?, resolved_at = ?
            WHERE reservation_id = ?
            """,
            (new_status, now_value, now_value, row["reservation_id"]),
        )
        insert_task_id_reservation_event(
            conn,
            reservation_id=str(row["reservation_id"]),
            event_type=event_type,
            actor_id=actor_id,
            payload={
                "series": row_series,
                "existing_task_ids": existing_task_ids,
                "open_task_ids": missing_task_ids,
            },
        )
        updates.append(
            {
                "reservation_id": row["reservation_id"],
                "status": new_status,
                "existing_task_ids": existing_task_ids,
                "open_task_ids": missing_task_ids,
            }
        )
    return updates


def next_task_id_payload(conn: sqlite3.Connection, *, series: str, actor_id: str) -> dict[str, Any]:
    normalized_series = normalize_task_id_series(series)
    reconcile_task_id_reservations(conn, series=normalized_series, actor_id=actor_id)
    task_numbers = fetch_task_numbers_for_series(conn, normalized_series)
    reservation_rows = fetch_task_id_reservation_rows(conn, series=normalized_series, status="active")
    highest_existing_number = max(task_numbers, default=0)
    highest_active_reservation_number = max((int(row["end_number"]) for row in reservation_rows), default=0)
    next_number = max(highest_existing_number, highest_active_reservation_number) + 1
    return {
        "series": normalized_series,
        "strategy": "monotonic_high_watermark",
        "next_number": next_number,
        "next_task_id": make_task_id(normalized_series, next_number),
        "highest_existing_number": highest_existing_number,
        "highest_active_reservation_number": highest_active_reservation_number,
        "active_reservation_count": len(reservation_rows),
    }


def reserve_task_id_range(
    conn: sqlite3.Connection,
    *,
    series: str,
    count: int,
    reserved_by: str,
    reserved_for: str | None,
    note: str | None,
    reservation_hours: int,
) -> dict[str, Any]:
    normalized_series = normalize_task_id_series(series)
    if count <= 0:
        die("reservation count must be positive")
    if count > MAX_TASK_ID_RESERVATION_COUNT:
        die(f"reservation count exceeds max {MAX_TASK_ID_RESERVATION_COUNT}")
    if reservation_hours <= 0:
        die("reservation duration must be positive hours")
    begin_immediate(conn)
    next_payload = next_task_id_payload(conn, series=normalized_series, actor_id=reserved_by)
    start_number = int(next_payload["next_number"])
    end_number = start_number + count - 1
    existing_numbers = fetch_task_numbers_for_series(conn, normalized_series)
    if any(number in existing_numbers for number in range(start_number, end_number + 1)):
        conn.rollback()
        die(f"reservation range collides with existing tasks in {normalized_series}")
    active_rows = fetch_task_id_reservation_rows(conn, series=normalized_series, status="active")
    for row in active_rows:
        if start_number <= int(row["end_number"]) and end_number >= int(row["start_number"]):
            conn.rollback()
            die(f"reservation range overlaps active reservation {row['reservation_id']}")
    created_at = now_iso()
    expires_at = datetime.fromtimestamp(
        parse_iso8601(created_at).timestamp() + reservation_hours * 3600,
        tz=timezone.utc,
    ).replace(microsecond=0).isoformat()
    reservation_id = f"{normalized_series}-{start_number}-{end_number}-{uuid.uuid4().hex[:8]}"
    conn.execute(
        """
        INSERT INTO task_id_reservations (
            reservation_id,
            series,
            start_number,
            end_number,
            reserved_by,
            reserved_for,
            note,
            status,
            metadata_json,
            created_at,
            updated_at,
            expires_at,
            resolved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, NULL)
        """,
        (
            reservation_id,
            normalized_series,
            start_number,
            end_number,
            reserved_by,
            reserved_for,
            note,
            compact_json({"source": "planner_cli"}),
            created_at,
            created_at,
            expires_at,
        ),
    )
    insert_task_id_reservation_event(
        conn,
        reservation_id=reservation_id,
        event_type="planner.task_id_reservation_created",
        actor_id=reserved_by,
        payload={
            "series": normalized_series,
            "start_number": start_number,
            "end_number": end_number,
            "reserved_for": reserved_for,
            "note": note,
            "expires_at": expires_at,
        },
    )
    conn.commit()
    row = fetch_task_id_reservation_rows(conn, reservation_id=reservation_id, limit=1)[0]
    events = fetch_task_id_reservation_events(conn, [reservation_id], limit_per_reservation=10)
    return build_task_id_reservation_payload(conn, row, include_events=True, events=events)


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


def detect_status_mismatch(
    *,
    task_id: str,
    planner_status: str,
    runtime_status: str | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if runtime_status is None or runtime_status not in TERMINAL_RUNTIME_STATUSES:
        return None
    if runtime_status == "done":
        if planner_status == "done":
            return None
        # awaiting_audit is a valid terminal planner state when the task has a paired audit child
        if planner_status == "awaiting_audit" and metadata and metadata.get("child_audit_task_id"):
            return None
        severity = "warning" if planner_status in AUTO_RECONCILE_PLANNER_STATUSES else "error"
        return {
            "task_id": task_id,
            "severity": severity,
            "code": "runtime_done_planner_not_done",
            "summary": f"runtime finished with done while planner remained {planner_status}",
            "operator_action": "inspect dispatcher auto-reconcile logs and rerun closeout only if the runtime result is trustworthy",
        }
    if runtime_status == "pending_review":
        if planner_status == "done":
            return {
                "task_id": task_id,
                "severity": "error",
                "code": "pending_review_planner_done",
                "summary": "runtime requires review but planner is already done",
                "operator_action": "re-open planner state or finish the review path explicitly",
            }
        return None
    if planner_status == "done":
        return {
            "task_id": task_id,
            "severity": "error",
            "code": f"planner_done_runtime_{runtime_status}",
            "summary": f"planner is done while runtime ended as {runtime_status}",
            "operator_action": "inspect runtime evidence and reconcile the planner closeout or reopen the task",
        }
    return None


def fetch_task_snapshots(
    conn: sqlite3.Connection,
    *,
    task_id: str | None = None,
    repo_id: str | None = None,
    planner_status: str | None = None,
    initiative: str | None = None,
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
    if initiative is not None:
        clauses.append("t.initiative = ?")
        params.append(initiative)
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
            rs.effective_worker_model,
            rs.worker_model_source,
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
        blocker_ids = [item["depends_on_task_id"] for item in dependencies if item["dependency_kind"] == "hard" and item["depends_on_status"] not in {"done", "awaiting_audit"}]
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
                "effective_worker_model": row["effective_worker_model"],
                "worker_model_source": row["worker_model_source"],
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
                "initiative": row["initiative"],
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
                "status_mismatch": detect_status_mismatch(
                    task_id=str(row["task_id"]),
                    planner_status=str(row["planner_status"]),
                    runtime_status=str(row["runtime_status"]) if row["runtime_status"] is not None else None,
                    metadata=metadata,
                ),
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
    status = runtime["runtime_status"]
    if status not in {"queued", "failed", "timeout"}:
        return False
    # Don't re-dispatch failed tasks that have exceeded max retries
    if status == "failed" and runtime.get("retry_count", 0) >= 5 and runtime.get("last_runtime_error") == "max_retries_exceeded":
        return False
    return True


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
        "initiative",
    ]
    for field in text_fields:
        if field in payload:
            value = payload[field]
            if value is None and field in ("worker_owner", "initiative"):
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
    resolve_task_repo_target(conn, normalized)
    task_series, _ = parse_task_id(normalized["task_id"])
    timestamp = now_iso()
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
            initiative,
            archived_at,
            created_at,
            updated_at,
            closed_at,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
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
            normalized.get("initiative"),
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
    reconcile_task_id_reservations(conn, series=task_series, actor_id=actor_id)
    return fetch_task_snapshots(conn, task_id=normalized["task_id"])[0]


def task_requires_audit(*, task_type: str, source_kind: str, metadata: dict[str, Any]) -> bool:
    """Return True when the task's metadata indicates an audit is required."""
    return bool(metadata.get("audit_required"))


def build_audit_task_payload(parent_payload: dict[str, Any], audit_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build an audit task payload derived from a parent task payload."""
    parent_task_id = str(parent_payload["task_id"])
    parent_title = str(parent_payload.get("title") or parent_task_id)
    audit_task_id = f"{parent_task_id}-AUDIT"

    # Fields inherited from parent
    inherited_fields = [
        "priority",
        "target_repo_id",
        "target_repo_root",
        "target_repo_display_name",
        "planner_owner",
        "worker_owner",
        "initiative",
        "execution",
        "source_kind",
    ]

    audit_payload: dict[str, Any] = {
        "task_id": audit_task_id,
        "task_type": "audit",
        "title": f"Audit {parent_task_id}: {parent_title}",
        "summary": f"Verify that {parent_task_id} built the correct thing in the real system.",
        "dependencies": [parent_task_id],
        "metadata": {
            "audit_required": False,
            "parent_task_id": parent_task_id,
            "relationship_kind": "audit",
            "fixup_threshold": "bounded_only",
        },
        "planner_status": "todo",
        "approval_required": False,
        "objective_md": f"Audit `{parent_task_id}` for requirement fidelity, real-environment behavior, and whole-system fit.",
        "context_md": (
            f"Parent task: `{parent_task_id}`.\n\n"
            "Ground the audit in the parent objective, acceptance criteria, artifacts, and runtime evidence."
        ),
        "scope_md": "Validate the delivered change against requirements and full-system behavior.",
        "deliverables_md": "Record an audit verdict with concrete evidence and any bounded fixups.",
        "acceptance_md": "Confirm the implementation matches intent, works in reality, and does not fail outside a narrow local window.",
        "testing_md": "Run reality-based validation and record commands, artifacts, and observed outcomes.",
        "dispatch_md": f"Dispatch after `{parent_task_id}` reaches `awaiting_audit`.",
        "closeout_md": "Record audit evidence, final verdict, and any bounded fixups performed during the audit.",
        "reconciliation_md": (
            f"When this audit is `done`, `{parent_task_id}` closes automatically. "
            "If this audit fails, planner follow-up is required."
        ),
    }

    for field in inherited_fields:
        if field in parent_payload and parent_payload[field] is not None:
            audit_payload[field] = parent_payload[field]

    if audit_override:
        audit_payload.update(audit_override)

    return audit_payload


def create_task_graph(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    actor_kind: str,
    actor_id: str,
) -> dict[str, Any]:
    """Create a task and, if audit is required, a paired audit task.

    Returns the parent task snapshot.
    """
    task_id = str(payload.get("task_id") or "")
    metadata = dict(payload.get("metadata") or {})
    task_type = str(payload.get("task_type") or "implementation")
    source_kind = str(payload.get("source_kind") or "planner")

    if task_requires_audit(task_type=task_type, source_kind=source_kind, metadata=metadata):
        audit_task_id = f"{task_id}-AUDIT"
        # Inject audit linkage into parent metadata
        parent_metadata = dict(metadata)
        parent_metadata["child_audit_task_id"] = audit_task_id
        parent_metadata["audit_verdict"] = "pending"
        parent_payload = dict(payload)
        parent_payload["metadata"] = parent_metadata
        parent_snapshot = create_task(conn, parent_payload, actor_kind=actor_kind, actor_id=actor_id)
        audit_payload = build_audit_task_payload(parent_payload)
        create_task(conn, audit_payload, actor_kind=actor_kind, actor_id=actor_id)
        return parent_snapshot
    else:
        return create_task(conn, payload, actor_kind=actor_kind, actor_id=actor_id)


def runtime_requeue_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    actor_id: str,
    reason: str,
    reset_retry_count: bool = False,
) -> dict[str, Any]:
    """Requeue a task by resetting its runtime_status to 'queued' and clearing the lease."""
    begin_immediate(conn)
    runtime_row = conn.execute("SELECT * FROM task_runtime_state WHERE task_id = ?", (task_id,)).fetchone()
    if runtime_row is None:
        conn.rollback()
        raise RuntimeError(f"runtime state missing for {task_id}")
    lease = fetch_active_lease(conn, task_id)
    had_active_lease = lease is not None
    if had_active_lease:
        close_active_assignments(
            conn,
            task_id=task_id,
            assignee_kind="worker",
            assignee_id=str(lease["lease_owner_id"]),
        )
        conn.execute("DELETE FROM task_active_leases WHERE task_id = ?", (task_id,))
    current = row_to_dict(runtime_row) or {}
    retry_count = int(current.get("retry_count") or 0) if not reset_retry_count else 0
    transition_at = now_iso()
    conn.execute(
        """
        UPDATE task_runtime_state
        SET runtime_status = 'queued',
            last_runtime_error = NULL,
            finished_at = NULL,
            pending_review_at = NULL,
            retry_count = ?,
            last_transition_at = ?
        WHERE task_id = ?
        """,
        (retry_count, transition_at, task_id),
    )
    insert_event(
        conn,
        task_id=task_id,
        event_type="runtime.requeued",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={
            "summary": reason,
            "had_active_lease": had_active_lease,
            "reset_retry_count": reset_retry_count,
        },
    )
    conn.commit()
    return fetch_task_snapshots(conn, task_id=task_id)[0]


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
    if "target_repo_id" in normalized or "target_repo_root" in normalized:
        resolve_task_repo_target(conn, normalized)
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
        "initiative",
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
            initiative = ?,
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
            merged.get("initiative"),
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
    if outcome not in {"done", "awaiting_audit", "failed"}:
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
    # When the planner closes a task as done, align runtime_status to avoid a
    # persistent planner_done_runtime_<other> mismatch. Only update if a runtime
    # state row exists and is in a terminal-but-not-done state.
    if outcome == "done":
        conn.execute(
            """
            UPDATE task_runtime_state
            SET runtime_status = 'done',
                last_transition_at = ?,
                finished_at = COALESCE(finished_at, ?)
            WHERE task_id = ?
              AND runtime_status IN ('failed', 'timeout', 'canceled', 'pending_review')
            """,
            (updated_at, updated_at, task_id),
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


def auto_reconcile_runtime_success(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    summary: str,
    notes: str | None,
    tests: str | None,
    artifacts: list[str],
    actor_id: str,
    run_id: str | None,
) -> dict[str, Any]:
    begin_immediate(conn)
    snapshots = fetch_task_snapshots(conn, task_id=task_id)
    if not snapshots:
        conn.rollback()
        raise RuntimeError(f"task not found: {task_id}")
    snapshot = snapshots[0]
    runtime = snapshot.get("runtime") or {}
    runtime_status = str(runtime.get("runtime_status") or "")
    planner_status = str(snapshot.get("planner_status") or "")
    if runtime_status != "done":
        conn.rollback()
        raise RuntimeError(f"task {task_id} is not eligible for auto-reconcile from runtime_status={runtime_status or 'none'}")
    if planner_status == "done":
        conn.rollback()
        return snapshot
    if planner_status not in AUTO_RECONCILE_PLANNER_STATUSES:
        conn.rollback()
        raise RuntimeError(f"task {task_id} cannot auto-reconcile planner_status={planner_status} from runtime_status=done")

    current_row = fetch_task_row(conn, task_id)
    if current_row is None:
        conn.rollback()
        raise RuntimeError(f"task not found: {task_id}")
    closeout_summary = summary.strip() or "runtime completed successfully"
    closeout_notes = notes.strip() if notes else None
    closeout_tests = tests.strip() if tests else None
    updated_at = now_iso()
    metadata = merge_task_metadata(current_row["metadata_json"], None)

    # Determine outcome: awaiting_audit when paired audit exists, otherwise done
    has_audit = bool(metadata.get("child_audit_task_id"))
    outcome = "awaiting_audit" if has_audit else "done"

    if has_audit:
        metadata["audit_verdict"] = "pending"

    metadata["closeout"] = {
        "outcome": outcome,
        "summary": closeout_summary,
        "notes": closeout_notes,
        "tests": closeout_tests,
        "reconciled_at": updated_at,
        "actor_id": actor_id,
        "source": "runtime_auto_reconcile",
        "runtime_status": runtime_status,
        "runtime_finished_at": runtime.get("finished_at"),
        "runtime_run_id": run_id,
    }
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
            int(current_row["version"]),
        ),
    )
    for artifact in artifacts:
        insert_artifact(
            conn,
            task_id=task_id,
            artifact_kind="planner_closeout",
            path_or_uri=artifact,
            label=Path(artifact).name,
            metadata={"reconciled_by": actor_id, "outcome": outcome, "source": "runtime_auto_reconcile"},
        )
    insert_event(
        conn,
        task_id=task_id,
        event_type="planner.task_auto_reconciled",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={
            "outcome": outcome,
            "summary": closeout_summary,
            "notes": closeout_notes,
            "tests": closeout_tests,
            "artifacts": artifacts,
            "run_id": run_id,
        },
    )
    conn.commit()
    return fetch_task_snapshots(conn, task_id=task_id)[0]


def reconcile_audit_rework(
    conn,
    *,
    audit_task_id: str,
    summary: str,
    actor_id: str,
):
    """Fail an audit task (verdict=rework_required) and propagate failure to the parent task."""
    begin_immediate(conn)
    audit_row = fetch_task_row(conn, audit_task_id)
    if audit_row is None:
        conn.rollback()
        raise RuntimeError(f"audit task not found: {audit_task_id}")

    audit_metadata = parse_json_text(audit_row["metadata_json"], default={})
    parent_task_id = str(audit_metadata.get("parent_task_id") or "")
    updated_at = now_iso()
    audit_version = int(audit_row["version"])

    audit_closeout = {
        "outcome": "failed",
        "summary": summary,
        "notes": "audit verdict: rework_required",
        "reconciled_at": updated_at,
        "actor_id": actor_id,
    }
    audit_metadata["closeout"] = audit_closeout
    conn.execute(
        """
        UPDATE tasks
        SET planner_status = 'failed',
            version = ?,
            worker_owner = NULL,
            updated_at = ?,
            metadata_json = ?
        WHERE task_id = ? AND version = ?
        """,
        (audit_version + 1, updated_at, compact_json(audit_metadata), audit_task_id, audit_version),
    )
    insert_event(
        conn,
        task_id=audit_task_id,
        event_type="planner.task_reconciled",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={"outcome": "failed", "summary": summary, "tests": None, "artifacts": []},
    )

    if parent_task_id:
        parent_row = fetch_task_row(conn, parent_task_id)
        if parent_row is not None:
            parent_metadata = parse_json_text(parent_row["metadata_json"], default={})
            parent_metadata["audit_verdict"] = "failed"
            parent_version = int(parent_row["version"])
            parent_updated_at = now_iso()
            conn.execute(
                """
                UPDATE tasks
                SET planner_status = 'failed',
                    version = ?,
                    updated_at = ?,
                    metadata_json = ?
                WHERE task_id = ? AND version = ?
                """,
                (parent_version + 1, parent_updated_at, compact_json(parent_metadata), parent_task_id, parent_version),
            )
            insert_event(
                conn,
                task_id=parent_task_id,
                event_type="planner.task_failed_by_audit",
                actor_kind="runtime",
                actor_id=actor_id,
                payload={"audit_task_id": audit_task_id, "summary": summary},
            )

    conn.commit()
    return fetch_task_snapshots(conn, task_id=audit_task_id)[0]


def summarize_portfolio(conn: sqlite3.Connection, *, initiative: str | None = None) -> dict[str, Any]:
    generated_at = now_iso()
    snapshots = fetch_task_snapshots(conn, initiative=initiative)
    eligible = order_eligible_snapshots(snapshots)
    planner_counts: dict[str, int] = {status: 0 for status in sorted(PLANNER_STATUSES)}
    runtime_counts: dict[str, int] = {status: 0 for status in sorted(RUNTIME_STATUSES)}
    per_repo: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "eligible": 0, "blocked": 0, "pending_review": 0, "running": 0})
    per_initiative: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "todo": 0, "in_progress": 0, "done": 0, "blocked": 0})
    blocked_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    mismatch_rows: list[dict[str, Any]] = []
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
        if snapshot["status_mismatch"]:
            mismatch_rows.append(snapshot)
        if snapshot.get("initiative"):
            init_summary = per_initiative[snapshot["initiative"]]
            init_summary["total"] += 1
            ps = snapshot["planner_status"]
            if ps in init_summary:
                init_summary[ps] += 1
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
        "mismatch_count": len(mismatch_rows),
        "mismatches": [
            {
                "task_id": row["task_id"],
                "repo": row["target_repo_id"],
                "planner_status": row["planner_status"],
                "runtime_status": row["runtime"]["runtime_status"] if row["runtime"] else None,
                "severity": row["status_mismatch"]["severity"],
                "summary": row["status_mismatch"]["summary"],
            }
            for row in mismatch_rows[:10]
        ],
        "per_repo": [
            {
                "repo_id": repo_id,
                **counts,
            }
            for repo_id, counts in sorted(per_repo.items())
        ],
        "per_initiative": [
            {
                "initiative": initiative,
                **counts,
            }
            for initiative, counts in sorted(per_initiative.items())
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
    lines.append(f"Mismatches: {summary['mismatch_count']}")
    if summary["mismatches"]:
        for mismatch in summary["mismatches"]:
            lines.append(
                f"- [{mismatch['severity']}] {mismatch['task_id']} planner={mismatch['planner_status']} runtime={mismatch['runtime_status']} | {mismatch['summary']}"
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
    if summary.get("per_initiative"):
        lines.append("")
        lines.append("Per initiative:")
        lines.append(render_table(summary["per_initiative"], [("initiative", "initiative"), ("total", "total"), ("todo", "todo"), ("in_progress", "in_progress"), ("done", "done"), ("blocked", "blocked")]))
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
                "effective_worker_model": snapshot["runtime"]["effective_worker_model"] if snapshot["runtime"] else "",
                "worker_model_source": snapshot["runtime"]["worker_model_source"] if snapshot["runtime"] else "",
                "title": snapshot["title"],
            }
        )
    return rows


def format_review_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        runtime = snapshot["runtime"]
        mismatch = snapshot["status_mismatch"]
        if (
            mismatch is None
            and (runtime is None or runtime["runtime_status"] not in {"pending_review", "failed", "timeout"})
        ):
            continue
        rows.append(
            {
                "task_id": snapshot["task_id"],
                "repo": snapshot["target_repo_id"],
                "runtime_status": runtime["runtime_status"] if runtime else "",
                "planner_status": snapshot["planner_status"],
                "age_at": (
                    (runtime.get("pending_review_at") or runtime.get("last_transition_at") or "")
                    if runtime
                    else ""
                ),
                "claimed_by": runtime.get("claimed_by") or "" if runtime else "",
                "last_error": runtime.get("last_runtime_error") or "" if runtime else "",
                "severity": mismatch["severity"] if mismatch else "review",
                "status_warning": mismatch["summary"] if mismatch else "",
                "title": snapshot["title"],
            }
        )
    return rows


def render_task_card(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": snapshot["task_id"],
        "title": snapshot["title"],
        "summary": snapshot["summary"],
        "version": snapshot["version"],
        "priority": snapshot["priority"],
        "planner_status": snapshot["planner_status"],
        "runtime_status": snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else None,
        "effective_worker_model": snapshot["runtime"]["effective_worker_model"] if snapshot["runtime"] else None,
        "worker_model_source": snapshot["runtime"]["worker_model_source"] if snapshot["runtime"] else None,
        "status_mismatch": snapshot["status_mismatch"],
        "target_repo_id": snapshot["target_repo_id"],
        "target_repo_root": snapshot["target_repo_root"],
        "initiative": snapshot["initiative"],
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
        *([
            f"- Effective Worker Model: `{snapshot['runtime']['effective_worker_model']}` (source: `{snapshot['runtime']['worker_model_source'] or 'unknown'}`)",
        ] if snapshot.get("runtime") and snapshot["runtime"].get("effective_worker_model") else []),
        *([f"- Initiative: `{snapshot['initiative']}`"] if snapshot.get("initiative") else []),
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


def render_generated_tasks_board(summary: dict[str, Any], snapshots: list[dict[str, Any]], *, generated_at: str) -> str:
    central_ops = [snapshot for snapshot in snapshots if str(snapshot["task_id"]).startswith("CENTRAL-OPS-")]
    other_tasks = [snapshot for snapshot in snapshots if not str(snapshot["task_id"]).startswith("CENTRAL-OPS-")]

    def _render_task_lines(task_rows: list[dict[str, Any]]) -> list[str]:
        if not task_rows:
            return ["- none"]
        lines: list[str] = []
        for snapshot in task_rows:
            runtime_status = snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else "none"
            effective_model = snapshot["runtime"]["effective_worker_model"] if snapshot.get("runtime") else None
            model_source = snapshot["runtime"]["worker_model_source"] if snapshot.get("runtime") else None
            model_suffix = f" | model: {effective_model} ({model_source})" if effective_model else ""
            lines.append(
                f"- [{snapshot['planner_status']}] {snapshot['task_id']} - {snapshot['title']}"
            )
            lines.append(
                f"  - priority: {snapshot['priority']} | repo: {snapshot['target_repo_id']} | runtime: {runtime_status}{model_suffix}"
            )
        return lines

    lines = [
        "# CENTRAL Generated Task Board",
        "",
        generated_banner(generated_at),
        "",
        "This file is a derived landing page generated from CENTRAL DB state.",
        "Do not edit it as the source of truth.",
        "",
        "## Portfolio Summary",
    ]
    for status, count in summary["planner_counts"].items():
        lines.append(f"- planner {status}: {count}")
    for status, count in summary["runtime_counts"].items():
        if count:
            lines.append(f"- runtime {status}: {count}")
    lines.extend(
        [
            f"- blocked count: {summary['blocked_count']}",
            f"- pending review count: {summary['pending_review_count']}",
            "",
            "## Top Eligible",
        ]
    )
    if summary["top_eligible"]:
        for item in summary["top_eligible"]:
            lines.append(
                f"- {item['task_id']} | p{item['priority']} | {item['target_repo_id']} | {item['title']}"
            )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## CENTRAL Canonical Task System Tasks",
            *_render_task_lines(central_ops),
        ]
    )
    if other_tasks:
        lines.extend(
            [
                "",
                "## Other Canonical Tasks",
                *_render_task_lines(other_tasks),
            ]
        )
    lines.append("")
    return "\n".join(lines)


def render_repo_markdown(repo_id: str, repo_rows: list[dict[str, Any]], *, generated_at: str) -> str:
    lines = [
        f"# CENTRAL Repo View: {repo_id}",
        "",
        generated_banner(generated_at),
        "",
    ]
    if not repo_rows:
        lines.extend(["- no tasks", ""])
        return "\n".join(lines)
    table_rows: list[dict[str, Any]] = []
    for snapshot in repo_rows:
        runtime_status = snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else "none"
        table_rows.append(
            {
                "task_id": snapshot["task_id"],
                "priority": snapshot["priority"],
                "planner_status": snapshot["planner_status"],
                "runtime_status": runtime_status,
                "dependency_blocked": "yes" if snapshot["dependency_blocked"] else "",
                "planner_owner": snapshot["planner_owner"],
                "worker_owner": snapshot["worker_owner"] or "",
                "lease_owner": snapshot["lease"]["lease_owner_id"] if snapshot["lease"] else "",
                "title": snapshot["title"],
            }
        )
    lines.extend(
        [
            "```text",
            render_table(
                table_rows,
                [
                    ("task_id", "task_id"),
                    ("p", "priority"),
                    ("planner", "planner_status"),
                    ("runtime", "runtime_status"),
                    ("dep_blocked", "dependency_blocked"),
                    ("planner_owner", "planner_owner"),
                    ("lease_owner", "lease_owner"),
                    ("title", "title"),
                ],
            ),
            "```",
            "",
        ]
    )
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
    raise_on_empty: bool = True,
) -> dict[str, Any] | None:
    begin_immediate(conn)
    snapshots = fetch_task_snapshots(conn, task_id=task_id) if task_id else fetch_task_snapshots(conn)
    ordered = order_eligible_snapshots(snapshots)
    if task_id is not None:
        ordered = [snapshot for snapshot in ordered if snapshot["task_id"] == task_id]
    if not ordered:
        conn.rollback()
        if raise_on_empty:
            die("no eligible task available to claim")
        return None
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
    effective_worker_model: str | None = None,
    worker_model_source: str | None = None,
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
    # Preserve existing model columns unless caller provides new values.
    existing_model = current.get("effective_worker_model")
    existing_source = current.get("worker_model_source")
    resolved_model = effective_worker_model if effective_worker_model is not None else existing_model
    resolved_source = worker_model_source if worker_model_source is not None else existing_source
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
            runtime_metadata_json = ?,
            effective_worker_model = ?,
            worker_model_source = ?
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
            resolved_model,
            resolved_source,
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


def runtime_clear_stale_failed(conn: sqlite3.Connection, *, actor_id: str) -> dict[str, Any]:
    """Set runtime_status=done for tasks where planner_status=done and runtime_status=failed.

    These are cosmetic mismatches from pre-fix worker runs where the planner
    successfully closed the task but the runtime record was never updated.
    """
    begin_immediate(conn)
    now_value = now_iso()
    rows = conn.execute(
        """
        SELECT rs.task_id
        FROM task_runtime_state rs
        JOIN tasks t ON t.task_id = rs.task_id
        WHERE rs.runtime_status = 'failed'
          AND t.planner_status = 'done'
        ORDER BY rs.task_id ASC
        """,
    ).fetchall()
    cleared: list[str] = []
    for row in rows:
        task_id = str(row["task_id"])
        conn.execute(
            """
            UPDATE task_runtime_state
            SET runtime_status = 'done',
                finished_at = COALESCE(finished_at, ?),
                last_transition_at = ?,
                last_runtime_error = NULL,
                runtime_metadata_json = ?
            WHERE task_id = ?
            """,
            (
                now_value,
                now_value,
                compact_json({"cleared_stale_failed_at": now_value}),
                task_id,
            ),
        )
        insert_event(
            conn,
            task_id=task_id,
            event_type="runtime.stale_failed_cleared",
            actor_kind="runtime",
            actor_id=actor_id,
            payload={"reason": "planner_done_runtime_failed cosmetic mismatch cleared"},
        )
        cleared.append(task_id)
    conn.commit()
    return {"cleared_count": len(cleared), "cleared": cleared, "cleared_at": now_value}


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


# ---------------------------------------------------------------------------
# Repo health snapshot persistence (CENTRAL-OPS-38)
# ---------------------------------------------------------------------------

DEFAULT_HEALTH_TTL_SECONDS = 3600  # 1 hour


def _health_insert_snapshot(conn: sqlite3.Connection, report: dict[str, Any], ttl_seconds: int) -> int:
    """Insert one per-repo report into repo_health_snapshots; return snapshot_id."""
    repo_section = report.get("repo") or {}
    summary = report.get("summary") or {}
    repo_id = str(repo_section.get("repo_id") or "unknown")
    captured_at = str(report.get("generated_at") or now_iso())
    cur = conn.execute(
        """
        INSERT INTO repo_health_snapshots
            (repo_id, captured_at, ttl_seconds,
             working_status, evidence_quality, overall_status,
             adapter_name, adapter_version, profile, report_json)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            repo_id,
            captured_at,
            ttl_seconds,
            str(summary.get("working_status") or "unknown"),
            str(summary.get("evidence_quality") or "none"),
            str(summary.get("overall_status") or "unknown"),
            str(repo_section.get("adapter_name") or "unknown"),
            str(repo_section.get("adapter_version") or "unknown"),
            str(repo_section.get("profile") or "unknown"),
            json.dumps(report, sort_keys=True),
        ),
    )
    return int(cur.lastrowid)


def _health_is_stale(captured_at: str, ttl_seconds: int, now: str) -> bool:
    """Return True when a snapshot has aged past its TTL."""
    try:
        from datetime import datetime, timezone as _tz

        def _parse(s: str) -> datetime:
            s = s.replace("+00:00", "Z")
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
                try:
                    return datetime.strptime(s, fmt).replace(tzinfo=_tz.utc)
                except ValueError:
                    continue
            raise ValueError(f"unrecognised datetime: {s!r}")

        return (_parse(now) - _parse(captured_at)).total_seconds() > ttl_seconds
    except Exception:
        return False


def _health_annotate_stale(rows: list[dict[str, Any]], now_str: str) -> list[dict[str, Any]]:
    for row in rows:
        row["is_stale"] = _health_is_stale(
            str(row.get("captured_at") or ""),
            int(row.get("ttl_seconds") or DEFAULT_HEALTH_TTL_SECONDS),
            now_str,
        )
        row["freshness"] = "stale" if row["is_stale"] else "fresh"
    return rows


def _health_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "snapshot_id": row["snapshot_id"],
        "repo_id": row["repo_id"],
        "captured_at": row["captured_at"],
        "ttl_seconds": row["ttl_seconds"],
        "working_status": row["working_status"],
        "evidence_quality": row["evidence_quality"],
        "overall_status": row["overall_status"],
        "adapter_name": row["adapter_name"],
        "adapter_version": row["adapter_version"],
        "profile": row["profile"],
        "created_at": row["created_at"],
        "report_json": row["report_json"],
    }


def command_health_snapshot_write(args: argparse.Namespace) -> int:
    """Write repo health snapshots from a bundle JSON into the CENTRAL DB."""
    conn, _ = open_initialized_connection(args.db_path)
    try:
        raw = sys.stdin.read() if args.bundle_file == "-" else Path(args.bundle_file).read_text(encoding="utf-8")
        bundle = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        die(f"could not read bundle: {exc}")

    repos = bundle.get("repos") if isinstance(bundle, dict) else None
    if isinstance(repos, list):
        reports = repos
    elif isinstance(bundle, dict) and "repo" in bundle:
        reports = [bundle]
    else:
        die("bundle must be a repo-health-bundle (with 'repos') or a single report (with 'repo')")

    ttl = int(getattr(args, "ttl_seconds", DEFAULT_HEALTH_TTL_SECONDS))
    written: list[dict[str, Any]] = []
    try:
        with conn:
            for report in reports:
                sid = _health_insert_snapshot(conn, report, ttl)
                repo_section = report.get("repo") or {}
                written.append(
                    {
                        "snapshot_id": sid,
                        "repo_id": str(repo_section.get("repo_id") or "unknown"),
                        "captured_at": str(report.get("generated_at") or ""),
                        "working_status": str((report.get("summary") or {}).get("working_status") or "unknown"),
                    }
                )
    finally:
        conn.close()

    return print_or_json(
        {"written": written, "count": len(written)},
        as_json=args.json,
        formatter=lambda d: "Wrote {} health snapshot(s): {}".format(
            d["count"],
            ", ".join("{}@{}(id={})".format(w["repo_id"], w["captured_at"], w["snapshot_id"]) for w in d["written"]),
        ),
    )


def command_health_snapshot_latest(args: argparse.Namespace) -> int:
    """Show the latest health snapshot per repo, with stale flags."""
    conn, _ = open_initialized_connection(args.db_path)
    try:
        if getattr(args, "repo_id", None):
            cur = conn.execute(
                "SELECT * FROM repo_health_snapshots WHERE repo_id = ? ORDER BY captured_at DESC LIMIT 1",
                (args.repo_id,),
            )
        else:
            cur = conn.execute(
                """
                SELECT s.* FROM repo_health_snapshots s
                INNER JOIN (
                    SELECT repo_id, MAX(captured_at) AS max_captured
                    FROM repo_health_snapshots GROUP BY repo_id
                ) latest ON s.repo_id = latest.repo_id AND s.captured_at = latest.max_captured
                ORDER BY s.repo_id
                """
            )
        rows = [_health_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    now_str = now_iso()
    rows = _health_annotate_stale(rows, now_str)

    def fmt(data: list[dict[str, Any]]) -> str:
        if not data:
            return "No health snapshots found. Run: python3 scripts/repo_health.py snapshot --persist"
        cols: list[tuple[str, int]] = [
            ("repo_id", 20), ("working_status", 9), ("evidence_quality", 10),
            ("freshness", 7), ("captured_at", 22), ("overall_status", 9),
        ]
        header = "  ".join(h.ljust(w) for h, w in cols)
        table_rows = ["  ".join(str(row.get(h) or "").ljust(w) for h, w in cols) for row in data]
        lines = ["Latest repo health snapshots (as of {})".format(now_str), "", header, "-" * len(header)] + table_rows
        stale = [r for r in data if r.get("is_stale")]
        if stale:
            lines += ["", "WARNING: {} snapshot(s) are stale. Re-run: python3 scripts/repo_health.py snapshot --persist".format(len(stale))]
        return "\n".join(lines)

    return print_or_json(rows, as_json=args.json, formatter=fmt)


def command_health_snapshot_history(args: argparse.Namespace) -> int:
    """Show recent health snapshot history for trend/drift inspection."""
    conn, _ = open_initialized_connection(args.db_path)
    limit = int(getattr(args, "limit", 20))
    try:
        if getattr(args, "repo_id", None):
            cur = conn.execute(
                "SELECT * FROM repo_health_snapshots WHERE repo_id = ? ORDER BY captured_at DESC LIMIT ?",
                (args.repo_id, limit),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM repo_health_snapshots ORDER BY captured_at DESC LIMIT ?",
                (limit,),
            )
        rows = [_health_row_to_dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    now_str = now_iso()
    rows = _health_annotate_stale(rows, now_str)

    def fmt(data: list[dict[str, Any]]) -> str:
        if not data:
            return "No health snapshot history found."
        cols: list[tuple[str, int]] = [
            ("snapshot_id", 12), ("repo_id", 20), ("working_status", 9),
            ("freshness", 7), ("captured_at", 22),
        ]
        header = "  ".join(h.ljust(w) for h, w in cols)
        table_rows = ["  ".join(str(row.get(h) or "").ljust(w) for h, w in cols) for row in data]
        return "\n".join(
            ["Repo health snapshot history ({} records)".format(len(data)), "", header, "-" * len(header)] + table_rows
        )

    return print_or_json(rows, as_json=args.json, formatter=fmt)


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


def command_snapshot_create(args: argparse.Namespace) -> int:
    conn, db_path = open_initialized_connection(args.db_path)
    durability_dir = resolve_durability_dir(args.durability_dir)
    snapshot_id = args.snapshot_id or generate_snapshot_id()
    snapshot_dir = snapshots_root(durability_dir) / snapshot_id
    if snapshot_dir.exists():
        die(f"snapshot already exists: {snapshot_dir}")
    snapshot_db_path = snapshot_dir / SNAPSHOT_DB_FILENAME
    manifest_path = snapshot_dir / SNAPSHOT_MANIFEST_FILENAME
    try:
        backup_connection_to_path(conn, snapshot_db_path)
    finally:
        conn.close()
    snapshot_conn = connect_read_only(snapshot_db_path)
    try:
        manifest = build_snapshot_manifest(
            snapshot_conn,
            snapshot_id=snapshot_id,
            source_db_path=db_path,
            snapshot_db_path=snapshot_db_path,
            actor_id=args.actor_id,
            note=args.note,
        )
    finally:
        snapshot_conn.close()
    write_json_document(manifest_path, manifest)
    pointer_payload = {
        "snapshot_id": snapshot_id,
        "created_at": manifest["created_at"],
        "manifest_path": str(manifest_path.relative_to(durability_dir)),
    }
    write_json_document(latest_snapshot_pointer_path(durability_dir), pointer_payload)
    payload = {
        "snapshot_id": snapshot_id,
        "created_at": manifest["created_at"],
        "source_db_path": str(db_path),
        "durability_dir": str(durability_dir),
        "snapshot_dir": str(snapshot_dir),
        "snapshot_db_path": str(snapshot_db_path),
        "manifest_path": str(manifest_path),
        "latest_pointer_path": str(latest_snapshot_pointer_path(durability_dir)),
        "db_sha256": manifest["db_sha256"],
        "db_bytes": manifest["db_bytes"],
        "task_count": manifest["task_count"],
        "event_count": manifest["event_count"],
        "planner_state_digest": manifest["planner_state_digest"],
        "runtime_state_digest": manifest["runtime_state_digest"],
        "note": args.note,
    }
    return print_or_json(payload, as_json=args.json, formatter=lambda data: "\n".join([
        f"Snapshot ID:      {data['snapshot_id']}",
        f"Created at:       {data['created_at']}",
        f"Source DB:        {data['source_db_path']}",
        f"Snapshot DB:      {data['snapshot_db_path']}",
        f"Manifest:         {data['manifest_path']}",
        f"Latest pointer:   {data['latest_pointer_path']}",
        f"Task count:       {data['task_count']}",
        f"DB bytes:         {data['db_bytes']}",
        f"Planner digest:   {data['planner_state_digest']}",
    ]))


def command_snapshot_list(args: argparse.Namespace) -> int:
    durability_dir = resolve_durability_dir(args.durability_dir)
    manifests = list_snapshot_manifests(durability_dir)
    if args.limit is not None:
        manifests = manifests[: args.limit]
    latest_snapshot_id: str | None = None
    pointer_path = latest_snapshot_pointer_path(durability_dir)
    if pointer_path.exists():
        latest_snapshot_id = str(load_latest_snapshot_pointer(durability_dir)["snapshot_id"])
    rows = render_snapshot_rows(manifests)
    if latest_snapshot_id is not None:
        for row in rows:
            if row["snapshot_id"] == latest_snapshot_id:
                row["note"] = (row["note"] + " [latest]").strip()
                break
    payload = {
        "durability_dir": str(durability_dir),
        "latest_snapshot_id": latest_snapshot_id,
        "count": len(manifests),
        "snapshots": manifests,
    }
    return print_or_json(payload, as_json=args.json, formatter=lambda data: "\n".join([
        f"Durability dir: {data['durability_dir']}",
        f"Snapshots:      {data['count']}",
        f"Latest:         {data['latest_snapshot_id'] or '(none)'}",
        "",
        render_table(rows, [("snapshot_id", "snapshot_id"), ("created_at", "created_at"), ("tasks", "task_count"), ("events", "event_count"), ("db_mb", "db_mb"), ("planner", "planner_digest"), ("note", "note")]),
    ]))


def command_snapshot_restore(args: argparse.Namespace) -> int:
    durability_dir = resolve_durability_dir(args.durability_dir)
    manifest, manifest_path = resolve_snapshot_manifest(durability_dir, args.snapshot_id)
    snapshot_db_path = manifest_path.parent / str(manifest.get("db_filename", SNAPSHOT_DB_FILENAME))
    if not snapshot_db_path.exists():
        die(f"snapshot database not found: {snapshot_db_path}")
    target_db_path = resolve_db_path(args.db_path)
    backup_path: str | None = None
    if target_db_path.exists() and not args.no_backup_existing:
        backup_dir = (
            Path(args.backup_dir).expanduser().resolve()
            if args.backup_dir
            else target_db_path.parent / "backups"
        )
        backup_name = f"{target_db_path.stem}.pre-restore-{manifest['snapshot_id']}.db"
        backup_target = backup_dir / backup_name
        copy_sqlite_database(target_db_path, backup_target)
        backup_path = str(backup_target)
    temp_target = target_db_path.parent / f".{target_db_path.name}.{manifest['snapshot_id']}.tmp"
    copy_sqlite_database(snapshot_db_path, temp_target)
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp_target, target_db_path)
    verify_conn = connect_read_only(target_db_path)
    try:
        require_initialized_db(verify_conn, target_db_path)
    finally:
        verify_conn.close()
    payload = {
        "snapshot_id": manifest["snapshot_id"],
        "manifest_path": str(manifest_path),
        "snapshot_db_path": str(snapshot_db_path),
        "target_db_path": str(target_db_path),
        "backup_path": backup_path,
        "task_count": manifest["task_count"],
        "planner_state_digest": manifest["planner_state_digest"],
        "runtime_state_digest": manifest["runtime_state_digest"],
    }
    return print_or_json(payload, as_json=args.json, formatter=lambda data: "\n".join([
        f"Restored snapshot: {data['snapshot_id']}",
        f"Source DB:         {data['snapshot_db_path']}",
        f"Target DB:         {data['target_db_path']}",
        f"Backup DB:         {data['backup_path'] or '(skipped)'}",
        f"Task count:        {data['task_count']}",
        f"Planner digest:    {data['planner_state_digest']}",
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
                metadata=None if args.metadata_json is None else parse_json_text(args.metadata_json, default={}),
            )
            if args.alias is not None:
                replace_repo_aliases(conn, repo_id=args.repo_id, aliases=args.alias)
        payload = fetch_repo_payload(conn, args.repo_id)
    finally:
        conn.close()
    if payload is None:
        die(f"repo not found after upsert: {args.repo_id}")
    return print_or_json(payload, as_json=args.json, formatter=lambda row: render_repo_rows([row]))


def command_repo_list(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        payload = fetch_repo_registry(conn)
    finally:
        conn.close()
    return print_or_json(payload, as_json=args.json, formatter=render_repo_rows)


def command_repo_resolve(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        payload = resolve_repo_reference(conn, args.repo, field="repo")
    finally:
        conn.close()
    if payload is None:
        die(f"repo not found: {args.repo}")
    return print_or_json(payload, as_json=args.json, formatter=lambda row: render_repo_rows([row]))


def command_repo_show(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        payload = resolve_repo_reference(conn, args.repo, field="repo")
    finally:
        conn.close()
    if payload is None:
        die(f"repo not found: {args.repo}")
    return print_or_json(payload, as_json=args.json, formatter=render_repo_detail)


def _build_batch_task_payload(
    item: dict[str, Any],
    *,
    task_id: str,
    resolved_repo: dict[str, Any],
    defaults: dict[str, Any],
    series: str,
    actor_id: str,
) -> dict[str, Any]:
    """Merge batch defaults + item fields into a full task payload."""
    merged = {**defaults, **item}
    title = merged.get("title") or ""
    repo_id = str(resolved_repo["repo_id"])
    payload: dict[str, Any] = {
        "task_id": task_id,
        "title": title,
        "summary": merged.get("summary") or markdown_summary(merged.get("objective") or title, fallback=title),
        "objective_md": merged.get("objective") or merged.get("objective_md") or f"Implement and verify {title}.",
        "context_md": merged.get("context") or merged.get("context_md") or "Context is TBD.",
        "scope_md": merged.get("scope") or merged.get("scope_md") or "Scope is narrow and aligned to this task.",
        "deliverables_md": merged.get("deliverables") or merged.get("deliverables_md") or "- [ ] Implement requested changes.\n- [ ] Add/update verification and docs where needed.",
        "acceptance_md": merged.get("acceptance") or merged.get("acceptance_md") or "- [ ] Task matches objective.\n- [ ] Planner and runtime expectations are satisfied.",
        "testing_md": merged.get("testing") or merged.get("testing_md") or "Run task-specific checks and record outcomes.",
        "dispatch_md": merged.get("dispatch") or merged.get("dispatch_md") or f"Dispatch from CENTRAL using repo={repo_id} do task {task_id}.",
        "closeout_md": merged.get("closeout") or merged.get("closeout_md") or f"Summarize results and closeout evidence for {task_id}.",
        "reconciliation_md": merged.get("reconciliation") or merged.get("reconciliation_md") or "Reconcile planner and runtime state according to normal closeout policy.",
        "planner_status": merged.get("planner_status", "todo"),
        "priority": merged.get("priority", 100),
        "task_type": merged.get("task_type", "mutating"),
        "planner_owner": merged.get("planner_owner", actor_id),
        "worker_owner": merged.get("worker_owner"),
        "target_repo_id": repo_id,
        "target_repo_root": str(resolved_repo["repo_root"]),
        "target_repo_display_name": str(resolved_repo["display_name"]),
        "approval_required": merged.get("approval_required", False),
        "source_kind": merged.get("source_kind", "batch_scaffold"),
        "metadata": {
            **(merged.get("metadata") or {}),
            "batch_series": series,
            "generated_by": "task-batch-create",
        },
        "execution": merged.get("execution") or {
            "task_kind": merged.get("task_kind", "mutating"),
            "sandbox_mode": merged.get("sandbox_mode", "workspace-write"),
            "approval_policy": merged.get("approval_policy", "never"),
            "additional_writable_dirs": merged.get("additional_writable_dirs") or [],
            "timeout_seconds": merged.get("timeout_seconds", 3600),
        },
        "dependencies": merged.get("dependencies") or [],
    }
    return payload


def command_task_batch_create(args: argparse.Namespace) -> int:
    """Create multiple tasks from a YAML or JSON batch file."""
    doc = load_batch_document(args.input)
    items = doc["tasks"]
    if not items:
        die("batch 'tasks' list is empty")

    actor_id: str = args.actor_id
    series: str = (args.series or doc.get("series") or DEFAULT_TASK_ID_SERIES).strip().upper()
    repo_ref: str = (args.repo or doc.get("repo") or "CENTRAL").strip()
    defaults: dict[str, Any] = doc.get("defaults") or {}
    dry_run: bool = args.dry_run

    # Validate items before touching the DB.
    validation_errors: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            validation_errors.append({"index": idx, "error": f"item must be an object, got {type(item).__name__}"})
            continue
        title = item.get("title") or defaults.get("title")
        if not title:
            validation_errors.append({"index": idx, "item": item, "error": "missing required field: title"})
    if validation_errors:
        print(json.dumps({"status": "validation_failed", "errors": validation_errors}, indent=2))
        return 1

    conn, _ = open_initialized_connection(args.db_path)
    results: list[dict[str, Any]] = []
    created_count = 0
    failed_count = 0
    try:
        # Resolve repo once for the whole batch.
        resolved_repo = resolve_repo_reference(conn, repo_ref, field="target_repo", allow_missing=True)
        if resolved_repo is None:
            die_repo_onboarding_required(
                conn,
                operation="batch task creation",
                repo_id=repo_ref,
                repo_root=None,
                reason=f"target repo is not registered: {repo_ref!r}.",
                aliases=[repo_ref],
            )

        # Reserve a contiguous ID range for items that don't supply explicit task_ids.
        needs_id_count = sum(1 for item in items if not item.get("task_id"))
        reserved_ids: list[str] = []
        if needs_id_count > 0 and not dry_run:
            with conn:
                reservation = reserve_task_id_range(
                    conn,
                    series=series,
                    count=needs_id_count,
                    reserved_by=actor_id,
                    reserved_for="task-batch-create",
                    note=f"batch of {len(items)} tasks",
                    reservation_hours=DEFAULT_TASK_ID_RESERVATION_HOURS,
                )
            start = int(reservation["range_start"])
            reserved_ids = [make_task_id(series, start + i) for i in range(needs_id_count)]
        elif needs_id_count > 0 and dry_run:
            # For dry-run, compute IDs without persisting.
            next_payload = next_task_id_payload(conn, series=series, actor_id=actor_id)
            start = int(next_payload["next_number"])
            reserved_ids = [make_task_id(series, start + i) for i in range(needs_id_count)]

        id_iter = iter(reserved_ids)
        for idx, item in enumerate(items):
            task_id = item.get("task_id") or next(id_iter)
            # Per-item repo override.
            item_repo_ref = item.get("repo") or repo_ref
            if item_repo_ref != repo_ref:
                item_resolved_repo = resolve_repo_reference(conn, item_repo_ref, field="target_repo", allow_missing=True)
                if item_resolved_repo is None:
                    results.append({"index": idx, "task_id": task_id, "status": "error",
                                    "error": f"repo not registered: {item_repo_ref!r}"})
                    failed_count += 1
                    continue
            else:
                item_resolved_repo = resolved_repo

            try:
                payload = _build_batch_task_payload(
                    item,
                    task_id=task_id,
                    resolved_repo=item_resolved_repo,
                    defaults=defaults,
                    series=series,
                    actor_id=actor_id,
                )
                validated = validate_task_payload(payload, for_update=False)
            except SystemExit:
                results.append({"index": idx, "task_id": task_id, "status": "error",
                                "error": "payload validation failed"})
                failed_count += 1
                continue

            if dry_run:
                results.append({"index": idx, "task_id": task_id, "status": "dry_run",
                                "title": validated.get("title")})
                created_count += 1
                continue

            try:
                with conn:
                    create_task(conn, validated, actor_kind="planner", actor_id=actor_id)
                results.append({"index": idx, "task_id": task_id, "status": "created",
                                "title": validated.get("title")})
                created_count += 1
            except Exception as exc:
                results.append({"index": idx, "task_id": task_id, "status": "error",
                                "error": str(exc)})
                failed_count += 1
    finally:
        conn.close()

    summary = {
        "status": "done" if failed_count == 0 else "partial",
        "dry_run": dry_run,
        "total": len(items),
        "created": created_count,
        "failed": failed_count,
        "results": results,
    }
    print(json.dumps(summary, indent=2))
    return 1 if failed_count > 0 else 0


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


def command_planner_new(args: argparse.Namespace) -> int:
    repo_reference = (args.repo or "CENTRAL").strip()
    conn, _ = open_initialized_connection(args.db_path)
    try:
        resolved_repo = resolve_repo_reference(
            conn,
            repo_reference,
            field="target_repo",
            allow_missing=True,
        )
        if resolved_repo is None:
            die_repo_onboarding_required(
                conn,
                operation="planner task scaffold creation",
                repo_id=repo_reference,
                repo_root=None,
                reason=f"target repo is not registered: {repo_reference!r}.",
                aliases=[repo_reference],
            )
        with conn:
            next_payload = next_task_id_payload(conn, series=args.series, actor_id=args.actor_id)
            task_id = str(next_payload["next_task_id"])
            payload = {
                "task_id": task_id,
                "title": args.title,
                "summary": markdown_summary(args.objective or args.title, fallback=args.title),
                "objective_md": args.objective or f"Implement and verify {args.title}.",
                "context_md": args.context or "Context is TBD.",
                "scope_md": args.scope or "Scope is narrow and aligned to this task.",
                "deliverables_md": args.deliverables or "- [ ] Implement requested changes.\n- [ ] Add/update verification and docs where needed.",
                "acceptance_md": args.acceptance or "- [ ] Task matches objective.\n- [ ] `scripts/build.sh` exits 0 (smoke test + full tests + build).",
                "testing_md": args.testing or "Run `scripts/build.sh` in the target repo. Task fails if the build script fails.",
                "dispatch_md": args.dispatch or f"Dispatch from CENTRAL using repo={repo_reference} do task {task_id}.",
                "closeout_md": args.closeout or f"Summarize results and closeout evidence for {task_id}.",
                "reconciliation_md": args.reconciliation or "Reconcile planner and runtime state according to normal closeout policy.",
                "planner_status": args.planner_status,
                "priority": args.priority,
                "task_type": args.task_type,
                "planner_owner": args.planner_owner,
                "worker_owner": None,
                "target_repo_id": str(resolved_repo["repo_id"]),
                "target_repo_root": str(resolved_repo["repo_root"]),
                "target_repo_display_name": str(resolved_repo["display_name"]),
                "approval_required": args.approval_required,
                "source_kind": "planner_scaffold",
                "initiative": getattr(args, "initiative", None) or None,
                "metadata": {
                    "planner_new_series": args.series,
                    "planner_new_next_number": next_payload["next_number"],
                },
                "execution": {
                    "task_kind": args.task_kind,
                    "sandbox_mode": args.sandbox_mode,
                    "approval_policy": args.approval_policy,
                    "additional_writable_dirs": args.additional_writable_dir or [],
                    "timeout_seconds": args.timeout_seconds,
                    "metadata": {
                        "generated_by": "planner-new",
                        "planner_new_allocation": next_payload,
                    },
                },
                "dependencies": args.depends_on,
            }
            payload = validate_task_payload(payload, for_update=False)
            resolve_task_repo_target(conn, payload)
            create_task(conn, payload, actor_kind="planner", actor_id=args.actor_id)
    finally:
        conn.close()
    if not args.depends_on:
        print(
            f"NOTE: {task_id} created with no --depends-on. "
            "Run 'dep-lint' after creation to check for undeclared dependency edges.",
            file=sys.stderr,
        )
    return print_or_json(payload, as_json=True, formatter=json_dumps)


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
        repo_id = resolve_repo_filter(conn, args.repo_id)
        initiative_filter = getattr(args, "initiative", None) or None
        snapshots = fetch_task_snapshots(conn, repo_id=repo_id, planner_status=args.planner_status, initiative=initiative_filter)
        rows = [
            {
                "task_id": snapshot["task_id"],
                "priority": snapshot["priority"],
                "planner_status": snapshot["planner_status"],
                "runtime_status": snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else "",
                "repo": snapshot["target_repo_id"],
                "initiative": snapshot["initiative"] or "",
                "planner_owner": snapshot["planner_owner"],
                "worker_owner": snapshot["worker_owner"] or "",
                "version": snapshot["version"],
                "title": snapshot["title"],
            }
            for snapshot in snapshots
        ]
    finally:
        conn.close()
    return print_or_json(rows, as_json=args.json, formatter=lambda data: render_table(data, [("task_id", "task_id"), ("p", "priority"), ("planner", "planner_status"), ("runtime", "runtime_status"), ("repo", "repo"), ("initiative", "initiative"), ("version", "version"), ("title", "title")]))


def command_task_id_next(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        with conn:
            payload = next_task_id_payload(conn, series=args.series, actor_id=args.actor_id)
    finally:
        conn.close()
    return print_or_json(
        payload,
        as_json=args.json,
        formatter=lambda data: "\n".join(
            [
                f"Series:                    {data['series']}",
                f"Strategy:                  {data['strategy']}",
                f"Next Task ID:              {data['next_task_id']}",
                f"Highest Existing Number:   {data['highest_existing_number']}",
                f"Highest Active Reserved:   {data['highest_active_reservation_number']}",
                f"Active Reservation Count:  {data['active_reservation_count']}",
            ]
        ),
    )


def command_task_id_reserve(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        payload = reserve_task_id_range(
            conn,
            series=args.series,
            count=args.count,
            reserved_by=args.actor_id,
            reserved_for=args.reserved_for,
            note=args.note,
            reservation_hours=args.hours,
        )
    finally:
        conn.close()
    return print_or_json(
        payload,
        as_json=args.json,
        formatter=lambda data: "\n".join(
            [
                f"Reservation ID:  {data['reservation_id']}",
                f"Series:          {data['series']}",
                f"Range:           {data['range_label']}",
                f"Status:          {data['status']}",
                f"Reserved By:     {data['reserved_by']}",
                f"Reserved For:    {data['reserved_for'] or '(unspecified)'}",
                f"Expires At:      {data['expires_at']}",
            ]
        ),
    )


def command_task_id_reservations(args: argparse.Namespace) -> int:
    if args.all and args.status is not None:
        die("--all cannot be combined with --status")
    conn, _ = open_initialized_connection(args.db_path)
    try:
        with conn:
            reconcile_task_id_reservations(conn, series=args.series, actor_id=args.actor_id)
        status = None if args.all else (args.status or "active")
        rows = fetch_task_id_reservation_rows(
            conn,
            series=args.series,
            status=status,
            reservation_id=args.reservation_id,
            limit=args.limit,
        )
        events = fetch_task_id_reservation_events(
            conn,
            [str(row["reservation_id"]) for row in rows],
            limit_per_reservation=10,
        )
        payload = [
            build_task_id_reservation_payload(conn, row, include_events=args.include_events, events=events)
            for row in rows
        ]
    finally:
        conn.close()
    return print_or_json(
        payload,
        as_json=args.json,
        formatter=lambda data: render_table(
            [
                {
                    "reservation_id": row["reservation_id"],
                    "series": row["series"],
                    "range": row["range_label"],
                    "status": row["status"],
                    "open": row["open_count"],
                    "filled": row["filled_count"],
                    "expires_at": row["expires_at"],
                    "reserved_by": row["reserved_by"],
                    "reserved_for": row["reserved_for"] or "",
                }
                for row in data
            ],
            [
                ("reservation_id", "reservation_id"),
                ("series", "series"),
                ("range", "range"),
                ("status", "status"),
                ("open", "open"),
                ("filled", "filled"),
                ("expires_at", "expires_at"),
                ("reserved_by", "reserved_by"),
                ("reserved_for", "reserved_for"),
            ],
        ),
    )


def command_view_summary(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        initiative_filter = getattr(args, "initiative", None) or None
        summary = summarize_portfolio(conn, initiative=initiative_filter)
    finally:
        conn.close()
    return print_or_json(summary, as_json=args.json, formatter=format_summary_text)


def command_view_eligible(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        repo_id = resolve_repo_filter(conn, args.repo_id)
        snapshots = fetch_task_snapshots(conn, repo_id=repo_id)
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
        repo_id = resolve_repo_filter(conn, args.repo_id)
        snapshots = fetch_task_snapshots(conn, repo_id=repo_id)
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


def command_view_active(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshots = fetch_task_snapshots(conn)
        # Filter to non-terminal states only
        active_snapshots = []
        for snapshot in snapshots:
            planner_status = snapshot["planner_status"]
            runtime_status = snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else ""

            # Include if planner is not done
            if planner_status != "done":
                # And if runtime is non-terminal or empty
                if runtime_status == "" or runtime_status in {"queued", "claimed", "running", "pending_review"}:
                    active_snapshots.append(snapshot)

        rows = [
            {
                "task_id": snapshot["task_id"],
                "priority": snapshot["priority"],
                "planner_status": snapshot["planner_status"],
                "runtime_status": snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else "",
                "repo": snapshot["target_repo_id"],
                "title": snapshot["title"],
            }
            for snapshot in active_snapshots
        ]
    finally:
        conn.close()
    return print_or_json(rows, as_json=args.json, formatter=lambda data: "\n".join([generated_banner(now_iso()), "", render_table(data, [("task_id", "task_id"), ("p", "priority"), ("planner", "planner_status"), ("runtime", "runtime_status"), ("repo", "repo"), ("title", "title")])]))


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
    return print_or_json(
        rows,
        as_json=args.json,
        formatter=lambda data: "\n".join(
            [
                generated_banner(now_iso()),
                "",
                render_table(
                    data,
                    [
                        ("task_id", "task_id"),
                        ("repo", "repo"),
                        ("severity", "severity"),
                        ("runtime", "runtime_status"),
                        ("planner", "planner_status"),
                        ("age_at", "age_at"),
                        ("claimed_by", "claimed_by"),
                        ("warning", "status_warning"),
                        ("last_error", "last_error"),
                    ],
                ),
            ]
        ),
    )


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


def command_dep_show(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshots = fetch_task_snapshots(conn, task_id=args.task_id)
        if not snapshots:
            die(f"task not found: {args.task_id}")
        snapshot = snapshots[0]
        forward = snapshot["dependencies"]
        reverse_rows = conn.execute(
            """
            SELECT d.task_id, t.planner_status, t.title
            FROM task_dependencies d
            JOIN tasks t ON t.task_id = d.task_id
            WHERE d.depends_on_task_id = ?
            ORDER BY d.task_id
            """,
            (args.task_id,),
        ).fetchall()
        reverse = [{"task_id": r["task_id"], "planner_status": r["planner_status"], "title": r["title"]} for r in reverse_rows]
        result = {
            "task_id": args.task_id,
            "title": snapshot["title"],
            "planner_status": snapshot["planner_status"],
            "depends_on": forward,
            "depended_on_by": reverse,
        }
    finally:
        conn.close()

    def fmt(data: dict[str, Any]) -> str:
        lines = [f"Task: {data['task_id']} ({data['planner_status']}) — {data['title']}", ""]
        lines.append("Depends on:")
        if data["depends_on"]:
            for dep in data["depends_on"]:
                marker = "[done]" if dep["depends_on_status"] == "done" else "[open]"
                lines.append(f"  {marker} {dep['depends_on_task_id']} ({dep['depends_on_status']}) — {dep['depends_on_title']}")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("Depended on by:")
        if data["depended_on_by"]:
            for dep in data["depended_on_by"]:
                lines.append(f"  [open] {dep['task_id']} ({dep['planner_status']}) — {dep['title']}")
        else:
            lines.append("  (none)")
        return "\n".join(lines)

    return print_or_json(result, as_json=args.json, formatter=fmt)


def command_dep_graph(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        query = """
            SELECT d.task_id, d.depends_on_task_id, d.dependency_kind,
                   t1.planner_status AS from_status, t1.title AS from_title,
                   t2.planner_status AS to_status, t2.title AS to_title
            FROM task_dependencies d
            JOIN tasks t1 ON t1.task_id = d.task_id
            JOIN tasks t2 ON t2.task_id = d.depends_on_task_id
        """
        params: list[Any] = []
        if not args.include_done:
            query += " WHERE t1.planner_status != 'done' OR t2.planner_status != 'done'"
        query += " ORDER BY d.task_id, d.depends_on_task_id"
        rows = conn.execute(query, params).fetchall()
        edges = [dict(r) for r in rows]
    finally:
        conn.close()

    def fmt(data: list[dict[str, Any]]) -> str:
        if not data:
            return "No dependency edges found."
        grouped: dict[str, list[dict[str, Any]]] = {}
        for edge in data:
            grouped.setdefault(edge["task_id"], []).append(edge)
        lines = []
        for task_id, task_edges in grouped.items():
            first = task_edges[0]
            lines.append(f"{task_id} ({first['from_status']}) — {first['from_title']}")
            for edge in task_edges:
                marker = "[done]" if edge["to_status"] == "done" else "[open]"
                lines.append(f"  → {marker} {edge['depends_on_task_id']} ({edge['to_status']}) — {edge['to_title']}")
            lines.append("")
        return "\n".join(lines).rstrip()

    return print_or_json(edges, as_json=args.json, formatter=fmt)


_TASK_ID_PATTERN = re.compile(r"\b([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+)\b")


def command_dep_lint(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshots = fetch_task_snapshots(conn)
        known_ids = {s["task_id"] for s in snapshots}
        warnings: list[dict[str, Any]] = []
        for s in snapshots:
            if s["planner_status"] == "done":
                continue
            existing_deps = {d["depends_on_task_id"] for d in s["dependencies"]}
            text = " ".join(
                filter(
                    None,
                    [
                        s.get("title"),
                        s.get("summary"),
                        s.get("objective_md"),
                        s.get("context_md"),
                        s.get("scope_md"),
                        s.get("deliverables_md"),
                    ],
                )
            )
            mentioned = (_TASK_ID_PATTERN.findall(text) and set(_TASK_ID_PATTERN.findall(text))) or set()
            mentioned = mentioned & known_ids - {s["task_id"]}
            missing = mentioned - existing_deps
            for m in sorted(missing):
                warnings.append(
                    {
                        "task_id": s["task_id"],
                        "title": s["title"],
                        "mentioned_task_id": m,
                        "warning": "task ID referenced in text but not declared as a dependency",
                    }
                )
    finally:
        conn.close()

    def fmt(data: list[dict[str, Any]]) -> str:
        if not data:
            return "dep-lint: no missing dependency edges detected."
        lines = [f"dep-lint: {len(data)} potential missing edge(s) found.", ""]
        for w in data:
            lines.append(f"  {w['task_id']} — {w['title']}")
            lines.append(f"    mentions {w['mentioned_task_id']} in text but no dependency edge declared")
        return "\n".join(lines)

    if warnings:
        print_or_json(warnings, as_json=args.json, formatter=fmt)
        return 1
    return print_or_json(warnings, as_json=args.json, formatter=fmt)


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


def command_export_tasks_board_md(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        generated_at = now_iso()
        summary = summarize_portfolio(conn)
        snapshots = fetch_task_snapshots(conn)
    finally:
        conn.close()
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else (DEFAULT_GENERATED_DIR / "tasks.md")
    )
    write_output(output_path, render_generated_tasks_board(summary, snapshots, generated_at=generated_at))
    payload = {
        "output_path": str(output_path),
        "generated_at": generated_at,
        "task_count": len(snapshots),
    }
    return print_or_json(payload, as_json=args.json, formatter=json_dumps)


def command_export_markdown_bundle(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        generated_at = now_iso()
        summary = summarize_portfolio(conn)
        snapshots = fetch_task_snapshots(conn)
        blocked_rows = format_blocked_rows(snapshots)
        review_rows = format_review_rows(snapshots)
        assignment_rows = format_assignments_rows(snapshots)
    finally:
        conn.close()

    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else DEFAULT_GENERATED_DIR
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    board_path = output_dir / "tasks.md"
    summary_path = output_dir / "portfolio_summary.md"
    blocked_path = output_dir / "blocked_tasks.md"
    review_path = output_dir / "review_queue.md"
    assignments_path = output_dir / "assignments.md"
    per_repo_dir = output_dir / "per_repo"
    task_cards_dir = output_dir / "task_cards"
    per_repo_dir.mkdir(parents=True, exist_ok=True)
    task_cards_dir.mkdir(parents=True, exist_ok=True)

    write_output(board_path, render_generated_tasks_board(summary, snapshots, generated_at=generated_at))
    write_output(summary_path, render_summary_markdown(summary))

    def _render_simple_markdown(title: str, rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
        lines = [f"# {title}", "", generated_banner(generated_at), ""]
        if rows:
            lines.append("```text")
            lines.append(render_table(rows, columns))
            lines.append("```")
        else:
            lines.append("- none")
        lines.append("")
        return "\n".join(lines)

    write_output(
        blocked_path,
        _render_simple_markdown(
            "Blocked Tasks",
            blocked_rows,
            [("task_id", "task_id"), ("repo", "repo"), ("planner_owner", "planner_owner"), ("blocked_at", "blocked_at"), ("blocker", "blocker")],
        ),
    )
    write_output(
        review_path,
        _render_simple_markdown(
            "Review Queue",
            review_rows,
            [
                ("task_id", "task_id"),
                ("repo", "repo"),
                ("severity", "severity"),
                ("runtime", "runtime_status"),
                ("planner", "planner_status"),
                ("age_at", "age_at"),
                ("claimed_by", "claimed_by"),
                ("warning", "status_warning"),
            ],
        ),
    )
    write_output(
        assignments_path,
        _render_simple_markdown(
            "Assignments And Leases",
            assignment_rows,
            [("task_id", "task_id"), ("repo", "repo"), ("planner_owner", "planner_owner"), ("worker_owner", "worker_owner"), ("lease_owner", "lease_owner"), ("lease_expires_at", "lease_expires_at"), ("runtime", "runtime_status")],
        ),
    )

    per_repo_paths: list[str] = []
    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snapshot in snapshots:
        by_repo[str(snapshot["target_repo_id"])].append(snapshot)
    for repo_id in sorted(by_repo.keys()):
        repo_path = per_repo_dir / f"{repo_id}.md"
        write_output(repo_path, render_repo_markdown(repo_id, by_repo[repo_id], generated_at=generated_at))
        per_repo_paths.append(str(repo_path))

    task_card_paths: list[str] = []
    for snapshot in snapshots:
        task_card_path = task_cards_dir / f"{snapshot['task_id']}.md"
        write_output(task_card_path, render_task_card_markdown(snapshot, generated_at=generated_at))
        task_card_paths.append(str(task_card_path))

    payload = {
        "generated_at": generated_at,
        "output_dir": str(output_dir),
        "board_path": str(board_path),
        "summary_path": str(summary_path),
        "blocked_path": str(blocked_path),
        "review_path": str(review_path),
        "assignments_path": str(assignments_path),
        "per_repo_count": len(per_repo_paths),
        "task_card_count": len(task_card_paths),
    }
    return print_or_json(payload, as_json=args.json, formatter=json_dumps)


def command_export_repo_md(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        generated_at = now_iso()
        repo_id = resolve_repo_filter(conn, args.repo_id)
        if repo_id is None:
            die("repo reference is required")
        snapshots = fetch_task_snapshots(conn, repo_id=repo_id)
    finally:
        conn.close()
    output_path = (
        Path(args.output).expanduser().resolve()
        if args.output
        else (DEFAULT_GENERATED_DIR / "per_repo" / f"{repo_id}.md")
    )
    write_output(output_path, render_repo_markdown(repo_id, snapshots, generated_at=generated_at))
    payload = {
        "generated_at": generated_at,
        "output_path": str(output_path),
        "repo_id": repo_id,
        "task_count": len(snapshots),
    }
    return print_or_json(payload, as_json=args.json, formatter=json_dumps)


def command_runtime_eligible(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        repo_id = resolve_repo_filter(conn, args.repo_id)
        rows = format_eligible_rows(fetch_task_snapshots(conn, repo_id=repo_id))
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
            effective_worker_model=args.effective_worker_model,
            worker_model_source=args.worker_model_source,
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


def operator_fail_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    actor_id: str,
    reason: str,
) -> dict[str, Any]:
    """Forcibly set runtime_status=failed for a task, recording an operator event."""
    begin_immediate(conn)
    runtime_row = conn.execute("SELECT * FROM task_runtime_state WHERE task_id = ?", (task_id,)).fetchone()
    if runtime_row is None:
        # Create a minimal runtime state row so we can set failed
        conn.rollback()
        raise RuntimeError(f"runtime state missing for {task_id}; run runtime-transition first or use task-show to confirm the task exists")
    lease = fetch_active_lease(conn, task_id)
    if lease is not None:
        close_active_assignments(conn, task_id=task_id, assignee_kind="worker", assignee_id=str(lease["lease_owner_id"]))
        conn.execute("DELETE FROM task_active_leases WHERE task_id = ?", (task_id,))
    current = row_to_dict(runtime_row) or {}
    retry_count = int(current.get("retry_count") or 0) + 1
    transition_at = now_iso()
    conn.execute(
        """
        UPDATE task_runtime_state
        SET runtime_status = 'failed',
            last_runtime_error = ?,
            finished_at = ?,
            last_transition_at = ?,
            retry_count = ?
        WHERE task_id = ?
        """,
        (reason, transition_at, transition_at, retry_count, task_id),
    )
    insert_event(
        conn,
        task_id=task_id,
        event_type="operator.fail",
        actor_kind="operator",
        actor_id=actor_id,
        payload={"summary": reason},
    )
    conn.commit()
    return fetch_task_snapshots(conn, task_id=task_id)[0]


def build_planner_panel(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate the richest single planner view used by the UI."""
    snapshots = fetch_task_snapshots(conn)
    generated_at = now_iso()

    eligible_work = []
    awaiting_audit = []
    recent_failures = []
    changed_since: list[dict[str, Any]] = []
    changed_cutoff_hours = 24
    from datetime import timedelta

    cutoff_dt = None
    try:
        from datetime import datetime, timezone, timedelta
        cutoff_dt = (datetime.now(timezone.utc) - timedelta(hours=changed_cutoff_hours)).isoformat()
    except Exception:
        pass

    for snapshot in snapshots:
        runtime = snapshot["runtime"]
        planner_status = snapshot["planner_status"]
        runtime_status = runtime["runtime_status"] if runtime else None
        task_type = snapshot.get("task_type") or "task"

        base = {
            "task_id": snapshot["task_id"],
            "title": snapshot["title"],
            "priority": snapshot["priority"],
            "repo": snapshot["target_repo_id"],
            "planner_status": planner_status,
            "runtime_status": runtime_status or "",
            "planner_owner": snapshot["planner_owner"],
            "worker_owner": snapshot["worker_owner"] or "",
            "task_type": task_type,
        }

        if task_is_eligible(snapshot):
            eligible_work.append(base)

        if planner_status == "awaiting_audit":
            awaiting_audit.append(base)

        if runtime_status == "failed":
            recent_failures.append({
                **base,
                "last_error": runtime.get("last_runtime_error") or "",
                "retry_count": runtime.get("retry_count", 0),
            })

        if cutoff_dt and snapshot.get("updated_at") and snapshot["updated_at"] >= cutoff_dt:
            changed_since.append({
                **base,
                "updated_at": snapshot["updated_at"],
            })

    # ready_audits: audit-type tasks that are eligible
    ready_audits = [t for t in eligible_work if t.get("task_type") == "audit"]

    # Sort eligible by priority
    eligible_work.sort(key=lambda t: t["priority"])
    changed_since.sort(key=lambda t: t.get("updated_at", ""), reverse=True)

    summary = {
        "eligible_count": len(eligible_work),
        "awaiting_audit_count": len(awaiting_audit),
        "failed_audit_count": sum(1 for s in snapshots if s.get("task_type") == "audit" and s["planner_status"] == "failed"),
        "stale_count": sum(
            1 for s in snapshots
            if s["runtime"] and s["runtime"]["runtime_status"] in {"timeout", "failed"}
            and s["planner_status"] in {"todo", "in_progress"}
        ),
        "changed_since_count": len(changed_since),
    }

    return {
        "generated_at": generated_at,
        "eligible_work": eligible_work,
        "awaiting_audit": awaiting_audit,
        "ready_audits": ready_audits,
        "recent_failures": recent_failures,
        "changed_since": changed_since[:20],
        "summary": summary,
    }


def build_audits_view(conn: sqlite3.Connection, *, section: str | None = None) -> list[dict[str, Any]]:
    """Return audit-related tasks. Section 'failed' filters to failed audit tasks."""
    snapshots = fetch_task_snapshots(conn)
    rows = []
    for snapshot in snapshots:
        task_type = snapshot.get("task_type") or "task"
        planner_status = snapshot["planner_status"]
        runtime = snapshot["runtime"]
        runtime_status = runtime["runtime_status"] if runtime else ""

        is_audit_type = task_type == "audit"
        is_awaiting_audit = planner_status == "awaiting_audit"

        if not is_audit_type and not is_awaiting_audit:
            continue

        row = {
            "task_id": snapshot["task_id"],
            "task_type": task_type,
            "title": snapshot["title"],
            "priority": snapshot["priority"],
            "repo": snapshot["target_repo_id"],
            "planner_status": planner_status,
            "runtime_status": runtime_status,
            "planner_owner": snapshot["planner_owner"],
            "worker_owner": snapshot["worker_owner"] or "",
            "updated_at": snapshot["updated_at"],
        }

        if section == "failed":
            if planner_status == "failed" or runtime_status == "failed":
                rows.append(row)
        else:
            rows.append(row)

    rows.sort(key=lambda r: r["priority"])
    return rows


def command_runtime_clear_stale_failed(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        payload = runtime_clear_stale_failed(conn, actor_id=args.actor_id)
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


def command_view_planner_panel(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        panel = build_planner_panel(conn)
    finally:
        conn.close()
    return print_or_json(panel, as_json=args.json, formatter=json_dumps)


def command_view_audits(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        section = getattr(args, "section", None) or None
        rows = build_audits_view(conn, section=section)
    finally:
        conn.close()
    return print_or_json(
        rows,
        as_json=args.json,
        formatter=lambda data: "\n".join(
            [
                generated_banner(now_iso()),
                "",
                render_table(
                    data,
                    [
                        ("task_id", "task_id"),
                        ("type", "task_type"),
                        ("repo", "repo"),
                        ("planner", "planner_status"),
                        ("runtime", "runtime_status"),
                        ("updated_at", "updated_at"),
                        ("title", "title"),
                    ],
                ),
            ]
        ),
    )


def command_runtime_requeue_task(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshot = runtime_requeue_task(
            conn,
            task_id=args.task_id,
            actor_id=args.actor_id,
            reason=args.reason,
            reset_retry_count=args.reset_retry_count,
        )
    finally:
        conn.close()
    return print_or_json(render_task_card(snapshot), as_json=args.json, formatter=json_dumps)


def command_operator_fail_task(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshot = operator_fail_task(
            conn,
            task_id=args.task_id,
            actor_id=args.actor_id,
            reason=args.reason,
        )
    finally:
        conn.close()
    return print_or_json(render_task_card(snapshot), as_json=args.json, formatter=json_dumps)


def add_db_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db-path", help="SQLite DB path. Defaults to CENTRAL_TASK_DB_PATH or CENTRAL/state/central_tasks.db")


def add_durability_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--durability-dir",
        help="Snapshot publish directory. Defaults to CENTRAL/durability/central_db",
    )


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

    snapshot_create_parser = subparsers.add_parser(
        "snapshot-create",
        help="Publish an immutable CENTRAL DB snapshot to the durability directory and update the latest pointer.",
    )
    add_db_argument(snapshot_create_parser)
    add_durability_argument(snapshot_create_parser)
    snapshot_create_parser.add_argument("--snapshot-id", help="Optional explicit snapshot ID.")
    snapshot_create_parser.add_argument("--note", help="Optional operator note stored in the snapshot manifest.")
    snapshot_create_parser.add_argument("--actor-id", default="planner/coordinator")
    add_json_argument(snapshot_create_parser)
    snapshot_create_parser.set_defaults(func=command_snapshot_create)

    snapshot_list_parser = subparsers.add_parser(
        "snapshot-list",
        help="List published CENTRAL DB snapshots from the durability directory.",
    )
    add_durability_argument(snapshot_list_parser)
    snapshot_list_parser.add_argument("--limit", type=int)
    add_json_argument(snapshot_list_parser)
    snapshot_list_parser.set_defaults(func=command_snapshot_list)

    snapshot_restore_parser = subparsers.add_parser(
        "snapshot-restore",
        help="Restore a published CENTRAL DB snapshot into a target DB path.",
    )
    add_db_argument(snapshot_restore_parser)
    add_durability_argument(snapshot_restore_parser)
    snapshot_restore_parser.add_argument("--snapshot-id", help="Snapshot ID to restore. Defaults to the latest published snapshot.")
    snapshot_restore_parser.add_argument("--backup-dir", help="Where to write a backup of the existing target DB before overwrite.")
    snapshot_restore_parser.add_argument("--no-backup-existing", action="store_true")
    add_json_argument(snapshot_restore_parser)
    snapshot_restore_parser.set_defaults(func=command_snapshot_restore)

    for command_name, help_text in [
        ("repo-onboard", "Register or refresh a canonical repo before planner task creation or dispatch."),
        ("repo-upsert", "Create or update a repo row in the CENTRAL DB."),
    ]:
        repo_upsert_parser = subparsers.add_parser(command_name, help=help_text)
        add_db_argument(repo_upsert_parser)
        repo_upsert_parser.add_argument("--repo-id", required=True)
        repo_upsert_parser.add_argument("--repo-root", required=True)
        repo_upsert_parser.add_argument("--display-name")
        repo_upsert_parser.add_argument("--alias", action="append", default=None, help="Repeatable repo alias list. Replaces existing aliases when provided.")
        repo_upsert_parser.add_argument("--metadata-json", help="JSON metadata object.")
        add_json_argument(repo_upsert_parser)
        repo_upsert_parser.set_defaults(func=command_repo_upsert)

    repo_list_parser = subparsers.add_parser("repo-list", help="List canonical repos plus configured aliases.")
    add_db_argument(repo_list_parser)
    add_json_argument(repo_list_parser)
    repo_list_parser.set_defaults(func=command_repo_list)

    repo_resolve_parser = subparsers.add_parser("repo-resolve", help="Resolve a repo alias or variant to the canonical repo record.")
    add_db_argument(repo_resolve_parser)
    repo_resolve_parser.add_argument("--repo", required=True)
    add_json_argument(repo_resolve_parser)
    repo_resolve_parser.set_defaults(func=command_repo_resolve)

    repo_show_parser = subparsers.add_parser("repo-show", help="Show canonical repository details from a repo reference.")
    add_db_argument(repo_show_parser)
    repo_show_parser.add_argument("--repo", required=True, help="Canonical repo_id or registered alias/root/display-name.")
    add_json_argument(repo_show_parser)
    repo_show_parser.set_defaults(func=command_repo_show)

    task_create_parser = subparsers.add_parser("task-create", help="Create a planner-owned task from JSON input. Target repos must already be onboarded.")
    add_db_argument(task_create_parser)
    task_create_parser.add_argument("--input", required=True, help="Path to JSON payload, or - for stdin.")
    task_create_parser.add_argument("--actor-id", default="planner/coordinator")
    add_json_argument(task_create_parser)
    task_create_parser.set_defaults(func=command_task_create)

    task_batch_create_parser = subparsers.add_parser(
        "task-batch-create",
        help="Create multiple planner-owned tasks from a YAML or JSON batch file.",
    )
    add_db_argument(task_batch_create_parser)
    task_batch_create_parser.add_argument(
        "--input", required=True,
        help="Path to a YAML or JSON batch file (list of tasks, or object with 'tasks' key), or - for stdin.",
    )
    task_batch_create_parser.add_argument(
        "--series", default=None,
        help=f"Task ID series for auto-allocation. Overrides batch-level series. Default: {DEFAULT_TASK_ID_SERIES}",
    )
    task_batch_create_parser.add_argument(
        "--repo", default=None,
        help="Default repo for all tasks. Overrides batch-level repo. Default: CENTRAL.",
    )
    task_batch_create_parser.add_argument("--actor-id", default="planner/coordinator")
    task_batch_create_parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate and preview IDs without writing to the DB.",
    )
    task_batch_create_parser.set_defaults(func=command_task_batch_create)

    planner_new_parser = subparsers.add_parser(
        "planner-new",
        help="Generate a planner-ready task payload scaffold with auto task-ID allocation.",
    )
    add_db_argument(planner_new_parser)
    planner_new_parser.add_argument("--title", required=True, help="Task title used for summary and output title.")
    planner_new_parser.add_argument("--series", default=DEFAULT_TASK_ID_SERIES, help="Task ID series for auto-allocation.")
    planner_new_parser.add_argument("--repo", default="CENTRAL", help="Canonical repo_id or alias used for target_repo_id/root.")
    planner_new_parser.add_argument("--objective", help="Objective markdown.")
    planner_new_parser.add_argument("--context", help="Context markdown.")
    planner_new_parser.add_argument("--scope", help="Scope markdown.")
    planner_new_parser.add_argument("--deliverables", help="Deliverables markdown.")
    planner_new_parser.add_argument("--acceptance", help="Acceptance criteria markdown.")
    planner_new_parser.add_argument("--testing", help="Testing markdown.")
    planner_new_parser.add_argument("--dispatch", help="Dispatch contract markdown.")
    planner_new_parser.add_argument("--closeout", help="Closeout markdown.")
    planner_new_parser.add_argument("--reconciliation", help="Reconciliation markdown.")
    planner_new_parser.add_argument("--planner-status", choices=sorted(PLANNER_STATUSES), default="todo")
    planner_new_parser.add_argument("--priority", type=int, default=100)
    planner_new_parser.add_argument("--task-type", default="mutating")
    planner_new_parser.add_argument("--planner-owner", default="planner/coordinator")
    planner_new_parser.add_argument("--approval-required", action="store_true", help="Mark task as approval-required.")
    planner_new_parser.add_argument("--task-kind", default="mutating")
    planner_new_parser.add_argument("--sandbox-mode", default="workspace-write")
    planner_new_parser.add_argument("--approval-policy", default="never")
    planner_new_parser.add_argument("--timeout-seconds", type=int, default=1800)
    planner_new_parser.add_argument(
        "--additional-writable-dir",
        action="append",
        default=[],
        dest="additional_writable_dir",
        help="Repeatable path to add to execution.additional_writable_dirs.",
    )
    planner_new_parser.add_argument(
        "--depends-on",
        action="append",
        default=[],
        help=(
            "Declare an upstream task ID this task must wait for (repeatable). "
            "IMPORTANT: declare all known blockers at creation time — use dep-lint after creation to catch missing edges."
        ),
    )
    planner_new_parser.add_argument("--initiative", default=None, help="Optional initiative/epic tag for grouping (e.g. 'dispatcher-infrastructure').")
    planner_new_parser.add_argument("--actor-id", default="planner/coordinator")
    add_json_argument(planner_new_parser)
    planner_new_parser.set_defaults(func=command_planner_new)

    task_update_parser = subparsers.add_parser("task-update", help="Update a planner-owned task from JSON input. Repo target changes must resolve to registered repos.")
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
    task_list_parser.add_argument("--initiative", default=None, help="Filter by initiative/epic tag.")
    add_json_argument(task_list_parser)
    task_list_parser.set_defaults(func=command_task_list)

    task_id_next_parser = subparsers.add_parser(
        "task-id-next",
        help="Show the next planner task ID for a series using the current task/reservation high-water mark.",
    )
    add_db_argument(task_id_next_parser)
    task_id_next_parser.add_argument("--series", default=DEFAULT_TASK_ID_SERIES)
    task_id_next_parser.add_argument("--actor-id", default="planner/coordinator")
    add_json_argument(task_id_next_parser)
    task_id_next_parser.set_defaults(func=command_task_id_next)

    task_id_reserve_parser = subparsers.add_parser(
        "task-id-reserve",
        help="Reserve a short contiguous planner task-ID range for a series.",
    )
    add_db_argument(task_id_reserve_parser)
    task_id_reserve_parser.add_argument("--series", default=DEFAULT_TASK_ID_SERIES)
    task_id_reserve_parser.add_argument("--count", required=True, type=int)
    task_id_reserve_parser.add_argument("--hours", type=int, default=DEFAULT_TASK_ID_RESERVATION_HOURS)
    task_id_reserve_parser.add_argument("--reserved-for", help="Short description of the planned task family.")
    task_id_reserve_parser.add_argument("--note", help="Optional planner note recorded with the reservation.")
    task_id_reserve_parser.add_argument("--actor-id", default="planner/coordinator")
    add_json_argument(task_id_reserve_parser)
    task_id_reserve_parser.set_defaults(func=command_task_id_reserve)

    task_id_reservations_parser = subparsers.add_parser(
        "task-id-reservations",
        help="List task-ID reservations and their current fulfillment state.",
    )
    add_db_argument(task_id_reservations_parser)
    task_id_reservations_parser.add_argument("--series")
    task_id_reservations_parser.add_argument("--status", choices=sorted(TASK_ID_RESERVATION_STATUSES))
    task_id_reservations_parser.add_argument("--reservation-id")
    task_id_reservations_parser.add_argument("--all", action="store_true")
    task_id_reservations_parser.add_argument("--include-events", action="store_true")
    task_id_reservations_parser.add_argument("--limit", type=int, default=25)
    task_id_reservations_parser.add_argument("--actor-id", default="planner/coordinator")
    add_json_argument(task_id_reservations_parser)
    task_id_reservations_parser.set_defaults(func=command_task_id_reservations)

    view_summary_parser = subparsers.add_parser("view-summary", help="Show portfolio summary generated from DB state.")
    add_db_argument(view_summary_parser)
    view_summary_parser.add_argument("--initiative", default=None, help="Filter summary to a single initiative/epic tag.")
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

    view_active_parser = subparsers.add_parser("view-active", help="Show only non-terminal tasks (running, queued, claimed, blocked, pending_review) across all repos.")
    add_db_argument(view_active_parser)
    add_json_argument(view_active_parser)
    view_active_parser.set_defaults(func=command_view_active)

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

    dep_show_parser = subparsers.add_parser("dep-show", help="Show forward and reverse dependency edges for a task.")
    add_db_argument(dep_show_parser)
    dep_show_parser.add_argument("--task-id", required=True, help="Task ID to inspect.")
    add_json_argument(dep_show_parser)
    dep_show_parser.set_defaults(func=command_dep_show)

    dep_graph_parser = subparsers.add_parser("dep-graph", help="Show the dependency graph for active tasks.")
    add_db_argument(dep_graph_parser)
    dep_graph_parser.add_argument("--include-done", action="store_true", help="Include edges where both tasks are done.")
    add_json_argument(dep_graph_parser)
    dep_graph_parser.set_defaults(func=command_dep_graph)

    dep_lint_parser = subparsers.add_parser("dep-lint", help="Flag task IDs referenced in text with no declared dependency edge.")
    add_db_argument(dep_lint_parser)
    add_json_argument(dep_lint_parser)
    dep_lint_parser.set_defaults(func=command_dep_lint)

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

    export_board_parser = subparsers.add_parser(
        "export-tasks-board-md",
        help="Write a non-canonical generated task-board landing page from CENTRAL DB state.",
    )
    add_db_argument(export_board_parser)
    export_board_parser.add_argument(
        "--output",
        help="Output path. Defaults to CENTRAL/generated/tasks.md",
    )
    add_json_argument(export_board_parser)
    export_board_parser.set_defaults(func=command_export_tasks_board_md)

    export_bundle_parser = subparsers.add_parser(
        "export-markdown-bundle",
        help="Write the standard non-canonical markdown export bundle from CENTRAL DB state.",
    )
    add_db_argument(export_bundle_parser)
    export_bundle_parser.add_argument(
        "--output-dir",
        help="Output directory. Defaults to CENTRAL/generated",
    )
    add_json_argument(export_bundle_parser)
    export_bundle_parser.set_defaults(func=command_export_markdown_bundle)

    export_repo_parser = subparsers.add_parser(
        "export-repo-md",
        help="Write a non-canonical per-repo markdown queue export from CENTRAL DB state.",
    )
    add_db_argument(export_repo_parser)
    export_repo_parser.add_argument("--repo-id", required=True)
    export_repo_parser.add_argument(
        "--output",
        help="Output path. Defaults to CENTRAL/generated/per_repo/<repo_id>.md",
    )
    add_json_argument(export_repo_parser)
    export_repo_parser.set_defaults(func=command_export_repo_md)

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
    runtime_transition_parser.add_argument("--effective-worker-model", dest="effective_worker_model", default=None, help="Effective model used by the worker (e.g. claude-sonnet-4-6).")
    runtime_transition_parser.add_argument("--worker-model-source", dest="worker_model_source", default=None, choices=["task_override", "policy_default", "dispatcher_default"], help="How the model was selected.")
    add_json_argument(runtime_transition_parser)
    runtime_transition_parser.set_defaults(func=command_runtime_transition)

    runtime_recover_parser = subparsers.add_parser("runtime-recover-stale", help="Recover expired leases into reclaimable queued state.")
    add_db_argument(runtime_recover_parser)
    runtime_recover_parser.add_argument("--limit", type=int, default=50)
    runtime_recover_parser.add_argument("--actor-id", default="dispatcher")
    add_json_argument(runtime_recover_parser)
    runtime_recover_parser.set_defaults(func=command_runtime_recover_stale)

    runtime_clear_stale_failed_parser = subparsers.add_parser(
        "runtime-clear-stale-failed",
        help="Set runtime_status=done for tasks where planner_status=done but runtime_status=failed (cosmetic mismatch cleanup).",
    )
    add_db_argument(runtime_clear_stale_failed_parser)
    runtime_clear_stale_failed_parser.add_argument("--actor-id", default="planner")
    add_json_argument(runtime_clear_stale_failed_parser)
    runtime_clear_stale_failed_parser.set_defaults(func=command_runtime_clear_stale_failed)

    migrate_parser = subparsers.add_parser("migrate-bootstrap", help="Import bootstrap markdown task records into the CENTRAL DB.")
    add_db_argument(migrate_parser)
    migrate_parser.add_argument("--tasks-dir", default=str(DEFAULT_TASKS_DIR))
    migrate_parser.add_argument("--packet-path", default=str(DEFAULT_PACKET_PATH))
    migrate_parser.add_argument("--actor-id", default="migration/bootstrap")
    migrate_parser.add_argument("--update-existing", action="store_true")
    add_json_argument(migrate_parser)
    migrate_parser.set_defaults(func=command_migrate_bootstrap)

    health_write_parser = subparsers.add_parser(
        "health-snapshot-write",
        help="Persist repo health snapshots from a bundle JSON into the CENTRAL DB.",
    )
    add_db_argument(health_write_parser)
    health_write_parser.add_argument(
        "bundle_file",
        help="Path to a repo-health bundle JSON file, or '-' to read from stdin.",
    )
    health_write_parser.add_argument(
        "--ttl-seconds",
        type=int,
        default=DEFAULT_HEALTH_TTL_SECONDS,
        help="Freshness TTL in seconds (default 3600). Snapshots older than this are shown as stale.",
    )
    add_json_argument(health_write_parser)
    health_write_parser.set_defaults(func=command_health_snapshot_write)

    health_latest_parser = subparsers.add_parser(
        "health-snapshot-latest",
        help="Show the latest health snapshot per repo instantly from CENTRAL DB (no live checks).",
    )
    add_db_argument(health_latest_parser)
    health_latest_parser.add_argument("--repo-id", default=None, help="Filter to a single repo.")
    add_json_argument(health_latest_parser)
    health_latest_parser.set_defaults(func=command_health_snapshot_latest)

    health_history_parser = subparsers.add_parser(
        "health-snapshot-history",
        help="Show recent health snapshot history for trend and drift inspection.",
    )
    add_db_argument(health_history_parser)
    health_history_parser.add_argument("--repo-id", default=None, help="Filter to a single repo.")
    health_history_parser.add_argument("--limit", type=int, default=20, help="Maximum rows to return (default 20).")
    add_json_argument(health_history_parser)
    health_history_parser.set_defaults(func=command_health_snapshot_history)

    view_planner_panel_parser = subparsers.add_parser("view-planner-panel", help="Show richest single planner view: eligible work, audits, recent failures, and recent changes.")
    add_db_argument(view_planner_panel_parser)
    add_json_argument(view_planner_panel_parser)
    view_planner_panel_parser.set_defaults(func=command_view_planner_panel)

    view_audits_parser = subparsers.add_parser("view-audits", help="Show audit-related tasks (audit-type tasks and tasks awaiting audit).")
    add_db_argument(view_audits_parser)
    view_audits_parser.add_argument("--section", choices=["failed"], help="Filter to a specific audit section (e.g. 'failed').")
    add_json_argument(view_audits_parser)
    view_audits_parser.set_defaults(func=command_view_audits)

    runtime_requeue_parser = subparsers.add_parser("runtime-requeue-task", help="Requeue a task by resetting runtime_status to queued and clearing the lease.")
    add_db_argument(runtime_requeue_parser)
    runtime_requeue_parser.add_argument("--task-id", required=True)
    runtime_requeue_parser.add_argument("--reason", required=True)
    runtime_requeue_parser.add_argument("--reset-retry-count", action="store_true", help="Reset retry_count to 0 before requeue.")
    runtime_requeue_parser.add_argument("--actor-id", default="operator")
    add_json_argument(runtime_requeue_parser)
    runtime_requeue_parser.set_defaults(func=command_runtime_requeue_task)

    operator_fail_parser = subparsers.add_parser("operator-fail-task", help="Forcibly set runtime_status=failed for a task, recording an operator event.")
    add_db_argument(operator_fail_parser)
    operator_fail_parser.add_argument("--task-id", required=True)
    operator_fail_parser.add_argument("--reason", required=True)
    operator_fail_parser.add_argument("--actor-id", default="operator")
    add_json_argument(operator_fail_parser)
    operator_fail_parser.set_defaults(func=command_operator_fail_task)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
