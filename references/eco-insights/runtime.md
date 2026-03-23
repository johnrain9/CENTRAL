# Ecosystem Runtime — Architectural Insights

Extracted from worker compaction summaries (25 sessions). Covers the orchestration layer: startup sequence, session activation, component wiring, and the turn cycle entry point. See also: `cross-cutting.md`.

---

## Architecture

**1. Stub-first wiring strategy**
The ecosystem uses a deliberate stub-then-wire approach: core functions like `execute_turn`, `update_session_status`, and `drain_cost_events` begin as no-op stubs and are later wired to real infrastructure. Stubs must be clearly marked (via comments) so they can be targeted in REWORK tasks; silent stubs are the most common source of wiring test failures.

**2. `TurnDeps` as the wiring boundary**
`execute_turn_with_deps` in `turn_cycle.rs` receives five trait objects via `TurnDeps`. The connection to real infrastructure is established by building production adapter structs and storing them in `LoopDependencies.turn_deps: Option<Arc<TurnDeps>>`. When `None`, the stub runs; when `Some`, the real cycle runs. See `cross-cutting.md` §1.

**3. `activate_session` is where all production adapters are assembled**
`runtime.rs::activate_session()` is the correct place to construct all five production adapters (`ProductionContextAdapter`, `ProductionInferenceAdapter`, `ProductionToolAdapter`, `ProductionPersistenceAdapter`, `SessionBudgetChecker`) and assemble them into a `TurnDeps`. All `Arc` refs to infrastructure components (PersistenceLayer, InferenceClient, ToolExecutor, ContextManager) are available at this point.

**4. Cost event wiring requires two separate implementation sites**
Cost tracking requires (1) `ProductionInferenceAdapter::call()` emitting a `CostEvent` to `inference_client.cost_tx` after each successful inference call, and (2) `drain_cost_events` reading from that channel and writing to `CostStore`. Fixing only one site has no visible effect. The `request_id` must be captured from the `StreamHandle` before `consume_stream` takes ownership. See `cross-cutting.md` §4.

**5. `update_session_status` must call `SessionStore::transition_status`**
The no-op stub prevents session status from persisting. `SessionStore::transition_status` validates the allowed `Idle ↔ Active` transition before writing. Both `activate_session` (Idle→Active) and `mark_idle` (Active→Idle) depend on this being a real write — the harness's `wait_for_session_not_active` loop polls the DB.

**6. Session loop delegates via state machine with explicit transitions**
`run_session_loop_inner` in `mod.rs` dispatches via `state.execute_turn()` (or directly to `execute_turn_with_deps` when `turn_deps` is `Some`). The `AgenticLoopState` struct owns the per-session loop state machine (Idle, AssemblingContext, CallingInference, PersistingTurn, CheckingQueue). Transitions are tracked via `transition_to()`.

**7. `SessionManager` holds deferred Arc refs to break circular construction**
All long-lived infrastructure references are stored as `Arc<T>`. `SessionManager` itself is initialized with a deferred attach step (step 10.5) to break the circular dependency where `EcosystemRuntime` needs `SessionManager` while `SessionManager` needs refs that `EcosystemRuntime` owns. The same pattern applies to `alert_tx` channels and other injected refs. See `cross-cutting.md` §2.

---

## Gotchas

**8. Compaction consuming mock inference responses causes unexpected test failures**
With the broken threshold formula (`compact_threshold = 1` for default 0.8), proactive compaction fires on the first context `assemble` call whenever any turns exist. This consumes the available mock response, leaving the actual turn with no response and causing a `MockInference script exhausted` panic or a harness timeout. See `cross-cutting.md` §§7–8.

**9. Graceful shutdown requires cancellation check before tool dispatch**
If the cancel token fires during the tool execution window but `dispatch_tools_sequential` does not check the token before calling `tool.execute`, the tool runs to OS-level completion and the turn returns `Complete` instead of `Cancelled`. The cancel check must occur at the start of each tool dispatch iteration.

**10. Tool process groups and orphan accumulation**
Spawning child processes via `tokio::process::Command` without killing the entire process group on cancellation leaves orphaned processes. Worker termination must send SIGKILL to the process group (`kill(-pid, SIGKILL)`), not just to the process itself.

**11. `LoopDependencies` test constructors must be updated on new fields**
Whenever a new field is added to `LoopDependencies` (e.g., `turn_deps`), every existing test construction site in `errors.rs` and elsewhere must be updated with the default value (`None`). Missing one causes a compile error but leaves no runtime signal.

**12. `SummaryStore.is_empty()` check is the correct double-compaction guard**
The proactive compaction path in `ContextAdapter::assemble` must check `summaries.is_empty()` before triggering compaction. If a summary already exists for the session, compaction must be skipped regardless of turn count. Failing to check this causes double-compaction in tests that seed a pre-existing summary.

**13. Harness `send_prompt` returns `Complete` even on cancellation**
`send_prompt_to_session` maps `Ok(_)` to `LoopTurnOutcome::Complete` and `Err(_)` to `Error`. To detect a `Cancelled` outcome, the harness must separately poll session status or inspect persisted tool result content for "Tool cancelled" markers.

**14. Workers sharing a repo cause transient compile failures under high concurrency**
Running many concurrent workers against the same Rust repo causes `cargo` to contend on the lock file and artifact cache, producing transient process crashes (not test failures). Cap concurrent workers per repo (e.g., 4) to eliminate these failures.

---

## Patterns

**15. Adapter pattern for trait object injection**
Production infrastructure is bridged to turn-cycle trait objects via thin adapter structs. Each adapter holds an `Arc` to its backing service and implements exactly one interface trait. See `cross-cutting.md` §1 for the full pattern.

**16. `spawn_blocking` for all SQLite calls from async**
Every call to a synchronous SQLite function from async context must go through `tokio::task::spawn_blocking`. The pattern: acquire the mutex, perform the write inside the blocking closure, return the result across the blocking boundary. See `cross-cutting.md` §9.

**17. Deferred component initialization via `Option<Arc<T>>`**
`SessionManager` holds `Option<Arc<T>>` fields populated after construction via `attach_*` methods. This breaks circular construction dependencies. The same pattern applies to `alert_tx` channels.

**18. Compaction as a synchronous summary-before-inference step**
Proactive compaction happens inside `ContextAdapter::assemble` before the context is returned. If compaction is needed, it calls `generate_summary` (an inference call) and then `SummaryStore::commit_compaction` before assembling the final context. A compacting turn will make N+1 total inference calls.

---

## Testing

**19. Wiring tests must seed precise turn and response counts**
Tests asserting on `turn_count` (e.g., "25 seeded + 2 new = 27") must control exactly which mock responses are consumed by compaction vs. the actual turn. Seeding a pre-existing summary prevents spurious compaction and isolates the test to a single inference call.

**20. Test isolation via in-memory SQLite**
Each wiring test creates a fresh SQLite `:memory:` database via `with_conn`. All seeded data (turns, sessions, summaries, cost records) is inserted in test setup and verified after the harness completes. No shared state between tests.

**21. `wait_for_request_count` is the synchronization primitive for cancellation tests**
The graceful shutdown test spawns a cancel task that fires the shutdown token when `mock.request_count() >= 1`. This races against the session loop's first inference call. The cancel must fire before tool dispatch begins for the `Cancelled` outcome to propagate correctly.

**22. All tool input schemas need a systematic validator test**
A single unit test (`all_standard_tool_schemas_are_openai_compatible`) verifies that every registered tool's `input_schema()` returns a valid OpenAI-compatible JSON schema (with `"properties": {}`). This prevents schema regressions from silently breaking live inference calls.
