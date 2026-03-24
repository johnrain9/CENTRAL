"""Remote Worker Coordination API.

Provides a FastAPI + Uvicorn HTTP server that remote workers use to claim tasks,
send heartbeats, submit results, stream logs, and check dispatcher status.

The server communicates with the dispatcher through a ``DispatcherBridge``
protocol, allowing it to be unit-tested with a mock bridge independently of the
running dispatcher (wired in REMOTE-4).

Endpoints (all require ``Authorization: Bearer <token>``):
  POST /api/v1/claim      — claim next eligible task
  POST /api/v1/heartbeat  — renew task lease
  POST /api/v1/result     — submit completed result
  POST /api/v1/log        — stream log chunk
  GET  /api/v1/status     — dispatcher health summary
"""

from __future__ import annotations

import asyncio
import hmac
import json
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

# Ensure scripts/ is importable.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import central_task_db as task_db
from central_runtime_v2.config import ActiveWorker, DispatcherConfig, RuntimePaths
from central_runtime_v2.log import DaemonLog
from central_runtime_v2.model_policy import build_worker_task, resolve_task_worker_backend
from central_runtime_v2.paths import utc_now

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_COORDINATION_PORT = 7429
DEFAULT_MAX_REMOTE_WORKERS = 5
DEFAULT_HEARTBEAT_SECONDS = 30.0
HEARTBEAT_LIVENESS_MULTIPLIER = 3

_CANCELLED_STATUSES = frozenset({"cancelled", "done", "rejected"})


# ---------------------------------------------------------------------------
# Coordination configuration
# ---------------------------------------------------------------------------


@dataclass
class CoordinationConfig:
    """Configuration for the coordination HTTP server."""

    port: int = DEFAULT_COORDINATION_PORT
    host: str = "0.0.0.0"
    token: str = ""
    max_remote_workers: int = DEFAULT_MAX_REMOTE_WORKERS
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS
    queue_name: str = "remote"


# ---------------------------------------------------------------------------
# Dispatcher bridge protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class DispatcherBridge(Protocol):
    """
    Protocol that ``CoordinationServer`` requires from the dispatcher.

    The real ``CentralDispatcher`` provides this via an adapter wired in
    REMOTE-4.  Unit tests supply a ``MockDispatcherBridge``.

    All attributes must exist on the concrete class; methods may be plain
    properties or zero-argument callables as indicated.
    """

    @property
    def db_path(self) -> Path: ...

    @property
    def paths(self) -> RuntimePaths: ...

    @property
    def dispatcher_config(self) -> DispatcherConfig: ...

    # Direct references to the dispatcher's shared mutable state.
    # The ``active_lock`` must be held when reading or writing ``active_workers``.
    @property
    def active_workers(self) -> dict[str, ActiveWorker]: ...

    @property
    def active_lock(self) -> threading.Lock: ...

    @property
    def logger(self) -> DaemonLog: ...

    def dispatcher_version(self) -> str: ...

    def dispatcher_id(self) -> str: ...

    def started_at(self) -> float: ...


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ClaimRequest(BaseModel):
    worker_id: str
    backends: list[str]
    central_version: str


class HeartbeatRequest(BaseModel):
    task_id: str
    run_id: str
    worker_id: str
    status: str = "running"
    progress_note: str | None = None
    reattach: bool = False


class ResultSubmission(BaseModel):
    task_id: str
    run_id: str
    worker_id: str
    result: dict[str, Any]
    result_branch: str | None = None
    result_commit_sha: str | None = None
    log_tail: str | None = None


class LogChunk(BaseModel):
    task_id: str
    run_id: str
    chunk: str


class WorkPackage(BaseModel):
    task_id: str
    run_id: str
    title: str
    worker_backend: str
    worker_model: str
    worker_effort: str
    repo_name: str
    repo_root_relative: str
    branch_prefix: str
    prompt_body: str
    task_kind: str
    category: str
    sandbox_mode: str | None = None
    deliverables_json: str
    scope_notes_json: str
    validation_commands_json: str
    env_allowlist: list[str]
    timeout_seconds: int
    dispatcher_version: str


class ClaimResponse(BaseModel):
    work_package: WorkPackage
    version_warning: str | None = None


# ---------------------------------------------------------------------------
# CoordinationServer
# ---------------------------------------------------------------------------


class CoordinationServer:
    """
    FastAPI + Uvicorn HTTP coordination server for remote workers.

    Lifecycle::

        server = CoordinationServer(bridge, coord_config)
        server.start()   # launches daemon thread
        # ...
        server.stop()    # signals Uvicorn to exit

    The ``finalization_queue`` attribute is a ``queue.Queue[tuple[str, str]]``
    that the dispatcher main loop must drain each cycle::

        while not server.finalization_queue.empty():
            task_id, run_id = server.finalization_queue.get_nowait()
            ...
    """

    def __init__(self, bridge: DispatcherBridge, config: CoordinationConfig) -> None:
        self._bridge = bridge
        self._config = config
        self.finalization_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._app = self._build_app()
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None

    # ------------------------------------------------------------------
    # FastAPI app construction
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="CENTRAL Coordination API", version="1.0.0")
        security = HTTPBearer()
        config = self._config

        def _verify_token(
            credentials: HTTPAuthorizationCredentials = Depends(security),
        ) -> None:
            if not config.token:
                raise HTTPException(status_code=500, detail="coordination token not configured")
            if not hmac.compare_digest(credentials.credentials, config.token):
                raise HTTPException(status_code=401, detail="invalid token")

        @app.post("/api/v1/claim", dependencies=[Depends(_verify_token)])
        async def claim_endpoint(request: ClaimRequest) -> Any:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._handle_claim, request)

        @app.post("/api/v1/heartbeat", dependencies=[Depends(_verify_token)])
        async def heartbeat_endpoint(request: HeartbeatRequest) -> Any:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._handle_heartbeat, request)

        @app.post("/api/v1/result", dependencies=[Depends(_verify_token)])
        async def result_endpoint(request: ResultSubmission) -> Any:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._handle_result, request)

        @app.post("/api/v1/log", dependencies=[Depends(_verify_token)])
        async def log_endpoint(request: LogChunk) -> Any:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._handle_log, request)

        @app.get("/api/v1/status", dependencies=[Depends(_verify_token)])
        async def status_endpoint() -> Any:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._handle_status)

        return app

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start Uvicorn in a daemon thread."""
        uv_config = uvicorn.Config(
            self._app,
            host=self._config.host,
            port=self._config.port,
            log_level="warning",
            loop="asyncio",
        )
        self._server = uvicorn.Server(config=uv_config)

        def _run() -> None:
            asyncio.run(self._server.serve())  # type: ignore[union-attr]

        self._thread = threading.Thread(target=_run, daemon=True, name="coordination-server")
        self._thread.start()

    def stop(self) -> None:
        """Signal Uvicorn to exit and wait for the thread to finish."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _matches_worker(req_worker_id: str, lease_owner_id: object) -> bool:
        """Accept current and legacy worker-id forms for lease ownership checks."""
        owner = str(lease_owner_id or "")
        if owner == req_worker_id:
            return True
        if owner.startswith("remote:"):
            return owner.startswith(f"remote:{req_worker_id}:")
        return False


    def _open_db(self):
        conn = task_db.connect(self._bridge.db_path)
        task_db.require_initialized_db(conn, self._bridge.db_path)
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _log_path(self, task_id: str, run_id: str) -> Path:
        p = self._bridge.paths.worker_logs_dir / task_id
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{run_id}.log"

    def _result_path(self, task_id: str, run_id: str) -> Path:
        p = self._bridge.paths.worker_results_dir / task_id
        p.mkdir(parents=True, exist_ok=True)
        return p / f"{run_id}.json"

    def _lease_seconds(self) -> int:
        return max(5, int(self._config.heartbeat_seconds * HEARTBEAT_LIVENESS_MULTIPLIER))

    @staticmethod
    def _future_utc(seconds: float) -> str:
        ts = time.time() + seconds
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat()

    # ------------------------------------------------------------------
    # Claim handler
    # ------------------------------------------------------------------

    def _handle_claim(self, req: ClaimRequest) -> Any:
        """
        Claim the next eligible task for the requesting worker.

        Holds ``active_lock`` across the entire cap-check → DB claim → in-memory
        register sequence to prevent concurrent remote workers from racing on caps.
        """
        bridge = self._bridge
        disp_config = bridge.dispatcher_config
        lease_seconds = self._lease_seconds()
        lease_worker_id = req.worker_id
        remote_worker_id = f"remote:{req.worker_id}:{int(time.time())}"

        with bridge.active_lock:
            # --- Global remote worker cap ---
            active_remote_count = sum(1 for w in bridge.active_workers.values() if w.is_remote)
            if active_remote_count >= self._config.max_remote_workers:
                return Response(status_code=204)

            # --- Find eligible candidate ---
            read_conn = self._open_db()
            try:
                snapshots = task_db.fetch_task_snapshots(read_conn)
                eligible = task_db.order_eligible_snapshots(snapshots, remote_only=True)
                active_counts = task_db.active_repo_worker_counts(read_conn)
            finally:
                read_conn.close()

            candidate: dict[str, Any] | None = None
            for snapshot in eligible:
                effective_backend = resolve_task_worker_backend(snapshot, disp_config.worker_mode)
                if effective_backend not in req.backends:
                    continue
                repo_count = active_counts.get(str(snapshot.get("target_repo_id", "")), 0)
                max_repo = task_db.resolve_repo_max_concurrent_workers(
                    snapshot.get("repo_metadata") or {}
                )
                if repo_count >= max_repo:
                    continue
                candidate = snapshot
                break

            if candidate is None:
                return Response(status_code=204)

            # --- Claim the candidate in DB ---
            claim_conn = self._open_db()
            try:
                claimed_snapshot = task_db.runtime_claim(
                    claim_conn,
                    worker_id=lease_worker_id,
                    queue_name=self._config.queue_name,
                    lease_seconds=lease_seconds,
                    task_id=candidate["task_id"],
                    remote_only=True,
                    actor_id="central.coordination",
                    raise_on_empty=False,
                )
            finally:
                claim_conn.close()

            if claimed_snapshot is None:
                # Lost a race to a local worker; tell the remote to retry.
                return Response(status_code=204)

            snapshot = claimed_snapshot
            task_id = snapshot["task_id"]
            effective_backend = resolve_task_worker_backend(snapshot, disp_config.worker_mode)
            worker_task = build_worker_task(
                snapshot,
                disp_config.default_worker_model,
                worker_mode=effective_backend,
                dispatcher_default_worker_model=disp_config.default_worker_model,
            )

            run_id = (
                (snapshot.get("lease") or {}).get("execution_run_id")
                or f"{task_id}-{int(time.time())}"
            )
            started_at = datetime.now(timezone.utc).replace(microsecond=0)
            timeout_seconds = int(
                (snapshot.get("execution") or {}).get("timeout_seconds") or 3600
            )

            # --- Write supervision metadata to lease ---
            result_path = self._result_path(task_id, run_id)
            log_path = self._log_path(task_id, run_id)
            sup_meta = {
                "is_remote": True,
                "remote_worker_id": req.worker_id,
                "run_id": run_id,
                "result_path": str(result_path),
                "log_path": str(log_path),
                "started_at": started_at.isoformat(),
                "timeout_seconds": timeout_seconds,
                "worker_model": str(worker_task.get("worker_model") or ""),
                "worker_backend": effective_backend,
            }
            sup_conn = self._open_db()
            try:
                with sup_conn:
                    existing_lease = task_db.fetch_active_lease(sup_conn, task_id)
                    if existing_lease is not None:
                        existing_meta_raw = existing_lease["lease_metadata_json"] or "{}"
                        try:
                            existing_meta = json.loads(existing_meta_raw)
                        except Exception:
                            existing_meta = {}
                        merged = {**existing_meta, **sup_meta}
                        sup_conn.execute(
                            "UPDATE task_active_leases SET lease_metadata_json = ? WHERE task_id = ?",
                            (json.dumps(merged), task_id),
                        )
            finally:
                sup_conn.close()

            # --- Register in _active (lock still held) ---
            state = ActiveWorker(
                task=snapshot,
                worker_id=lease_worker_id,
                run_id=run_id,
                pid=-1,
                proc=None,
                log_handle=None,
                prompt_path=(
                    bridge.paths.worker_prompts_dir / task_id / f"{run_id}.md"
                ),
                result_path=result_path,
                log_path=log_path,
                process_start_token=None,
                started_at=started_at,
                start_monotonic=time.monotonic(),
                last_heartbeat_monotonic=time.monotonic(),
                timeout_seconds=timeout_seconds,
                selected_worker_model=str(worker_task.get("worker_model") or "") or None,
                selected_worker_backend=effective_backend,
                is_remote=True,
                remote_worker_id=req.worker_id,
            )
            bridge.active_workers[task_id] = state

        # --- Build response outside lock ---
        dispatcher_ver = bridge.dispatcher_version()
        version_warning: str | None = (
            "stale" if req.central_version != dispatcher_ver else None
        )

        repo_root = str(snapshot.get("target_repo_root") or "")
        home_prefix = str(Path.home()) + "/"
        repo_root_relative = (
            repo_root[len(home_prefix):]
            if repo_root.startswith(home_prefix)
            else repo_root
        )

        wp = WorkPackage(
            task_id=task_id,
            run_id=run_id,
            title=snapshot.get("title") or "",
            worker_backend=effective_backend,
            worker_model=str(worker_task.get("worker_model") or ""),
            worker_effort=str(worker_task.get("worker_effort") or "medium"),
            repo_name=Path(repo_root).name if repo_root else "",
            repo_root_relative=repo_root_relative,
            branch_prefix=f"worker/{task_id}",
            prompt_body=worker_task.get("prompt_body") or "",
            task_kind=worker_task.get("task_kind") or "mutating",
            category=worker_task.get("category") or "implementation",
            sandbox_mode=worker_task.get("sandbox_mode"),
            deliverables_json=worker_task.get("deliverables_json") or "[]",
            scope_notes_json=worker_task.get("scope_notes_json") or "[]",
            validation_commands_json=worker_task.get("validation_commands_json") or "[]",
            env_allowlist=[
                "GROK_API_KEY",
                "XAI_API_KEY",
                "GEMINI_API_KEY",
                "GOOGLE_API_KEY",
                "ANTHROPIC_API_KEY",
            ],
            timeout_seconds=timeout_seconds,
            dispatcher_version=dispatcher_ver,
        )
        return ClaimResponse(work_package=wp, version_warning=version_warning)

    # ------------------------------------------------------------------
    # Heartbeat handler
    # ------------------------------------------------------------------

    def _handle_heartbeat(self, req: HeartbeatRequest) -> Any:
        bridge = self._bridge
        lease_seconds = self._lease_seconds()

        # Quick in-memory check (no lock needed for read here — worst case stale)
        with bridge.active_lock:
            in_active = req.task_id in bridge.active_workers

        if not in_active and not req.reattach:
            raise HTTPException(status_code=404, detail="task not found in active workers")

        # DB-authoritative checks + lease renewal
        conn = self._open_db()
        try:
            lease = task_db.fetch_active_lease(conn, req.task_id)
            if lease is None:
                raise HTTPException(status_code=410, detail="lease expired or task cancelled")

            runtime_row = conn.execute(
                "SELECT runtime_status FROM task_runtime_state WHERE task_id = ?",
                (req.task_id,),
            ).fetchone()
            if runtime_row and runtime_row["runtime_status"] in _CANCELLED_STATUSES:
                raise HTTPException(status_code=410, detail="task is done or cancelled")

            if not self._matches_worker(req.worker_id, lease["lease_owner_id"]):
                raise HTTPException(status_code=410, detail="lease reassigned to different worker")

            # Renew lease
            heartbeat_at = utc_now()
            lease_expires_at = self._future_utc(lease_seconds)
            lease_owner_id = str(lease["lease_owner_id"])
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE task_active_leases"
                " SET lease_expires_at = ?, last_heartbeat_at = ?"
                " WHERE task_id = ? AND lease_owner_id = ?",
                (lease_expires_at, heartbeat_at, req.task_id, lease_owner_id),
            )
            task_db.insert_event(
                conn,
                task_id=req.task_id,
                event_type="runtime.heartbeat",
                actor_kind="runtime",
                actor_id="central.coordination",
                payload={
                    "worker_id": req.worker_id,
                    "status": req.status,
                    "progress_note": req.progress_note,
                    "lease_expires_at": lease_expires_at,
                },
            )
            conn.commit()
        finally:
            conn.close()

        # Update in-memory heartbeat timestamp
        with bridge.active_lock:
            state = bridge.active_workers.get(req.task_id)
            if state is not None:
                state.last_heartbeat_monotonic = time.monotonic()
                state.last_remote_heartbeat = heartbeat_at

        # Reattach: reconstruct ActiveWorker after dispatcher restart
        if req.reattach and not in_active:
            self._reattach_worker(req.task_id, req.worker_id, req.run_id)

        return {"ok": True, "lease_renewed": True}

    def _reattach_worker(self, task_id: str, worker_id: str, run_id: str) -> None:
        """Reconstruct an ActiveWorker entry from DB lease metadata after dispatcher restart."""
        bridge = self._bridge
        conn = self._open_db()
        try:
            lease = task_db.fetch_active_lease(conn, task_id)
            if lease is None:
                return
            meta_raw = lease["lease_metadata_json"] or "{}"
            try:
                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
            except Exception:
                meta = {}
            if not meta.get("is_remote"):
                return
            snapshots = task_db.fetch_task_snapshots(conn, task_id=task_id)
            if not snapshots:
                return
            snapshot = snapshots[0]
        finally:
            conn.close()

        result_path = Path(
            meta.get("result_path")
            or str(bridge.paths.worker_results_dir / task_id / f"{run_id}.json")
        )
        log_path = Path(
            meta.get("log_path")
            or str(bridge.paths.worker_logs_dir / task_id / f"{run_id}.log")
        )
        timeout_seconds = int(meta.get("timeout_seconds") or 3600)

        state = ActiveWorker(
            task=snapshot,
            worker_id=worker_id,
            run_id=run_id,
            pid=-1,
            proc=None,
            log_handle=None,
            prompt_path=bridge.paths.worker_prompts_dir / task_id / f"{run_id}.md",
            result_path=result_path,
            log_path=log_path,
            process_start_token=None,
            started_at=None,
            start_monotonic=None,
            last_heartbeat_monotonic=time.monotonic(),
            timeout_seconds=timeout_seconds,
            selected_worker_model=meta.get("worker_model") or None,
            selected_worker_backend=meta.get("worker_backend") or None,
            is_remote=True,
            remote_worker_id=meta.get("remote_worker_id") or worker_id,
        )
        with bridge.active_lock:
            bridge.active_workers[task_id] = state

    # ------------------------------------------------------------------
    # Result handler
    # ------------------------------------------------------------------

    def _handle_result(self, req: ResultSubmission) -> Any:
        result_path = self._result_path(req.task_id, req.run_id)
        if result_path.exists():
            raise HTTPException(status_code=409, detail="result already submitted for this run")

        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(req.result, indent=2), encoding="utf-8")

        if req.log_tail:
            log_path = self._log_path(req.task_id, req.run_id)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("\n--- remote log tail ---\n")
                fh.write(req.log_tail)

        self.finalization_queue.put((req.task_id, req.run_id))

        self._bridge.logger.emit(
            "INF",
            "central.coordination",
            f"result_received task={req.task_id} run={req.run_id} worker={req.worker_id}",
        )
        return {"ok": True, "queued": True}

    # ------------------------------------------------------------------
    # Log handler
    # ------------------------------------------------------------------

    def _handle_log(self, req: LogChunk) -> Any:
        log_path = self._log_path(req.task_id, req.run_id)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(req.chunk)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Status handler
    # ------------------------------------------------------------------

    def _handle_status(self) -> Any:
        bridge = self._bridge

        with bridge.active_lock:
            active_snapshot = list(bridge.active_workers.items())

        local_count = sum(1 for _, w in active_snapshot if not w.is_remote)
        remote_count = sum(1 for _, w in active_snapshot if w.is_remote)

        agents: dict[str, dict[str, Any]] = {}
        for _, w in active_snapshot:
            if w.is_remote and w.remote_worker_id:
                entry = agents.setdefault(w.remote_worker_id, {"active": 0, "last_seen": None})
                entry["active"] += 1
                if w.last_remote_heartbeat:
                    if (
                        entry["last_seen"] is None
                        or w.last_remote_heartbeat > entry["last_seen"]
                    ):
                        entry["last_seen"] = w.last_remote_heartbeat

        try:
            conn = self._open_db()
            try:
                snaps = task_db.fetch_task_snapshots(conn)
                eligible_count = len(task_db.order_eligible_snapshots(snaps))
            finally:
                conn.close()
        except Exception:
            eligible_count = -1

        return {
            "dispatcher_id": bridge.dispatcher_id(),
            "dispatcher_version": bridge.dispatcher_version(),
            "active_local_workers": local_count,
            "active_remote_workers": remote_count,
            "remote_agents": agents,
            "eligible_tasks": eligible_count,
            "uptime_seconds": int(time.time() - bridge.started_at()),
        }
