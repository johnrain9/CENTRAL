#!/usr/bin/env python3
"""Manage the canonical CENTRAL SQLite task database."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import hmac
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
from datetime import datetime, timedelta, timezone
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
TASK_FILE_NAME_RE = re.compile(r"^CENTRAL-OPS-[0-9]+\.md$")
TASK_ID_RE = re.compile(r"^(?P<series>[A-Z0-9]+(?:-[A-Z0-9]+)*)-(?P<number>[0-9]+)$")
TASK_ID_SERIES_RE = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)*$")
REPO_MAX_CONCURRENT_WORKERS_METADATA_KEY = "max_concurrent_workers"
DEFAULT_REPO_MAX_CONCURRENT_WORKERS = 3
REPO_LOOKUP_TOKEN_RE = re.compile(r"[^a-z0-9]+")
SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
KEY_VALUE_RE = re.compile(r"^- `([^`]+)`: (.+)$", re.MULTILINE)
TASK_PACKET_RE = re.compile(r"^## Task (CENTRAL-OPS-[0-9]+): (.+)$", re.MULTILINE)
SNAPSHOT_DB_FILENAME = "central_tasks.db"
SNAPSHOT_MANIFEST_FILENAME = "manifest.json"
SNAPSHOT_POINTER_FILENAME = "latest.json"
CAPABILITY_ID_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
CAPABILITY_STATUSES = {"proposed", "active", "deprecated"}
CAPABILITY_SCOPE_KINDS = {"local", "cross_repo_contract", "workflow"}
CAPABILITY_VERIFICATION_LEVELS = {"provisional", "planner_verified", "audited"}
PREFLIGHT_ALGORITHM_VERSION = "capability-preflight-v1"
PREFLIGHT_TOKEN_VERSION = "capability-preflight-token-v1"
PREFLIGHT_ISSUER = "CENTRAL"
PREFLIGHT_REQUEST_CHANNELS = {"task-create", "task-update", "planner-review", "bootstrap"}
PREFLIGHT_ERROR_CODES = {
    "preflight_missing",
    "preflight_untrusted",
    "preflight_stale",
    "preflight_classification_invalid",
    "preflight_related_references_invalid",
    "preflight_override_forbidden",
    "preflight_override_invalid",
    "preflight_duplicate_blocked",
    "preflight_strong_overlap_blocked",
}
CAPABILITY_SOURCE_RELATIONSHIP_KINDS = {
    "created_by",
    "updated_by",
    "deprecated_by",
    "superseded_by",
    "seeded_from",
}
CAPABILITY_CLOSEOUT_TASK_TYPE_CATEGORIES = {"must_emit", "may_emit", "must_not_emit"}
CAPABILITY_MUTATION_ACTIONS = {"create", "update", "deprecate", "supersede"}
TASK_CAPABILITY_DRAFT_STATUS = "proposed"


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


def parse_positive_int(value: Any, *, field: str) -> int:
    parsed = parse_int(value, field=field)
    if parsed <= 0:
        die(f"{field} must be >= 1")
    return parsed


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


def utc_rfc3339(value: str | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip()
    if normalized.endswith("Z"):
        return normalized
    return normalized.replace("+00:00", "Z")


def normalize_text_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def markdown_to_plain_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[#>*_\-\[\]\(\)]", " ", text)
    return normalize_text_whitespace(text)


def lexical_tokens(value: Any) -> list[str]:
    text = markdown_to_plain_text(value).casefold()
    return [
        token
        for token in re.findall(r"[a-z0-9]+", text)
        if not re.fullmatch(r"\d+", token)
    ]


def lexical_token_set(value: Any) -> set[str]:
    return set(lexical_tokens(value))


def jaccard_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def preflight_secret() -> bytes:
    return os.environ.get("CENTRAL_PREFLIGHT_SECRET", "central-preflight-dev-secret").encode("utf-8")


def current_utc_datetime() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = str(value).strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def sorted_unique_strings(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        die("expected a JSON array of strings")
    normalized = [normalize_text_whitespace(value) for value in values]
    cleaned = [value for value in normalized if value]
    return sorted(set(cleaned))


def parse_preflight_json_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        die(f"{field} must be a JSON object")
    return value


def canonicalize_task_intent(payload: dict[str, Any]) -> dict[str, Any]:
    intent = {
        "title": normalize_text_whitespace(payload.get("title")),
        "summary": normalize_text_whitespace(payload.get("summary")),
        "objective_md": normalize_text_whitespace(payload.get("objective_md")),
        "scope_md": normalize_text_whitespace(payload.get("scope_md")),
        "deliverables_md": normalize_text_whitespace(payload.get("deliverables_md")),
        "acceptance_md": normalize_text_whitespace(payload.get("acceptance_md")),
        "target_repo_id": normalize_text_whitespace(payload.get("target_repo_id")),
        "task_type": normalize_text_whitespace(payload.get("task_type")),
        "dependency_task_ids": sorted_unique_strings(payload.get("dependency_task_ids", payload.get("dependencies", []))),
        "dependency_kinds": payload.get("dependency_kinds") if isinstance(payload.get("dependency_kinds"), dict) else {},
        "parent_task_id": payload.get("parent_task_id"),
        "initiative_key": payload.get("initiative_key", payload.get("initiative")),
        "related_repo_ids": sorted_unique_strings(payload.get("related_repo_ids", [])),
        "requested_capability_ids": sorted_unique_strings(payload.get("requested_capability_ids", [])),
        "requested_task_ids": sorted_unique_strings(payload.get("requested_task_ids", [])),
        "labels": sorted_unique_strings(payload.get("labels", [])),
    }
    return intent


def intent_comparison_fields(intent: dict[str, Any]) -> dict[str, Any]:
    objective_text = markdown_to_plain_text(intent.get("objective_md"))
    scope_text = markdown_to_plain_text(intent.get("scope_md"))
    deliverables_text = markdown_to_plain_text(intent.get("deliverables_md"))
    acceptance_text = markdown_to_plain_text(intent.get("acceptance_md"))
    summary_text = markdown_to_plain_text(intent.get("summary"))
    title_text = markdown_to_plain_text(intent.get("title"))
    intent_text = normalize_text_whitespace(
        " ".join([title_text, summary_text, objective_text, scope_text, deliverables_text, acceptance_text])
    )
    repo_scope = sorted(
        {
            normalize_text_whitespace(intent.get("target_repo_id")),
            *sorted_unique_strings(intent.get("related_repo_ids", [])),
        }
        - {""}
    )
    fingerprint_payload = {
        "title": title_text.casefold(),
        "summary": summary_text.casefold(),
        "objective": objective_text.casefold(),
        "scope": scope_text.casefold(),
        "deliverables": deliverables_text.casefold(),
        "acceptance": acceptance_text.casefold(),
        "target_repo_id": normalize_text_whitespace(intent.get("target_repo_id")),
        "task_type": normalize_text_whitespace(intent.get("task_type")),
        "dependency_task_ids": sorted_unique_strings(intent.get("dependency_task_ids", [])),
    }
    return {
        "title_text": title_text,
        "summary_text": summary_text,
        "objective_text": objective_text,
        "scope_text": scope_text,
        "deliverables_text": deliverables_text,
        "acceptance_text": acceptance_text,
        "intent_text": intent_text,
        "intent_terms": sorted(lexical_token_set(intent_text)),
        "intent_fingerprint": f"sha256:{stable_sha256(fingerprint_payload)}",
        "repo_scope": repo_scope,
        "deliverable_terms": sorted(
            lexical_token_set(" ".join([deliverables_text, acceptance_text]))
        ),
    }


def canonicalize_preflight_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = parse_preflight_json_object(payload, field="preflight request")
    intent = canonicalize_task_intent(parse_preflight_json_object(request.get("normalized_task_intent"), field="normalized_task_intent"))
    search_scope = dict(parse_preflight_json_object(request.get("search_scope") or {}, field="search_scope"))
    repo_ids = sorted_unique_strings(search_scope.get("repo_ids") or [intent["target_repo_id"], *intent.get("related_repo_ids", [])])
    if not repo_ids:
        repo_ids = [intent["target_repo_id"]]
    include_recent_done_days = int(search_scope.get("include_recent_done_days", 90))
    if include_recent_done_days < 30 or include_recent_done_days > 180:
        die("search_scope.include_recent_done_days must be in 30..180")
    max_candidates_per_kind = int(search_scope.get("max_candidates_per_kind", 50))
    if max_candidates_per_kind > 200:
        die("search_scope.max_candidates_per_kind may not exceed 200")
    request_context = parse_preflight_json_object(request.get("request_context") or {}, field="request_context")
    requested_by = normalize_text_whitespace(request_context.get("requested_by"))
    if not requested_by:
        die("request_context.requested_by is required")
    request_channel = normalize_text_whitespace(request_context.get("request_channel"))
    if request_channel not in PREFLIGHT_REQUEST_CHANNELS:
        die(f"invalid request_context.request_channel: {request_channel!r}")
    canonical = {
        "normalized_task_intent": intent,
        "search_scope": {
            "repo_ids": repo_ids,
            "include_active_tasks": bool(search_scope.get("include_active_tasks", True)),
            "include_recent_done_days": include_recent_done_days,
            "include_capabilities": bool(search_scope.get("include_capabilities", True)),
            "include_deprecated_capabilities": bool(search_scope.get("include_deprecated_capabilities", True)),
            "max_candidates_per_kind": max_candidates_per_kind,
        },
        "request_context": {
            "requested_by": requested_by,
            "request_channel": request_channel,
            "is_material_update": bool(request_context.get("is_material_update", False)),
            "existing_task_id": request_context.get("existing_task_id"),
            "existing_task_version": request_context.get("existing_task_version"),
        },
    }
    if canonical["request_context"]["is_material_update"]:
        if not canonical["request_context"]["existing_task_id"] or canonical["request_context"]["existing_task_version"] is None:
            die("existing_task_id and existing_task_version are required for material-update preflight")
    return canonical


def preflight_error_payload(
    *,
    error_code: str,
    message: str,
    response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if error_code not in PREFLIGHT_ERROR_CODES:
        die(f"unknown preflight error code: {error_code}")
    response = response or {}
    return {
        "error_code": error_code,
        "message": message,
        "preflight_bucket": response.get("blocking_bucket", "none"),
        "matched_task_ids": list(response.get("matched_task_ids", [])),
        "matched_capability_ids": list(response.get("matched_capability_ids", [])),
        "rerun_required": error_code == "preflight_stale",
    }


def die_preflight(
    *,
    error_code: str,
    message: str,
    response: dict[str, Any] | None = None,
) -> "None":
    die(json_dumps(preflight_error_payload(error_code=error_code, message=message, response=response)))


def canonical_preflight_response_body(response: dict[str, Any]) -> dict[str, Any]:
    body = dict(response)
    body.pop("preflight_token", None)
    return body


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


def resolve_repo_max_concurrent_workers(repo_metadata: dict[str, Any]) -> int:
    raw_value = repo_metadata.get(REPO_MAX_CONCURRENT_WORKERS_METADATA_KEY)
    if raw_value is None:
        return DEFAULT_REPO_MAX_CONCURRENT_WORKERS
    return parse_positive_int(raw_value, field=f"repo metadata `{REPO_MAX_CONCURRENT_WORKERS_METADATA_KEY}`")


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


def normalize_capability_id(value: Any) -> str:
    capability_id = str(value or "").strip()
    if not capability_id:
        die("capability_id is required")
    if not CAPABILITY_ID_RE.match(capability_id):
        die(
            "invalid capability_id: "
            f"{capability_id!r} (expected lowercase slug with underscores)"
        )
    return capability_id


def normalize_string_list(value: Any, *, field: str, allow_empty: bool = True) -> list[str]:
    if value is None:
        items: list[Any] = []
    elif isinstance(value, list):
        items = value
    else:
        die(f"{field} must be a JSON array")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text:
            die(f"{field} cannot contain blank values")
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    if not allow_empty and not normalized:
        die(f"{field} must contain at least one value")
    return normalized


def normalize_capability_source_tasks(value: Any, *, fallback_task_id: str | None) -> list[dict[str, str]]:
    if value is None:
        if fallback_task_id is None:
            return []
        return [{"task_id": fallback_task_id, "relationship_kind": "created_by"}]
    if not isinstance(value, list):
        die("source_tasks must be a JSON array")
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in value:
        if not isinstance(entry, dict):
            die("source_tasks entries must be objects")
        task_id = str(entry.get("task_id") or "").strip()
        if not task_id:
            die("source_tasks entries require task_id")
        relationship_kind = str(entry.get("relationship_kind") or "").strip()
        if relationship_kind not in CAPABILITY_SOURCE_RELATIONSHIP_KINDS:
            die(
                "invalid source_tasks relationship_kind: "
                f"{relationship_kind!r}; expected one of {sorted(CAPABILITY_SOURCE_RELATIONSHIP_KINDS)}"
            )
        key = (task_id, relationship_kind)
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"task_id": task_id, "relationship_kind": relationship_kind})
    return normalized


def validate_capability_lifecycle(
    *,
    capability_id: str,
    status: str,
    verification_level: str,
    scope_kind: str,
    owning_repo_id: str,
    affected_repo_ids: list[str],
    verified_by_task_id: str | None,
    replaced_by_capability_id: str | None,
    metadata: dict[str, Any],
) -> None:
    if status not in CAPABILITY_STATUSES:
        die(f"invalid capability status: {status!r}")
    if verification_level not in CAPABILITY_VERIFICATION_LEVELS:
        die(f"invalid capability verification_level: {verification_level!r}")
    if scope_kind not in CAPABILITY_SCOPE_KINDS:
        die(f"invalid capability scope_kind: {scope_kind!r}")
    if not verified_by_task_id:
        die("verified_by_task_id is required for canonical capability provenance")
    if replaced_by_capability_id is not None and replaced_by_capability_id == capability_id:
        die("replaced_by_capability_id must refer to a different capability")

    if status == "proposed" and verification_level == "audited":
        die("status='proposed' cannot use verification_level='audited'")
    if status == "active" and verification_level == "provisional" and not parse_bool(
        metadata.get("bootstrap_mode", False),
        field="metadata.bootstrap_mode",
    ):
        die("active provisional capabilities require metadata.bootstrap_mode=true")
    if status == "deprecated" and verification_level == "provisional":
        die("status='deprecated' cannot use verification_level='provisional'")

    if scope_kind == "local":
        if affected_repo_ids != [owning_repo_id]:
            die("scope_kind='local' requires affected_repo_ids to contain exactly owning_repo_id")
        return
    if scope_kind == "cross_repo_contract":
        if len(affected_repo_ids) < 2:
            die("scope_kind='cross_repo_contract' requires at least two affected repos")
        if owning_repo_id not in affected_repo_ids:
            die("scope_kind='cross_repo_contract' requires owning_repo_id in affected_repo_ids")
        return
    if owning_repo_id not in affected_repo_ids:
        die("scope_kind='workflow' requires owning_repo_id in affected_repo_ids")


def canonicalize_capability_payload(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
) -> dict[str, Any]:
    capability_id = normalize_capability_id(payload.get("capability_id"))
    name = str(payload.get("name") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    when_to_use_md = str(payload.get("when_to_use_md") or "").strip()
    if not name:
        die("name is required")
    if not summary:
        die("summary is required")
    if not kind:
        die("kind is required")
    if not when_to_use_md:
        die("when_to_use_md is required")

    owning_repo = resolve_repo_reference(conn, str(payload.get("owning_repo_id") or ""), field="owning_repo_id")
    if owning_repo is None:
        die(f"unknown owning_repo_id for capability {capability_id}")
    affected_repo_ids = sorted(
        {
            str(resolve_repo_reference(conn, repo_id, field="affected_repo_id")["repo_id"])
            for repo_id in normalize_string_list(payload.get("affected_repo_ids"), field="affected_repo_ids", allow_empty=False)
        }
    )
    metadata = payload.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        die("metadata must be a JSON object")

    verified_by_task_id = str(payload.get("verified_by_task_id") or "").strip() or None
    replaced_by_raw = str(payload.get("replaced_by_capability_id") or "").strip() or None
    replaced_by_capability_id = normalize_capability_id(replaced_by_raw) if replaced_by_raw is not None else None

    source_tasks = normalize_capability_source_tasks(
        payload.get("source_tasks"),
        fallback_task_id=verified_by_task_id,
    )
    validate_capability_lifecycle(
        capability_id=capability_id,
        status=str(payload.get("status") or "").strip(),
        verification_level=str(payload.get("verification_level") or "").strip(),
        scope_kind=str(payload.get("scope_kind") or "").strip(),
        owning_repo_id=str(owning_repo["repo_id"]),
        affected_repo_ids=affected_repo_ids,
        verified_by_task_id=verified_by_task_id,
        replaced_by_capability_id=replaced_by_capability_id,
        metadata=metadata,
    )

    timestamp = now_iso()
    return {
        "capability_id": capability_id,
        "name": name,
        "summary": summary,
        "status": str(payload["status"]).strip(),
        "kind": kind,
        "scope_kind": str(payload["scope_kind"]).strip(),
        "owning_repo_id": str(owning_repo["repo_id"]),
        "when_to_use_md": when_to_use_md,
        "do_not_use_for_md": str(payload.get("do_not_use_for_md") or "").strip(),
        "entrypoints": normalize_string_list(payload.get("entrypoints"), field="entrypoints"),
        "keywords": normalize_string_list(payload.get("keywords"), field="keywords"),
        "affected_repo_ids": affected_repo_ids,
        "evidence_summary_md": str(payload.get("evidence_summary_md") or "").strip(),
        "verification_level": str(payload["verification_level"]).strip(),
        "verified_by_task_id": verified_by_task_id,
        "replaced_by_capability_id": replaced_by_capability_id,
        "created_at": str(payload.get("created_at") or timestamp),
        "updated_at": str(payload.get("updated_at") or timestamp),
        "archived_at": str(payload.get("archived_at") or "").strip() or None,
        "metadata": metadata,
        "source_tasks": source_tasks,
        "event_type": str(payload.get("event_type") or "capability.created").strip(),
        "event_payload": payload.get("event_payload") if isinstance(payload.get("event_payload"), dict) else {},
    }


def insert_capability_event(
    conn: sqlite3.Connection,
    *,
    capability_id: str,
    event_type: str,
    actor_kind: str,
    actor_id: str,
    payload: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO capability_events (capability_id, event_type, actor_kind, actor_id, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            capability_id,
            event_type,
            actor_kind,
            actor_id,
            compact_json(payload or {}),
            created_at or now_iso(),
        ),
    )


def create_capability(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    actor_kind: str,
    actor_id: str,
) -> dict[str, Any]:
    normalized = canonicalize_capability_payload(conn, payload)
    existing = conn.execute(
        "SELECT capability_id FROM capabilities WHERE capability_id = ?",
        (normalized["capability_id"],),
    ).fetchone()
    if existing is not None:
        die(f"capability already exists: {normalized['capability_id']}")

    conn.execute(
        """
        INSERT INTO capabilities (
            capability_id,
            name,
            summary,
            status,
            kind,
            scope_kind,
            owning_repo_id,
            when_to_use_md,
            do_not_use_for_md,
            entrypoints_json,
            keywords_json,
            evidence_summary_md,
            verification_level,
            verified_by_task_id,
            replaced_by_capability_id,
            created_at,
            updated_at,
            archived_at,
            metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            normalized["capability_id"],
            normalized["name"],
            normalized["summary"],
            normalized["status"],
            normalized["kind"],
            normalized["scope_kind"],
            normalized["owning_repo_id"],
            normalized["when_to_use_md"],
            normalized["do_not_use_for_md"],
            compact_json(normalized["entrypoints"]),
            compact_json(normalized["keywords"]),
            normalized["evidence_summary_md"],
            normalized["verification_level"],
            normalized["verified_by_task_id"],
            normalized["replaced_by_capability_id"],
            normalized["created_at"],
            normalized["updated_at"],
            normalized["archived_at"],
            compact_json(normalized["metadata"]),
        ),
    )
    conn.executemany(
        """
        INSERT INTO capability_affected_repos (capability_id, repo_id, created_at)
        VALUES (?, ?, ?)
        """,
        [
            (normalized["capability_id"], repo_id, normalized["created_at"])
            for repo_id in normalized["affected_repo_ids"]
        ],
    )
    conn.executemany(
        """
        INSERT INTO capability_source_tasks (capability_id, task_id, relationship_kind, created_at)
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                normalized["capability_id"],
                source["task_id"],
                source["relationship_kind"],
                normalized["created_at"],
            )
            for source in normalized["source_tasks"]
        ],
    )
    insert_capability_event(
        conn,
        capability_id=normalized["capability_id"],
        event_type=normalized["event_type"],
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload=normalized["event_payload"] or {
            "status": normalized["status"],
            "verification_level": normalized["verification_level"],
            "source_task_ids": [source["task_id"] for source in normalized["source_tasks"]],
        },
        created_at=normalized["created_at"],
    )
    return fetch_capability_payload(conn, normalized["capability_id"]) or {}


def upsert_capability(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    actor_kind: str,
    actor_id: str,
) -> dict[str, Any]:
    normalized = canonicalize_capability_payload(conn, payload)
    existing = conn.execute(
        "SELECT capability_id FROM capabilities WHERE capability_id = ?",
        (normalized["capability_id"],),
    ).fetchone()
    if existing is None:
        return create_capability(conn, payload, actor_kind=actor_kind, actor_id=actor_id)

    conn.execute(
        """
        UPDATE capabilities
        SET name = ?,
            summary = ?,
            status = ?,
            kind = ?,
            scope_kind = ?,
            owning_repo_id = ?,
            when_to_use_md = ?,
            do_not_use_for_md = ?,
            entrypoints_json = ?,
            keywords_json = ?,
            evidence_summary_md = ?,
            verification_level = ?,
            verified_by_task_id = ?,
            replaced_by_capability_id = ?,
            updated_at = ?,
            archived_at = ?,
            metadata_json = ?
        WHERE capability_id = ?
        """,
        (
            normalized["name"],
            normalized["summary"],
            normalized["status"],
            normalized["kind"],
            normalized["scope_kind"],
            normalized["owning_repo_id"],
            normalized["when_to_use_md"],
            normalized["do_not_use_for_md"],
            compact_json(normalized["entrypoints"]),
            compact_json(normalized["keywords"]),
            normalized["evidence_summary_md"],
            normalized["verification_level"],
            normalized["verified_by_task_id"],
            normalized["replaced_by_capability_id"],
            normalized["updated_at"],
            normalized["archived_at"],
            compact_json(normalized["metadata"]),
            normalized["capability_id"],
        ),
    )
    conn.execute(
        "DELETE FROM capability_affected_repos WHERE capability_id = ?",
        (normalized["capability_id"],),
    )
    conn.executemany(
        """
        INSERT INTO capability_affected_repos (capability_id, repo_id, created_at)
        VALUES (?, ?, ?)
        """,
        [
            (normalized["capability_id"], repo_id, normalized["updated_at"])
            for repo_id in normalized["affected_repo_ids"]
        ],
    )
    conn.executemany(
        """
        INSERT OR IGNORE INTO capability_source_tasks (capability_id, task_id, relationship_kind, created_at)
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                normalized["capability_id"],
                source["task_id"],
                source["relationship_kind"],
                normalized["updated_at"],
            )
            for source in normalized["source_tasks"]
        ],
    )
    insert_capability_event(
        conn,
        capability_id=normalized["capability_id"],
        event_type=normalized["event_type"],
        actor_kind=actor_kind,
        actor_id=actor_id,
        payload=normalized["event_payload"] or {
            "status": normalized["status"],
            "verification_level": normalized["verification_level"],
            "source_task_ids": [source["task_id"] for source in normalized["source_tasks"]],
        },
        created_at=normalized["updated_at"],
    )
    return fetch_capability_payload(conn, normalized["capability_id"]) or {}


def derive_task_capability_id(task_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", task_id.strip().lower()).strip("_")
    return normalize_capability_id(f"task_{slug}")


def task_scaffold_keywords(payload: dict[str, Any], *, limit: int = 12) -> list[str]:
    text = " ".join(
        [
            str(payload.get("title") or ""),
            str(payload.get("summary") or ""),
            str(payload.get("objective_md") or ""),
            str(payload.get("scope_md") or ""),
        ]
    )
    keywords: list[str] = []
    seen: set[str] = set()
    for token in lexical_tokens(text):
        if token in seen:
            continue
        seen.add(token)
        keywords.append(token)
        if len(keywords) >= limit:
            break
    return keywords


def task_scaffold_entrypoints(payload: dict[str, Any], *, limit: int = 12) -> list[str]:
    source_text = "\n".join(
        [
            str(payload.get("scope_md") or ""),
            str(payload.get("objective_md") or ""),
            str(payload.get("deliverables_md") or ""),
            str(payload.get("dispatch_md") or ""),
        ]
    )
    matches = re.findall(r"`([^`]+)`", source_text)
    entrypoints: list[str] = []
    seen: set[str] = set()
    for raw in matches:
        cleaned = normalize_text_whitespace(raw)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        entrypoints.append(cleaned)
        if len(entrypoints) >= limit:
            break
    return entrypoints


def register_task_draft_capability(
    conn: sqlite3.Connection,
    *,
    task_payload: dict[str, Any],
    actor_kind: str,
    actor_id: str,
) -> dict[str, Any]:
    task_id = str(task_payload.get("task_id") or "")
    if not task_id:
        return {}
    target_repo_id = str(task_payload.get("target_repo_id") or "").strip()
    if not target_repo_id:
        return {}
    capability_id = derive_task_capability_id(task_id)
    if fetch_capability_payload(conn, capability_id) is not None:
        return {}
    title = str(task_payload.get("title") or task_id).strip() or task_id
    summary = str(task_payload.get("summary") or "").strip()
    if not summary:
        summary = markdown_summary(str(task_payload.get("objective_md") or ""), fallback=title)
    scope_md = str(task_payload.get("scope_md") or "").strip()
    create_capability(
        conn,
        {
            "capability_id": capability_id,
            "name": f"{title} (draft capability)",
            "summary": summary,
            "status": TASK_CAPABILITY_DRAFT_STATUS,
            "kind": str(task_payload.get("task_type") or "workflow"),
            "scope_kind": "workflow",
            "owning_repo_id": target_repo_id,
            "affected_repo_ids": [target_repo_id],
            "when_to_use_md": scope_md or f"Use when implementing the scope tracked by {task_id}.",
            "do_not_use_for_md": "Do not treat as active until task reconciliation outcome is done.",
            "entrypoints": task_scaffold_entrypoints(task_payload),
            "keywords": task_scaffold_keywords(task_payload),
            "evidence_summary_md": f"Draft capability scaffolded from task creation intent for {task_id}.",
            "verification_level": "provisional",
            "verified_by_task_id": task_id,
            "metadata": {
                "task_capability_lifecycle": {
                    "task_id": task_id,
                    "stage": "draft",
                    "created_from": "task-create",
                }
            },
            "source_tasks": [{"task_id": task_id, "relationship_kind": "created_by"}],
            "event_type": "capability.task_draft_registered",
            "event_payload": {"task_id": task_id, "stage": "draft"},
        },
        actor_kind=actor_kind,
        actor_id=actor_id,
    )
    return {"capability_id": capability_id}


def activate_task_capability(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    actor_kind: str,
    actor_id: str,
    closeout_artifacts: list[str] | None = None,
    source: str,
) -> dict[str, Any]:
    capability_id = derive_task_capability_id(task_id)
    existing = fetch_capability_payload(conn, capability_id)
    if existing is None:
        return {}
    artifacts = fetch_artifacts(conn, task_id)
    runtime_artifacts = [
        str(item["path_or_uri"])
        for item in artifacts
        if str(item.get("artifact_kind") or "") == "runtime_artifact" and str(item.get("path_or_uri") or "").strip()
    ]
    all_artifacts = sorted(
        {
            *(closeout_artifacts or []),
            *runtime_artifacts,
        }
    )
    metadata = dict(existing.get("metadata") or {})
    lifecycle = dict(metadata.get("task_capability_lifecycle") or {})
    lifecycle.update(
        {
            "task_id": task_id,
            "stage": "active",
            "activated_from": source,
            "activated_at": now_iso(),
        }
    )
    metadata["task_capability_lifecycle"] = lifecycle
    entrypoints = list(existing.get("entrypoints") or [])
    entrypoints.extend(all_artifacts)
    updated = dict(existing)
    updated["status"] = "active"
    updated["verification_level"] = "planner_verified"
    updated["verified_by_task_id"] = task_id
    updated["entrypoints"] = sorted(set(entrypoints))
    updated["updated_at"] = now_iso()
    updated["metadata"] = metadata
    updated["source_tasks"] = [{"task_id": task_id, "relationship_kind": "updated_by"}]
    updated["event_type"] = "capability.task_draft_activated"
    updated["event_payload"] = {
        "task_id": task_id,
        "source": source,
        "closeout_artifact_count": len(closeout_artifacts or []),
        "runtime_artifact_count": len(runtime_artifacts),
    }
    if all_artifacts:
        updated["evidence_summary_md"] = (
            f"Activated from task closeout for {task_id} with artifacts: "
            + ", ".join(all_artifacts[:5])
        )
    return upsert_capability(conn, updated, actor_kind=actor_kind, actor_id=actor_id)


def _require_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        die(f"{field} must be a JSON object")
    return value


def _normalize_capability_mutations_list(value: Any, *, field: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        die(f"{field} must be a JSON array")
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            die(f"{field}[{index}] must be an object")
        normalized.append(dict(entry))
    return normalized


def parse_capability_closeout_payload(worker_result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(worker_result, dict):
        return {
            "present": False,
            "task_type_category": None,
            "capability_emission_required": False,
            "capability_emission_reason": "",
            "capability_mutations": [],
        }

    closeout_raw = worker_result.get("capability_closeout")
    legacy_mutation = worker_result.get("capability_mutation")
    if closeout_raw is None and legacy_mutation is None:
        return {
            "present": False,
            "task_type_category": None,
            "capability_emission_required": False,
            "capability_emission_reason": "",
            "capability_mutations": [],
        }

    closeout = _require_mapping(closeout_raw, field="capability_closeout") if closeout_raw is not None else {}
    task_type_category = str(closeout.get("task_type_category") or "").strip()
    capability_emission_reason = str(closeout.get("capability_emission_reason") or "").strip()
    if not capability_emission_reason and legacy_mutation is not None:
        capability_emission_reason = "legacy capability_mutation field present"

    if closeout_raw is not None:
        for field in (
            "task_type_category",
            "capability_emission_required",
            "capability_emission_reason",
            "capability_mutations",
        ):
            if field not in closeout:
                die(f"capability_closeout.{field} is required when capability_closeout is present")
        if task_type_category not in CAPABILITY_CLOSEOUT_TASK_TYPE_CATEGORIES:
            die(
                "invalid capability_closeout.task_type_category: "
                f"{task_type_category!r}; expected one of {sorted(CAPABILITY_CLOSEOUT_TASK_TYPE_CATEGORIES)}"
            )
        if not capability_emission_reason:
            die("capability_closeout.capability_emission_reason must be non-empty")
        capability_emission_required = parse_bool(
            closeout.get("capability_emission_required"),
            field="capability_closeout.capability_emission_required",
        )
    else:
        capability_emission_required = legacy_mutation is not None

    mutations = _normalize_capability_mutations_list(
        closeout.get("capability_mutations"),
        field="capability_closeout.capability_mutations",
    )
    if legacy_mutation is not None:
        if mutations:
            die("worker result may not provide both capability_closeout.capability_mutations and capability_mutation")
        if isinstance(legacy_mutation, list):
            mutations = _normalize_capability_mutations_list(legacy_mutation, field="capability_mutation")
        elif isinstance(legacy_mutation, dict):
            legacy_mapping = dict(legacy_mutation)
            if isinstance(legacy_mapping.get("capability_mutations"), list):
                mutations = _normalize_capability_mutations_list(
                    legacy_mapping.get("capability_mutations"),
                    field="capability_mutation.capability_mutations",
                )
            else:
                mutations = [legacy_mapping]
        else:
            die("capability_mutation must be an object or array")

    if capability_emission_required and not mutations:
        die("capability mutation is required but no capability_mutations were provided")

    return {
        "present": True,
        "task_type_category": task_type_category or None,
        "capability_emission_required": capability_emission_required,
        "capability_emission_reason": capability_emission_reason,
        "capability_mutations": mutations,
    }


def _require_mutation_fields(mutation: dict[str, Any], *, fields: Iterable[str], field_prefix: str) -> None:
    for field in fields:
        value = mutation.get(field)
        if value is None:
            die(f"{field_prefix}.{field} is required")
        if isinstance(value, str) and not value.strip():
            die(f"{field_prefix}.{field} is required")


def validate_capability_mutation_set(worker_result: dict[str, Any] | None) -> dict[str, Any]:
    closeout = parse_capability_closeout_payload(worker_result)
    mutations = list(closeout["capability_mutations"])
    seen_targets: set[str] = set()
    seen_prior: set[str] = set()

    for index, mutation in enumerate(mutations):
        field_prefix = f"capability_mutations[{index}]"
        action = str(mutation.get("action") or "").strip()
        if action not in CAPABILITY_MUTATION_ACTIONS:
            die(f"{field_prefix}.action must be one of {sorted(CAPABILITY_MUTATION_ACTIONS)}; got {action!r}")
        if action == "create":
            _require_mutation_fields(
                mutation,
                fields=(
                    "capability_id",
                    "name",
                    "summary",
                    "kind",
                    "scope_kind",
                    "owning_repo_id",
                    "affected_repo_ids",
                    "entrypoints",
                    "when_to_use_md",
                    "do_not_use_for_md",
                    "evidence_summary_md",
                    "verification_level",
                ),
                field_prefix=field_prefix,
            )
            target_id = normalize_capability_id(mutation.get("capability_id"))
            if target_id in seen_targets:
                die(f"duplicate capability mutation target: {target_id}")
            seen_targets.add(target_id)
            continue
        if action == "update":
            _require_mutation_fields(mutation, fields=("capability_id",), field_prefix=field_prefix)
            target_id = normalize_capability_id(mutation.get("capability_id"))
            if target_id in seen_targets:
                die(f"duplicate capability mutation target: {target_id}")
            seen_targets.add(target_id)
            scope_fields = {"scope_kind", "owning_repo_id", "affected_repo_ids"}
            if scope_fields.intersection(mutation.keys()) and not scope_fields.issubset(mutation.keys()):
                die(
                    f"{field_prefix} must include scope_kind, owning_repo_id, and affected_repo_ids together "
                    "when changing scope"
                )
            continue
        if action == "deprecate":
            _require_mutation_fields(mutation, fields=("capability_id",), field_prefix=field_prefix)
            target_id = normalize_capability_id(mutation.get("capability_id"))
            if target_id in seen_targets:
                die(f"duplicate capability mutation target: {target_id}")
            seen_targets.add(target_id)
            continue

        _require_mutation_fields(mutation, fields=("prior_capability_id", "replacement"), field_prefix=field_prefix)
        prior_capability_id = normalize_capability_id(mutation.get("prior_capability_id"))
        if prior_capability_id in seen_prior:
            die(f"duplicate supersede prior_capability_id: {prior_capability_id}")
        seen_prior.add(prior_capability_id)
        replacement = _require_mapping(mutation.get("replacement"), field=f"{field_prefix}.replacement")
        _require_mutation_fields(
            replacement,
            fields=(
                "capability_id",
                "name",
                "summary",
                "kind",
                "scope_kind",
                "owning_repo_id",
                "affected_repo_ids",
                "entrypoints",
                "when_to_use_md",
                "do_not_use_for_md",
                "evidence_summary_md",
                "verification_level",
            ),
            field_prefix=f"{field_prefix}.replacement",
        )
        replacement_id = normalize_capability_id(replacement.get("capability_id"))
        if replacement_id == prior_capability_id:
            die(f"{field_prefix}.replacement.capability_id must differ from prior_capability_id")
        if replacement_id in seen_targets:
            die(f"duplicate capability mutation target: {replacement_id}")
        seen_targets.add(replacement_id)

    return closeout


def _record_capability_mutation_application(
    conn: sqlite3.Connection,
    *,
    audit_task_id: str,
    parent_version: int,
    mutation_digest: str,
    actor_id: str,
    metadata: dict[str, Any],
) -> bool:
    application_key = hashlib.sha256(
        f"{audit_task_id}:{parent_version}:{mutation_digest}".encode("utf-8")
    ).hexdigest()
    existing = conn.execute(
        "SELECT application_key FROM capability_mutation_applications WHERE application_key = ?",
        (application_key,),
    ).fetchone()
    if existing is not None:
        return False
    conn.execute(
        """
        INSERT INTO capability_mutation_applications (
            application_key,
            source_task_id,
            source_task_version,
            mutation_digest,
            applied_at,
            actor_id,
            outcome,
            metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, 'applied', ?)
        """,
        (
            application_key,
            audit_task_id,
            parent_version,
            mutation_digest,
            now_iso(),
            actor_id,
            compact_json(metadata),
        ),
    )
    return True


def apply_audit_capability_mutations(
    conn: sqlite3.Connection,
    *,
    audit_task_id: str,
    parent_task_id: str | None,
    parent_version: int | None,
    actor_id: str,
    worker_result: dict[str, Any] | None,
) -> dict[str, Any]:
    closeout = validate_capability_mutation_set(worker_result)
    if closeout["task_type_category"] == "must_emit" and closeout["capability_emission_required"] and not closeout["capability_mutations"]:
        die("audit acceptance blocked: required capability mutation payload is missing")

    mutations = list(closeout["capability_mutations"])
    if not mutations:
        return closeout

    mutation_digest = hashlib.sha256(compact_json(mutations).encode("utf-8")).hexdigest()
    should_apply = _record_capability_mutation_application(
        conn,
        audit_task_id=audit_task_id,
        parent_version=parent_version or 0,
        mutation_digest=mutation_digest,
        actor_id=actor_id,
        metadata={
            "parent_task_id": parent_task_id,
            "task_type_category": closeout["task_type_category"],
            "capability_emission_reason": closeout["capability_emission_reason"],
            "mutation_count": len(mutations),
        },
    )
    if not should_apply:
        return closeout

    event_timestamp = now_iso()
    for mutation in mutations:
        action = str(mutation["action"]).strip()
        if action == "create":
            payload = dict(mutation)
            payload["status"] = "active"
            payload["verification_level"] = "audited"
            payload["verified_by_task_id"] = audit_task_id
            payload["source_tasks"] = [{"task_id": audit_task_id, "relationship_kind": "created_by"}]
            payload["created_at"] = event_timestamp
            payload["updated_at"] = event_timestamp
            payload["event_type"] = "capability.created"
            payload["event_payload"] = {"audit_task_id": audit_task_id, "action": action}
            create_capability(conn, payload, actor_kind="runtime", actor_id=actor_id)
            continue

        if action == "update":
            capability_id = normalize_capability_id(mutation["capability_id"])
            existing = fetch_capability_payload(conn, capability_id)
            if existing is None:
                die(f"cannot update missing capability: {capability_id}")
            metadata_payload = dict(existing.get("metadata") or {})
            if isinstance(mutation.get("metadata"), dict):
                metadata_payload.update(dict(mutation["metadata"]))
            payload = dict(existing)
            payload.update({key: value for key, value in mutation.items() if key not in {"action", "metadata"}})
            payload["metadata"] = metadata_payload
            payload["verification_level"] = "audited"
            payload["verified_by_task_id"] = audit_task_id
            payload["updated_at"] = event_timestamp
            payload["source_tasks"] = [{"task_id": audit_task_id, "relationship_kind": "updated_by"}]
            payload["event_type"] = "capability.updated"
            payload["event_payload"] = {"audit_task_id": audit_task_id, "action": action}
            upsert_capability(conn, payload, actor_kind="runtime", actor_id=actor_id)
            continue

        if action == "deprecate":
            capability_id = normalize_capability_id(mutation["capability_id"])
            existing = fetch_capability_payload(conn, capability_id)
            if existing is None:
                die(f"cannot deprecate missing capability: {capability_id}")
            metadata_payload = dict(existing.get("metadata") or {})
            if isinstance(mutation.get("metadata"), dict):
                metadata_payload.update(dict(mutation["metadata"]))
            payload = dict(existing)
            payload["status"] = "deprecated"
            payload["metadata"] = metadata_payload
            if mutation.get("evidence_summary_md") is not None:
                payload["evidence_summary_md"] = str(mutation.get("evidence_summary_md") or "").strip()
            payload["verification_level"] = "audited"
            payload["verified_by_task_id"] = audit_task_id
            payload["updated_at"] = event_timestamp
            payload["source_tasks"] = [{"task_id": audit_task_id, "relationship_kind": "deprecated_by"}]
            payload["event_type"] = "capability.deprecated"
            payload["event_payload"] = {"audit_task_id": audit_task_id, "action": action}
            upsert_capability(conn, payload, actor_kind="runtime", actor_id=actor_id)
            continue

        prior_capability_id = normalize_capability_id(mutation["prior_capability_id"])
        existing = fetch_capability_payload(conn, prior_capability_id)
        if existing is None:
            die(f"cannot supersede missing capability: {prior_capability_id}")
        replacement = _require_mapping(mutation.get("replacement"), field="capability_mutations.replacement")
        replacement_payload = dict(replacement)
        replacement_payload["status"] = "active"
        replacement_payload["verification_level"] = "audited"
        replacement_payload["verified_by_task_id"] = audit_task_id
        replacement_payload["source_tasks"] = [{"task_id": audit_task_id, "relationship_kind": "created_by"}]
        replacement_payload["created_at"] = event_timestamp
        replacement_payload["updated_at"] = event_timestamp
        replacement_payload["event_type"] = "capability.created"
        replacement_payload["event_payload"] = {
            "audit_task_id": audit_task_id,
            "action": action,
            "supersedes": prior_capability_id,
        }
        create_capability(conn, replacement_payload, actor_kind="runtime", actor_id=actor_id)

        prior_payload = dict(existing)
        prior_payload["status"] = "deprecated"
        prior_payload["replaced_by_capability_id"] = replacement_payload["capability_id"]
        prior_payload["verification_level"] = "audited"
        prior_payload["verified_by_task_id"] = audit_task_id
        prior_payload["updated_at"] = event_timestamp
        prior_payload["source_tasks"] = [{"task_id": audit_task_id, "relationship_kind": "superseded_by"}]
        prior_payload["event_type"] = "capability.superseded"
        prior_payload["event_payload"] = {
            "audit_task_id": audit_task_id,
            "action": action,
            "replacement_capability_id": replacement_payload["capability_id"],
        }
        upsert_capability(conn, prior_payload, actor_kind="runtime", actor_id=actor_id)

    return closeout


def build_capability_payload(
    row: sqlite3.Row,
    *,
    affected_repo_ids: list[str] | None = None,
    source_tasks: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "capability_id": str(row["capability_id"]),
        "name": str(row["name"]),
        "summary": str(row["summary"]),
        "status": str(row["status"]),
        "kind": str(row["kind"]),
        "scope_kind": str(row["scope_kind"]),
        "owning_repo_id": str(row["owning_repo_id"]),
        "when_to_use_md": str(row["when_to_use_md"]),
        "do_not_use_for_md": str(row["do_not_use_for_md"]),
        "entrypoints": parse_json_text(row["entrypoints_json"], default=[]),
        "keywords": parse_json_text(row["keywords_json"], default=[]),
        "affected_repo_ids": affected_repo_ids or [],
        "evidence_summary_md": str(row["evidence_summary_md"]),
        "verification_level": str(row["verification_level"]),
        "verified_by_task_id": row["verified_by_task_id"],
        "replaced_by_capability_id": row["replaced_by_capability_id"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "archived_at": row["archived_at"],
        "metadata": parse_json_text(row["metadata_json"], default={}),
        "source_tasks": source_tasks or [],
    }
    if events is not None:
        payload["events"] = events
    return payload


def fetch_capability_payload(conn: sqlite3.Connection, capability_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM capabilities WHERE capability_id = ?",
        (capability_id,),
    ).fetchone()
    if row is None:
        return None
    affected_repo_rows = conn.execute(
        """
        SELECT repo_id
        FROM capability_affected_repos
        WHERE capability_id = ?
        ORDER BY repo_id ASC
        """,
        (capability_id,),
    ).fetchall()
    source_task_rows = conn.execute(
        """
        SELECT task_id, relationship_kind, created_at
        FROM capability_source_tasks
        WHERE capability_id = ?
        ORDER BY created_at ASC, task_id ASC, relationship_kind ASC
        """,
        (capability_id,),
    ).fetchall()
    event_rows = conn.execute(
        """
        SELECT event_id, event_type, actor_kind, actor_id, payload_json, created_at
        FROM capability_events
        WHERE capability_id = ?
        ORDER BY created_at ASC, event_id ASC
        """,
        (capability_id,),
    ).fetchall()
    return build_capability_payload(
        row,
        affected_repo_ids=[str(repo_row["repo_id"]) for repo_row in affected_repo_rows],
        source_tasks=[
            {
                "task_id": str(source_row["task_id"]),
                "relationship_kind": str(source_row["relationship_kind"]),
                "created_at": str(source_row["created_at"]),
            }
            for source_row in source_task_rows
        ],
        events=[
            {
                "event_id": int(event_row["event_id"]),
                "event_type": str(event_row["event_type"]),
                "actor_kind": str(event_row["actor_kind"]),
                "actor_id": str(event_row["actor_id"]),
                "payload": parse_json_text(event_row["payload_json"], default={}),
                "created_at": str(event_row["created_at"]),
            }
            for event_row in event_rows
        ],
    )


def fetch_capability_registry(
    conn: sqlite3.Connection,
    *,
    repo_id: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    verification_level: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if repo_id is not None:
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM capability_affected_repos car
                WHERE car.capability_id = capabilities.capability_id
                  AND car.repo_id = ?
            )
            """
        )
        params.append(repo_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)
    if verification_level is not None:
        clauses.append("verification_level = ?")
        params.append(verification_level)
    query = "SELECT * FROM capabilities"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY updated_at DESC, capability_id ASC"
    rows = conn.execute(query, tuple(params)).fetchall()
    if not rows:
        return []
    capability_ids = [str(row["capability_id"]) for row in rows]
    placeholders = ", ".join("?" for _ in capability_ids)
    affected_map: dict[str, list[str]] = defaultdict(list)
    affected_rows = conn.execute(
        f"""
        SELECT capability_id, repo_id
        FROM capability_affected_repos
        WHERE capability_id IN ({placeholders})
        ORDER BY capability_id ASC, repo_id ASC
        """,
        tuple(capability_ids),
    ).fetchall()
    for affected_row in affected_rows:
        affected_map[str(affected_row["capability_id"])].append(str(affected_row["repo_id"]))
    return [
        build_capability_payload(
            row,
            affected_repo_ids=affected_map.get(str(row["capability_id"]), []),
        )
        for row in rows
    ]


def render_capability_rows(rows: list[dict[str, Any]]) -> str:
    return render_table(
        [
            {
                "capability_id": row["capability_id"],
                "status": row["status"],
                "verification": row["verification_level"],
                "owner": row["owning_repo_id"],
                "scope": row["scope_kind"],
                "kind": row["kind"],
                "repos": ", ".join(row.get("affected_repo_ids", [])),
                "name": row["name"],
            }
            for row in rows
        ],
        [
            ("capability_id", "capability_id"),
            ("status", "status"),
            ("verification", "verification"),
            ("owner", "owner"),
            ("scope", "scope"),
            ("kind", "kind"),
            ("repos", "repos"),
            ("name", "name"),
        ],
    )


def render_capability_detail(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") or {}
    metadata_text = json.dumps(metadata, indent=2, sort_keys=True) if metadata else "(none)"
    source_task_text = (
        "\n".join(
            f"- {item['task_id']} ({item['relationship_kind']})"
            for item in row.get("source_tasks", [])
        )
        or "(none)"
    )
    event_text = (
        "\n".join(
            f"- #{item['event_id']} {item['event_type']} by {item['actor_kind']}:{item['actor_id']}"
            for item in row.get("events", [])
        )
        or "(none)"
    )
    return "\n".join(
        [
            f"capability_id: {row['capability_id']}",
            f"name: {row['name']}",
            f"status: {row['status']}",
            f"verification_level: {row['verification_level']}",
            f"kind: {row['kind']}",
            f"scope_kind: {row['scope_kind']}",
            f"owning_repo_id: {row['owning_repo_id']}",
            f"affected_repo_ids: {', '.join(row.get('affected_repo_ids', []))}",
            f"verified_by_task_id: {row['verified_by_task_id'] or '(none)'}",
            f"replaced_by_capability_id: {row['replaced_by_capability_id'] or '(none)'}",
            f"when_to_use_md: {row['when_to_use_md']}",
            f"do_not_use_for_md: {row['do_not_use_for_md'] or '(none)'}",
            f"entrypoints: {', '.join(row.get('entrypoints', [])) or '(none)'}",
            f"keywords: {', '.join(row.get('keywords', [])) or '(none)'}",
            "metadata:",
            "\n  ".join(metadata_text.splitlines()),
            "source_tasks:",
            source_task_text,
            "events:",
            event_text,
            f"created_at: {row['created_at']}",
            f"updated_at: {row['updated_at']}",
            f"archived_at: {row['archived_at'] or '(none)'}",
        ]
    )


def candidate_band_rank(band: str) -> int:
    return {
        "exact_duplicate": 4,
        "strong_overlap": 3,
        "related_capability": 2,
        "related_recent_work": 1,
        "non_actionable": 0,
    }.get(band, 0)


def compute_scope_fingerprint(
    *,
    target_repo_id: str,
    search_scope: dict[str, Any],
    actionable_candidate_ids: list[str],
) -> str:
    payload = {
        "target_repo_id": target_repo_id,
        "repo_ids": sorted_unique_strings(search_scope.get("repo_ids", [])),
        "include_recent_done_days": int(search_scope.get("include_recent_done_days", 90)),
        "include_active_tasks": bool(search_scope.get("include_active_tasks", True)),
        "include_capabilities": bool(search_scope.get("include_capabilities", True)),
        "include_deprecated_capabilities": bool(search_scope.get("include_deprecated_capabilities", True)),
        "candidate_ids": sorted(set(actionable_candidate_ids)),
    }
    return f"sha256:{stable_sha256(payload)}"


def fetch_preflight_task_candidates(conn: sqlite3.Connection, request: dict[str, Any]) -> list[sqlite3.Row]:
    scope = request["search_scope"]
    repo_ids = scope["repo_ids"]
    if not repo_ids:
        return []
    params: list[Any] = list(repo_ids)
    clauses = [f"target_repo_id IN ({', '.join('?' for _ in repo_ids)})", "archived_at IS NULL"]
    if request["request_context"]["is_material_update"] and request["request_context"]["existing_task_id"]:
        clauses.append("task_id != ?")
        params.append(request["request_context"]["existing_task_id"])
    active_statuses = sorted(PLANNER_STATUSES - {"done"})
    active_clause = f"planner_status IN ({', '.join('?' for _ in active_statuses)})"
    params.extend(active_statuses)
    recent_threshold = (current_utc_datetime() - timedelta(days=int(scope["include_recent_done_days"]))).isoformat()
    clauses.append(f"(({active_clause}) OR (planner_status = 'done' AND COALESCE(closed_at, updated_at) >= ?))")
    params.append(recent_threshold)
    query = f"""
        SELECT *
        FROM tasks
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC, task_id ASC
        LIMIT ?
    """
    params.append(int(scope["max_candidates_per_kind"]))
    return conn.execute(query, tuple(params)).fetchall()


def fetch_preflight_capability_candidates(conn: sqlite3.Connection, request: dict[str, Any]) -> list[sqlite3.Row]:
    scope = request["search_scope"]
    if not scope["include_capabilities"]:
        return []
    repo_ids = scope["repo_ids"]
    if not repo_ids:
        return []
    status_values = ["active"]
    if scope.get("include_deprecated_capabilities", True):
        status_values.append("deprecated")
    params = list(repo_ids) + list(repo_ids) + status_values + [int(scope["max_candidates_per_kind"])]
    query = f"""
        SELECT DISTINCT c.*
        FROM capabilities c
        LEFT JOIN capability_affected_repos car ON car.capability_id = c.capability_id
        WHERE (
            c.owning_repo_id IN ({', '.join('?' for _ in repo_ids)})
            OR car.repo_id IN ({', '.join('?' for _ in repo_ids)})
        )
        AND c.status IN ({', '.join('?' for _ in status_values)})
        ORDER BY c.updated_at DESC, c.capability_id ASC
        LIMIT ?
    """
    return conn.execute(query, tuple(params)).fetchall()


def fetch_capability_scope_map(conn: sqlite3.Connection, capability_ids: list[str]) -> dict[str, list[str]]:
    if not capability_ids:
        return {}
    placeholders = ", ".join("?" for _ in capability_ids)
    rows = conn.execute(
        f"""
        SELECT capability_id, repo_id
        FROM capability_affected_repos
        WHERE capability_id IN ({placeholders})
        ORDER BY capability_id ASC, repo_id ASC
        """,
        tuple(capability_ids),
    ).fetchall()
    scope_map: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        scope_map[str(row["capability_id"])].append(str(row["repo_id"]))
    return dict(scope_map)


def load_latest_task_event_ids(conn: sqlite3.Connection, task_ids: list[str]) -> dict[str, int]:
    if not task_ids:
        return {}
    placeholders = ", ".join("?" for _ in task_ids)
    rows = conn.execute(
        f"""
        SELECT task_id, MAX(event_id) AS max_event_id
        FROM task_events
        WHERE task_id IN ({placeholders})
        GROUP BY task_id
        """,
        tuple(task_ids),
    ).fetchall()
    return {str(row["task_id"]): int(row["max_event_id"] or 0) for row in rows}


def load_latest_capability_event_ids(conn: sqlite3.Connection, capability_ids: list[str]) -> dict[str, int]:
    if not capability_ids:
        return {}
    placeholders = ", ".join("?" for _ in capability_ids)
    rows = conn.execute(
        f"""
        SELECT capability_id, MAX(event_id) AS max_event_id
        FROM capability_events
        WHERE capability_id IN ({placeholders})
        GROUP BY capability_id
        """,
        tuple(capability_ids),
    ).fetchall()
    return {str(row["capability_id"]): int(row["max_event_id"] or 0) for row in rows}


def task_candidate_fingerprint(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    dependencies = load_dependencies(conn, [str(row["task_id"])]).get(str(row["task_id"]), [])
    payload = {
        "title": markdown_to_plain_text(row["title"]).casefold(),
        "summary": markdown_to_plain_text(row["summary"]).casefold(),
        "objective": markdown_to_plain_text(row["objective_md"]).casefold(),
        "scope": markdown_to_plain_text(row["scope_md"]).casefold(),
        "deliverables": markdown_to_plain_text(row["deliverables_md"]).casefold(),
        "acceptance": markdown_to_plain_text(row["acceptance_md"]).casefold(),
        "target_repo_id": str(row["target_repo_id"]),
        "task_type": str(row["task_type"]),
        "dependency_task_ids": sorted(dep["depends_on_task_id"] for dep in dependencies),
    }
    return f"sha256:{stable_sha256(payload)}"


def score_preflight_candidate(
    *,
    request: dict[str, Any],
    request_fields: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    weights = {
        "repo_scope_match": 10,
        "same_target_repo": 10,
        "dependency_reference_match": 10,
        "title_exact_match": 25,
        "summary_exact_match": 20,
        "intent_fingerprint_match": 50,
        "high_text_overlap": 25,
        "moderate_text_overlap": 15,
        "entrypoint_keyword_match": 10,
        "deliverable_contract_match": 15,
        "candidate_is_active_task": 20,
        "candidate_is_recent_done_task": 12,
        "candidate_is_active_capability": 20,
        "candidate_is_deprecated_capability": 8,
        "candidate_verified_high_trust": 8,
        "candidate_completed_recently": 8,
        "creator_marked_supersede_target": 12,
        "creator_marked_extension_target": 12,
        "same_capability_surface": 20,
        "same_work_surface": 20,
    }
    requested_ids = (
        set(request["normalized_task_intent"]["requested_task_ids"])
        | set(request["normalized_task_intent"]["requested_capability_ids"])
        | set(request["normalized_task_intent"]["dependency_task_ids"])
    )
    repo_scope_match = bool(set(request_fields["repo_scope"]) & set(candidate["repo_scope"]))
    same_target_repo = candidate["target_repo_id"] == request["normalized_task_intent"]["target_repo_id"]
    dependency_reference_match = candidate["candidate_id"] in requested_ids
    title_exact_match = candidate["title_text"].casefold() == request_fields["title_text"].casefold()
    summary_exact_match = candidate["summary_text"].casefold() == request_fields["summary_text"].casefold()
    intent_fingerprint_match = candidate["intent_fingerprint"] == request_fields["intent_fingerprint"]
    overlap = jaccard_overlap(set(request_fields["intent_terms"]), set(candidate["intent_terms"]))
    high_text_overlap = overlap >= 0.65
    moderate_text_overlap = 0.40 <= overlap < 0.65
    entrypoint_keyword_match = len(set(request_fields["intent_terms"]) & set(candidate["entrypoint_terms"])) >= 2
    deliverable_contract_match = bool(set(request_fields["deliverable_terms"]) & set(candidate["deliverable_terms"]))
    candidate_is_active_task = candidate["candidate_kind"] == "task" and candidate["status"] != "done"
    candidate_is_recent_done_task = candidate["candidate_kind"] == "task" and candidate["status"] == "done"
    candidate_is_active_capability = candidate["candidate_kind"] == "capability" and candidate["status"] == "active"
    candidate_is_deprecated_capability = candidate["candidate_kind"] == "capability" and candidate["status"] == "deprecated"
    candidate_verified_high_trust = candidate.get("verification_level") in {"planner_verified", "audited"}
    completed_at = parse_iso_datetime(candidate.get("completed_at"))
    candidate_completed_recently = bool(completed_at and completed_at >= current_utc_datetime() - timedelta(days=30))
    creator_marked_supersede_target = candidate["candidate_id"] in (
        set(request["normalized_task_intent"]["requested_task_ids"]) | set(request["normalized_task_intent"]["requested_capability_ids"])
    )
    creator_marked_extension_target = candidate["candidate_id"] in set(request["normalized_task_intent"]["requested_capability_ids"])
    shared_surface = len(set(request_fields["intent_terms"]) & set(candidate["intent_terms"])) >= 3
    same_capability_surface = candidate["candidate_kind"] == "capability" and entrypoint_keyword_match and shared_surface
    same_work_surface = candidate["candidate_kind"] == "task" and same_target_repo and overlap >= 0.50
    flags = {
        "repo_scope_match": repo_scope_match,
        "same_target_repo": same_target_repo,
        "dependency_reference_match": dependency_reference_match,
        "title_exact_match": title_exact_match,
        "summary_exact_match": summary_exact_match,
        "intent_fingerprint_match": intent_fingerprint_match,
        "high_text_overlap": high_text_overlap,
        "moderate_text_overlap": moderate_text_overlap,
        "entrypoint_keyword_match": entrypoint_keyword_match,
        "deliverable_contract_match": deliverable_contract_match,
        "candidate_is_active_task": candidate_is_active_task,
        "candidate_is_recent_done_task": candidate_is_recent_done_task,
        "candidate_is_active_capability": candidate_is_active_capability,
        "candidate_is_deprecated_capability": candidate_is_deprecated_capability,
        "candidate_verified_high_trust": candidate_verified_high_trust,
        "candidate_completed_recently": candidate_completed_recently,
        "creator_marked_supersede_target": creator_marked_supersede_target,
        "creator_marked_extension_target": creator_marked_extension_target,
        "same_capability_surface": same_capability_surface,
        "same_work_surface": same_work_surface,
    }
    score = sum(weight for key, weight in weights.items() if flags.get(key))
    if candidate_is_deprecated_capability and not same_capability_surface and not deliverable_contract_match:
        score -= 20
    score = max(0, min(100, score))
    band = "non_actionable"
    if intent_fingerprint_match or (title_exact_match and summary_exact_match and same_target_repo) or (
        same_capability_surface and title_exact_match and candidate_is_active_capability and candidate_verified_high_trust
    ):
        band = "exact_duplicate"
        score = 100
    elif score >= 75 or (
        candidate_is_active_capability and same_capability_surface and deliverable_contract_match
    ) or (
        candidate_is_active_task and same_work_surface and high_text_overlap
    ) or (
        candidate_is_recent_done_task and candidate_completed_recently and same_work_surface and deliverable_contract_match
    ):
        band = "strong_overlap"
    elif candidate["candidate_kind"] == "capability" and score >= 40 and (
        entrypoint_keyword_match or deliverable_contract_match or creator_marked_extension_target
    ):
        band = "related_capability"
    elif candidate["candidate_kind"] == "task" and score >= 35 and (
        moderate_text_overlap or dependency_reference_match or candidate_completed_recently
    ):
        band = "related_recent_work"
    candidate["band"] = band
    candidate["score"] = score
    candidate["reason_codes"] = sorted(key for key, value in flags.items() if value)
    return candidate


def build_task_preflight_response(
    conn: sqlite3.Connection,
    request: dict[str, Any],
    *,
    issued_at: str | None = None,
) -> dict[str, Any]:
    request_fields = intent_comparison_fields(request["normalized_task_intent"])
    task_rows = fetch_preflight_task_candidates(conn, request)
    capability_rows = fetch_preflight_capability_candidates(conn, request)
    capability_scope_map = fetch_capability_scope_map(conn, [str(row["capability_id"]) for row in capability_rows])
    task_event_ids = load_latest_task_event_ids(conn, [str(row["task_id"]) for row in task_rows])
    capability_event_ids = load_latest_capability_event_ids(conn, [str(row["capability_id"]) for row in capability_rows])
    actionable_candidate_ids = [f"task:{row['task_id']}" for row in task_rows] + [f"capability:{row['capability_id']}" for row in capability_rows]
    candidates: list[dict[str, Any]] = []
    for row in task_rows:
        task_payload = {
            "candidate_kind": "task",
            "candidate_id": str(row["task_id"]),
            "target_repo_id": str(row["target_repo_id"]),
            "repo_scope": [str(row["target_repo_id"])],
            "title_text": markdown_to_plain_text(row["title"]),
            "summary_text": markdown_to_plain_text(row["summary"]),
            "intent_terms": sorted(
                lexical_token_set(
                    " ".join(
                        [
                            str(row["title"]),
                            str(row["summary"]),
                            str(row["objective_md"]),
                            str(row["scope_md"]),
                            str(row["deliverables_md"]),
                            str(row["acceptance_md"]),
                        ]
                    )
                )
            ),
            "entrypoint_terms": [],
            "deliverable_terms": sorted(lexical_token_set(" ".join([str(row["deliverables_md"]), str(row["acceptance_md"])]))),
            "intent_fingerprint": task_candidate_fingerprint(conn, row),
            "status": str(row["planner_status"]),
            "summary": str(row["summary"]),
            "verification_level": None,
            "completed_at": utc_rfc3339(str(row["closed_at"] or row["updated_at"] or "")) if row["planner_status"] == "done" else None,
        }
        candidates.append(score_preflight_candidate(request=request, request_fields=request_fields, candidate=task_payload))
    for row in capability_rows:
        capability_payload = {
            "candidate_kind": "capability",
            "candidate_id": str(row["capability_id"]),
            "target_repo_id": str(row["owning_repo_id"]),
            "repo_scope": sorted({str(row["owning_repo_id"]), *capability_scope_map.get(str(row["capability_id"]), [])}),
            "title_text": markdown_to_plain_text(row["name"]),
            "summary_text": markdown_to_plain_text(row["summary"]),
            "intent_terms": sorted(
                lexical_token_set(
                    " ".join(
                        [
                            str(row["name"]),
                            str(row["summary"]),
                            str(row["when_to_use_md"]),
                            " ".join(parse_json_text(row["entrypoints_json"], default=[])),
                            " ".join(parse_json_text(row["keywords_json"], default=[])),
                        ]
                    )
                )
            ),
            "entrypoint_terms": sorted(
                lexical_token_set(
                    " ".join(parse_json_text(row["entrypoints_json"], default=[]) + parse_json_text(row["keywords_json"], default=[]))
                )
            ),
            "deliverable_terms": sorted(lexical_token_set(" ".join([str(row["summary"]), str(row["when_to_use_md"])]))),
            "intent_fingerprint": "",
            "status": str(row["status"]),
            "summary": str(row["summary"]),
            "verification_level": str(row["verification_level"]),
            "completed_at": None,
        }
        candidates.append(score_preflight_candidate(request=request, request_fields=request_fields, candidate=capability_payload))
    candidates = [candidate for candidate in candidates if candidate["band"] != "non_actionable"]
    candidates.sort(key=lambda item: (-candidate_band_rank(item["band"]), -int(item["score"]), item["candidate_id"]))
    duplicate_count = sum(1 for candidate in candidates if candidate["band"] == "exact_duplicate")
    strong_overlap_count = sum(1 for candidate in candidates if candidate["band"] == "strong_overlap")
    warning_count = sum(1 for candidate in candidates if candidate["band"] in {"related_capability", "related_recent_work"})
    if duplicate_count:
        blocking_bucket = "duplicate"
        classification_options = ["duplicate_do_not_create"]
        override_allowed = False
        override_kind = "none"
    elif strong_overlap_count:
        blocking_bucket = "strong_overlap"
        classification_options = ["follow_on", "extends_existing", "supersedes"]
        override_allowed = True
        override_kind = "strong_overlap_privileged"
    elif warning_count:
        blocking_bucket = "weak_overlap"
        if any(candidate["band"] == "related_capability" for candidate in candidates):
            classification_options = ["extends_existing", "follow_on", "supersedes", "new"]
        else:
            classification_options = ["follow_on", "new", "supersedes", "extends_existing"]
        override_allowed = True
        override_kind = "weak_overlap"
    else:
        blocking_bucket = "none"
        classification_options = ["new", "follow_on", "extends_existing", "supersedes"]
        override_allowed = False
        override_kind = "none"
    preflight_revision = {
        "algorithm_version": PREFLIGHT_ALGORITHM_VERSION,
        "scope_fingerprint": compute_scope_fingerprint(
            target_repo_id=request["normalized_task_intent"]["target_repo_id"],
            search_scope=request["search_scope"],
            actionable_candidate_ids=actionable_candidate_ids,
        ),
        "task_domain": {
            "max_updated_at": max((utc_rfc3339(str(row["updated_at"])) for row in task_rows), default=None),
            "max_task_event_id": max(task_event_ids.values(), default=0),
        },
        "capability_domain": {
            "max_updated_at": max((utc_rfc3339(str(row["updated_at"])) for row in capability_rows), default=None),
            "max_capability_event_id": max(capability_event_ids.values(), default=0),
        },
    }
    issued_at_value = utc_rfc3339(issued_at) or now_iso().replace("+00:00", "Z")
    response = {
        "preflight_revision": preflight_revision,
        "issued_at": issued_at_value,
        "issued_by": PREFLIGHT_ISSUER,
        "classification_options": classification_options,
        "blocking_bucket": blocking_bucket,
        "override_allowed": override_allowed,
        "override_kind": override_kind,
        "strong_overlap_count": strong_overlap_count,
        "duplicate_count": duplicate_count,
        "warning_count": warning_count,
        "candidates": [
            {
                "candidate_kind": candidate["candidate_kind"],
                "candidate_id": candidate["candidate_id"],
                "band": candidate["band"],
                "score": candidate["score"],
                "reason_codes": candidate["reason_codes"],
                "status": candidate["status"],
                "summary": candidate["summary"],
            }
            for candidate in candidates
        ],
        "matched_task_ids": [candidate["candidate_id"] for candidate in candidates if candidate["candidate_kind"] == "task"],
        "matched_capability_ids": [candidate["candidate_id"] for candidate in candidates if candidate["candidate_kind"] == "capability"],
        "related_task_ids_suggested": [candidate["candidate_id"] for candidate in candidates if candidate["candidate_kind"] == "task"][:3],
        "related_capability_ids_suggested": [candidate["candidate_id"] for candidate in candidates if candidate["candidate_kind"] == "capability"][:3],
        "novelty_rationale_template": (
            "No material overlap detected."
            if blocking_bucket == "none"
            else "Explain how this work differs from the matched task/capability set and why a new task is still needed."
        ),
    }
    response_body = canonical_preflight_response_body(response)
    token_payload = {
        "version": PREFLIGHT_TOKEN_VERSION,
        "request_sha256": f"sha256:{stable_sha256(request)}",
        "response_sha256": f"sha256:{stable_sha256(response_body)}",
        "preflight_revision_sha256": f"sha256:{stable_sha256(preflight_revision)}",
        "issued_at": issued_at_value,
        "issuer": PREFLIGHT_ISSUER,
    }
    token_message = compact_json(token_payload).encode("utf-8")
    signature = hmac.new(preflight_secret(), token_message, hashlib.sha256).hexdigest()
    response["preflight_token"] = (
        base64.urlsafe_b64encode(token_message).decode("ascii").rstrip("=") + "." + signature
    )
    return response


def decode_preflight_token(token: str) -> dict[str, Any]:
    try:
        encoded, signature = token.split(".", 1)
        padded = encoded + "=" * (-len(encoded) % 4)
        token_message = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        die_preflight(error_code="preflight_untrusted", message=f"invalid preflight token format: {exc}")
    expected_signature = hmac.new(preflight_secret(), token_message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        die_preflight(error_code="preflight_untrusted", message="preflight token signature verification failed")
    token_payload = json.loads(token_message.decode("utf-8"))
    if token_payload.get("issuer") != PREFLIGHT_ISSUER:
        die_preflight(error_code="preflight_untrusted", message="preflight token issuer mismatch")
    return token_payload


def validate_preflight_binding(
    *,
    request: dict[str, Any],
    response: dict[str, Any],
    token: str,
) -> None:
    token_payload = decode_preflight_token(token)
    request_sha = f"sha256:{stable_sha256(request)}"
    response_sha = f"sha256:{stable_sha256(canonical_preflight_response_body(response))}"
    revision_sha = f"sha256:{stable_sha256(response['preflight_revision'])}"
    if (
        token_payload.get("request_sha256") != request_sha
        or token_payload.get("response_sha256") != response_sha
        or token_payload.get("preflight_revision_sha256") != revision_sha
    ):
        die_preflight(error_code="preflight_untrusted", message="preflight token digest mismatch", response=response)


def validate_override_payload(
    *,
    override: dict[str, Any] | None,
    response: dict[str, Any],
    classification: str,
    actor_kind: str,
    actor_id: str,
    related_task_ids: list[str],
    related_capability_ids: list[str],
) -> tuple[str, str | None, dict[str, Any]]:
    bucket = response["blocking_bucket"]
    recommended = response["classification_options"][0]
    override_exercised = bucket in {"strong_overlap", "duplicate"} or (bucket == "weak_overlap" and classification != recommended)
    if not override_exercised:
        return "none", None, {}
    if not override:
        if bucket == "strong_overlap":
            die_preflight(error_code="preflight_strong_overlap_blocked", message="strong overlap requires privileged override", response=response)
        if bucket == "duplicate":
            die_preflight(error_code="preflight_duplicate_blocked", message="duplicate task creation is blocked", response=response)
        die_preflight(error_code="preflight_override_invalid", message="override payload is required for this classification", response=response)
    override_kind = normalize_text_whitespace(override.get("override_kind"))
    override_reason = normalize_text_whitespace(override.get("override_reason"))
    override_actor_id = normalize_text_whitespace(override.get("override_actor_id"))
    override_authority = normalize_text_whitespace(override.get("override_authority"))
    acknowledged_candidate_ids = sorted_unique_strings(override.get("acknowledged_candidate_ids", []))
    selected_related_task_ids = sorted_unique_strings(override.get("selected_related_task_ids", related_task_ids))
    selected_related_capability_ids = sorted_unique_strings(override.get("selected_related_capability_ids", related_capability_ids))
    if not override_reason or not override_actor_id or not override_authority:
        die_preflight(error_code="preflight_override_invalid", message="override_reason, override_actor_id, and override_authority are required", response=response)
    if override_actor_id != actor_id:
        die_preflight(error_code="preflight_override_invalid", message="override_actor_id must match the authenticated actor", response=response)
    expected_kind = "weak_overlap" if bucket == "weak_overlap" else response["override_kind"]
    if override_kind != expected_kind:
        die_preflight(error_code="preflight_override_invalid", message=f"override_kind must equal {expected_kind}", response=response)
    required_acknowledged = [
        candidate["candidate_id"]
        for candidate in response["candidates"]
        if candidate["band"] in {"exact_duplicate", "strong_overlap"}
    ]
    if override_kind == "weak_overlap":
        required_acknowledged.extend(
            [
                candidate["candidate_id"]
                for candidate in response["candidates"]
                if candidate["band"] in {"related_capability", "related_recent_work"}
            ][:3]
        )
    if not set(required_acknowledged).issubset(set(acknowledged_candidate_ids)):
        die_preflight(error_code="preflight_override_invalid", message="override acknowledged_candidate_ids is incomplete", response=response)
    if bucket == "strong_overlap" and actor_kind not in {"planner", "admin"}:
        die_preflight(error_code="preflight_override_forbidden", message="strong-overlap overrides require planner/admin authority", response=response)
    if bucket == "duplicate":
        die_preflight(error_code="preflight_duplicate_blocked", message="duplicate creation requires bootstrap/admin bypass outside the ordinary planner path", response=response)
    return override_kind, override_reason, {
        "override_actor_id": override_actor_id,
        "override_authority": override_authority,
        "acknowledged_candidate_ids": acknowledged_candidate_ids,
        "override_timestamp": now_iso(),
        "override_request_channel": "task-create",
        "selected_related_task_ids": selected_related_task_ids,
        "selected_related_capability_ids": selected_related_capability_ids,
    }


def persist_task_creation_preflight(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    task_version: int,
    request: dict[str, Any],
    response: dict[str, Any],
    classification: str,
    novelty_rationale: str,
    related_task_ids: list[str],
    related_capability_ids: list[str],
    override_kind: str,
    override_reason: str | None,
    override_metadata: dict[str, Any],
    performed_by: str,
) -> None:
    conn.execute(
        """
        INSERT INTO task_creation_preflight (
            task_id,
            task_version,
            preflight_revision,
            preflight_token,
            preflight_request_json,
            preflight_response_json,
            query_text,
            classification,
            novelty_rationale,
            override_reason,
            override_kind,
            related_task_ids_json,
            related_capability_ids_json,
            matched_task_ids_json,
            matched_capability_ids_json,
            blocking_bucket,
            strong_overlap_count,
            override_allowed,
            performed_at,
            performed_by,
            metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            task_version,
            compact_json(response["preflight_revision"]),
            response["preflight_token"],
            compact_json(request),
            compact_json(canonical_preflight_response_body(response)),
            request["normalized_task_intent"]["summary"],
            classification,
            novelty_rationale,
            override_reason,
            override_kind,
            compact_json(related_task_ids),
            compact_json(related_capability_ids),
            compact_json(response["matched_task_ids"]),
            compact_json(response["matched_capability_ids"]),
            response["blocking_bucket"],
            int(response["strong_overlap_count"]),
            1 if response["override_allowed"] else 0,
            now_iso(),
            performed_by,
            compact_json(override_metadata),
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
    task_type: str | None = None,
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
    if task_type is not None:
        clauses.append("t.task_type = ?")
        params.append(task_type)
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


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return False


def _is_remote_only_task(snapshot: dict[str, Any]) -> bool:
    metadata = snapshot.get("metadata") or {}
    if "remote_only" in metadata:
        return _coerce_bool(metadata.get("remote_only"))
    if "remote" in metadata:
        return _coerce_bool(metadata.get("remote"))
    return False


def task_is_eligible(snapshot: dict[str, Any], *, remote_only: bool | None = None) -> bool:
    if snapshot["planner_status"] not in {"todo", "in_progress"}:
        return False
    is_remote_only = _is_remote_only_task(snapshot)
    if remote_only is True and not is_remote_only:
        return False
    if remote_only is False and is_remote_only:
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
    # Don't re-dispatch tasks whose parent gate failure is immutable (parent is done/canceled).
    # Retrying cannot fix missing or empty completion_gates on a finished parent task.
    last_error = runtime.get("last_runtime_error") or ""
    if status == "failed" and last_error.startswith("parent gate check permanently failed:"):
        return False
    return True


def order_eligible_snapshots(
    snapshots: list[dict[str, Any]], *, remote_only: bool | None = None
) -> list[dict[str, Any]]:
    eligible = [snapshot for snapshot in snapshots if task_is_eligible(snapshot, remote_only=remote_only)]
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
            if value is None and field == "worker_owner":
                normalized[field] = None
                continue
            if value is None:
                if field == "initiative":
                    die(
                        'initiative is required. Use a named initiative (e.g. "capability-registry") '
                        'or "one-off" for standalone tasks that do not belong to an initiative.'
                    )
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
        missing_fields = [f for f in required_fields if f not in normalized]
        if "approval_required" not in normalized:
            normalized["approval_required"] = False
        if "source_kind" not in normalized:
            normalized["source_kind"] = "planner"
        if "metadata" not in normalized:
            normalized["metadata"] = {}
        if "dependencies" not in normalized:
            normalized["dependencies"] = []
        if "execution" not in normalized:
            missing_fields.append("execution")
        if missing_fields:
            die("missing required fields: " + ", ".join(missing_fields))
        # initiative is required on create. Use "one-off" for standalone tasks
        # that don't belong to a named initiative.
        if not normalized.get("initiative"):
            die(
                'initiative is required. Use a named initiative (e.g. "capability-registry") '
                'or "one-off" for standalone tasks that do not belong to an initiative.'
            )
    return normalized


def merge_task_metadata(existing_raw: str, incoming: dict[str, Any] | None) -> dict[str, Any]:
    current = parse_json_text(existing_raw, default={})
    if incoming:
        current.update(incoming)
    return current


def create_task(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    *,
    actor_kind: str,
    actor_id: str,
    auto_register_capability: bool = False,
) -> dict[str, Any]:
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
    if auto_register_capability:
        register_task_draft_capability(
            conn,
            task_payload=normalized,
            actor_kind=actor_kind,
            actor_id=actor_id,
        )
    reconcile_task_id_reservations(conn, series=task_series, actor_id=actor_id)
    return fetch_task_snapshots(conn, task_id=normalized["task_id"])[0]


def task_requires_audit(*, task_type: str, source_kind: str, metadata: dict[str, Any]) -> bool:
    """Return True when the task's metadata indicates an audit is required.

    Investigation tasks are exempt — auditing an investigation is low-value
    and wastes tokens. This is a hard constraint, not a default.

    audit_required MUST be explicitly set in metadata. Missing values are a
    fatal error — no silent defaults.
    """
    if task_type == "investigation":
        return False
    if "audit_required" not in metadata:
        raise ValueError(
            f"metadata.audit_required is missing for task_type={task_type!r}, "
            f"source_kind={source_kind!r}. Every task must explicitly declare "
            f"audit_required — there is no default. Fix the creation path."
        )
    return bool(metadata["audit_required"])


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
            "Ground the audit in the parent objective, acceptance criteria, artifacts, and runtime evidence.\n\n"
            "Before passing, reproduce the original bug or reported behavior from the task context when applicable. "
            "Confirm the described behavior no longer occurs. If you cannot reproduce or verify it, flag that "
            "explicitly in the verdict and do not pass by default."
        ),
        "scope_md": "Validate the delivered change against requirements and full-system behavior.",
        "deliverables_md": "Record an audit verdict with concrete evidence and any bounded fixups.",
        "acceptance_md": (
            "Confirm the implementation matches intent, works in reality, and does not fail outside a narrow local "
            "window. Before passing, reproduce the original bug or reported behavior from the task context when "
            "applicable and confirm it no longer occurs."
        ),
        "testing_md": (
            "Run reality-based validation and record commands, artifacts, and observed outcomes. Before passing, "
            "reproduce the original bug or reported behavior from the task context when applicable. If you cannot "
            "reproduce or verify it, say so explicitly in the verdict and do not pass by default."
        ),
        "dispatch_md": f"Dispatch after `{parent_task_id}` reaches `awaiting_audit`.",
        "closeout_md": (
            "Record audit evidence, final verdict, and any bounded fixups performed during the audit. Explicitly state "
            "whether you reproduced the original bug or reported behavior and verified it no longer occurs; if that "
            "verification was not possible, call that out and do not pass by default."
        ),
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
    skip_preflight: bool = False,
) -> dict[str, Any]:
    """Create a task and, if audit is required, a paired audit task.

    Returns the parent task snapshot.

    skip_preflight=True bypasses the preflight check entirely. Use only for
    product repos (non-platform) where capability deduplication is not needed.
    """
    normalized_payload = validate_task_payload(payload, for_update=False)
    resolve_task_repo_target(conn, normalized_payload)
    task_id = str(normalized_payload.get("task_id") or "")
    metadata = dict(normalized_payload.get("metadata") or {})
    task_type = str(normalized_payload.get("task_type") or "implementation")
    source_kind = str(normalized_payload.get("source_kind") or "planner")

    if skip_preflight:
        # Product repo — skip capability deduplication entirely.
        try:
            begin_immediate(conn)
            if task_requires_audit(task_type=task_type, source_kind=source_kind, metadata=metadata):
                audit_task_id = f"{task_id}-AUDIT"
                parent_metadata = dict(metadata)
                parent_metadata["child_audit_task_id"] = audit_task_id
                parent_metadata["audit_verdict"] = "pending"
                parent_payload = dict(normalized_payload)
                parent_payload["metadata"] = parent_metadata
                parent_snapshot = create_task(
                    conn,
                    parent_payload,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    auto_register_capability=False,
                )
                audit_payload = build_audit_task_payload(parent_payload)
                create_task(conn, audit_payload, actor_kind=actor_kind, actor_id=actor_id)
            else:
                parent_snapshot = create_task(
                    conn,
                    normalized_payload,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    auto_register_capability=False,
                )
            conn.commit()
            return parent_snapshot
        except BaseException:
            conn.rollback()
            raise

    preflight_block = payload.get("preflight")
    if not isinstance(preflight_block, dict):
        die_preflight(error_code="preflight_missing", message="task creation requires a preflight block")
    request = canonicalize_preflight_request(preflight_block.get("request") or {})
    submitted_intent = request["normalized_task_intent"]
    expected_intent = canonicalize_task_intent(normalized_payload)
    if submitted_intent != expected_intent:
        die_preflight(error_code="preflight_untrusted", message="normalized task payload does not match preflight intent")
    response = parse_preflight_json_object(preflight_block.get("response"), field="preflight.response")
    token = normalize_text_whitespace(preflight_block.get("preflight_token") or response.get("preflight_token"))
    if not token:
        die_preflight(error_code="preflight_missing", message="preflight_token is required")
    response = dict(response)
    response["preflight_token"] = token
    classification = normalize_text_whitespace(preflight_block.get("classification"))
    novelty_rationale = normalize_text_whitespace(preflight_block.get("novelty_rationale"))
    related_task_ids = sorted_unique_strings(preflight_block.get("related_task_ids", []))
    related_capability_ids = sorted_unique_strings(preflight_block.get("related_capability_ids", []))
    if not classification or not novelty_rationale:
        die_preflight(error_code="preflight_missing", message="classification and novelty_rationale are required", response=response)
    validate_preflight_binding(request=request, response=response, token=token)
    if classification not in response.get("classification_options", []):
        die_preflight(
            error_code="preflight_classification_invalid",
            message=f"classification {classification!r} is not allowed for bucket {response.get('blocking_bucket')!r}",
            response=response,
        )
    if not set(related_task_ids).issubset(set(response.get("matched_task_ids", []))):
        die_preflight(error_code="preflight_related_references_invalid", message="related_task_ids must come from matched task IDs", response=response)
    if not set(related_capability_ids).issubset(set(response.get("matched_capability_ids", []))):
        die_preflight(error_code="preflight_related_references_invalid", message="related_capability_ids must come from matched capability IDs", response=response)
    override_kind, override_reason, override_metadata = validate_override_payload(
        override=payload.get("override") if isinstance(payload.get("override"), dict) else None,
        response=response,
        classification=classification,
        actor_kind=actor_kind,
        actor_id=actor_id,
        related_task_ids=related_task_ids,
        related_capability_ids=related_capability_ids,
    )
    if override_kind != "none":
        metadata["preflight_override"] = {
            "kind": override_kind,
            "reason": override_reason,
            "actor_id": override_metadata.get("override_actor_id"),
            "acknowledged_candidate_ids": override_metadata.get("acknowledged_candidate_ids", []),
        }
    normalized_payload["metadata"] = metadata

    try:
        begin_immediate(conn)
        current_response = build_task_preflight_response(conn, request, issued_at=response.get("issued_at"))
        if current_response["preflight_revision"] != response["preflight_revision"]:
            die_preflight(error_code="preflight_stale", message="preflight revision is stale; rerun task-preflight", response=response)
        if compact_json(canonical_preflight_response_body(current_response)) != compact_json(canonical_preflight_response_body(response)):
            die_preflight(error_code="preflight_untrusted", message="submitted preflight response does not match canonical recomputation", response=response)
        bucket = response["blocking_bucket"]
        if bucket == "strong_overlap" and override_kind != "strong_overlap_privileged":
            die_preflight(error_code="preflight_strong_overlap_blocked", message="strong overlap blocks ordinary task creation", response=response)
        if bucket == "duplicate":
            die_preflight(error_code="preflight_duplicate_blocked", message="duplicate overlap blocks ordinary task creation", response=response)
        if bucket == "weak_overlap" and not novelty_rationale:
            die_preflight(error_code="preflight_missing", message="weak overlap requires novelty_rationale", response=response)
        if task_requires_audit(task_type=task_type, source_kind=source_kind, metadata=metadata):
            audit_task_id = f"{task_id}-AUDIT"
            parent_metadata = dict(metadata)
            parent_metadata["child_audit_task_id"] = audit_task_id
            parent_metadata["audit_verdict"] = "pending"
            parent_payload = dict(normalized_payload)
            parent_payload["metadata"] = parent_metadata
            parent_snapshot = create_task(
                conn,
                parent_payload,
                actor_kind=actor_kind,
                actor_id=actor_id,
                auto_register_capability=True,
            )
            audit_payload = build_audit_task_payload(parent_payload)
            create_task(conn, audit_payload, actor_kind=actor_kind, actor_id=actor_id)
        else:
            parent_snapshot = create_task(
                conn,
                normalized_payload,
                actor_kind=actor_kind,
                actor_id=actor_id,
                auto_register_capability=True,
            )
        persist_task_creation_preflight(
            conn,
            task_id=task_id,
            task_version=int(parent_snapshot["version"]),
            request=request,
            response=response,
            classification=classification,
            novelty_rationale=novelty_rationale,
            related_task_ids=related_task_ids,
            related_capability_ids=related_capability_ids,
            override_kind=override_kind,
            override_reason=override_reason,
            override_metadata=override_metadata,
            performed_by=actor_id,
        )
        conn.commit()
        return parent_snapshot
    except BaseException:
        conn.rollback()
        raise


def runtime_requeue_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    actor_id: str,
    reason: str,
    reset_retry_count: bool = True,
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
    # Reset planner status to 'todo' so the task is dispatch-eligible.
    # A requeued task with planner_status='failed' silently blocks dispatch.
    planner_row = conn.execute("SELECT planner_status FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    old_planner_status = planner_row["planner_status"] if planner_row else None
    if old_planner_status in ("failed", "in_progress", "awaiting_audit"):
        conn.execute(
            "UPDATE tasks SET planner_status = 'todo', updated_at = ? WHERE task_id = ?",
            (transition_at, task_id),
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
            "planner_status_reset": old_planner_status if old_planner_status in ("failed", "in_progress", "awaiting_audit") else None,
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
    if outcome == "done":
        activate_task_capability(
            conn,
            task_id=task_id,
            actor_kind=actor_kind,
            actor_id=actor_id,
            closeout_artifacts=artifacts,
            source="task-reconcile",
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
    if outcome == "done":
        activate_task_capability(
            conn,
            task_id=task_id,
            actor_kind="runtime",
            actor_id=actor_id,
            closeout_artifacts=artifacts,
            source="runtime-auto-reconcile",
        )
    conn.commit()
    return fetch_task_snapshots(conn, task_id=task_id)[0]


MAX_REWORK_RETRIES = 3


def reconcile_audit_rework(
    conn,
    *,
    audit_task_id: str,
    summary: str,
    actor_id: str,
):
    """Handle audit verdict=rework_required.

    If the parent task is within MAX_REWORK_RETRIES:
      - Reset parent planner_status to todo and requeue runtime with audit
        findings injected as rework_context so the next worker knows exactly
        what to fix.
      - Reset the audit task to todo so it re-runs after the rework lands.
    If the limit is exceeded:
      - Fail both tasks permanently and surface for operator attention.
    """
    begin_immediate(conn)
    audit_row = fetch_task_row(conn, audit_task_id)
    if audit_row is None:
        conn.rollback()
        raise RuntimeError(f"audit task not found: {audit_task_id}")

    audit_metadata = parse_json_text(audit_row["metadata_json"], default={})
    parent_task_id = str(audit_metadata.get("parent_task_id") or "")
    updated_at = now_iso()
    audit_version = int(audit_row["version"])

    # Always close out the audit task as failed (verdict was rework_required)
    audit_metadata["closeout"] = {
        "outcome": "failed",
        "summary": summary,
        "notes": "audit verdict: rework_required",
        "reconciled_at": updated_at,
        "actor_id": actor_id,
    }
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

    if not parent_task_id:
        conn.commit()
        return fetch_task_snapshots(conn, task_id=audit_task_id)[0]

    parent_row = fetch_task_row(conn, parent_task_id)
    if parent_row is None:
        conn.commit()
        return fetch_task_snapshots(conn, task_id=audit_task_id)[0]

    parent_metadata = parse_json_text(parent_row["metadata_json"], default={})
    parent_version = int(parent_row["version"])
    rework_count = int(parent_metadata.get("rework_count") or 0) + 1
    parent_updated_at = now_iso()

    if rework_count > MAX_REWORK_RETRIES:
        # Limit exceeded — fail permanently for operator attention
        # Align audit runtime to done (it's truly closed)
        conn.execute(
            "UPDATE task_runtime_state SET runtime_status = 'done', last_transition_at = ?, finished_at = COALESCE(finished_at, ?) WHERE task_id = ?",
            (updated_at, updated_at, audit_task_id),
        )
        parent_metadata["audit_verdict"] = "failed"
        parent_metadata["rework_count"] = rework_count
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
            payload={"audit_task_id": audit_task_id, "summary": summary,
                     "rework_count": rework_count, "max_rework_retries": MAX_REWORK_RETRIES},
        )
        conn.commit()
        return fetch_task_snapshots(conn, task_id=audit_task_id)[0]

    # Within limit — auto-requeue parent with audit findings as rework context
    parent_metadata["audit_verdict"] = "rework_required"
    parent_metadata["rework_count"] = rework_count
    parent_metadata["rework_context"] = summary  # injected into worker prompt
    conn.execute(
        """
        UPDATE tasks
        SET planner_status = 'todo',
            version = ?,
            worker_owner = NULL,
            updated_at = ?,
            metadata_json = ?
        WHERE task_id = ? AND version = ?
        """,
        (parent_version + 1, parent_updated_at, compact_json(parent_metadata), parent_task_id, parent_version),
    )
    insert_event(
        conn,
        task_id=parent_task_id,
        event_type="planner.task_auto_rework",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={"audit_task_id": audit_task_id, "rework_count": rework_count,
                 "max_rework_retries": MAX_REWORK_RETRIES, "summary": summary},
    )
    # Requeue parent runtime (reset retry count for fresh attempt).
    # Inline the requeue here — we're already inside BEGIN IMMEDIATE so we
    # cannot call runtime_requeue_task (which would issue its own BEGIN).
    # Use INSERT OR IGNORE to create the runtime state row if it doesn't exist
    # (e.g. parent was never claimed through the normal dispatch path).
    _requeue_at = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO task_runtime_state
            (task_id, runtime_status, last_transition_at, retry_count, runtime_metadata_json)
        VALUES (?, 'queued', ?, 0, '{}')
        """,
        (parent_task_id, _requeue_at),
    )
    conn.execute(
        """
        UPDATE task_runtime_state
        SET runtime_status = 'queued',
            last_runtime_error = NULL,
            finished_at = NULL,
            pending_review_at = NULL,
            retry_count = 0,
            last_transition_at = ?
        WHERE task_id = ?
        """,
        (_requeue_at, parent_task_id),
    )
    insert_event(
        conn,
        task_id=parent_task_id,
        event_type="runtime.requeued",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={
            "summary": f"auto-rework #{rework_count}: {summary[:300]}",
            "had_active_lease": False,
            "reset_retry_count": True,
        },
    )

    # Reset audit task to todo so it re-runs after the rework lands
    audit_reopen_version = int(fetch_task_row(conn, audit_task_id)["version"])
    audit_metadata_reopen = parse_json_text(fetch_task_row(conn, audit_task_id)["metadata_json"], default={})
    audit_metadata_reopen.pop("closeout", None)
    conn.execute(
        """
        UPDATE tasks
        SET planner_status = 'todo',
            worker_owner = NULL,
            updated_at = ?,
            metadata_json = ?
        WHERE task_id = ? AND version = ?
        """,
        (parent_updated_at, compact_json(audit_metadata_reopen), audit_task_id, audit_reopen_version),
    )
    conn.execute(
        "UPDATE task_runtime_state SET runtime_status = 'queued', last_transition_at = ?, finished_at = NULL, last_runtime_error = NULL, retry_count = 0 WHERE task_id = ?",
        (parent_updated_at, audit_task_id),
    )
    insert_event(
        conn,
        task_id=audit_task_id,
        event_type="planner.audit_reset_for_rework",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={"rework_count": rework_count, "parent_task_id": parent_task_id},
    )

    conn.commit()
    return fetch_task_snapshots(conn, task_id=audit_task_id)[0]


def reconcile_audit_pass(
    conn,
    *,
    audit_task_id: str,
    summary: str,
    actor_id: str,
    worker_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Handle audit verdict=accepted (or any non-rework verdict that closes the audit as done).

    - Close the audit task as done.
    - Close the parent task (awaiting_audit → done) and align its runtime_status.
    Returns the audit task snapshot.
    """
    begin_immediate(conn)
    audit_row = fetch_task_row(conn, audit_task_id)
    if audit_row is None:
        conn.rollback()
        raise RuntimeError(f"audit task not found: {audit_task_id}")

    audit_metadata = parse_json_text(audit_row["metadata_json"], default={})
    parent_task_id = str(audit_metadata.get("parent_task_id") or "")
    updated_at = now_iso()
    audit_version = int(audit_row["version"])

    parent_row = fetch_task_row(conn, parent_task_id) if parent_task_id else None
    parent_version = int(parent_row["version"]) if parent_row is not None else None
    try:
        capability_closeout = apply_audit_capability_mutations(
            conn,
            audit_task_id=audit_task_id,
            parent_task_id=parent_task_id or None,
            parent_version=parent_version,
            actor_id=actor_id,
            worker_result=worker_result,
        )
    except SystemExit as exc:
        conn.rollback()
        raise RuntimeError("audit acceptance blocked by invalid or missing capability mutation payload") from exc

    # Close the audit as done
    audit_metadata["closeout"] = {
        "outcome": "done",
        "summary": summary,
        "notes": "audit verdict: accepted",
        "reconciled_at": updated_at,
        "actor_id": actor_id,
        "capability_mutation_applied": bool(capability_closeout["capability_mutations"]),
        "capability_emission_required": bool(capability_closeout["capability_emission_required"]),
    }
    conn.execute(
        """
        UPDATE tasks
        SET planner_status = 'done',
            version = ?,
            worker_owner = NULL,
            updated_at = ?,
            closed_at = ?,
            metadata_json = ?
        WHERE task_id = ? AND version = ?
        """,
        (audit_version + 1, updated_at, updated_at, compact_json(audit_metadata), audit_task_id, audit_version),
    )
    conn.execute(
        "UPDATE task_runtime_state SET runtime_status = 'done', last_transition_at = ?, finished_at = COALESCE(finished_at, ?) WHERE task_id = ?",
        (updated_at, updated_at, audit_task_id),
    )
    insert_event(
        conn,
        task_id=audit_task_id,
        event_type="planner.task_reconciled",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={"outcome": "done", "summary": summary, "tests": None, "artifacts": []},
    )

    if not parent_task_id:
        conn.commit()
        return fetch_task_snapshots(conn, task_id=audit_task_id)[0]

    if parent_row is None or str(parent_row["planner_status"]) != "awaiting_audit":
        conn.commit()
        return fetch_task_snapshots(conn, task_id=audit_task_id)[0]

    parent_metadata = parse_json_text(parent_row["metadata_json"], default={})
    parent_version = int(parent_row["version"])
    parent_metadata["audit_verdict"] = "accepted"
    parent_updated_at = now_iso()
    conn.execute(
        """
        UPDATE tasks
        SET planner_status = 'done',
            version = ?,
            worker_owner = NULL,
            updated_at = ?,
            closed_at = ?,
            metadata_json = ?
        WHERE task_id = ? AND version = ?
        """,
        (parent_version + 1, parent_updated_at, parent_updated_at, compact_json(parent_metadata), parent_task_id, parent_version),
    )
    conn.execute(
        """UPDATE task_runtime_state
           SET runtime_status = 'done', last_transition_at = ?, finished_at = COALESCE(finished_at, ?)
           WHERE task_id = ? AND runtime_status NOT IN ('done', 'canceled')""",
        (parent_updated_at, parent_updated_at, parent_task_id),
    )
    insert_event(
        conn,
        task_id=parent_task_id,
        event_type="planner.task_closed_by_audit",
        actor_kind="runtime",
        actor_id=actor_id,
        payload={"audit_task_id": audit_task_id, "summary": summary},
    )
    activate_task_capability(
        conn,
        task_id=parent_task_id,
        actor_kind="runtime",
        actor_id=actor_id,
        closeout_artifacts=[],
        source="audit-pass",
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


def active_repo_worker_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT t.target_repo_id AS repo_id, COUNT(*) AS active_count
        FROM task_active_leases l
        JOIN tasks t ON t.task_id = l.task_id
        GROUP BY t.target_repo_id
        """
    ).fetchall()
    return {
        str(row["repo_id"]): int(row["active_count"])
        for row in rows
    }


def _session_lock_allows_dispatch(
    snapshot: dict[str, Any],
    session_locks: dict[tuple[str, str], str],
) -> bool:
    """Return True if the task may be dispatched given current session locks.

    A task is blocked only when ALL of:
    - The repo has ``session_persistence_enabled``
    - The task has a ``session_focus``
    - The task is not an audit
    - The (repo_id, focus) session is currently locked by another task
    """
    if not session_locks:
        return True
    repo_meta = snapshot.get("repo_metadata") or {}
    if not repo_meta.get("session_persistence_enabled"):
        return True
    task_focus = str((snapshot.get("metadata") or {}).get("session_focus") or "")
    if not task_focus:
        return True
    if str(snapshot.get("task_type") or "").strip().lower() == "audit":
        return True
    return (str(snapshot["target_repo_id"]), task_focus) not in session_locks


def runtime_claim(
    conn: sqlite3.Connection,
    *,
    worker_id: str,
    queue_name: str,
    lease_seconds: int,
    task_id: str | None,
    actor_id: str,
    remote_only: bool | None = None,
    raise_on_empty: bool = True,
    session_locks: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any] | None:
    begin_immediate(conn)
    snapshots = fetch_task_snapshots(conn, task_id=task_id) if task_id else fetch_task_snapshots(conn)
    ordered = order_eligible_snapshots(snapshots, remote_only=remote_only)
    if task_id is not None:
        ordered = [snapshot for snapshot in ordered if snapshot["task_id"] == task_id]
    active_counts = active_repo_worker_counts(conn)
    ordered = [
        snapshot
        for snapshot in ordered
        if active_counts.get(str(snapshot["target_repo_id"]), 0)
        < resolve_repo_max_concurrent_workers(snapshot.get("repo_metadata") or {})
    ]
    # Session lock filtering: skip tasks whose resume-in-place session is held
    if session_locks:
        ordered = [s for s in ordered if _session_lock_allows_dispatch(s, session_locks)]
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
    exit_code: int | None = None,
    exit_category: str | None = None,
    tokens_used: int | None = None,
    tokens_cost_usd: float | None = None,
    runtime_metadata: dict[str, Any] | None = None,
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
    # Never regress from a clean terminal state. If the row is already done or
    # canceled, any attempt to overwrite with timeout/failed/etc is a stale
    # write (e.g. dispatcher timeout firing after the worker already finished).
    _CLEAN_TERMINAL = {"done", "canceled"}
    if current.get("runtime_status") in _CLEAN_TERMINAL and status not in _CLEAN_TERMINAL:
        conn.rollback()
        return current
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
    # Preserve existing exit_code/tokens unless caller provides new values.
    resolved_exit_code = exit_code if exit_code is not None else current.get("exit_code")
    resolved_exit_category = exit_category if exit_category is not None else current.get("exit_category")
    resolved_tokens_used = tokens_used if tokens_used is not None else current.get("tokens_used")
    resolved_tokens_cost_usd = tokens_cost_usd if tokens_cost_usd is not None else current.get("tokens_cost_usd")
    merged_runtime_metadata: dict[str, Any] = (
        parse_json_text(current.get("runtime_metadata_json"), default={}) or {}
    )
    if runtime_metadata is None:
        runtime_metadata = {}
    if not isinstance(runtime_metadata, dict):
        conn.rollback()
        die("runtime_metadata must be a JSON object")
    merged_runtime_metadata.update(runtime_metadata)
    if notes is not None:
        merged_runtime_metadata["notes"] = notes
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
            worker_model_source = ?,
            exit_code = ?,
            exit_category = ?,
            tokens_used = ?,
            tokens_cost_usd = ?
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
            compact_json(merged_runtime_metadata),
            resolved_model,
            resolved_source,
            resolved_exit_code,
            resolved_exit_category,
            resolved_tokens_used,
            resolved_tokens_cost_usd,
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
        "initiative": "one-off",
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
            "timeout_seconds": parse_int(execution.get("Timeout Seconds", 3600), field=f"{task_id}.Timeout Seconds"),
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
            "initiative": "one-off",
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
                "timeout_seconds": 3600,
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


def _health_enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] | None = None
    try:
        parsed = json.loads(str(row.get("report_json") or ""))
        if isinstance(parsed, dict):
            report = parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        report = None
    if report is None:
        return row

    repo = report.get("repo") or {}
    coverage = report.get("coverage") or {}
    metadata = report.get("metadata") or {}
    test_run = metadata.get("test_run") or {}
    counts = test_run.get("counts")
    if not isinstance(counts, dict):
        counts = metadata.get("counts") if isinstance(metadata.get("counts"), dict) else {}

    row["report"] = report
    row["repo_root"] = repo.get("repo_root")
    row["display_name"] = repo.get("display_name")
    row["runner"] = test_run.get("runner") or metadata.get("runner")
    row["test_summary"] = {
        "runner": row.get("runner"),
        "exit_code": test_run.get("exit_code"),
        "counts": counts,
        "coverage_percent": coverage.get("measured_percent"),
        "coverage_status": coverage.get("status"),
    }
    return row


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
    rows = [_health_enrich_row(row) for row in rows]

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
        metadata = None if args.metadata_json is None else parse_json_text(args.metadata_json, default={})
        if args.max_concurrent_workers is not None:
            if metadata is None:
                existing = fetch_repo_payload(conn, args.repo_id)
                metadata = dict(existing.get("metadata") or {}) if existing is not None else {}
            metadata[REPO_MAX_CONCURRENT_WORKERS_METADATA_KEY] = parse_positive_int(
                args.max_concurrent_workers,
                field="--max-concurrent-workers",
            )
        with conn:
            ensure_repo(
                conn,
                repo_id=args.repo_id,
                repo_root=args.repo_root,
                display_name=args.display_name or args.repo_id,
                metadata=metadata,
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


_CAPABILITY_CREATE_TEMPLATE = {
    "capability_id": "dispatcher_parked_task_visibility",
    "name": "Dispatcher parked task visibility",
    "summary": "Dispatcher status surfaces parked non-eligible tasks.",
    "status": "active",
    "kind": "reporting_surface",
    "scope_kind": "workflow",
    "owning_repo_id": "CENTRAL",
    "affected_repo_ids": ["CENTRAL"],
    "when_to_use_md": "Use when triaging queue state and non-eligible work.",
    "do_not_use_for_md": "Do not treat as scheduler policy output.",
    "entrypoints": ["scripts/dispatcher_control.py status"],
    "keywords": ["dispatcher", "visibility", "parked"],
    "evidence_summary_md": "Seeded from accepted implementation work.",
    "verification_level": "planner_verified",
    "verified_by_task_id": "CENTRAL-OPS-0000",
    "metadata": {"bootstrap_mode": True},
    "source_tasks": [{"task_id": "CENTRAL-OPS-0000", "relationship_kind": "seeded_from"}],
}


def command_capability_list(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        repo_id = resolve_repo_filter(conn, args.repo_id)
        payload = fetch_capability_registry(
            conn,
            repo_id=repo_id,
            status=args.status,
            kind=args.kind,
            verification_level=args.verification_level,
        )
    finally:
        conn.close()
    return print_or_json(payload, as_json=args.json, formatter=render_capability_rows)


def command_capability_show(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        payload = fetch_capability_payload(conn, normalize_capability_id(args.capability_id))
    finally:
        conn.close()
    if payload is None:
        die(f"capability not found: {args.capability_id}")
    return print_or_json(payload, as_json=args.json, formatter=render_capability_detail)


def command_capability_create(args: argparse.Namespace) -> int:
    if args.template:
        print(json_dumps(_CAPABILITY_CREATE_TEMPLATE))
        return 0
    if not args.input:
        die("--input is required when --template is not set")
    payload = load_json_document(args.input)
    conn, _ = open_initialized_connection(args.db_path)
    try:
        with conn:
            created = create_capability(
                conn,
                payload,
                actor_kind=args.actor_kind,
                actor_id=args.actor_id,
            )
    finally:
        conn.close()
    return print_or_json(created, as_json=args.json, formatter=render_capability_detail)


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
        "initiative": merged.get("initiative"),
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


_TASK_CREATE_TEMPLATE = {
    "task_id": "",
    "title": "",
    "summary": "",
    "objective_md": "",
    "context_md": "",
    "scope_md": "",
    "deliverables_md": "",
    "acceptance_md": "",
    "testing_md": "",
    "dispatch_md": "",
    "closeout_md": "",
    "reconciliation_md": "",
    "planner_status": "pending",
    "priority": 50,
    "task_type": "",
    "planner_owner": "planner/coordinator",
    "worker_owner": None,
    "target_repo_id": "",
    "target_repo_root": "",
    "source_kind": "planner",
    "initiative": None,
    "approval_required": False,
    "metadata": {},
    "dependencies": [],
    "execution": {
        "task_kind": "",
        "timeout_seconds": 3600,
        "sandbox_mode": "normal",
        "approval_policy": "auto",
    },
}

_TASK_PREFLIGHT_TEMPLATE = {
    "normalized_task_intent": {
        "title": "",
        "summary": "",
        "objective_md": "",
        "scope_md": "",
        "deliverables_md": "",
        "acceptance_md": "",
        "target_repo_id": "CENTRAL",
        "task_type": "implementation",
        "dependency_task_ids": [],
        "dependency_kinds": {},
        "parent_task_id": None,
        "initiative_key": None,
        "related_repo_ids": [],
        "requested_capability_ids": [],
        "requested_task_ids": [],
        "labels": [],
    },
    "search_scope": {
        "repo_ids": ["CENTRAL"],
        "include_active_tasks": True,
        "include_recent_done_days": 90,
        "include_capabilities": True,
        "include_deprecated_capabilities": True,
        "max_candidates_per_kind": 50,
    },
    "request_context": {
        "requested_by": "planner/coordinator",
        "request_channel": "task-create",
        "is_material_update": False,
        "existing_task_id": None,
        "existing_task_version": None,
    },
}


def command_task_preflight(args: argparse.Namespace) -> int:
    if args.template:
        print(json_dumps(_TASK_PREFLIGHT_TEMPLATE))
        return 0
    if not args.input:
        die("--input is required when --template is not set")
    request = canonicalize_preflight_request(load_json_document(args.input))
    conn, _ = open_initialized_connection(args.db_path)
    try:
        response = build_task_preflight_response(conn, request)
    finally:
        conn.close()
    return print_or_json(response, as_json=True, formatter=json_dumps)


def command_task_create(args: argparse.Namespace) -> int:
    if args.template:
        print(json_dumps(_TASK_CREATE_TEMPLATE))
        return 0
    if not args.input:
        die("--input is required when --template is not set")
    payload = load_json_document(args.input)
    conn, _ = open_initialized_connection(args.db_path)
    try:
        snapshot = create_task_graph(conn, payload, actor_kind="planner", actor_id=args.actor_id, skip_preflight=getattr(args, "skip_preflight", False))
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
    finally:
        conn.close()
    if not args.depends_on:
        print(
            f"NOTE: {task_id} scaffolded with no --depends-on. "
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
        task_type_filter = getattr(args, "task_type", None) or None
        snapshots = fetch_task_snapshots(conn, repo_id=repo_id, planner_status=args.planner_status, initiative=initiative_filter, task_type=task_type_filter)
        rows = [
            {
                "task_id": snapshot["task_id"],
                "priority": snapshot["priority"],
                "planner_status": snapshot["planner_status"],
                "runtime_status": snapshot["runtime"]["runtime_status"] if snapshot["runtime"] else "",
                "repo": snapshot["target_repo_id"],
                "task_type": snapshot.get("task_type", ""),
                "initiative": snapshot["initiative"] or "",
                "planner_owner": snapshot["planner_owner"],
                "worker_owner": snapshot["worker_owner"] or "",
                "version": snapshot["version"],
                "title": snapshot["title"],
                "closed_at": snapshot.get("closed_at"),
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
    had_active_lease = lease is not None
    lease_metadata = parse_json_text(str(lease["lease_metadata_json"]), default={}) if lease is not None else {}
    supervision = lease_metadata.get("supervision") if isinstance(lease_metadata, dict) else None
    worker_pid = (supervision or {}).get("worker_pid")
    worker_process_start_token = (supervision or {}).get("worker_process_start_token")
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
    conn.execute(
        "UPDATE tasks SET planner_status = 'failed', updated_at = ? WHERE task_id = ?",
        (transition_at, task_id),
    )
    insert_event(
        conn,
        task_id=task_id,
        event_type="runtime.operator_stop_requested",
        actor_kind="operator",
        actor_id=actor_id,
        payload={"summary": reason, "had_active_lease": had_active_lease},
    )
    insert_event(
        conn,
        task_id=task_id,
        event_type="planner.operator_stop_reconciled",
        actor_kind="operator",
        actor_id=actor_id,
        payload={"summary": reason, "planner_status": "failed"},
    )
    conn.commit()
    snapshot = fetch_task_snapshots(conn, task_id=task_id)[0]
    return {
        "task_id": task_id,
        "reason": reason,
        "snapshot": render_task_card(snapshot),
        "kill_target": {
            "had_active_lease": had_active_lease,
            "worker_pid": worker_pid,
            "worker_process_start_token": worker_process_start_token,
        },
    }


def build_planner_panel(
    conn: sqlite3.Connection,
    *,
    stale_hours: int = 72,
    changed_since_hours: int = 24,
    limit: int = 20,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """Aggregate the richest single planner view used by the UI."""
    from datetime import datetime, timezone, timedelta
    snapshots = fetch_task_snapshots(conn)
    if repo_id:
        snapshots = [s for s in snapshots if s["target_repo_id"] == repo_id]
    generated_at = now_iso()

    cutoff_dt = (datetime.now(timezone.utc) - timedelta(hours=changed_since_hours)).isoformat()
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(hours=stale_hours)).isoformat()

    eligible_work = []
    awaiting_audit = []
    recent_failures = []
    changed_since: list[dict[str, Any]] = []
    parked_rows: list[dict[str, Any]] = []
    stale_rows: list[dict[str, Any]] = []

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

        if runtime_status == "failed" and planner_status not in {"done", "cancelled"}:
            recent_failures.append({
                **base,
                "last_error": runtime.get("last_runtime_error") or "",
                "retry_count": runtime.get("retry_count", 0),
            })

        if snapshot.get("updated_at") and snapshot["updated_at"] >= cutoff_dt:
            changed_since.append({
                **base,
                "updated_at": snapshot["updated_at"],
            })

        if snapshot["dependency_blocked"] and planner_status in {"todo", "in_progress"}:
            parked_rows.append({**base, "parked_reason": "dependency-blocked"})

        updated_at = snapshot.get("updated_at") or ""
        if (
            planner_status in {"todo", "in_progress"}
            and not snapshot["dependency_blocked"]
            and updated_at < stale_cutoff
        ):
            stale_rows.append({**base, "updated_at": updated_at})

    # ready_audits: audit-type tasks that are eligible
    ready_audits = [t for t in eligible_work if t.get("task_type") == "audit"]

    # Sort eligible by priority
    eligible_work.sort(key=lambda t: t["priority"])
    changed_since.sort(key=lambda t: t.get("updated_at", ""), reverse=True)
    stale_rows.sort(key=lambda t: t.get("updated_at", ""))

    parked_reason_counts: dict[str, int] = {}
    for t in parked_rows:
        r = str(t.get("parked_reason") or "unknown")
        parked_reason_counts[r] = parked_reason_counts.get(r, 0) + 1

    summary = {
        "eligible_count": len(eligible_work),
        "awaiting_audit_count": len(awaiting_audit),
        "failed_audit_count": sum(1 for s in snapshots if s.get("task_type") == "audit" and s["planner_status"] == "failed"),
        "recent_failure_count": len(recent_failures),
        "stale_count": len(stale_rows),
        "changed_since_count": len(changed_since),
    }

    return {
        "generated_at": generated_at,
        "eligible_work": eligible_work[:limit],
        "awaiting_audit": awaiting_audit[:limit],
        "ready_audits": ready_audits[:limit],
        "recent_failures": recent_failures[:limit],
        "changed_since": changed_since[:limit],
        "parked_work": {"rows": parked_rows[:limit], "reason_counts": parked_reason_counts},
        "stale_or_low_activity": stale_rows[:limit],
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


def render_planner_panel_text(panel: dict[str, Any]) -> str:
    lines = ["Planner control panel", "=" * 40]
    s = panel.get("summary") or {}
    lines.append(f"Eligible: {s.get('eligible_count', 0)}  Parked: {s.get('stale_count', 0)}  Failures: {s.get('recent_failure_count', 0)}  Changed: {s.get('changed_since_count', 0)}")
    lines.append("")

    def _section(title: str, rows: list[dict[str, Any]]) -> None:
        lines.append(f"{title}")
        if not rows:
            lines.append("  (none)")
        for r in rows:
            lines.append(f"  {r.get('task_id', '?')}  [{r.get('planner_status', '?')}]  {r.get('title', '')}")
        lines.append("")

    _section("Eligible work:", panel.get("eligible_work") or [])
    _section("Parked work:", (panel.get("parked_work") or {}).get("rows") or [])
    _section("Awaiting audit:", panel.get("awaiting_audit") or [])
    _section("Stale or low activity:", panel.get("stale_or_low_activity") or [])
    _section("Recent failures:", panel.get("recent_failures") or [])
    _section("Changed since:", panel.get("changed_since") or [])
    return "\n".join(lines)


def command_view_planner_panel(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        panel = build_planner_panel(
            conn,
            stale_hours=getattr(args, "stale_hours", 72),
            changed_since_hours=getattr(args, "changed_since_hours", 24),
            limit=getattr(args, "limit", 20),
            repo_id=getattr(args, "repo_id", None) or None,
        )
    finally:
        conn.close()
    return print_or_json(panel, as_json=args.json, formatter=render_planner_panel_text)


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
            reset_retry_count=not getattr(args, "keep_retry_count", False),
        )
    finally:
        conn.close()
    return print_or_json(render_task_card(snapshot), as_json=args.json, formatter=json_dumps)


def command_operator_fail_task(args: argparse.Namespace) -> int:
    conn, _ = open_initialized_connection(args.db_path)
    try:
        result = operator_fail_task(
            conn,
            task_id=args.task_id,
            actor_id=args.actor_id,
            reason=args.reason,
        )
    finally:
        conn.close()
    return print_or_json(result, as_json=args.json, formatter=json_dumps)


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
        repo_upsert_parser.add_argument(
            "--max-concurrent-workers",
            type=int,
            help=(
                "Per-repo runtime claim cap. Stored in repo metadata as "
                f"`{REPO_MAX_CONCURRENT_WORKERS_METADATA_KEY}`."
            ),
        )
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

    capability_list_parser = subparsers.add_parser("capability-list", help="List canonical capability registry entries.")
    add_db_argument(capability_list_parser)
    capability_list_parser.add_argument("--repo-id", help="Filter to capabilities affecting a specific canonical repo or alias.")
    capability_list_parser.add_argument("--status", choices=sorted(CAPABILITY_STATUSES))
    capability_list_parser.add_argument("--kind")
    capability_list_parser.add_argument("--verification-level", choices=sorted(CAPABILITY_VERIFICATION_LEVELS))
    add_json_argument(capability_list_parser)
    capability_list_parser.set_defaults(func=command_capability_list)

    capability_show_parser = subparsers.add_parser("capability-show", help="Show a capability row with scope, provenance, and events.")
    add_db_argument(capability_show_parser)
    capability_show_parser.add_argument("--capability-id", required=True)
    add_json_argument(capability_show_parser)
    capability_show_parser.set_defaults(func=command_capability_show)

    capability_create_parser = subparsers.add_parser(
        "capability-create",
        help="Create a canonical capability row plus scope/provenance join rows. Internal/admin bootstrap helper.",
    )
    add_db_argument(capability_create_parser)
    capability_create_parser.add_argument("--input", help="Path to JSON payload, or - for stdin.")
    capability_create_parser.add_argument("--template", action="store_true", help="Print a skeleton JSON payload and exit 0.")
    capability_create_parser.add_argument("--actor-kind", default="admin")
    capability_create_parser.add_argument("--actor-id", default="planner/coordinator")
    add_json_argument(capability_create_parser)
    capability_create_parser.set_defaults(func=command_capability_create)

    task_preflight_parser = subparsers.add_parser("task-preflight", help="Run canonical capability/task overlap preflight for a proposed task.")
    add_db_argument(task_preflight_parser)
    task_preflight_parser.add_argument("--input", help="Path to JSON payload, or - for stdin.")
    task_preflight_parser.add_argument("--template", action="store_true", help="Print a skeleton preflight request JSON and exit 0.")
    add_json_argument(task_preflight_parser)
    task_preflight_parser.set_defaults(func=command_task_preflight)

    task_create_parser = subparsers.add_parser("task-create", help="Create a planner-owned task from JSON input. Target repos must already be onboarded.")
    add_db_argument(task_create_parser)
    task_create_parser.add_argument("--input", help="Path to JSON payload, or - for stdin.")
    task_create_parser.add_argument("--template", action="store_true", help="Print a skeleton JSON with all required fields and exit 0.")
    task_create_parser.add_argument("--actor-id", default="planner/coordinator")
    task_create_parser.add_argument("--skip-preflight", action="store_true", help="Skip capability preflight check. Use for product repos (non-platform) where capability deduplication is not needed.")
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
    planner_new_parser.add_argument("--timeout-seconds", type=int, default=3600)
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
    task_list_parser.add_argument("--task-type", default=None, help="Filter by task type (e.g., investigation, bugfix, feature).")
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
    view_planner_panel_parser.add_argument("--stale-hours", type=int, default=72, dest="stale_hours", help="Hours before a task is considered stale (default 72).")
    view_planner_panel_parser.add_argument("--changed-since-hours", type=int, default=24, dest="changed_since_hours", help="Hours window for changed-since section (default 24).")
    view_planner_panel_parser.add_argument("--limit", type=int, default=20, help="Max rows per section (default 20).")
    view_planner_panel_parser.add_argument("--repo-id", dest="repo_id", help="Filter to a specific repo.")
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
    runtime_requeue_parser.add_argument("--reset-retry-count", action="store_true", default=True, help="Reset retry_count to 0 before requeue (default: true).")
    runtime_requeue_parser.add_argument("--keep-retry-count", action="store_true", help="Preserve existing retry_count instead of resetting to 0.")
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
