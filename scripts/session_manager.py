#!/usr/bin/env python3
"""Session registry helpers for Claude persistent base sessions."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from central_task_db import DEFAULT_DB_PATH, connect, parse_json_text


CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_SEED_MODEL = "claude-sonnet-4-6"
DEFAULT_REFRESH_AFTER_FORKS = 50
DEFAULT_REFRESH_AFTER_HOURS = 72


@dataclass(frozen=True)
class SessionForkResult:
    args: list[str]
    session_id: str
    stale: bool
    stale_reason: str | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_text() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _load_repo(conn: sqlite3.Connection, repo_id: str) -> tuple[sqlite3.Row, dict[str, Any]] | None:
    row = conn.execute(
        "SELECT repo_id, display_name, repo_root, metadata_json FROM repos WHERE repo_id = ?",
        (repo_id,),
    ).fetchone()
    if row is None:
        return None
    return row, parse_json_text(str(row["metadata_json"]), default={})


def _claude_project_dir_name(repo_root: str | Path) -> str:
    path = Path(repo_root).expanduser().resolve()
    parts = [part for part in path.parts if part not in {path.anchor, "/"}]
    return "-" + "-".join(parts)


def validate_session(session_id: str, repo_root: str | Path | None = None) -> bool:
    candidates: list[Path]
    if repo_root is not None:
        project_dir = CLAUDE_PROJECTS_DIR / _claude_project_dir_name(repo_root)
        candidates = [project_dir / f"{session_id}.jsonl"]
    else:
        candidates = list(CLAUDE_PROJECTS_DIR.glob(f"*/{session_id}.jsonl"))
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size > 0:
                return True
        except OSError:
            continue
    if repo_root is not None:
        return validate_session(session_id)
    return False


def _default_seed_prompt(repo_name: str) -> str:
    return (
        f"You are being initialized as a persistent base session for the {repo_name} repository.\n"
        "Your goal is to build a deep understanding of this codebase that will be inherited by\n"
        "future task workers via session forking.\n\n"
        "Please do the following:\n"
        "1. Read AI_GUIDE.md if it exists at the repo root.\n"
        "2. Explore the top-level directory structure and identify key modules.\n"
        "3. Read the main entry points and understand the application architecture.\n"
        "4. Identify the test infrastructure (test runner, fixture patterns, mock strategies).\n"
        "5. Note any patterns, conventions, or gotchas that a worker should know.\n"
        "6. Summarize your understanding in a structured format.\n\n"
        "Do NOT make any changes to files. This is a read-only exploration session.\n"
    )


def _resolve_prompt(repo_row: sqlite3.Row, meta: dict[str, Any], prompt_file: str | None) -> tuple[str, Path | None]:
    repo_root = Path(str(repo_row["repo_root"])).expanduser().resolve()
    prompt_path: Path | None = None
    if prompt_file:
        prompt_path = Path(prompt_file).expanduser()
        if not prompt_path.is_absolute():
            prompt_path = (repo_root / prompt_path).resolve()
        return prompt_path.read_text(encoding="utf-8"), prompt_path
    meta_prompt = meta.get("session_seed_prompt_file")
    if isinstance(meta_prompt, str) and meta_prompt.strip():
        prompt_path = (repo_root / meta_prompt).resolve()
        return prompt_path.read_text(encoding="utf-8"), prompt_path
    repo_name = str(repo_row["display_name"] or repo_row["repo_id"])
    return _default_seed_prompt(repo_name), None


def _current_prompt_hash(repo_row: sqlite3.Row, meta: dict[str, Any]) -> str | None:
    try:
        prompt_text, _ = _resolve_prompt(repo_row, meta, None)
    except OSError:
        return None
    return sha256(prompt_text.encode("utf-8")).hexdigest()


def _stale_reason(row: sqlite3.Row | dict[str, Any], meta: dict[str, Any], repo_row: sqlite3.Row) -> str | None:
    refresh_after_forks = int(meta.get("session_refresh_after_forks", DEFAULT_REFRESH_AFTER_FORKS) or DEFAULT_REFRESH_AFTER_FORKS)
    refresh_after_hours = int(meta.get("session_refresh_after_hours", DEFAULT_REFRESH_AFTER_HOURS) or DEFAULT_REFRESH_AFTER_HOURS)
    if int(row["fork_count"] or 0) >= refresh_after_forks:
        return f"fork_count_exceeded({refresh_after_forks})"
    completed_at = _parse_timestamp(str(row["seed_completed_at"] or ""))
    if completed_at is not None and _utc_now() - completed_at > timedelta(hours=refresh_after_hours):
        return f"age_exceeded({refresh_after_hours}h)"
    current_prompt_hash = _current_prompt_hash(repo_row, meta)
    if row["seed_prompt_hash"] and current_prompt_hash is None:
        return "prompt_hash_unavailable"
    if row["seed_prompt_hash"] and str(row["seed_prompt_hash"]) != current_prompt_hash:
        return "prompt_hash_changed"
    return None


def _is_stale(row: sqlite3.Row | dict[str, Any], meta: dict[str, Any], repo_row: sqlite3.Row) -> bool:
    return _stale_reason(row, meta, repo_row) is not None


def get_fork_args(repo_id: str, db_path: Path) -> SessionForkResult | None:
    conn = connect(db_path)
    try:
        if not _table_exists(conn, "session_registry"):
            return None
        loaded = _load_repo(conn, repo_id)
        if loaded is None:
            return None
        repo_row, meta = loaded
        if not meta.get("session_persistence_enabled"):
            return None
        rows = conn.execute(
            """
            SELECT session_id, status, fork_count, seed_completed_at, seed_prompt_hash, context_tokens, seed_cwd
            FROM session_registry
            WHERE repo_id = ? AND status IN ('active', 'stale') AND seed_completed_at IS NOT NULL
            ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, seed_completed_at DESC, registry_id DESC
            """,
            (repo_id,),
        ).fetchall()
        for row in rows:
            if not validate_session(str(row["session_id"]), repo_root=str(row["seed_cwd"])):
                validation_note = f"validation_failed:{row['session_id']}"
                with conn:
                    conn.execute(
                        """
                        UPDATE session_registry
                        SET status = 'retired',
                            updated_at = ?,
                            notes = CASE
                                WHEN notes IS NULL OR notes = '' THEN ?
                                ELSE notes || '\n' || ?
                            END
                        WHERE repo_id = ? AND session_id = ?
                        """,
                        (
                            _utc_now_text(),
                            validation_note,
                            validation_note,
                            repo_id,
                            row["session_id"],
                        ),
                    )
                continue

            stale_reason = _stale_reason(row, meta, repo_row)
            forked_at = _utc_now_text()
            with conn:
                conn.execute(
                    """
                    UPDATE session_registry
                    SET fork_count = fork_count + 1,
                        last_forked_at = ?,
                        updated_at = ?
                    WHERE repo_id = ? AND session_id = ?
                    """,
                    (forked_at, forked_at, repo_id, row["session_id"]),
                )
            return SessionForkResult(
                args=["--resume", str(row["session_id"]), "--fork-session"],
                session_id=str(row["session_id"]),
                stale=stale_reason is not None,
                stale_reason=stale_reason,
            )
        return None
    finally:
        conn.close()


def _extract_context_tokens(completed: subprocess.CompletedProcess[str]) -> int | None:
    for stream in (completed.stdout, completed.stderr):
        if not stream:
            continue
        for line in reversed(stream.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = payload.get("usage") or {}
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                if usage.get(key) is not None:
                    return sum(int(usage.get(token_key) or 0) for token_key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return None


def seed_session(
    repo_id: str,
    db_path: Path,
    model: str = DEFAULT_SEED_MODEL,
    prompt_file: str | None = None,
    retire_statuses: tuple[str, ...] = ("active", "stale"),
) -> str:
    conn = connect(db_path)
    try:
        if not _table_exists(conn, "session_registry"):
            raise RuntimeError("session_registry table does not exist")
        loaded = _load_repo(conn, repo_id)
        if loaded is None:
            raise RuntimeError(f"unknown repo: {repo_id}")
        repo_row, meta = loaded
        prompt_text, resolved_prompt_path = _resolve_prompt(repo_row, meta, prompt_file)
        session_id = str(uuid4())
        repo_root = Path(str(repo_row["repo_root"])).expanduser().resolve()
        notes = str(resolved_prompt_path) if resolved_prompt_path is not None else "default_prompt"
        with conn:
            conn.execute(
                """
                INSERT INTO session_registry (
                    repo_id, session_id, session_name, status, seed_model, seed_cwd, seed_prompt_hash, notes, created_at, updated_at
                )
                VALUES (?, ?, ?, 'seeding', ?, ?, ?, ?, ?, ?)
                """,
                (
                    repo_id,
                    session_id,
                    f"{repo_root.name}-base-{_utc_now().strftime('%Y%m%d%H%M%S')}",
                    model,
                    str(repo_root),
                    sha256(prompt_text.encode("utf-8")).hexdigest(),
                    notes,
                    _utc_now_text(),
                    _utc_now_text(),
                ),
            )
        command = [
            "claude",
            "--name",
            f"{repo_root.name}-base",
            "--session-id",
            session_id,
            "--model",
            model,
            "--dangerously-skip-permissions",
            "-p",
        ]
        try:
            completed = subprocess.run(
                command,
                input=prompt_text,
                text=True,
                capture_output=True,
                cwd=str(repo_root),
                check=False,
            )
        except Exception:
            with conn:
                conn.execute(
                    "DELETE FROM session_registry WHERE repo_id = ? AND session_id = ?",
                    (repo_id, session_id),
                )
            raise
        if completed.returncode != 0:
            with conn:
                conn.execute(
                    "DELETE FROM session_registry WHERE repo_id = ? AND session_id = ?",
                    (repo_id, session_id),
                )
            raise RuntimeError((completed.stderr or completed.stdout or "session seed failed").strip())
        context_tokens = _extract_context_tokens(completed)
        completed_at = _utc_now_text()
        with conn:
            if retire_statuses:
                placeholders = ", ".join("?" for _ in retire_statuses)
                conn.execute(
                    f"""
                    UPDATE session_registry
                    SET status = 'retired',
                        updated_at = ?
                    WHERE repo_id = ? AND session_id != ? AND status IN ({placeholders})
                    """,
                    (completed_at, repo_id, session_id, *retire_statuses),
                )
            conn.execute(
                """
                UPDATE session_registry
                SET status = 'active',
                    seed_completed_at = ?,
                    context_tokens = ?,
                    updated_at = ?
                WHERE repo_id = ? AND session_id = ?
                """,
                (completed_at, context_tokens, completed_at, repo_id, session_id),
            )
        return session_id
    finally:
        conn.close()


def refresh_session(repo_id: str, db_path: Path, model: str = DEFAULT_SEED_MODEL, prompt_file: str | None = None) -> str:
    conn = connect(db_path)
    try:
        if not _table_exists(conn, "session_registry"):
            raise RuntimeError("session_registry table does not exist")
        retired_at = _utc_now_text()
        with conn:
            conn.execute(
                """
                UPDATE session_registry
                SET status = 'stale',
                    updated_at = ?
                WHERE repo_id = ? AND status = 'active'
                """,
                (retired_at, repo_id),
            )
    finally:
        conn.close()
    return seed_session(
        repo_id,
        db_path,
        model=model,
        prompt_file=prompt_file,
        retire_statuses=("stale",),
    )


def list_sessions(db_path: Path, repo_id: str | None = None) -> list[dict[str, Any]]:
    conn = connect(db_path)
    try:
        if not _table_exists(conn, "session_registry"):
            return []
        sql = """
            SELECT registry_id, repo_id, session_id, session_name, status, seed_started_at, seed_completed_at,
                   last_forked_at, fork_count, context_tokens, seed_model, seed_cwd, seed_prompt_hash, notes,
                   created_at, updated_at
            FROM session_registry
        """
        params: tuple[Any, ...] = ()
        if repo_id is not None:
            sql += " WHERE repo_id = ?"
            params = (repo_id,)
        sql += " ORDER BY repo_id, seed_started_at DESC, registry_id DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed_parser = subparsers.add_parser("seed")
    seed_parser.add_argument("--repo", required=True)
    seed_parser.add_argument("--model", default=DEFAULT_SEED_MODEL)
    seed_parser.add_argument("--prompt-file")

    refresh_parser = subparsers.add_parser("refresh")
    refresh_parser.add_argument("--repo", required=True)
    refresh_parser.add_argument("--model", default=DEFAULT_SEED_MODEL)
    refresh_parser.add_argument("--prompt-file")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--repo")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    db_path = Path(args.db_path).expanduser().resolve()
    if args.command == "seed":
        session_id = seed_session(args.repo, db_path, model=args.model, prompt_file=args.prompt_file)
        print(json.dumps({"repo_id": args.repo, "session_id": session_id}, indent=2))
        return 0
    if args.command == "refresh":
        session_id = refresh_session(args.repo, db_path, model=args.model, prompt_file=args.prompt_file)
        print(json.dumps({"repo_id": args.repo, "session_id": session_id}, indent=2))
        return 0
    sessions = list_sessions(db_path, repo_id=args.repo)
    print(json.dumps(sessions, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
