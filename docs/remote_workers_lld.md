# Remote Workers LLD: Queue-Based Distributed Worker Execution

This document defines the low-level design for running CENTRAL dispatcher workers on remote machines (initially WSL2/Windows, extensible to any Linux host).

This revision incorporates the implementation-reality review (Opus) by:

- designing the backend filtering mechanism at claim time (was hand-waved)
- specifying the remote finalization path explicitly (was assumed to reuse local path)
- moving bearer token auth to Phase 1 (was deferred to Phase 3)
- fixing HTTP verb for claim endpoint (GET→POST for state-mutating operation)
- specifying thread-safety model for HTTP server + dispatcher loop
- requiring git worktrees for concurrent same-repo execution
- adding global remote worker caps and per-repo caps across local+remote
- adding CENTRAL self-sync and version handshake
- specifying Dispatcher repo dependency and schema shipping
- documenting full config persistence flow
- defining branch naming convention and commit transport

This revision incorporates the Grok research review by:

- adopting FastAPI + Uvicorn for the coordination API (over raw http.server)
- adding SSE (Server-Sent Events) as Phase 2 upgrade from polling
- adding WSL2-specific filesystem and networking constraints
- adding Zeroconf service discovery as Phase 3 option

This revision incorporates the second review (Sonnet) by:

- separating heartbeat liveness window from task timeout (was using task timeout for crash detection)
- fixing per-repo cap race condition in claim handler (check-then-act under single lock hold)
- fixing finalization_pending/finalization_queue inconsistency in pseudocode
- using env-overridable CENTRAL_ROOT instead of hardcoded Path.home() path
- requiring auth on /api/v1/status endpoint (was contradicting auth-from-day-one decision)
- fixing log_tail write to append when streaming was active (was overwriting)
- normalizing worker_id format across all examples (was inconsistent)
- adding cancellation trigger path (operator cancel → 410 on next heartbeat)
- adding return code checks to _sync_central() subprocess calls
- adding worktree path collision handling on task retry
- calling out GitHub auth as hard prerequisite for Phase 2 auto-push

It defines:

- the coordination protocol between dispatcher and remote worker agent
- the work package schema exchanged over the wire
- the result transport and log collection contracts
- the heartbeat and liveness model for remote workers
- the git sync, worktree isolation, and commit transport strategy
- the security model for API key and secret distribution
- the concurrency and thread-safety model
- changes to existing dispatcher code

This LLD is implementation-driving. A worker should be able to implement the remote worker agent from this document without making further design decisions.

## Scope

This LLD covers:

- a lightweight HTTP coordination API added to the dispatcher
- a standalone `worker_agent.py` daemon that runs on the remote machine
- the work package format sent from dispatcher to worker
- result and log transport back to the dispatcher
- heartbeat, timeout, and cancellation over the network
- git repo sync, worktree isolation, and commit transport
- API key provisioning on the remote machine
- thread-safety and SQLite concurrency model
- changes to `dispatcher.py`, `backends.py`, `config.py`, and `dispatcher_control.py`

This LLD does not cover:

- multi-dispatcher federation (single dispatcher remains the orchestrator)
- container-based isolation (bare metal WSL2 is the target)
- cloud autoscaling or ephemeral worker provisioning
- TLS/mTLS for the coordination API (deferred to hardening phase)

## Assumptions

### Identical Repo Layout

Both machines maintain the same directory structure relative to `$HOME`:

```
$HOME/projects/
  CENTRAL/
  Dispatcher/
  eco-system/
  motoHelper/
  aimSoloAnalysis/
  ...
```

This includes the `Dispatcher` repo, which contains the autonomy runner and worker result schema (`worker_result.schema.json`). Backend classes in `central_runtime_v2` have hard dependencies on `AUTONOMY_ROOT` and `AUTONOMY_SCHEMA_PATH` (resolved via `CENTRAL_AUTONOMY_ROOT` env var or `REPO_ROOT.parent / "Dispatcher"`). The remote machine must have this repo cloned.

This is already true on the user's WSL2 setup. The remote worker agent resolves `repo_root` by replacing the dispatcher's `$HOME` prefix with its own. No path mapping table is needed.

### Network Reachability

The Mac (dispatcher) and Windows/WSL2 (worker) are on the same local network. The dispatcher binds an HTTP server on a configurable port. The worker agent connects to `http://<dispatcher-ip>:<port>`.

For non-LAN setups, a Tailscale/WireGuard VPN or SSH tunnel can provide the same connectivity without changes to this design.

### API Keys Live on the Remote Machine

API keys (`GROK_API_KEY`, `GEMINI_API_KEY`, etc.) are configured in the remote machine's shell profile, loaded by the worker agent at startup using the same `_load_shell_api_keys()` pattern. Keys never traverse the network.

### WSL2-Specific Constraints

WSL2 is a lightweight VM with its own NAT layer. Key constraints for this design:

- **Filesystem performance:** Git worktrees and all worker I/O MUST use the native Linux filesystem (`/home/...`), NOT the Windows mount (`/mnt/c/...`). The 9P bridge to `/mnt/c` is 10-50x slower for git operations and file-heavy tasks like Playwright screenshots. The `repo_root_relative` path resolution already targets `$HOME/projects/...` which is correct.
- **Networking:** WSL2 worker connects outbound to the Mac's LAN IP. No port exposure needed on the WSL side. Use raw IPs in config (not hostnames — WSL2 DNS resolution is unreliable across sleep/resume). The Mac's firewall must allow inbound connections on the coordination port.
- **Windows firewall:** May block WSL2-initiated outbound connections to the LAN. Add an allow rule for the WSL2 virtual subnet (typically `172.x.x.x`).
- **Aggressive suspend:** WSL2 suspends when idle. The worker agent must handle connection failures with exponential backoff and automatically resume polling when WSL wakes. In-flight tasks will be detected as dead via the heartbeat liveness window (~90s) and retried.
- **IP instability:** WSL2's IP changes on reboot. The worker connects outbound to the Mac (stable LAN IP), so this doesn't affect normal operation. If the Mac needs to reach WSL2 (e.g., for git fetch of worker branches), use `wsl hostname -I` to discover the current IP, or rely on GitHub as the shared remote.

## Design Decisions

### 1. Queue-Based Pull Model Over SSH

**Decision:** The remote worker agent polls the dispatcher for work rather than the dispatcher SSHing into the remote machine to spawn processes.

**Why:**
- SSH-based spawning is fragile across network interruptions and WSL2 sleep/resume cycles
- Pull model lets the worker self-heal: if the dispatcher restarts, the worker just keeps polling
- Pull model scales to N workers on M machines without the dispatcher managing SSH sessions
- Worker has full control over its own process lifecycle and cleanup

**Trade-off:** Slightly higher latency (poll interval) vs. immediate SSH dispatch. Acceptable for tasks that run minutes to hours.

### 2. FastAPI Coordination API Over Message Broker

**Decision:** The dispatcher exposes a FastAPI + Uvicorn HTTP API rather than introducing Redis, RabbitMQ, or a hand-rolled `http.server`.

**Why:**
- FastAPI over raw `http.server`: Pydantic validation for work packages/results, built-in `HTTPBearer` auth, auto-generated `/docs` endpoint for debugging, async support for SSE (Phase 2). ~20 min more setup, saves hours of hand-rolling JSON parsing and routing.
- FastAPI over a message broker: the dispatcher already has the task queue (SQLite DB) — no need to duplicate state. Worker count is small (1-5 remote workers). Easier to debug: `curl` the API to inspect state.

**Dependencies:** `pip install fastapi uvicorn pydantic` on the dispatcher machine only. The worker agent uses `httpx` (or stdlib `urllib`) to call the API.

**Trade-off:** No built-in pub/sub or delivery guarantees. Acceptable because the dispatcher DB is the source of truth and the protocol is idempotent. SSE (Phase 2) adds push-style notification without a broker.

### 3. Thick Worker Agent

**Decision:** The remote worker agent has all backend code and builds its own execution commands locally.

**Why:**
- Worker needs the backend-specific tooling installed anyway (Claude CLI, Codex CLI, Python + OpenAI SDK for Grok/Gemini)
- Avoids shipping shell commands over the network (security concern, escaping issues)
- Worker can adapt to its local environment (paths, installed versions)
- Dispatcher sends a high-level work package, not a low-level command

**Trade-off:** Worker agent must stay in sync with dispatcher code. Mitigated by version handshake on claim (see `POST /api/v1/claim` → Version handshake) and CENTRAL self-sync before task execution.

### 4. Result Push Over Shared Storage

**Decision:** The worker agent POSTs results back to the dispatcher via HTTP rather than writing to shared NFS/SMB storage.

**Why:**
- No shared filesystem to configure and maintain
- Results are small (JSON, typically <100KB)
- Logs can be streamed incrementally or uploaded on completion
- Works across any network topology (LAN, VPN, cloud)

### 5. Bearer Token Auth From Day One

**Decision:** The coordination API requires a shared bearer token from Phase 1, not deferred.

**Why:**
- The API mutates task state (claims leases, finalizes tasks, triggers reconciliation)
- Even on a home LAN, any device can reach the API — guests, compromised IoT, misconfigured services
- A single `Authorization: Bearer <token>` header check is trivial to implement
- Cost of adding it later is the same as adding it now, but the security gap is real in the interim

### 6. Dispatcher Pre-Computes Backend for Claim Filtering

**Decision:** The dispatcher computes effective backends for eligible tasks before returning them to remote workers, rather than adding a backend column to the DB.

**Why:**
- `effective_backend` is a runtime computation that depends on dispatcher config + task metadata + model policy
- Adding a stored column would require keeping it in sync with every config change
- The eligible task set is small (tens, not thousands) — iterating and computing is cheap
- Claim requests are infrequent (one every 5s per idle worker)

### 7. Git Worktrees Required for Concurrent Same-Repo Execution

**Decision:** The worker agent creates a git worktree for each task targeting the same repo when running concurrently.

**Why:**
- Without worktrees, concurrent tasks on the same repo corrupt each other (`git checkout main` in task B destroys task A's working state)
- Worktrees provide complete isolation with minimal overhead
- The alternative (max-1-per-repo) would underutilize the 64GB remote machine

## Coordination API

The dispatcher starts a FastAPI + Uvicorn server in a background thread on a configurable port (default: `7429`) when remote workers are enabled. Uvicorn runs in a single-worker async mode (no multiprocessing). The auto-generated OpenAPI docs at `/docs` are available for debugging.

### Authentication

Every request (including `GET /api/v1/status`) must include `Authorization: Bearer <token>` where `<token>` matches `CENTRAL_COORDINATION_TOKEN` in the dispatcher's environment. Implemented via FastAPI's `HTTPBearer` dependency. Token comparison uses `hmac.compare_digest()` to prevent timing attacks.

`CENTRAL_COORDINATION_TOKEN` is added to `_SAFE_SHELL_KEYS` in `dispatcher_control.py` so it is loaded from shell profiles alongside API keys.

### Endpoints

#### `POST /api/v1/claim`

Worker requests a task. Dispatcher claims the next eligible task matching the worker's capabilities and returns a work package. POST (not GET) because this mutates state (creates a lease).

**Request:**
```json
{
  "worker_id": "wsl2-ryzen-7700x",
  "backends": ["claude", "gemini", "grok"],
  "central_version": "39fb29d"
}
```

**Claim filtering mechanism:** The dispatcher iterates eligible snapshots from `order_eligible_snapshots`, calls `resolve_task_worker_backend(snapshot, self.config.worker_mode)` for each, and returns the first whose effective backend is in the worker's `backends` list. This is the same computation `_spawn_worker()` performs today, just done before claiming instead of after. If no match is found after iterating all eligible tasks, returns 204.

Remote claims also check global caps: if `active_remote_count >= config.max_remote_workers`, return 204. Per-repo caps apply across local + remote: if `active_count_for_repo(repo) >= config.max_repo_workers`, skip that task and try the next.

**Version handshake:** The `central_version` field is the short git SHA of the worker's CENTRAL checkout. The dispatcher compares it to its own. If they diverge, the response includes `"version_warning": "stale"` but still serves work. The worker agent logs the warning. A future hardening step can make this a hard reject.

**Response 200:**
```json
{
  "work_package": {
    "task_id": "ECO-578",
    "run_id": "ECO-578-1711200000",
    "title": "Add Playwright e2e tests for dashboard",
    "worker_backend": "claude",
    "worker_model": "claude-sonnet-4-6",
    "worker_effort": "high",
    "repo_name": "eco-system",
    "repo_root_relative": "projects/eco-system",
    "branch_prefix": "worker/ECO-578",
    "prompt_body": "## Objective\n...",
    "task_kind": "mutating",
    "category": "implementation",
    "sandbox_mode": "workspace-write",
    "deliverables_json": "[...]",
    "scope_notes_json": "[...]",
    "validation_commands_json": "[...]",
    "env_allowlist": ["GROK_API_KEY", "XAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "timeout_seconds": 3600,
    "dispatcher_version": "39fb29d"
  },
  "version_warning": null
}
```

**Response 204:** No eligible work (or caps reached). Worker should back off and retry after poll interval.

Key fields:
- `repo_root_relative` is relative to `$HOME` — worker resolves `$HOME/projects/eco-system`
- `branch_prefix` is the branch naming convention for mutating tasks (worker creates `worker/ECO-578` branch)
- `env_allowlist` tells the worker which env vars are needed (worker loads them locally)
- `prompt_body` is the full assembled prompt (same as what `build_worker_task()` produces)

#### `POST /api/v1/heartbeat`

Worker sends periodic heartbeats to keep its lease alive.

**Request:**
```json
{
  "task_id": "ECO-578",
  "run_id": "ECO-578-1711200000",
  "worker_id": "wsl2-ryzen-7700x",
  "status": "running",
  "progress_note": "Running pytest suite (47/120 tests)"
}
```

**Response 200:** Lease renewed.

**Response 404:** Dispatcher has no record of this task assignment (e.g., after dispatcher restart). Worker should re-register by including `"reattach": true` in the next heartbeat. The dispatcher reconstructs the `ActiveWorker` entry from lease metadata in the DB (see §Remote Worker Re-Adoption).

**Response 410 (Gone):** Task was cancelled or reassigned. Worker must stop execution immediately.

**Cancellation trigger path:** When an operator runs `dispatcher_control.py cancel ECO-578` (or the dispatcher auto-cancels due to max retries), the dispatcher:
1. Transitions the task's runtime state to `cancelled` in the DB
2. Removes the task from `self._active` (if present)
3. The remote worker's next heartbeat for that task finds no active lease → returns 410
4. The worker receives 410, kills its subprocess, cleans up the worktree, and resumes polling

The worker only discovers cancellation on its next heartbeat (up to 30s delay). This is acceptable — local workers also have a small delay between the cancel command and SIGTERM delivery.

**Thread-safety:** The HTTP handler does NOT update `self._active` directly. Instead, it writes the heartbeat to the DB via `_sync_worker_lease()` (each HTTP request opens its own SQLite connection). The main dispatcher loop's `_process_active()` reads heartbeat freshness from the DB for remote workers (see §Concurrency Model).

#### `POST /api/v1/result`

Worker submits the completed result.

**Request:**
```json
{
  "task_id": "ECO-578",
  "run_id": "ECO-578-1711200000",
  "worker_id": "wsl2-ryzen-7700x",
  "result": { "...standard worker_result schema..." },
  "result_branch": "worker/ECO-578",
  "result_commit_sha": "a1b2c3d4e5f6",
  "log_tail": "last 200 lines of worker output"
}
```

**Response 200:** Result accepted, task finalized.

**Response 409:** Task already finalized (duplicate submission). Worker discards.

**Result persistence:** The HTTP handler writes the result JSON to `worker_results_dir/{task_id}/{run_id}.json` and the log tail to `worker_logs/{task_id}/{run_id}.log` on the dispatcher's local filesystem. It then enqueues a finalization request (see §Concurrency Model) that the main dispatcher loop picks up, calling the existing `_parse_worker_result()` and `_finalize_worker()` pipeline against these local files. This avoids a parallel finalization code path — remote results are finalized through the same code as local results.

#### `POST /api/v1/log`

Optional: worker streams log chunks during execution for live monitoring.

**Request:**
```json
{
  "task_id": "ECO-578",
  "run_id": "ECO-578-1711200000",
  "chunk": "2026-03-23 14:05:12 Running validation command 3/5...\n"
}
```

**Response 200:** Logged. Appended to `worker_logs/{task_id}/{run_id}.log`.

#### `GET /api/v1/status`

Health check endpoint. Returns dispatcher state summary. Requires auth (consistent with Design Decision #5 — the response exposes worker IDs, agent IPs, version SHAs, and task counts).

**Response 200:**
```json
{
  "dispatcher_id": "mac-m1-dispatcher",
  "dispatcher_version": "39fb29d",
  "active_local_workers": 3,
  "active_remote_workers": 2,
  "remote_agents": {"wsl2-ryzen-7700x": {"active": 2, "last_seen": "2026-03-23T14:05:00Z"}},
  "eligible_tasks": 7,
  "uptime_seconds": 14400
}
```

## Concurrency Model

### Thread-Safety Between FastAPI Server and Dispatcher Loop

The coordination server (FastAPI + Uvicorn) runs in a daemon `threading.Thread`. Uvicorn runs a single-worker async event loop in that thread. The main dispatcher loop runs synchronously in the main thread. They share:

1. **SQLite DB** — each thread opens its own connection. All connections set `PRAGMA busy_timeout = 5000` to handle concurrent write contention gracefully. This is a new requirement — existing dispatcher connections must also set this pragma.

2. **`self._active` dictionary** — guarded by a `threading.Lock` (`self._active_lock`). Both the HTTP handler and the main loop acquire this lock when reading or writing `_active`. The claim handler holds the lock across the full cap-check → claim → register sequence (see §Coordination Server Thread). The main loop holds it when adding/removing entries in `_process_active()` and `_fill_workers()`. Lock hold times are short (no I/O while holding — DB writes in the claim handler are the exception, justified by the cap-race fix).

3. **Finalization queue** — a `queue.Queue` for result submissions. The HTTP handler writes result files to disk, then puts a `(task_id, run_id)` tuple on the queue. The main loop's `_process_active()` drains this queue each cycle, calling `_finalize_worker()` for each entry. This ensures finalization runs single-threaded in the main loop, reusing all existing finalization code unchanged.

### Remote Worker Monitoring in `_process_active()`

For remote workers (`state.is_remote == True`), the monitoring path differs:

```python
# In _process_active():

# First: drain the finalization queue (results submitted via HTTP)
# Lock held briefly per entry — finalize_worker itself doesn't need the lock
while not self._coordination_server._finalization_queue.empty():
    task_id, run_id = self._coordination_server._finalization_queue.get_nowait()
    with self._active_lock:
        state = self._active.pop(task_id, None)
    if state is not None:
        self._finalize_worker(state, timed_out=False)

# Snapshot active items under lock, then iterate without holding it
with self._active_lock:
    active_snapshot = list(self._active.items())

HEARTBEAT_LIVENESS_MULTIPLIER = 3
heartbeat_liveness_window = self.config.heartbeat_seconds * HEARTBEAT_LIVENESS_MULTIPLIER

for task_id, state in active_snapshot:
    if state.is_remote:
        # Check heartbeat liveness (NOT task timeout — that's for total execution time)
        # Remote crash detection uses a heartbeat liveness window: 3x heartbeat interval.
        # With 30s heartbeats, a crashed worker is detected in ~90s.
        # Local workers get immediate detection via proc.poll(); this is the remote equivalent.
        lease = task_db.fetch_active_lease(conn, task_id)
        timed_out = False
        if lease and lease["last_heartbeat_at"]:
            heartbeat_age = (utc_now() - parse_utc(lease["last_heartbeat_at"])).total_seconds()
            if heartbeat_age > heartbeat_liveness_window:
                timed_out = True
        # Also check total task timeout (same as local workers)
        if not timed_out:
            elapsed = (utc_now() - parse_utc(state.started_at)).total_seconds()
            if elapsed > state.timeout_seconds:
                timed_out = True
        if timed_out:
            with self._active_lock:
                self._active.pop(task_id, None)
            self._finalize_worker(state, timed_out=True)
        continue
    # ... existing local worker monitoring (proc.poll(), etc.)
```

No `proc.poll()`, no `process_matches()`, no local PID checks for remote workers.

**Two-tier timeout model for remote workers:**
- **Heartbeat liveness window** (`heartbeat_seconds * 3`, e.g., 90s): detects crashes and network partitions. Comparable responsiveness to local `proc.poll()`.
- **Task execution timeout** (`timeout_seconds`, e.g., 3600s): caps total execution time, same as local workers.

## Worker Agent

### `scripts/worker_agent.py`

A standalone daemon that runs on the remote machine. It:

1. Loads local API keys from shell profile (same `_load_shell_api_keys()` pattern)
2. Computes its CENTRAL version: `git -C $CENTRAL_ROOT rev-parse --short HEAD`
3. Syncs CENTRAL itself: `git -C $CENTRAL_ROOT fetch && git pull --ff-only`
4. Polls `POST /api/v1/claim` at configurable interval (default: 5s)
5. On receiving a work package:
   a. Resolves local repo path from `repo_root_relative`
   b. Runs `git fetch` on target repo
   c. Creates a git worktree: `git worktree add ../worktrees/{run_id} main`
   d. Instantiates the appropriate backend (from `worker_backend` field)
   e. Calls `backend.prepare()` to get the command
   f. Spawns the worker subprocess locally in the worktree directory
   g. Monitors subprocess: sends heartbeats every 30s via `POST /api/v1/heartbeat`
   h. On completion: reads result JSON, collects branch/commit info, POSTs to `POST /api/v1/result`
   i. Cleans up worktree (`git worktree remove`), returns to polling

### Configuration

```bash
# Start worker agent
python3 scripts/worker_agent.py \
  --dispatcher-url http://192.168.1.100:7429 \
  --auth-token <shared-token> \
  --worker-id wsl2-ryzen-7700x \
  --max-concurrent 3 \
  --poll-interval 5 \
  --backends claude,gemini,grok
```

- `--dispatcher-url`: Where to reach the dispatcher API
- `--auth-token`: Shared bearer token (must match `CENTRAL_COORDINATION_TOKEN` on dispatcher)
- `--worker-id`: Unique identifier for this worker machine (used in leases)
- `--max-concurrent`: How many tasks to run in parallel on this machine
- `--poll-interval`: Seconds between claim attempts when idle
- `--backends`: Which backends this worker can execute (skip codex if Codex CLI not installed)

### CENTRAL Self-Sync

Before starting the poll loop (and optionally between tasks), the worker agent syncs its own CENTRAL checkout:

```python
def _sync_central(self) -> str:
    """Sync CENTRAL repo and return current short SHA.

    Respects CENTRAL_ROOT env var for portable paths (per CLAUDE.md).
    Logs warnings on non-zero exit codes instead of silently continuing.
    """
    central_root = Path(os.environ.get("CENTRAL_ROOT", str(Path.home() / "projects" / "CENTRAL")))

    fetch = subprocess.run(
        ["git", "-C", str(central_root), "fetch", "--prune"],
        capture_output=True, text=True, timeout=60,
    )
    if fetch.returncode != 0:
        log.warning("CENTRAL fetch failed: %s", fetch.stderr.strip())

    pull = subprocess.run(
        ["git", "-C", str(central_root), "pull", "--ff-only"],
        capture_output=True, text=True, timeout=60,
    )
    if pull.returncode != 0:
        log.warning(
            "CENTRAL pull --ff-only failed (local commits or diverged branch?): %s",
            pull.stderr.strip(),
        )

    rev = subprocess.run(
        ["git", "-C", str(central_root), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    return rev.stdout.strip()
```

This keeps backend code, schema files, and the worker agent itself in sync with the dispatcher. Non-zero exit codes are logged as warnings so stale-code conditions are visible in worker agent logs rather than silently ignored.

## Git Sync and Worktree Isolation

### Pre-Task Sync

Before executing each task, the worker agent syncs the target repo and creates an isolated worktree:

```python
def _prepare_worktree(self, repo_path: Path, task_id: str, run_id: str, branch: str = "main") -> Path:
    """Fetch, create worktree, return worktree path.

    Uses run_id (not just task_id) in the path to avoid collisions on task retry.
    A retried task gets a new run_id, so its worktree path is unique even if the
    previous run's worktree wasn't cleaned up (e.g., after a crash).
    """
    # Fetch latest from remote
    subprocess.run(
        ["git", "-C", str(repo_path), "fetch", "--prune"],
        capture_output=True, timeout=60
    )
    # Clean stale worktrees from prior crashes
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "prune"],
        capture_output=True, timeout=10
    )
    # Create worktree for this run (run_id is unique across retries)
    worktree_path = repo_path.parent / "worktrees" / run_id
    if worktree_path.exists():
        # Shouldn't happen with run_id, but defensive cleanup
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "remove", "--force", str(worktree_path)],
            capture_output=True, timeout=30
        )
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", str(worktree_path), branch],
        capture_output=True, timeout=30
    )
    return worktree_path
```

Each task runs in its own worktree. Multiple tasks targeting the same repo run concurrently without conflict.

### Post-Task Commit Transport

Workers that produce commits (mutating tasks) leave them on a named branch in the worktree. The naming convention is `worker/{task_id}` (provided in the work package as `branch_prefix`).

The result payload includes:
- `result_branch`: The branch name containing the worker's commits (e.g., `worker/ECO-578`)
- `result_commit_sha`: The HEAD commit SHA of that branch

**Commit retrieval:** The dispatcher or operator retrieves remote commits by adding the remote machine as a git remote:

```bash
# One-time setup per remote worker
git remote add wsl2 ssh://user@wsl2-host/home/user/projects/eco-system

# Fetch worker's branch
git fetch wsl2 worker/ECO-578
```

Alternatively, if both machines push to the same GitHub remote, the worker can `git push origin worker/ECO-578` and the dispatcher fetches from origin. This is the preferred path — it reuses existing GitHub auth and avoids SSH setup between machines.

### Worktree Cleanup

After result submission, the worker agent removes the worktree:

```python
subprocess.run(
    ["git", "-C", str(repo_path), "worktree", "remove", str(worktree_path)],
    capture_output=True, timeout=30
)
```

Failed tasks also clean up their worktrees. Stale worktrees (from crashes) are cleaned on agent restart via `git worktree prune`.

## Remote Worker Re-Adoption

When the dispatcher restarts, in-flight remote workers have no local PID to check. The existing `_adopt_active_workers()` code looks for local PIDs via `process_matches()` — this doesn't work for remote workers.

### Adoption mechanism:

1. On restart, the dispatcher loads all active leases from `task_active_leases`
2. For leases where `supervision.is_remote == True`, the dispatcher creates `ActiveWorker` entries with `is_remote=True`, `proc=None`, and populates fields from the supervision metadata
3. These entries go into `self._active` with a grace period: the dispatcher waits `heartbeat_seconds * HEARTBEAT_LIVENESS_MULTIPLIER` (same as the liveness window, e.g., 90s) for a heartbeat from the remote worker
4. The remote worker's next heartbeat hits the `/api/v1/heartbeat` endpoint. If the dispatcher has the task in `_active`, normal heartbeat processing resumes. If not (timing race), the worker includes `"reattach": true` and the dispatcher reconstructs the `ActiveWorker` from the lease
5. If no heartbeat arrives within the grace period, the task is marked failed (worker presumed dead)

### Supervision metadata for remote workers:

```json
{
  "is_remote": true,
  "remote_worker_id": "wsl2-ryzen-7700x",
  "remote_worker_ip": "192.168.1.50",
  "run_id": "ECO-578-1711200000",
  "result_path": "worker_results/ECO-578/ECO-578-1711200000.json",
  "log_path": "worker_logs/ECO-578/ECO-578-1711200000.log",
  "started_at": "2026-03-23T14:00:00Z",
  "timeout_seconds": 3600,
  "worker_model": "claude-sonnet-4-6",
  "worker_backend": "claude"
}
```

No `worker_pid`, `worker_pgid`, or `process_start_token` fields — these are local-only.

## Log Collection

### During Execution

The worker agent captures stdout/stderr into a local log file (same as local workers). Optionally streams chunks to `POST /api/v1/log` for live monitoring. The dispatcher appends chunks to `worker_logs/{task_id}/{run_id}.log`.

### On Completion

The final `POST /api/v1/result` includes `log_tail` (last 200 lines). The dispatcher appends this to `worker_logs/{task_id}/{run_id}.log` if the file already exists (from log streaming), or creates it otherwise. A `--- log_tail (final submission) ---` separator is inserted before the tail when appending.

Full logs remain on the remote machine at `$HOME/projects/CENTRAL/state/central_runtime/worker_logs/`. Retrievable via SSH/scp if needed for debugging.

## Security Model

### API Keys

- API keys are configured locally on each machine (shell profile)
- Keys never sent over the coordination API
- The `env_allowlist` field in work packages is informational only — tells the worker which keys it should have loaded
- Worker agent validates it has required keys before accepting a task; rejects with specific error if missing

### Coordination API Authentication

Shared bearer token configured on both machines, required from Phase 1.

```
# Dispatcher environment (add to shell profile)
CENTRAL_COORDINATION_TOKEN=<random-64-char-hex>

# Worker agent CLI
--auth-token <same-token>
```

Every request includes `Authorization: Bearer <token>`. The dispatcher validates using `hmac.compare_digest(received_token, expected_token)` to prevent timing side-channels.

`CENTRAL_COORDINATION_TOKEN` is added to `_SAFE_SHELL_KEYS` in `dispatcher_control.py` so it is loaded from shell profiles at dispatcher startup alongside API keys.

### Worker Identity

Each worker agent registers with a unique `worker_id` (e.g., `wsl2-ryzen-7700x`). The dispatcher tracks which tasks are assigned to which worker. A worker can only heartbeat/submit results for tasks it was assigned.

## Dispatcher Changes

### New: Config Fields and Persistence

```python
# In config.py
REMOTE_WORKERS_ENABLED_ENV = "CENTRAL_REMOTE_WORKERS"
COORDINATION_PORT_ENV = "CENTRAL_COORDINATION_PORT"
COORDINATION_TOKEN_ENV = "CENTRAL_COORDINATION_TOKEN"
DEFAULT_COORDINATION_PORT = 7429
DEFAULT_MAX_REMOTE_WORKERS = 3
DEFAULT_MAX_REPO_WORKERS = 3  # per-repo cap across local + remote
```

```python
# In DispatcherConfig dataclass
remote_workers_enabled: bool = False
coordination_port: int = 7429
max_remote_workers: int = 3
max_repo_workers: int = 3
```

**Config persistence flow:**

1. `dispatcher_control.py` argparse adds:
   - `--remote-workers` (store_true)
   - `--coordination-port PORT` (int, default 7429)
   - `--max-remote-workers N` (int, default 3)
   - `--max-repo-workers N` (int, default 3)

2. `save_config()` persists these to `state/central_runtime/dispatcher_config.json`:
   ```json
   {
     "worker_mode": "claude",
     "default_worker_model": "claude-sonnet-4-6",
     "remote_workers_enabled": true,
     "coordination_port": 7429,
     "max_remote_workers": 3,
     "max_repo_workers": 3
   }
   ```

3. `load_saved_config()` reads them back, with defaults for missing fields (backward compat)

4. `start_dispatcher()` passes them through to `DispatcherConfig()`

5. The `config` subcommand displays them in status output

### New: Coordination Server Thread

The coordination server runs FastAPI + Uvicorn in a daemon thread. FastAPI's async handlers run in Uvicorn's event loop; SQLite calls and `_active` mutations use `run_in_executor` to avoid blocking the event loop.

```python
class CoordinationServer(threading.Thread):
    """FastAPI + Uvicorn coordination API for remote workers."""

    def __init__(self, dispatcher: Dispatcher, port: int, token: str):
        super().__init__(daemon=True)
        self._dispatcher = dispatcher
        self._port = port
        self._token = token
        self._finalization_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._app = self._build_app()

    def run(self):
        import uvicorn
        uvicorn.run(self._app, host="0.0.0.0", port=self._port, log_level="warning")

    def _build_app(self):
        from fastapi import FastAPI, Depends, HTTPException
        from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

        app = FastAPI(title="CENTRAL Dispatcher Coordination API")
        security = HTTPBearer()

        def verify_token(creds: HTTPAuthorizationCredentials = Depends(security)):
            if not hmac.compare_digest(creds.credentials, self._token):
                raise HTTPException(status_code=401, detail="Invalid token")

        @app.post("/api/v1/claim", dependencies=[Depends(verify_token)])
        async def claim(body: ClaimRequest): ...

        @app.post("/api/v1/heartbeat", dependencies=[Depends(verify_token)])
        async def heartbeat(body: HeartbeatRequest): ...

        @app.post("/api/v1/result", dependencies=[Depends(verify_token)])
        async def result(body: ResultSubmission): ...

        @app.get("/api/v1/status", dependencies=[Depends(verify_token)])
        async def status(): ...

        return app

    def _handle_claim(self, body: dict) -> Response:
        """Claim next eligible task for remote worker.

        IMPORTANT: The _active_lock is held across the full cap-check → claim → register
        sequence to prevent two concurrent claim requests from both passing the per-repo
        cap check before either registers. The lock is held during the DB claim call,
        which is fast (single row update). This is acceptable because claim requests are
        infrequent (~1 per 5s per idle worker).
        """
        worker_id = body["worker_id"]
        backends = body["backends"]
        central_version = body.get("central_version")

        conn = self._dispatcher._connect()
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            eligible = task_db.order_eligible_snapshots(conn, ...)
            work_package = None

            # Hold lock across cap check + claim + register to prevent race
            with self._dispatcher._active_lock:
                # Check global remote cap
                remote_count = sum(1 for w in self._dispatcher._active.values() if w.is_remote)
                if remote_count >= self._dispatcher.config.max_remote_workers:
                    return Response(204)

                for snapshot in eligible:
                    effective_backend = resolve_task_worker_backend(
                        snapshot, self._dispatcher.config.worker_mode
                    )
                    if effective_backend not in backends:
                        continue
                    # Check per-repo cap (accurate because we hold the lock)
                    repo = snapshot.get("target_repo_root", "")
                    repo_count = sum(
                        1 for w in self._dispatcher._active.values()
                        if w.task.get("target_repo_root") == repo
                    )
                    if repo_count >= self._dispatcher.config.max_repo_workers:
                        continue
                    # Claim this task (DB write while holding lock — fast, acceptable)
                    claimed = task_db.runtime_claim(conn, snapshot["task_id"], ...)
                    if claimed is None:
                        continue  # Lost race to local dispatcher or another remote, try next
                    worker_task = build_worker_task(claimed, ...)
                    work_package = self._build_work_package(claimed, worker_task)
                    # Register in _active (already under lock)
                    state = ActiveWorker(
                        task=claimed, is_remote=True,
                        remote_worker_id=worker_id, ...
                    )
                    self._dispatcher._active[claimed["task_id"]] = state
                    break  # Claimed one task, done

            if work_package is None:
                return Response(204)  # No matching eligible work

            version_warning = None
            if central_version and central_version != self._dispatcher._central_version:
                version_warning = "stale"
            return Response(200, json={
                "work_package": work_package,
                "version_warning": version_warning,
            })
        finally:
            conn.close()

    def _handle_heartbeat(self, body: dict) -> Response:
        """Renew lease via DB. Main loop reads freshness from DB."""
        task_id = body["task_id"]
        worker_id = body["worker_id"]
        reattach = body.get("reattach", False)

        conn = self._dispatcher._connect()
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            lease = task_db.fetch_active_lease(conn, task_id)
            if lease is None:
                return Response(410)  # Task gone
            # Check ownership via supervision metadata (not lease_owner_id, which is
            # a dispatcher-generated value that the remote worker doesn't know)
            supervision = json.loads(lease.get("lease_metadata_json") or "{}")
            remote_id = (supervision.get("supervision") or {}).get("remote_worker_id")
            if remote_id != worker_id and not reattach:
                return Response(410)  # Not your task

            # Renew lease in DB
            task_db.renew_lease(conn, task_id, lease_seconds=...)
            # Update progress note in supervision metadata
            ...

            # If dispatcher restarted and lost _active entry, reconstruct it
            with self._dispatcher._active_lock:
                if task_id not in self._dispatcher._active:
                    if reattach:
                        state = self._reconstruct_remote_worker(lease)
                        self._dispatcher._active[task_id] = state
                    else:
                        return Response(404)  # Tell worker to reattach
            return Response(200)
        finally:
            conn.close()

    def _handle_result(self, body: dict) -> Response:
        """Write result to disk, enqueue finalization for main loop."""
        task_id = body["task_id"]
        run_id = body["run_id"]

        # Write result JSON to local filesystem
        result_path = self._dispatcher.paths.worker_results_dir / task_id / f"{run_id}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(body["result"]), encoding="utf-8")

        # Write log tail — append if log streaming was active (file already exists),
        # otherwise create. This avoids overwriting streamed chunks with just the tail.
        log_path = self._dispatcher.paths.worker_logs_dir / task_id / f"{run_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if log_path.exists() else "w"
        with open(log_path, mode, encoding="utf-8") as f:
            if mode == "a":
                f.write("\n--- log_tail (final submission) ---\n")
            f.write(body.get("log_tail", ""))

        # Store branch/commit info in supervision metadata
        ...

        # Enqueue for main-loop finalization
        self._finalization_queue.put((task_id, run_id))
        return Response(200)
```

### Modified: `_process_active()` Handles Remote Workers

The pseudocode for remote worker monitoring in `_process_active()` is specified in §Concurrency Model above (Remote Worker Monitoring). The dispatcher changes section here focuses on the structural additions (new classes, new fields).

### Modified: SQLite Connections

All `sqlite3.connect()` calls in the dispatcher gain:

```python
conn = sqlite3.connect(str(self._db_path))
conn.execute("PRAGMA busy_timeout = 5000")
```

This prevents `OperationalError: database is locked` when the HTTP thread and main loop write concurrently. The 5-second timeout is generous for the expected write frequency.

### Modified: `dispatcher_control.py` Status Display

```
Dispatcher Status
  Mode: claude | Model: claude-sonnet-4-6
  Local workers: 3/5
  Remote workers: 2/3 (wsl2-ryzen-7700x: 2 active)
  Coordination API: http://0.0.0.0:7429 (auth: enabled)
  Version: 39fb29d
```

## Failure Modes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Worker crashes mid-task | Heartbeat stops arriving | Dispatcher detects via heartbeat liveness window (~90s), marks failed, eligible for retry |
| Network partition | Heartbeat stops arriving | Same as crash — detected within liveness window, task retried (possibly on local worker) |
| Dispatcher restarts | Worker gets connection refused | Worker retries connection; sends heartbeat with `reattach: true`; dispatcher reconstructs ActiveWorker from DB lease |
| Worker machine sleeps | Heartbeat stops | Lease expires, task retried. Worker agent detects stale task on wake, kills subprocess, cleans up worktree |
| Duplicate result submission | 409 from `/api/v1/result` | Worker discards, moves on |
| Dirty repo on remote | Pre-task worktree creation handles this | Worktrees start clean from the fetched main branch |
| CENTRAL version drift | Version warning on claim | Worker logs warning; future hardening can reject |
| SQLite busy contention | `PRAGMA busy_timeout = 5000` | Retries automatically for up to 5s; extremely unlikely to exhaust with <10 concurrent writers |

## Rollout Plan

### Phase 1: Foundation

1. Add `PRAGMA busy_timeout = 5000` to all existing SQLite connections
2. Add `threading.Lock` for `self._active` dictionary
3. Add `CoordinationServer` to dispatcher (all endpoints, bearer auth from day one)
4. Add config fields to `DispatcherConfig`, `save_config()`, `load_saved_config()`, argparse
5. Create `worker_agent.py` with single-worker execution + git worktrees
6. Ship `worker_result.schema.json` as part of CENTRAL (copy from Dispatcher repo) so remote machines don't strictly require the Dispatcher repo for non-Codex backends
7. Test with stub backend on LAN
8. Add `--remote-workers` flag to `dispatcher_control.py`

### Phase 2: Production Use

**Prerequisite:** GitHub SSH key or HTTPS credential configured on the WSL2 machine, with push access to all target repos. Required for step 12 (commit transport). Verify with `ssh -T git@github.com` or `gh auth status` on the remote machine before proceeding.

9. Enable multi-concurrent on worker agent
10. Add SSE endpoint (`GET /api/v1/events`) for push-style task notification — worker opens one long-lived connection, dispatcher pushes task-available events via FastAPI `StreamingResponse`. Replaces 5s polling with near-instant pickup. Worker falls back to polling if SSE connection drops.
11. Add log streaming via `POST /api/v1/log`
12. Run Playwright/heavy tasks on WSL2, lightweight tasks on Mac
13. Add commit transport (worker pushes branches to GitHub, dispatcher fetches)

### Phase 3: Hardening

14. Add worker health dashboard to planner panel
15. Make version handshake a hard reject on major drift
16. Handle WSL2 sleep/resume gracefully (stale worktree cleanup on wake, exponential backoff reconnect)
17. Add per-backend concurrency caps (e.g., max 2 claude tasks globally across all workers)
18. Add Zeroconf/mDNS service discovery (`pip install zeroconf`) — dispatcher advertises `_central-dispatcher._tcp.local.`, worker agent auto-discovers without hardcoded IPs

## Resolved Questions

1. **Worktree isolation:** Required from Phase 1. All concurrent tasks on the same repo use separate git worktrees. No open question.

2. **Backend filtering mechanism:** Dispatcher iterates eligible snapshots and computes `effective_backend` per-task before claiming. No DB schema change needed. Resolved.

3. **Dispatcher repo dependency:** Ship `worker_result.schema.json` into CENTRAL. Codex backend still requires the Dispatcher repo; non-Codex backends (Claude, Gemini, Grok) work without it. Document that Codex workers need the Dispatcher repo.

## Open Questions

1. **Should the worker push commits to GitHub automatically?** Current design leaves this to the operator. Auto-push would close the loop faster but requires GitHub auth on the remote machine. Worth enabling in Phase 2.
