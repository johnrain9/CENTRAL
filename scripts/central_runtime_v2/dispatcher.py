"""CentralDispatcher: the main dispatcher daemon and worker lifecycle manager.

This module is a direct adaptation of the CentralDispatcher class from
central_runtime.py, with _finalize_worker decomposed into three private
helpers and imports updated to the central_runtime_v2 package.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import socket
import subprocess
import threading
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from central_runtime_v2.config import (
    ActiveWorker,
    DEFAULT_CODEX_MODEL,
    DispatcherConfig,
    RuntimePaths,
    snapshot_retry_count,
)
from central_runtime_v2.coordination import CoordinationConfig, CoordinationServer
from central_runtime_v2.paths import (
    acquire_lock,
    build_runtime_paths,
    ensure_runtime_dirs,
    release_lock,
    utc_now,
)
from central_runtime_v2.log import DaemonLog
from central_runtime_v2.model_policy import (
    build_worker_task,
    normalize_optional_string,
    resolve_task_worker_backend,
)
from central_runtime_v2.backends import (
    get_worker_backend,
    load_autonomy_runner,
    normalize_claude_result,
)
from central_runtime_v2.observation import (
    ACTIVE_WORKER_RUNTIME_STATUSES,
    add_artifacts,
    iso_or_none,
    parse_timestamp,
    success_runtime_status,
    summarize_validation_results,
)

# Ensure scripts/ is importable so central_task_db can be found.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import central_task_db as task_db

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_HANDOFF_LEASE_SECONDS = 90
HEALTH_SNAPSHOT_SCRIPT = Path(__file__).resolve().parent.parent / "repo_health_check.py"

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def future_utc(seconds: float) -> str:
    return datetime.fromtimestamp(time.time() + seconds, tz=timezone.utc).replace(microsecond=0).isoformat()


def _title_kv(task: dict) -> str:
    """Return a log-safe `title="..."` field string, or empty string if no title."""
    raw = str(task.get("title") or "").replace('"', "'")
    return f' title="{raw}"' if raw else ""


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def process_start_token(pid: int | None) -> str | None:
    if not pid or pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    end = raw.rfind(")")
    if end < 0:
        return None
    fields = raw[end + 1 :].strip().split()
    if len(fields) <= 19:
        return None
    return fields[19]


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def process_matches(pid: int | None, expected_start_token: str | None) -> bool:
    if not _pid_alive(pid):
        return False
    if not expected_start_token:
        return True
    current = process_start_token(pid)
    return current == expected_start_token if current is not None else False


def terminate_process(
    pid: int | None,
    proc: subprocess.Popen[str] | None,
    pgid: int | None = None,
) -> None:
    """Send SIGTERM to the worker's process group, then SIGKILL after a grace period.

    Using start_new_session=True at spawn means the worker PID is also the PGID,
    so killing the group terminates cargo builds, claude subprocesses, and any other
    children that would otherwise become orphans.
    """
    # Resolve pgid: prefer the stored value, fall back to looking up from proc/pid.
    _pgid = pgid
    if _pgid is None and proc is not None:
        try:
            _pgid = os.getpgid(proc.pid)
        except OSError:
            pass
    if _pgid is None and pid and pid > 0:
        try:
            _pgid = os.getpgid(pid)
        except OSError:
            pass

    if _pgid and _pgid > 0:
        try:
            os.killpg(_pgid, signal.SIGTERM)
        except OSError:
            pass

        def _deferred_sigkill(pgid: int) -> None:
            time.sleep(5)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass

        threading.Thread(target=_deferred_sigkill, args=(_pgid,), daemon=True).start()
    elif pid and pid > 0:
        # Fallback: plain SIGTERM if we couldn't determine a pgid.
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _kill_orphan_pgid(pgid: int | None, logger: Any, task_id: str) -> None:
    """Kill an orphaned process group left behind by a dead worker.

    Called during startup adoption when the worker PID is gone but the process
    group may still contain orphaned children (e.g. cargo test, claude CLI).
    """
    if not pgid or pgid <= 0:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
        logger.emit("INF", "central.dispatcher", f"orphan_pgid_sigterm pgid={pgid} task={task_id}")
    except OSError:
        return
    # Deferred SIGKILL for anything that ignored SIGTERM.
    def _deferred(pgid: int) -> None:
        time.sleep(5)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
    threading.Thread(target=_deferred, args=(pgid,), daemon=True).start()


def classify_exit_code(
    returncode: int | None,
    *,
    timed_out: bool = False,
    error_text: str | None = None,
) -> tuple[int | None, str | None]:
    """Return (exit_code, exit_category) for storage in task_runtime_state.

    Categories:
      success      - clean exit (returncode 0) with result written
      timeout      - dispatcher killed the worker for exceeding timeout_seconds
      quota        - quota / rate-limit exhaustion detected from exit code or error text
      operator_kill - SIGKILL from the dispatcher or operator (returncode -9 / 137)
      code_error   - any other non-zero exit
    """
    if timed_out:
        return returncode, "timeout"
    if returncode is None:
        return None, None
    if returncode == 0:
        return 0, "success"
    if returncode in (-9, 137):
        return returncode, "operator_kill"
    err_lower = (error_text or "").lower()
    if any(kw in err_lower for kw in ("quota", "rate_limit", "rate limit", "overloaded", "529")):
        return returncode, "quota"
    return returncode, "code_error"


def connect_initialized(db_path: Path):
    conn = task_db.connect(db_path)
    task_db.require_initialized_db(conn, db_path)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def derive_worker_id(task_id: str) -> str:
    suffix = int(time.time() * 1000)
    return f"central-worker:{socket.gethostname()}:{os.getpid()}:{task_id}:{suffix}"


# ---------------------------------------------------------------------------
# CentralDispatcher
# ---------------------------------------------------------------------------


class CentralDispatcher:
    def __init__(self, config: DispatcherConfig):
        self.config = config
        self.paths = build_runtime_paths(config.state_dir)
        ensure_runtime_dirs(self.paths)
        self.logger = DaemonLog(self.paths.log_path)
        self._running = False
        self._stop_requested = False
        self._force_stop = False
        self._active: dict[str, ActiveWorker] = {}
        self._active_lock = threading.Lock()
        self._last_recovery_monotonic = 0.0
        self._last_status_heartbeat_monotonic = 0.0
        self._capacity_backoff_until: float = 0.0
        self._spark_quota_exhausted: bool = False
        self._health_snapshot_last_run: dict[str, float] = {}
        self._notify_queue: list[dict] = []
        self._notify_last_sent: float = 0.0
        self._notify_batch_seconds: float = 300.0  # max one notification per 5 min
        self._started_at: float = time.time()
        self._coordination_server: CoordinationServer | None = None
        self._cycle_count: int = 0
        self._worker_prev_log_sizes: dict[str, int] = {}

    # ------------------------------------------------------------------
    # DispatcherBridge protocol (used by CoordinationServer)
    # ------------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.config.db_path

    @property
    def dispatcher_config(self) -> DispatcherConfig:
        return self.config

    @property
    def active_workers(self) -> dict[str, ActiveWorker]:
        return self._active

    @property
    def active_lock(self) -> threading.Lock:
        return self._active_lock

    def dispatcher_version(self) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(Path(__file__).resolve().parent.parent.parent), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            return "unknown"

    def dispatcher_id(self) -> str:
        return socket.gethostname()

    def started_at(self) -> float:
        return self._started_at

    def _capacity_backoff_active(self) -> bool:
        """Return True if the dispatcher is currently in a capacity backoff window."""
        return time.monotonic() < self._capacity_backoff_until

    def _signal_handler(self, _signum: int, _frame: Any) -> None:
        if not self._stop_requested:
            self._stop_requested = True
            self.logger.emit("INF", "central.dispatcher", "dispatcher_stopping")
        else:
            self._force_stop = True
            self.logger.emit("INF", "central.dispatcher", "dispatcher_force_stop_requested")

    def _setup_signals(self) -> None:
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _connect(self):
        return connect_initialized(self.config.db_path)

    def _lease_seconds(self) -> int:
        return max(5, int(self.config.heartbeat_seconds * 3))

    def _worker_elapsed_seconds(self, state: ActiveWorker) -> float:
        if state.start_monotonic is not None:
            return max(0.0, time.monotonic() - state.start_monotonic)
        if state.started_at is not None:
            return max(0.0, (datetime.now(timezone.utc) - state.started_at).total_seconds())
        return 0.0

    def _worker_is_running(self, state: ActiveWorker) -> bool:
        if state.proc is not None and state.proc.poll() is not None:
            return False
        return process_matches(state.pid, state.process_start_token)

    def _close_worker_state(self, state: ActiveWorker) -> None:
        if state.log_handle is not None:
            try:
                state.log_handle.close()
            except Exception:
                pass
            state.log_handle = None

    def _supervision_payload(self, state: ActiveWorker) -> dict[str, Any]:
        payload = {
            "run_id": state.run_id,
            "worker_pid": state.pid,
            "worker_pgid": state.pgid,
            "worker_process_start_token": state.process_start_token,
            "worker_mode": self.config.worker_mode,
            "prompt_path": str(state.prompt_path),
            "result_path": str(state.result_path),
            "log_path": str(state.log_path),
            "repo_root": str(state.task["target_repo_root"]),
            "timeout_seconds": state.timeout_seconds,
            "started_at": iso_or_none(state.started_at),
            "dispatcher_pid": os.getpid(),
            "dispatcher_host": socket.gethostname(),
            "updated_at": utc_now(),
            "adopted": state.adopted,
        }
        if state.selected_worker_model is not None:
            payload["worker_model"] = state.selected_worker_model
            # Backward compat: also write codex_model for codex backend
            if state.selected_worker_backend == "codex":
                payload["codex_model"] = state.selected_worker_model
        if state.selected_worker_model_source is not None:
            payload["worker_model_source"] = state.selected_worker_model_source
            if state.selected_worker_backend == "codex":
                payload["codex_model_source"] = state.selected_worker_model_source
        if state.selected_worker_backend is not None:
            payload["worker_backend"] = state.selected_worker_backend
        return payload

    def _sync_worker_lease(
        self,
        state: ActiveWorker,
        *,
        lease_seconds: int | None = None,
        metadata_updates: dict[str, Any] | None = None,
        event_type: str | None = None,
        event_payload: dict[str, Any] | None = None,
        actor_id: str = "central.dispatcher",
    ) -> None:
        conn = self._connect()
        try:
            task_db.begin_immediate(conn)
            lease = task_db.fetch_active_lease(conn, state.task["task_id"])
            if lease is None:
                conn.rollback()
                raise RuntimeError(f"no active lease for {state.task['task_id']}")
            if str(lease["lease_owner_id"]) != state.worker_id:
                conn.rollback()
                raise RuntimeError(
                    f"lease owner mismatch for {state.task['task_id']}: expected {lease['lease_owner_id']}, got {state.worker_id}"
                )
            metadata = task_db.parse_json_text(lease["lease_metadata_json"], default={})
            set_clauses: list[str] = []
            params: list[Any] = []
            if metadata_updates is not None:
                metadata.update(metadata_updates)
                set_clauses.append("lease_metadata_json = ?")
                params.append(task_db.compact_json(metadata))
            heartbeat_at = None
            lease_expires_at = None
            if lease_seconds is not None:
                heartbeat_at = utc_now()
                lease_expires_at = future_utc(lease_seconds)
                set_clauses.extend(["lease_expires_at = ?", "last_heartbeat_at = ?"])
                params.extend([lease_expires_at, heartbeat_at])
            if set_clauses:
                conn.execute(
                    f"UPDATE task_active_leases SET {', '.join(set_clauses)} WHERE task_id = ? AND lease_owner_id = ?",
                    (*params, state.task["task_id"], state.worker_id),
                )
            if event_type:
                task_db.insert_event(
                    conn,
                    task_id=state.task["task_id"],
                    event_type=event_type,
                    actor_kind="runtime",
                    actor_id=actor_id,
                    payload=event_payload or {},
                )
            conn.commit()
        finally:
            conn.close()
        lease_payload = state.task.setdefault("lease", {})
        if metadata_updates is not None:
            current_metadata = lease_payload.get("metadata") or {}
            if isinstance(current_metadata, dict):
                current_metadata.update(metadata_updates)
                lease_payload["metadata"] = current_metadata
        if lease_seconds is not None and heartbeat_at is not None and lease_expires_at is not None:
            lease_payload["last_heartbeat_at"] = heartbeat_at
            lease_payload["lease_expires_at"] = lease_expires_at
            state.last_heartbeat_monotonic = time.monotonic()

    def _persist_worker_supervision(
        self,
        state: ActiveWorker,
        *,
        handoff: dict[str, Any] | None = None,
        event_type: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> None:
        metadata_updates = {"supervision": self._supervision_payload(state)}
        if handoff is not None:
            metadata_updates["handoff"] = handoff
        self._sync_worker_lease(
            state,
            metadata_updates=metadata_updates,
            event_type=event_type,
            event_payload=event_payload,
        )

    def _heartbeat_worker(self, state: ActiveWorker, *, lease_seconds: int | None = None, actor_id: str = "central.dispatcher") -> None:
        self._sync_worker_lease(state, lease_seconds=lease_seconds or self._lease_seconds(), actor_id=actor_id)

    def _handoff_lease_seconds(self, state: ActiveWorker) -> int:
        lease_metadata = ((state.task.get("lease") or {}).get("metadata") or {})
        try:
            current_lease_seconds = int(lease_metadata.get("lease_seconds") or self._lease_seconds())
        except (TypeError, ValueError):
            current_lease_seconds = self._lease_seconds()
        return max(current_lease_seconds, DEFAULT_HANDOFF_LEASE_SECONDS)

    def _dispatcher_snapshot(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            snapshots = task_db.fetch_task_snapshots(conn)
            eligible = task_db.order_eligible_snapshots(snapshots)
            runtime_summary = conn.execute(
                "SELECT runtime_status, COUNT(*) AS c FROM task_runtime_state GROUP BY runtime_status"
            ).fetchall()
            active_leases = int(conn.execute("SELECT COUNT(*) AS c FROM task_active_leases").fetchone()["c"])
            counts = {str(row["runtime_status"]): int(row["c"]) for row in runtime_summary}
            # actionable_failed: only tasks still open from the planner's perspective
            actionable_failed = int(conn.execute(
                "SELECT COUNT(*) AS c FROM task_runtime_state trs"
                " JOIN tasks t ON t.task_id = trs.task_id"
                " WHERE trs.runtime_status = 'failed'"
                " AND t.planner_status NOT IN ('done', 'cancelled')"
            ).fetchone()["c"])
            mismatch_ids = [s["task_id"] for s in snapshots if s.get("status_mismatch")]
        finally:
            conn.close()
        return {
            "eligible_count": len(eligible),
            "next_eligible_task_id": eligible[0]["task_id"] if eligible else None,
            "runtime_counts": counts,
            "actionable_failed": actionable_failed,
            "active_leases": active_leases,
            "mismatch_count": len(mismatch_ids),
            "mismatch_ids": mismatch_ids,
        }

    @staticmethod
    def _format_task_ids(task_ids: list[str], limit: int = 5) -> str:
        if not task_ids:
            return "-"
        if len(task_ids) <= limit:
            return ",".join(task_ids)
        shown = ",".join(task_ids[:limit])
        remaining = len(task_ids) - limit
        return f"{shown},+{remaining}_more"

    def _emit_status_heartbeat(self, *, force: bool = False, elapsed_ms: int | None = None) -> None:
        now = time.monotonic()
        if not force and now - self._last_status_heartbeat_monotonic < self.config.status_heartbeat_seconds:
            return
        snapshot = self._dispatcher_snapshot()
        active_ids = sorted(self._active)
        idle_slots = max(0, self.config.max_workers - len(active_ids))
        backoff_remaining = max(0.0, self._capacity_backoff_until - time.monotonic())
        backoff_str = f" quota_backoff={backoff_remaining:.0f}s" if backoff_remaining > 0 else ""
        elapsed_str = f" elapsed_ms={elapsed_ms}" if elapsed_ms is not None else ""
        done_tasks = snapshot["runtime_counts"].get("done", 0)
        self.logger.emit(
            "INF",
            "central.dispatcher",
            (
                "heartbeat "
                f"cycle={self._cycle_count} "
                f"state={'stopping' if self._stop_requested else 'running'} "
                f"workers={len(active_ids)}/{self.config.max_workers} "
                f"idle_slots={idle_slots} "
                f"running_tasks={self._format_task_ids(active_ids)} "
                f"eligible={snapshot['eligible_count']} "
                f"done={done_tasks} "
                f"next={snapshot['next_eligible_task_id'] or '-'} "
                f"leases={snapshot['active_leases']} "
                f"parked={snapshot['runtime_counts'].get('parked', 0)} "
                f"review={snapshot['runtime_counts'].get('pending_review', 0)} "
                f"failed={snapshot['actionable_failed']} "
                f"mismatch={snapshot['mismatch_count']}"
                f"{backoff_str}"
                f"{elapsed_str}"
            ),
        )
        # Notify if dispatcher is fully stalled: backoff active, no running workers, tasks waiting
        if (
            backoff_remaining > 0
            and len(active_ids) == 0
            and snapshot["eligible_count"] > 0
            and not getattr(self, "_quota_stall_notified", False)
        ):
            self._quota_stall_notified = True
            self._maybe_notify(
                title="⚠️ Dispatcher stalled — quota limit",
                runtime_status="failed",
                summary=f"{snapshot['eligible_count']} tasks waiting, no workers running. Switch backend or wait {backoff_remaining:.0f}s.",
            )
        elif backoff_remaining == 0:
            self._quota_stall_notified = False
        self._last_status_heartbeat_monotonic = now
        # Persist heartbeat snapshot to DB for time-series metrics.
        try:
            hb_conn = self._connect()
            try:
                with hb_conn:
                    hb_conn.execute(
                        "INSERT INTO dispatcher_heartbeat_history"
                        " (captured_at, active_workers, max_workers, queued_count, running_tasks)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (
                            utc_now(),
                            len(active_ids),
                            self.config.max_workers,
                            snapshot["eligible_count"],
                            json.dumps(active_ids),
                        ),
                    )
            finally:
                hb_conn.close()
        except Exception as _hb_exc:
            self.logger.emit("WRN", "central.dispatcher", f"heartbeat_db_write_failed error={_hb_exc}")

    def _claim_next(self) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            return task_db.runtime_claim(
                conn,
                worker_id=derive_worker_id("slot"),
                queue_name="default",
                lease_seconds=max(5, int(self.config.heartbeat_seconds * 3)),
                task_id=None,
                actor_id="central.dispatcher",
                remote_only=False,
                raise_on_empty=False,
            )
        finally:
            conn.close()

    def _spawn_worker(self, snapshot: dict[str, Any]) -> None:
        effective_backend = resolve_task_worker_backend(snapshot, self.config.worker_mode)
        # Use audit-specific model when configured and task is an audit.
        task_type = (snapshot.get("task_type") or "").strip().lower()
        is_audit = task_type == "audit"
        effective_model = self.config.default_worker_model
        if is_audit and self.config.audit_worker_model:
            effective_model = self.config.audit_worker_model
            # If no per-task backend override, infer backend from the audit model name.
            has_task_backend_override = bool(
                (snapshot.get("execution") or {}).get("metadata", {}).get("worker_backend")
            )
            if not has_task_backend_override:
                m = effective_model.lower()
                if m.startswith("claude"):
                    effective_backend = "claude"
                elif m.startswith("gemini"):
                    effective_backend = "gemini"
        worker_task = build_worker_task(
            snapshot,
            effective_model,
            worker_mode=effective_backend,
            dispatcher_default_worker_model=effective_model,
        )
        run_id = (snapshot.get("lease") or {}).get("execution_run_id") or f"{snapshot['task_id']}-{int(time.time())}"
        prompts_dir = self.paths.worker_prompts_dir / snapshot["task_id"]
        results_dir = self.paths.worker_results_dir / snapshot["task_id"]
        logs_dir = self.paths.worker_logs_dir / snapshot["task_id"]
        prompts_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)
        prompt_path = prompts_dir / f"{run_id}.md"
        result_path = results_dir / f"{run_id}.json"
        log_path = logs_dir / f"{run_id}.log"

        backend = get_worker_backend(effective_backend)
        prompt_text, command, stdin_mode = backend.prepare(snapshot, worker_task, run_id, result_path)

        prompt_path.write_text(prompt_text, encoding="utf-8")
        log_handle = log_path.open("a", encoding="utf-8")

        conn = self._connect()
        try:
            with conn:
                runtime_notes = [f"worker_mode={effective_backend}"]
                if worker_task.get("worker_model"):
                    runtime_notes.append(f"model={worker_task['worker_model']}")
                    runtime_notes.append(f"model_source={worker_task.get('worker_model_source', '-')}")
                task_db.runtime_transition(
                    conn,
                    task_id=snapshot["task_id"],
                    status="running",
                    worker_id=(snapshot.get("lease") or {}).get("lease_owner_id"),
                    error_text=None,
                    notes=" ".join(runtime_notes),
                    artifacts=[],
                    actor_id="central.dispatcher",
                    effective_worker_model=worker_task.get("worker_model") or None,
                    worker_model_source=worker_task.get("worker_model_source") or None,
                )
        finally:
            conn.close()

        # Strip ANTHROPIC_API_KEY so claude workers use the OAuth session (Claude Max)
        # rather than a bare API key that may have no credits (e.g. ecosystem test key).
        worker_env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        # Apply backend-specific env overrides (e.g. GrokBackend sets OPENAI_API_KEY from XAI_API_KEY)
        worker_env.update(backend.env_overrides())
        proc = subprocess.Popen(
            command,
            cwd=worker_task["repo_root"],
            stdin=stdin_mode,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env=worker_env,
        )
        if stdin_mode == subprocess.PIPE and proc.stdin is not None:
            proc.stdin.write(prompt_text)
            proc.stdin.close()

        try:
            worker_pgid: int | None = os.getpgid(proc.pid)
        except OSError:
            worker_pgid = None

        started_at = datetime.now(timezone.utc).replace(microsecond=0)
        state = ActiveWorker(
            task=snapshot,
            worker_id=(snapshot.get("lease") or {}).get("lease_owner_id") or derive_worker_id(snapshot["task_id"]),
            run_id=run_id,
            pid=proc.pid,
            proc=proc,
            log_handle=log_handle,
            prompt_path=prompt_path,
            result_path=result_path,
            log_path=log_path,
            process_start_token=process_start_token(proc.pid),
            started_at=started_at,
            start_monotonic=time.monotonic(),
            last_heartbeat_monotonic=time.monotonic(),
            timeout_seconds=int((snapshot.get("execution") or {}).get("timeout_seconds") or 3600),
            selected_worker_model=str(worker_task["worker_model"]) if worker_task.get("worker_model") else None,
            selected_worker_model_source=str(worker_task["worker_model_source"]) if worker_task.get("worker_model_source") else None,
            selected_worker_backend=effective_backend,
            pgid=worker_pgid,
        )
        try:
            self._persist_worker_supervision(state, handoff={})
        except Exception:
            terminate_process(state.pid, state.proc, pgid=state.pgid)
            self._close_worker_state(state)
            raise

        log_parts = [
            f"worker_spawned task={snapshot['task_id']} run={run_id} pid={proc.pid} mode={effective_backend}{_title_kv(snapshot)}",
            f"model={worker_task.get('worker_model', '-')}",
            f"model_source={worker_task.get('worker_model_source', '-')}",
        ]
        if self.config.audit_worker_model:
            log_parts.append(f"impl_model={self.config.default_worker_model}")
            log_parts.append(f"audit_model={self.config.audit_worker_model}")
        self.logger.emit("INF", "central.dispatcher", " ".join(p for p in log_parts if p))
        with self._active_lock:
            self._active[snapshot["task_id"]] = state
        self._emit_status_heartbeat(force=True)

    def _adopt_active_workers(self) -> int:
        conn = self._connect()
        try:
            snapshots = task_db.fetch_task_snapshots(conn)
        finally:
            conn.close()
        adopted = 0
        for snapshot in snapshots:
            runtime = snapshot.get("runtime") or {}
            lease = snapshot.get("lease") or {}
            if snapshot["task_id"] in self._active:
                continue
            if str(runtime.get("runtime_status") or "") not in ACTIVE_WORKER_RUNTIME_STATUSES:
                continue
            if not lease:
                continue
            metadata = lease.get("metadata") or {}
            supervision = metadata.get("supervision") if isinstance(metadata, dict) else None
            if not isinstance(supervision, dict):
                continue
            run_id = str(supervision.get("run_id") or lease.get("execution_run_id") or "")
            worker_pid = supervision.get("worker_pid")
            try:
                pid = int(worker_pid)
            except (TypeError, ValueError):
                continue
            try:
                adopted_pgid: int | None = int(supervision["worker_pgid"])
            except (TypeError, ValueError, KeyError):
                adopted_pgid = None
            prompt_path = Path(str(supervision.get("prompt_path") or self.paths.worker_prompts_dir / snapshot["task_id"] / f"{run_id}.md"))
            result_path = Path(str(supervision.get("result_path") or self.paths.worker_results_dir / snapshot["task_id"] / f"{run_id}.json"))
            log_path = Path(str(supervision.get("log_path") or self.paths.worker_logs_dir / snapshot["task_id"] / f"{run_id}.log"))
            try:
                timeout_seconds = int(supervision.get("timeout_seconds") or (snapshot.get("execution") or {}).get("timeout_seconds") or 3600)
            except (TypeError, ValueError):
                timeout_seconds = int((snapshot.get("execution") or {}).get("timeout_seconds") or 3600)
            state = ActiveWorker(
                task=snapshot,
                worker_id=str(lease.get("lease_owner_id") or runtime.get("claimed_by") or ""),
                run_id=run_id,
                pid=pid,
                proc=None,
                log_handle=None,
                prompt_path=prompt_path,
                result_path=result_path,
                log_path=log_path,
                process_start_token=str(supervision.get("worker_process_start_token") or "") or None,
                started_at=parse_timestamp(str(supervision.get("started_at") or runtime.get("started_at") or runtime.get("claimed_at") or "")),
                start_monotonic=None,
                last_heartbeat_monotonic=0.0,
                timeout_seconds=timeout_seconds,
                adopted=True,
                selected_worker_model=normalize_optional_string(supervision.get("worker_model") or supervision.get("codex_model")),
                selected_worker_model_source=normalize_optional_string(supervision.get("worker_model_source") or supervision.get("codex_model_source")),
                selected_worker_backend=normalize_optional_string(supervision.get("worker_backend") or supervision.get("worker_mode")),
                pgid=adopted_pgid,
            )
            if not process_matches(state.pid, state.process_start_token):
                # Worker PID is gone but its process group may have orphaned children
                # (e.g. cargo builds, claude subprocesses). Kill the group now.
                _kill_orphan_pgid(state.pgid, self.logger, snapshot["task_id"])
                if state.result_path.exists() or state.log_path.exists() or state.prompt_path.exists():
                    handoff_state = (metadata.get("handoff") or {}).get("state", "")
                    was_interrupted = handoff_state in {"interrupted_by_restart", "pending_adoption"}
                    self.logger.emit(
                        "INF",
                        "central.dispatcher",
                        f"worker_reconcile_post_restart task={snapshot['task_id']} run={state.run_id} pid={state.pid} interrupted={was_interrupted}",
                    )
                    self._finalize_worker(state, timed_out=False, interrupted_by_restart=was_interrupted)
                continue
            self._persist_worker_supervision(
                state,
                handoff={
                    "state": "adopted",
                    "adopted_at": utc_now(),
                    "adopted_by_dispatcher_pid": os.getpid(),
                },
                event_type="runtime.worker_adopted",
                event_payload={"run_id": state.run_id, "worker_id": state.worker_id, "worker_pid": state.pid},
            )
            self._heartbeat_worker(state, actor_id="central.dispatcher.adopt")
            with self._active_lock:
                self._active[snapshot["task_id"]] = state
            adopted += 1
            self.logger.emit(
                "INF",
                "central.dispatcher",
                f"worker_adopted task={snapshot['task_id']} run={state.run_id} pid={state.pid}",
            )
        return adopted

    def _prepare_handoff(self) -> None:
        if not self._active:
            return
        handed_off = 0
        for state in list(self._active.values()):
            handoff_lease_seconds = self._handoff_lease_seconds(state)
            handoff = {
                "state": "pending_adoption",
                "requested_at": utc_now(),
                "requested_by_dispatcher_pid": os.getpid(),
                "grace_expires_at": future_utc(handoff_lease_seconds),
            }
            try:
                self._persist_worker_supervision(
                    state,
                    handoff=handoff,
                    event_type="runtime.dispatcher_handoff_requested",
                    event_payload={
                        "run_id": state.run_id,
                        "worker_id": state.worker_id,
                        "worker_pid": state.pid,
                        "grace_expires_at": handoff["grace_expires_at"],
                    },
                )
                self._heartbeat_worker(
                    state,
                    lease_seconds=handoff_lease_seconds,
                    actor_id="central.dispatcher.handoff",
                )
                handed_off += 1
            except Exception as exc:
                self.logger.emit(
                    "INF",
                    "central.dispatcher",
                    f"worker_handoff_error task={state.task['task_id']} run={state.run_id} error={exc}",
                )
        self.logger.emit(
            "INF",
            "central.dispatcher",
            f"dispatcher_handoff_prepared active_workers={handed_off}",
        )

    _SPARK_MODEL = "gpt-5.3-codex-spark"
    _SPARK_FALLBACK_MODEL = "gpt-5.3-codex"

    _CODEX_USAGE_LIMIT_MARKER = "you've hit your usage limit"
    _CLAUDE_QUOTA_MARKERS = (
        "rate limit exceeded",
        "rate_limit_error",
        "too many requests",
        "overloaded_error",
        "529",  # Anthropic overload status
        "quota exceeded",
        "usage limit",
    )

    @staticmethod
    def _detect_codex_capacity_hit(log_path: Any) -> bool:
        """Return True if the codex log file contains a usage-limit signal."""
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace").lower()
            return CentralDispatcher._CODEX_USAGE_LIMIT_MARKER in text
        except Exception:
            return False

    @staticmethod
    def _detect_claude_capacity_hit(log_path: Any) -> bool:
        """Return True if the claude log file contains a rate-limit or quota signal."""
        try:
            text = Path(log_path).read_text(encoding="utf-8", errors="replace").lower()
            return any(m in text for m in CentralDispatcher._CLAUDE_QUOTA_MARKERS)
        except Exception:
            return False

    def _maybe_notify(self, *, title: str, runtime_status: str, summary: str | None = None, task_id: str | None = None) -> None:
        if not self.config.notify:
            return
        # Only notify for actionable failures — success is visible via menu bar pulse.
        if runtime_status in {"done", "pending_review"}:
            return
        self._notify_queue.append({
            "title": title,
            "runtime_status": runtime_status,
            "summary": summary,
            "task_id": task_id,
        })
        self._flush_notify_if_due()

    def _flush_notify_if_due(self) -> None:
        if not self._notify_queue:
            return
        if time.monotonic() - self._notify_last_sent < self._notify_batch_seconds:
            return
        self._send_batched_notification()

    def _send_batched_notification(self) -> None:
        import shutil as _shutil
        import subprocess as _subprocess
        pending = self._notify_queue[:]
        self._notify_queue.clear()
        self._notify_last_sent = time.monotonic()

        done = [n for n in pending if n["runtime_status"] in {"done", "pending_review"}]
        failed = [n for n in pending if n["runtime_status"] not in {"done", "pending_review"}]
        any_failed = bool(failed)

        if len(pending) == 1:
            n = pending[0]
            if n["runtime_status"] in {"done", "pending_review"}:
                notif_title = f"\u2705 {n['title']}"
                sound = "Glass"
            elif n["runtime_status"] == "timeout":
                notif_title = f"\u23f1 {n['title']}"
                sound = "Basso"
            else:
                notif_title = f"\u274c {n['title']}"
                sound = "Basso"
            body = (n["summary"] or n["runtime_status"])[:100]
            open_url = f"http://localhost:7099/#{n['task_id']}" if n.get("task_id") else "http://localhost:7099/"
        else:
            parts = []
            if done:
                parts.append(f"\u2705 {len(done)} done")
            if failed:
                parts.append(f"\u274c {len(failed)} failed")
            notif_title = ", ".join(parts)
            sound = "Basso" if any_failed else "Glass"
            ids = [n["task_id"] for n in pending if n.get("task_id")]
            body = ", ".join(ids[:5]) + ("…" if len(ids) > 5 else "")
            open_url = "http://localhost:7099/"

        if _shutil.which("terminal-notifier"):
            cmd = ["terminal-notifier", "-title", notif_title, "-message", body, "-sound", sound, "-open", open_url]
            _subprocess.run(cmd, check=False)
        else:
            body_esc = body.replace('"', '\\"')
            title_esc = notif_title.replace('"', '\\"')
            _subprocess.run(
                ["osascript", "-e", f'display notification "{body_esc}" with title "{title_esc}" sound name "{sound}"'],
                check=False,
            )

    # ------------------------------------------------------------------
    # _finalize_worker helpers
    # ------------------------------------------------------------------

    def _parse_worker_result(
        self,
        state: ActiveWorker,
        *,
        terminal_artifacts: list[str],
        interrupted_by_restart: bool,
    ) -> tuple[str, str | None, str | None, str | None, Any, dict[str, Any] | None, list[str], list[tuple[str, str, dict[str, Any]]]]:
        """Read the result file and determine runtime_status, notes, error_text, tests, result, artifacts.

        Returns: (runtime_status, notes, error_text, tests, result, raw_result_payload, result_artifacts, extra_artifacts)
        """
        task_id = state.task["task_id"]
        runtime_status = "failed"
        error_text: str | None = None
        notes: str | None = None
        tests: str | None = None
        result = None
        raw_result_payload: dict[str, Any] | None = None
        extra_artifacts: list[tuple[str, str, dict[str, Any]]] = []
        result_artifacts = terminal_artifacts.copy()

        # For Claude workers, claude -p JSON output goes to the log file (stdout=log_handle).
        # result_path is never written by the subprocess itself, so normalize it here before
        # the result_path.exists() check below.
        if not state.result_path.exists() and getattr(state, "selected_worker_backend", None) == "claude":
            if normalize_claude_result(state.log_path, state.result_path, task_id, state.run_id):
                self.logger.emit("INF", "central.dispatcher", f"claude_result_normalized task={task_id} run={state.run_id}")
            else:
                self.logger.emit("INF", "central.dispatcher", f"claude_result_normalize_failed task={task_id} run={state.run_id}")

        if state.result_path.exists():
            result_artifacts.append(str(state.result_path))
            try:
                raw_payload = json.loads(state.result_path.read_text(encoding="utf-8"))
                if isinstance(raw_payload, dict):
                    raw_result_payload = raw_payload
                autonomy_runner = load_autonomy_runner()
                result = autonomy_runner.load_result_file(state.result_path, task_id=task_id, run_id=state.run_id)
                runtime_status = success_runtime_status(state.task) if result.status == "COMPLETED" else "failed"
                notes = result.summary
                tests = summarize_validation_results(result.validation)
                error_text = None if runtime_status in {"done", "pending_review"} else result.summary
                for artifact in result.artifacts:
                    artifact_path = str(artifact.get("path") or "").strip()
                    if artifact_path:
                        extra_artifacts.append((
                            f"worker_{str(artifact.get('type') or 'artifact')}",
                            artifact_path,
                            {"notes": artifact.get("notes") or "", "run_id": state.run_id},
                        ))
            except Exception as exc:
                runtime_status = "failed"
                error_text = f"result parse failed: {exc}"
        else:
            runtime_status = "failed"
            if interrupted_by_restart:
                error_text = "interrupted_by_restart"
                notes = "worker was interrupted by dispatcher restart or force-stop before result emission"
            elif state.proc is not None:
                rc = state.proc.returncode
                if rc is not None and rc == 0:
                    error_text = "result_emit_failed"
                    notes = "worker exited cleanly (exit 0) but did not write result file"
                else:
                    code_str = str(rc) if rc is not None else "unknown"
                    error_text = f"worker_crashed (exit {code_str})"
                    notes = f"worker process exited with code {code_str}"
            else:
                error_text = "worker_crashed"
                notes = "worker process not found; presumed crashed before result emission"

        return runtime_status, notes, error_text, tests, result, raw_result_payload, result_artifacts, extra_artifacts

    _HEALTH_SNAPSHOT_THROTTLE_SECONDS = 1800  # 30 minutes per repo

    def _run_health_snapshot_in_background(self, repo_root: str | None, *, task_id: str, run_id: str) -> None:
        if not repo_root:
            return
        if self.config.worker_mode == "stub":
            return  # Never run health snapshots in stub/test mode — they spawn pytest recursively

        now = time.monotonic()
        last = self._health_snapshot_last_run.get(repo_root, 0.0)
        if now - last < self._HEALTH_SNAPSHOT_THROTTLE_SECONDS:
            return
        self._health_snapshot_last_run[repo_root] = now

        if not HEALTH_SNAPSHOT_SCRIPT.exists():
            self.logger.emit(
                "WRN",
                "central.dispatcher",
                f"health_snapshot_script_missing task={task_id} script={HEALTH_SNAPSHOT_SCRIPT}",
            )
            return

        def _worker() -> None:
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(HEALTH_SNAPSHOT_SCRIPT),
                        str(Path(repo_root).resolve()),
                        "--db-path",
                        str(self.config.db_path),
                    ],
                    check=False,
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if result.returncode != 0:
                    self.logger.emit(
                        "WRN",
                        "central.dispatcher",
                        f"health_snapshot_failed task={task_id} run={run_id} repo={repo_root} rc={result.returncode} stderr={result.stderr.strip()}",
                    )
                else:
                    self.logger.emit(
                        "INF",
                        "central.dispatcher",
                        f"health_snapshot_written task={task_id} run={run_id} repo={repo_root}",
                    )
            except Exception as exc:
                self.logger.emit(
                    "WRN",
                    "central.dispatcher",
                    f"health_snapshot_spawn_failed task={task_id} run={run_id} repo={repo_root} error={exc}",
                )

        threading.Thread(target=_worker, daemon=True, name=f"health-snapshot:{task_id}").start()

    def _activate_spark_fallback(self) -> None:
        """Permanently switch from spark to fallback model and persist to config file."""
        self._spark_quota_exhausted = True
        self.config.default_worker_model = self._SPARK_FALLBACK_MODEL
        # Persist so a restart picks up the fallback automatically
        config_path = self.paths.state_dir / "dispatcher-config.json"
        try:
            existing: dict[str, Any] = {}
            if config_path.exists():
                existing = json.loads(config_path.read_text(encoding="utf-8"))
            existing["default_worker_model"] = self._SPARK_FALLBACK_MODEL
            existing["updated_at"] = utc_now()
            config_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except Exception as exc:
            self.logger.emit("WRN", "central.dispatcher", f"spark_fallback_config_save_failed error={exc}")
        self.logger.emit(
            "WRN",
            "central.dispatcher",
            f"spark_quota_exhausted fallback_model={self._SPARK_FALLBACK_MODEL} persisted=true",
        )
        self._maybe_notify(
            title="Spark quota exhausted — switching to gpt-5.4",
            runtime_status="failed",
            summary=f"Trial quota for {self._SPARK_MODEL} used up. All future workers will use {self._SPARK_FALLBACK_MODEL}.",
        )

    def _handle_capacity_limit(self, state: ActiveWorker, runtime_status: str) -> bool:
        """Detect codex/claude capacity hits; requeue task and set backoff if found.

        Returns True if a capacity limit was detected and handled (caller should skip
        the normal transition/reconcile path).
        """
        if runtime_status != "failed":
            return False
        task_id = state.task["task_id"]
        backend = str(getattr(state, "selected_worker_backend", None) or "")
        quota_hit = (
            (backend == "codex" and self._detect_codex_capacity_hit(state.log_path))
            or (backend == "claude" and self._detect_claude_capacity_hit(state.log_path))
        )
        if not quota_hit:
            return False
        requeue_conn = self._connect()
        try:
            task_db.runtime_requeue_task(
                requeue_conn,
                task_id=task_id,
                actor_id="central.dispatcher",
                reason=f"{backend} usage limit reached; requeueing with fallback model",
                reset_retry_count=True,
            )
        finally:
            requeue_conn.close()

        model = str(getattr(state, "selected_worker_model", None) or "")
        is_spark = model == self._SPARK_MODEL

        if is_spark:
            # Spark trial quota exhausted — switch permanently to fallback, no backoff.
            self._activate_spark_fallback()
            self.logger.emit(
                "WRN",
                "central.dispatcher",
                f"worker_quota_hit task={task_id} run={state.run_id} backend={backend} model={model} fallback={self._SPARK_FALLBACK_MODEL} backoff_seconds=0",
            )
        else:
            # Normal quota hit — back off and let the operator decide.
            backoff_seconds = 300
            self._capacity_backoff_until = time.monotonic() + backoff_seconds
            task_title = str(state.task.get("title") or task_id)
            self.logger.emit(
                "WRN",
                "central.dispatcher",
                f"worker_quota_hit task={task_id} run={state.run_id} backend={backend} model={model} backoff_seconds={backoff_seconds}",
            )
            self._maybe_notify(
                title=f"Quota limit hit — {backend}",
                runtime_status="failed",
                summary=f"{task_title} requeued; dispatcher backing off {backoff_seconds}s.",
            )
        return True

    def _reconcile_done(
        self,
        state: ActiveWorker,
        *,
        result: Any,
        notes: str | None,
        tests: str | None,
        result_artifacts: list[str],
        raw_result_payload: dict[str, Any] | None = None,
    ) -> None:
        """Handle audit-rework routing and auto-reconciliation for a task that reached 'done'."""
        task_id = state.task["task_id"]
        reconcile_conn = self._connect()
        try:
            is_audit_task = str(state.task.get("task_type") or "") == "audit"
            verdict = str(getattr(result, "verdict", "") or "") if result is not None else ""
            # Audit task with rework_required verdict: auto-requeue parent with findings
            if is_audit_task and verdict == "rework_required":
                rework_snap = task_db.reconcile_audit_rework(
                    reconcile_conn,
                    audit_task_id=task_id,
                    summary=notes or "audit verdict: rework_required",
                    actor_id="central.dispatcher",
                )
                audit_meta = rework_snap.get("metadata") or {}
                parent_id = audit_meta.get("parent_task_id") or "?"
                try:
                    parent_snap = task_db.fetch_task_snapshots(reconcile_conn, task_id=parent_id)
                    rework_count = (parent_snap[0].get("metadata") or {}).get("rework_count", "?") if parent_snap else "?"
                    parent_status = parent_snap[0].get("planner_status", "?") if parent_snap else "?"
                except Exception:
                    rework_count, parent_status = "?", "?"
                self.logger.emit(
                    "INF",
                    "central.dispatcher",
                    f"worker_audit_rework task={task_id} run={state.run_id} parent={parent_id} rework_count={rework_count} parent_status={parent_status}{_title_kv(state.task)}",
                )
            # Audit task with accepted verdict: close audit + auto-close parent
            elif is_audit_task and verdict in {"accepted", "pass", "passed", "done", ""}:
                pass_snap = task_db.reconcile_audit_pass(
                    reconcile_conn,
                    audit_task_id=task_id,
                    summary=notes or "audit verdict: accepted",
                    actor_id="central.dispatcher",
                    worker_result=raw_result_payload,
                )
                audit_meta = pass_snap.get("metadata") or {}
                parent_id = audit_meta.get("parent_task_id") or "?"
                try:
                    parent_snap = task_db.fetch_task_snapshots(reconcile_conn, task_id=parent_id)
                    parent_status = parent_snap[0].get("planner_status", "?") if parent_snap else "?"
                except Exception:
                    parent_status = "?"
                self.logger.emit(
                    "INF",
                    "central.dispatcher",
                    f"worker_audit_pass task={task_id} run={state.run_id} parent={parent_id} parent_status={parent_status}{_title_kv(state.task)}",
                )
            else:
                reconciled = task_db.auto_reconcile_runtime_success(
                    reconcile_conn,
                    task_id=task_id,
                    summary=notes or "runtime completed successfully",
                    notes=notes,
                    tests=tests,
                    artifacts=result_artifacts,
                    actor_id="central.dispatcher",
                    run_id=state.run_id,
                )
                reconciled_planner_status = reconciled["planner_status"]
                if reconciled_planner_status == "awaiting_audit":
                    self.logger.emit(
                        "INF",
                        "central.dispatcher",
                        f"worker_auto_reconcile_skipped task={task_id} run={state.run_id} reason=awaiting_audit{_title_kv(state.task)}",
                    )
                else:
                    self.logger.emit(
                        "INF",
                        "central.dispatcher",
                        f"worker_auto_reconciled task={task_id} run={state.run_id} planner_status={reconciled_planner_status} version={reconciled['version']}{_title_kv(state.task)}",
                    )
        except Exception as exc:
            with reconcile_conn:
                task_db.insert_event(
                    reconcile_conn,
                    task_id=task_id,
                    event_type="planner.task_auto_reconcile_failed",
                    actor_kind="runtime",
                    actor_id="central.dispatcher",
                    payload={"run_id": state.run_id, "error": str(exc)},
                )
            self.logger.emit(
                "ERR",
                "central.dispatcher",
                f"worker_auto_reconcile_failed task={task_id} run={state.run_id} error={exc}",
            )
        finally:
            reconcile_conn.close()

    # ------------------------------------------------------------------
    # _finalize_worker
    # ------------------------------------------------------------------

    def _finalize_worker(
        self, state: ActiveWorker, *, timed_out: bool = False, interrupted_by_restart: bool = False
    ) -> None:
        task_id = state.task["task_id"]
        terminal_artifacts = [str(state.prompt_path), str(state.log_path)]
        # Capture raw exit code before any result parsing so it's always available.
        raw_exit_code: int | None = state.proc.returncode if state.proc is not None else None
        conn = self._connect()
        try:
            if timed_out:
                _exit_code, _exit_category = classify_exit_code(raw_exit_code, timed_out=True)
                # Use "failed" instead of "timeout" so the task is NOT automatically
                # retried.  Timeouts almost never succeed on retry — the task likely
                # needs a scope/prompt fix before re-dispatch.  Operators can manually
                # requeue with `runtime-requeue-task` if they disagree.
                with conn:
                    task_db.runtime_transition(
                        conn,
                        task_id=task_id,
                        status="failed",
                        worker_id=state.worker_id,
                        error_text="worker timeout (no auto-retry)",
                        notes="process exceeded timeout_seconds; timed-out tasks are not retried automatically",
                        artifacts=terminal_artifacts,
                        actor_id="central.dispatcher",
                        exit_code=_exit_code,
                        exit_category=_exit_category,
                    )
                self.logger.emit("WRN", "central.dispatcher", f"worker_timeout_failed task={task_id} run={state.run_id}{_title_kv(state.task)}")
                self._maybe_notify(title=str(state.task.get("title") or task_id), runtime_status="failed", summary="hard timeout exceeded (no auto-retry)", task_id=task_id)
                return

            runtime_status, notes, error_text, tests, result, raw_result_payload, result_artifacts, extra_artifacts = (
                self._parse_worker_result(
                    state, terminal_artifacts=terminal_artifacts, interrupted_by_restart=interrupted_by_restart
                )
            )

            _exit_code, _exit_category = classify_exit_code(raw_exit_code, error_text=error_text)
            # Extract token/cost from result payload (schema_version 2+).
            _tokens_used: int | None = None
            _tokens_cost_usd: float | None = None
            if isinstance(raw_result_payload, dict):
                _tokens_used = raw_result_payload.get("tokens_used") or None
                _tokens_cost_usd = raw_result_payload.get("tokens_cost_usd") or None

            if not self._handle_capacity_limit(state, runtime_status):
                with conn:
                    task_db.runtime_transition(
                        conn,
                        task_id=task_id,
                        status=runtime_status,
                        worker_id=state.worker_id,
                        error_text=error_text,
                        notes=notes,
                        artifacts=result_artifacts,
                        actor_id="central.dispatcher",
                        exit_code=_exit_code,
                        exit_category=_exit_category,
                        tokens_used=_tokens_used,
                        tokens_cost_usd=_tokens_cost_usd,
                    )
                self._maybe_notify(title=str(state.task.get("title") or task_id), runtime_status=runtime_status, summary=notes or error_text, task_id=task_id)
                if runtime_status == "done":
                    self._reconcile_done(state, result=result, notes=notes, tests=tests, result_artifacts=result_artifacts, raw_result_payload=raw_result_payload)
                    self._run_health_snapshot_in_background(
                        str(state.task.get("target_repo_root") or ""),
                        task_id=task_id,
                        run_id=state.run_id,
                    )

            if extra_artifacts:
                add_artifacts(task_id, extra_artifacts, self.config.db_path)
            self.logger.emit(
                "INF",
                "central.dispatcher",
                f"worker_finished task={task_id} run={state.run_id} runtime_status={runtime_status}{_title_kv(state.task)}",
            )
        finally:
            conn.close()

    def _process_active(self) -> None:
        # Drain finalization queue from remote worker results submitted via HTTP
        if self._coordination_server is not None:
            fq = self._coordination_server.finalization_queue
            while True:
                try:
                    task_id, _run_id = fq.get_nowait()
                except queue.Empty:
                    break
                with self._active_lock:
                    state = self._active.pop(task_id, None)
                if state is not None:
                    self._finalize_worker(state, timed_out=False)
                    self._close_worker_state(state)
                    self._emit_status_heartbeat(force=True)

        HEARTBEAT_LIVENESS_MULTIPLIER = 3
        heartbeat_liveness_window = self.config.heartbeat_seconds * HEARTBEAT_LIVENESS_MULTIPLIER

        with self._active_lock:
            active_snapshot = list(self._active.items())
        for task_id, state in active_snapshot:
            if state.is_remote:
                # Remote workers: use heartbeat liveness window for crash detection.
                # The HTTP heartbeat handler updates last_heartbeat_monotonic in-memory.
                heartbeat_age = time.monotonic() - state.last_heartbeat_monotonic
                timed_out = heartbeat_age > heartbeat_liveness_window
                # Also enforce total task execution timeout
                if not timed_out:
                    elapsed = self._worker_elapsed_seconds(state)
                    timed_out = elapsed > state.timeout_seconds
                if timed_out:
                    self.logger.emit(
                        "WRN",
                        "central.dispatcher",
                        f"remote_worker_timeout task={task_id} run={state.run_id} "
                        f"heartbeat_age={heartbeat_age:.0f}s liveness_window={heartbeat_liveness_window:.0f}s{_title_kv(state.task)}",
                    )
                    with self._active_lock:
                        self._active.pop(task_id, None)
                    self._worker_prev_log_sizes.pop(task_id, None)
                    self._finalize_worker(state, timed_out=True)
                    self._close_worker_state(state)
                    self._emit_status_heartbeat(force=True)
                continue

            elapsed = self._worker_elapsed_seconds(state)
            if elapsed > state.timeout_seconds:
                terminate_process(state.pid, state.proc, pgid=state.pgid)
                self._finalize_worker(state, timed_out=True)
                self._close_worker_state(state)
                with self._active_lock:
                    self._active.pop(task_id, None)
                self._worker_prev_log_sizes.pop(task_id, None)
                self._emit_status_heartbeat(force=True)
                continue

            if time.monotonic() - state.last_heartbeat_monotonic >= self.config.heartbeat_seconds:
                try:
                    self._heartbeat_worker(state)
                    elapsed = self._worker_elapsed_seconds(state)
                    try:
                        log_bytes = state.log_path.stat().st_size
                    except Exception:
                        log_bytes = 0
                    prev_bytes = self._worker_prev_log_sizes.get(task_id, 0)
                    growing = log_bytes > prev_bytes
                    self._worker_prev_log_sizes[task_id] = log_bytes
                    self.logger.emit(
                        "INF",
                        "central.dispatcher",
                        f"worker_heartbeat task={task_id} run={state.run_id} "
                        f"elapsed_s={elapsed:.0f} log_bytes={log_bytes} growing={growing}"
                        f"{_title_kv(state.task)}",
                    )
                except Exception as exc:
                    self.logger.emit(
                        "INF",
                        "central.dispatcher",
                        f"worker_heartbeat_error task={task_id} run={state.run_id} error={exc}{_title_kv(state.task)}",
                    )

            if not self._worker_is_running(state):
                self._finalize_worker(state, timed_out=False)
                self._close_worker_state(state)
                with self._active_lock:
                    self._active.pop(task_id, None)
                self._worker_prev_log_sizes.pop(task_id, None)
                self._emit_status_heartbeat(force=True)

    def _run_stale_recovery(self) -> None:
        if time.monotonic() - self._last_recovery_monotonic < self.config.stale_recovery_seconds:
            return
        conn = self._connect()
        try:
            with conn:
                result = task_db.runtime_recover_stale(conn, limit=50, actor_id="central.dispatcher")
        finally:
            conn.close()
        self._last_recovery_monotonic = time.monotonic()
        if result.get("recovered_count"):
            self.logger.emit(
                "INF",
                "central.dispatcher",
                f"stale_recovery recovered={result['recovered_count']}",
            )

    def _abort_if_max_retries(self, snapshot: dict[str, Any]) -> bool:
        """Transition task to failed with max_retries_exceeded if retry_count >= max_retries.

        Returns True if the task was aborted, False if it should proceed to spawn.
        """
        retry_count = snapshot_retry_count(snapshot)
        if retry_count < self.config.max_retries:
            return False
        task_id = snapshot["task_id"]
        worker_id = (snapshot.get("lease") or {}).get("lease_owner_id")
        self.logger.emit(
            "WRN",
            "central.dispatcher",
            f"max_retries_exceeded task={task_id} retry_count={retry_count} max_retries={self.config.max_retries}",
        )
        conn = self._connect()
        try:
            with conn:
                task_db.runtime_transition(
                    conn,
                    task_id=task_id,
                    status="failed",
                    worker_id=worker_id,
                    error_text="max_retries_exceeded",
                    notes=f"retry_count={retry_count} reached max_retries={self.config.max_retries}; halting automatic retry",
                    artifacts=[],
                    actor_id="central.dispatcher",
                )
        finally:
            conn.close()
        return True

    def _fill_workers(self) -> None:
        with self._active_lock:
            active_count = len(self._active)
        while active_count < self.config.max_workers and not self._stop_requested:
            snapshot = self._claim_next()
            if snapshot is None:
                break
            if self._abort_if_max_retries(snapshot):
                with self._active_lock:
                    active_count = len(self._active)
                continue
            try:
                self._spawn_worker(snapshot)
            except Exception as exc:
                self.logger.emit("INF", "central.dispatcher", f"worker_spawn_error task={snapshot['task_id']} error={exc}")
                conn = self._connect()
                try:
                    with conn:
                        task_db.runtime_transition(
                            conn,
                            task_id=snapshot["task_id"],
                            status="failed",
                            worker_id=(snapshot.get("lease") or {}).get("lease_owner_id"),
                            error_text=f"spawn error: {exc}",
                            notes=None,
                            artifacts=[],
                            actor_id="central.dispatcher",
                        )
                finally:
                    conn.close()
            with self._active_lock:
                active_count = len(self._active)

    def run_once(self, *, emit_result: bool = True) -> int:
        self._run_stale_recovery()
        snapshot = self._claim_next()
        if snapshot is None:
            if emit_result:
                print(json_dumps({"claimed": False, "reason": "no_eligible_task"}))
            return 0
        if self._abort_if_max_retries(snapshot):
            if emit_result:
                print(json_dumps({"claimed": True, "task_id": snapshot["task_id"], "aborted": "max_retries_exceeded"}))
            return 0
        self._spawn_worker(snapshot)
        while self._active:
            self._process_active()
            time.sleep(max(0.2, self.config.poll_interval))
        if emit_result:
            print(json_dumps({"claimed": True, "task_id": snapshot["task_id"]}))
        return 0

    def run_daemon(self) -> int:
        acquire_lock(self.paths, self.config)
        self._setup_signals()
        self._running = True
        self._started_at = time.time()

        # Start coordination server for remote workers if enabled
        if self.config.remote_workers_enabled:
            token = os.environ.get("CENTRAL_COORDINATION_TOKEN", "")
            coord_config = CoordinationConfig(
                port=self.config.coordination_port,
                token=token,
                max_remote_workers=self.config.max_remote_workers,
                heartbeat_seconds=self.config.heartbeat_seconds,
            )
            self._coordination_server = CoordinationServer(self, coord_config)
            self._coordination_server.start()
            self.logger.emit(
                "INF",
                "central.dispatcher",
                f"coordination_server_started port={self.config.coordination_port} "
                f"max_remote_workers={self.config.max_remote_workers} "
                f"auth={'enabled' if token else 'WARNING:no_token'}",
            )

        adopted = self._adopt_active_workers()
        self.logger.emit(
            "INF",
            "central.dispatcher",
            (
                f"dispatcher_started max_workers={self.config.max_workers} "
                f"worker_mode={self.config.worker_mode} "
                f"default_worker_model={self.config.default_worker_model} "
                f"remote_workers_enabled={self.config.remote_workers_enabled} "
                f"adopted_workers={adopted}"
            ),
        )
        self._emit_status_heartbeat(force=True)
        try:
            while True:
                _cycle_start = time.monotonic()
                self._process_active()
                if self._force_stop:
                    for state in self._active.values():
                        try:
                            self._persist_worker_supervision(
                                state,
                                handoff={
                                    "state": "interrupted_by_restart",
                                    "interrupted_at": utc_now(),
                                    "interrupted_by_dispatcher_pid": os.getpid(),
                                },
                                event_type="runtime.worker_interrupted",
                                event_payload={
                                    "run_id": state.run_id,
                                    "worker_id": state.worker_id,
                                    "reason": "force_stop",
                                },
                            )
                        except Exception:
                            pass
                        terminate_process(state.pid, state.proc, pgid=state.pgid)
                    break
                if self._stop_requested:
                    self._prepare_handoff()
                    break
                self._run_stale_recovery()
                if not self._stop_requested:
                    self._fill_workers()
                self._cycle_count += 1
                _elapsed_ms = int((time.monotonic() - _cycle_start) * 1000)
                self._emit_status_heartbeat(elapsed_ms=_elapsed_ms)
                self._flush_notify_if_due()
                time.sleep(max(0.2, self.config.poll_interval))
        finally:
            for state in list(self._active.values()):
                self._close_worker_state(state)
            if self._coordination_server is not None:
                self._coordination_server.stop()
            release_lock(self.paths)
            self.logger.emit("INF", "central.dispatcher", "dispatcher_stopped")
        return 0
