1. Severity: critical
   Location: Coordination API > `GET /api/v1/claim`
   Issue: The LLD says `_claim_next()` will accept an optional `backend_filter` parameter, but the current `_claim_next()` (dispatcher.py:528-541) delegates to `task_db.runtime_claim()`, which has no concept of backend filtering. `runtime_claim` selects the next eligible task from the DB using `order_eligible_snapshots` — the `effective_backend` is not determined until `_spawn_worker()` calls `resolve_task_worker_backend()` *after* claiming. The backend is a runtime computation (based on dispatcher config, task metadata, and model inference at lines 544-559), not a stored DB column. There is no way to filter by backend at claim time without either (a) adding a backend column to the DB, (b) claiming-then-rejecting in a loop, or (c) computing effective backend for every eligible snapshot before claiming. The LLD hand-waves this as "also filter by effective_backend" but the data model doesn't support it.
   Suggested fix: Design and document the actual filtering mechanism. Either add `effective_backend` as a computed column in `task_runtime_state`, or specify that the dispatcher pre-computes backends for all eligible tasks and only returns matching ones to the remote worker's claim request. Document the performance implications of either approach.

2. Severity: critical
   Location: Dispatcher Changes > Modified: `_process_active()` Handles Remote Workers (lines 313-330)
   Issue: The `ActiveWorker` dataclass (config.py:129-149) is a plain `@dataclass`, not frozen, but the LLD proposes adding `is_remote`, `remote_worker_id`, and `last_remote_heartbeat` fields. The real problem is that `_process_active()` (dispatcher.py:1301-1326) and `_finalize_worker()` (dispatcher.py:1224-1299) deeply assume a local subprocess: they call `state.proc.poll()`, `state.proc.returncode`, read `state.result_path` from the local filesystem, and call `normalize_claude_result()` on local log files. For remote workers, none of these exist. The LLD says "no `proc` object" and "liveness determined by heartbeat freshness" but doesn't specify how `_finalize_worker` will work without a local result file, log file, or returncode. The entire finalization pipeline — result parsing, capacity-limit detection, health snapshots, artifact collection — needs a parallel remote path that the LLD doesn't design.
   Suggested fix: Design the remote finalization path explicitly. Specify that `POST /api/v1/result` triggers finalization directly in the HTTP handler, bypassing `_process_active()` polling. Document which `_finalize_worker` helpers apply to remote results (received via HTTP) and which are local-only.

3. Severity: critical
   Location: Rollout Plan > Phase 1 vs Phase 3 (lines 468-489)
   Issue: Bearer token authentication is deferred to Phase 3 (line 486: "Add bearer token auth"), but the coordination API is deployed on the LAN in Phase 1. Without authentication, any device on the LAN can claim tasks, submit fake results, or inject malicious log entries. The `POST /api/v1/result` endpoint finalizes tasks and triggers auto-reconciliation (dispatcher.py:1179-1201), which can close tasks and modify planner status. An unauthenticated API that mutates the task DB is a significant security gap, even on a home LAN — any compromised device, guest, or misconfigured service can corrupt the entire task pipeline.
   Suggested fix: Move bearer token auth to Phase 1. It's a single `Authorization` header check — trivial to implement and test. Deploy it alongside the first HTTP endpoint, not two phases later.

4. Severity: major
   Location: Coordination API > Endpoints > `GET /api/v1/claim` (lines 118-155)
   Issue: The claim endpoint is a GET with query parameters (`?backends=claude,gemini,grok&max=1`), but it mutates state (claims a task, creating a lease in the DB). GET requests should be idempotent. HTTP intermediaries (proxies, load balancers, browser prefetch, monitoring tools) may replay GET requests. A retried GET claim could double-claim tasks or produce confusing 409 responses. The LLD even acknowledges the need for idempotency (line 88) but then uses a non-idempotent verb.
   Suggested fix: Change `GET /api/v1/claim` to `POST /api/v1/claim` with the backend list and max-concurrent in the request body. This correctly signals state mutation.

5. Severity: major
   Location: Worker Agent > Step 3b (line 236)
   Issue: The git sync strategy runs `git checkout main && git pull` on every task, but many tasks in the work package target feature branches (workers create branches for mutating tasks per line 411). If worker A is running a mutating task on repo X and worker B claims a second task for repo X, the `git checkout main` will fail or corrupt worker A's in-progress work. The `--max-concurrent 3` flag (line 249) makes this scenario likely. The LLD acknowledges worktree isolation as an open question (line 496) but doesn't address the concrete race condition for Phase 1.
   Suggested fix: Either (a) mandate `--max-concurrent 1` per repo (not per worker) in Phase 1 and document the limitation, or (b) require git worktrees from the start for concurrent execution on the same repo. The current design silently corrupts concurrent work.

6. Severity: major
   Location: Dispatcher Changes > New: Coordination Server Thread (lines 270-301)
   Issue: The LLD proposes running the HTTP server in a `threading.Thread`, but the dispatcher's `_claim_next()` (dispatcher.py:528-541) opens a new SQLite connection each call and uses `task_db.runtime_claim()` which does `BEGIN IMMEDIATE` transactions. SQLite's default threading mode is `serialized`, but `sqlite3.connect()` returns connections that are not safe to share across threads. The coordination server thread calling `_claim_next()` concurrently with the main dispatcher loop calling `_claim_next()`, `_fill_workers()`, and `_process_active()` creates concurrent write transactions on the same DB. While SQLite handles this via locking, the dispatcher has no retry logic for `SQLITE_BUSY` errors, and the default busy timeout is 0 — meaning concurrent writes will raise `OperationalError: database is locked` under load.
   Suggested fix: Specify a `busy_timeout` on all connections (e.g., `conn.execute("PRAGMA busy_timeout = 5000")`), or route all DB-mutating operations through a single-threaded queue/lock. Document the concurrency model explicitly.

7. Severity: major
   Location: Dispatcher Changes > Modified: Worker Slot Accounting (lines 335-343)
   Issue: Remote workers are not counted against `max_workers`, and the remote agent self-limits via `--max-concurrent`. But there's no global cap on remote workers. If someone starts 10 worker agents each with `--max-concurrent 5`, the dispatcher will hand out 50 tasks simultaneously. The dispatcher's `_claim_next()` has no awareness of how many remote tasks are active. Combined with the lack of per-repo concurrency enforcement for remote workers (the existing `DEFAULT_REPO_MAX_CONCURRENT_WORKERS = 3` in dispatcher_control.py:719 only applies to local slot counting), this could overwhelm repos, API rate limits, or the DB.
   Suggested fix: Add a `max_remote_workers` config field to the dispatcher. The coordination server should track active remote leases and reject claims when the cap is reached. Per-repo caps should also apply across local + remote workers.

8. Severity: major
   Location: Worker Agent (lines 226-258)
   Issue: The worker agent is specified as `scripts/worker_agent.py`, a standalone daemon, but it needs to instantiate backend classes from `central_runtime_v2.backends` (step 3c-3d, lines 235-237). The backend classes have hard dependencies on `AUTONOMY_ROOT` and `AUTONOMY_SCHEMA_PATH` (config.py:27-30), which require the `Dispatcher` repo to be present as a sibling. The LLD assumes the remote machine has the same repo layout, but `Dispatcher` is not listed in the assumed repos (lines 43-49). If the Dispatcher repo is absent, `load_autonomy_runner()` fails, and the `CodexBackend` is completely broken. `ClaudeBackend` also depends on `AUTONOMY_SCHEMA_PATH` for `--json-schema` (backends.py:59-61).
   Suggested fix: Document that the `Dispatcher` repo must also be cloned on the remote machine, or refactor the worker agent to not depend on `AUTONOMY_ROOT`. Consider shipping the schema file as part of CENTRAL or making it optional.

9. Severity: major
   Location: Coordination API > `POST /api/v1/heartbeat` (lines 156-173)
   Issue: The dispatcher's existing heartbeat mechanism (dispatcher.py:412-413, `_heartbeat_worker`) writes to the SQLite DB via `_sync_worker_lease()`, updating `task_active_leases`. Remote worker heartbeats arrive via HTTP in a background thread, but the LLD doesn't specify how the coordination server thread will update `ActiveWorker.last_heartbeat_monotonic` in the main thread's `_active` dictionary. Without this update, `_process_active()` will see stale `last_heartbeat_monotonic` values and erroneously time out healthy remote workers. Thread-safe access to `self._active` is not addressed anywhere in the LLD.
   Suggested fix: Define the synchronization mechanism for `self._active` updates from the HTTP thread. Options include a `threading.Lock` guarding `_active`, or having the HTTP handler only write to the DB and letting `_process_active()` read heartbeat freshness from the DB for remote workers.

10. Severity: major
    Location: Failure Modes > Dispatcher restarts (line 463)
    Issue: The table says "Worker keeps running, re-heartbeats when dispatcher returns; dispatcher adopts via lease metadata." But the dispatcher's adoption logic (`_adopt_active_workers`, dispatcher.py:668-758) looks for local PIDs via `process_matches(state.pid, state.process_start_token)` (line 726). Remote workers have no local PID. The adoption code will either skip remote workers entirely (no `supervision` metadata with a valid local PID) or try to check a PID that doesn't exist on the dispatcher machine. The LLD doesn't design the remote adoption path.
    Suggested fix: Design remote worker re-adoption explicitly. The coordination server should accept re-registration from workers that were running during a dispatcher restart. Define a `POST /api/v1/register` endpoint or modify `/api/v1/heartbeat` to handle the case where the dispatcher has no record of the worker's task.

11. Severity: major
    Location: Design Decisions > §3 Thick Worker Agent (lines 90-100)
    Issue: The LLD says "Worker agent must stay in sync with dispatcher code. Mitigated by both running from the same CENTRAL repo checkout." But the git sync strategy (lines 383-408) only syncs target repos (e.g., eco-system), not CENTRAL itself. If CENTRAL is updated on the Mac but the remote machine's CENTRAL checkout is stale, the worker agent will run with outdated backend code, potentially producing incompatible results or crashing on schema mismatches. The LLD doesn't address self-update of the worker agent code.
    Suggested fix: Add CENTRAL itself to the pre-task sync list, or add a version handshake to the claim endpoint where the dispatcher returns its code version and the worker rejects claims if versions diverge.

12. Severity: minor
    Location: Coordination API > `POST /api/v1/result` (lines 175-192)
    Issue: The result payload includes `log_tail` as "last 200 lines of worker output," but the dispatcher's `_parse_worker_result` (dispatcher.py:906-977) expects `result_path` to contain a full `worker_result` JSON file with specific schema fields (`status`, `schema_version`, `summary`, `validation`, etc.). The LLD's result endpoint wraps this in a `{"result": {...}}` envelope, but doesn't specify how the dispatcher will extract and write this to a local `result_path` for `_parse_worker_result` to consume. The existing finalization pipeline reads from disk, not from an in-memory HTTP payload.
    Suggested fix: Specify that the coordination server's `_handle_result` writes the received result JSON to `worker_results_dir/{task_id}/{run_id}.json` before calling `_finalize_worker`, or document a new finalization path that operates on in-memory data.

13. Severity: minor
    Location: New: Config Fields (lines 347-358)
    Issue: The LLD proposes `remote_workers_enabled` and `coordination_port` fields in `DispatcherConfig`, but `DispatcherConfig` (config.py:152-166) is constructed in `dispatcher_control.py`'s `start_dispatcher()` via the runtime CLI args. The LLD shows env vars (`CENTRAL_REMOTE_WORKERS`, `CENTRAL_COORDINATION_PORT`) but doesn't show how these flow through the `dispatcher_control.py` argument parsing, `save_config()`, or `load_saved_config()`. The existing config persistence (dispatcher_control.py:408-426) has no handling for these new fields.
    Suggested fix: Show the full config flow: add the fields to `save_config()`, `load_saved_config()`, the argparse `config` subcommand, and the `start_dispatcher()` CLI args builder. Without this, the feature is undeployable via the existing operator interface.

14. Severity: minor
    Location: Security Model > Coordination API Authentication (lines 438-451)
    Issue: The shared bearer token is a "random-64-char-hex" stored in an environment variable on both machines. But `_load_shell_api_keys()` (dispatcher_control.py:93-119) only loads keys from `_SAFE_SHELL_KEYS` — `CENTRAL_COORDINATION_TOKEN` is not in that allowlist. If the dispatcher is started from a non-login shell, the token won't be present. Additionally, the token comparison must use constant-time comparison (`hmac.compare_digest`) to prevent timing attacks, but this isn't specified.
    Suggested fix: Add `CENTRAL_COORDINATION_TOKEN` to the allowlist or document that it must be set in the environment before starting. Specify `hmac.compare_digest` for token comparison.

15. Severity: minor
    Location: Post-Task Commit Handling (lines 410-415)
    Issue: "Workers that produce commits leave them on a local branch. The result payload includes `files_changed` and the branch/commit reference." But the work package schema (lines 123-144) has no `target_branch` field — it only has `repo_root_relative`. The worker doesn't know what branch to create or what naming convention to use. The result schema in `worker_result.schema.json` has `files_changed` as a list of file paths, not branch/commit references. There's no mechanism for the dispatcher to pull or fetch from the remote machine's git to obtain these commits.
    Suggested fix: Add `target_branch` or `branch_prefix` to the work package schema. Add `result_branch` and `result_commit_sha` to the result payload. Define how the dispatcher retrieves remote commits (e.g., `git fetch` from the remote machine's repo, or the worker pushes to a shared remote).
