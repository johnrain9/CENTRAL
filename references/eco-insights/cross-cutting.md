# Cross-Cutting Ecosystem Insights

Insights that span multiple subsystems. Consolidated from per-subsystem files to avoid duplication. Each subsystem file links to the canonical explanation here.

---

## Architecture

**1. Adapter pattern via `TurnDeps` — the primary integration boundary**
The agentic loop's turn cycle (`execute_turn_with_deps`) operates through five trait objects bundled in a `TurnDeps` struct: `ContextInterface`, `InferenceInterface`, `ToolInterface`, `PersistenceInterface`, and `BudgetInterface`. Production adapters in `src/agentic_loop/adapters.rs` bridge real infrastructure to these traits; the mock implementations of each trait enable isolated unit testing. The session loop checks `deps.turn_deps.is_some()` and dispatches to `execute_turn_with_deps`; when `None`, a stub runs (loop-control unit tests only).

**2. Deferred refs pattern for circular initialization**
Components with mutual Arc dependencies (SessionManager ↔ InferenceClient ↔ ToolExecutor ↔ MessageRouter) cannot inject each other at construction time. Each component exposes an `attach_*` method (or `set_deferred_refs`) that accepts the missing refs and stores them in `OnceLock<Arc<T>>` or `Option<Arc<T>>` fields. All deferred refs are wired at a single startup step (step 10.5 in `EcosystemRuntime::new`) — using any component before this step completes causes a panic on the unwrap.

**3. Two-phase turn persistence: `begin_turn_write` then `finalize_turn`**
`TurnStore::persist_turn` splits each write into two SQL steps: `begin_turn_write` inserts the row with `is_complete = 0`, then `finalize_turn` sets `is_complete = 1` and atomically increments `sessions.turn_count`. Rows not yet finalized are invisible to `load_recent_turns` (which filters `AND is_complete = 1`). One prompt round writes 2 rows (user + assistant) and increments `turn_count` by 2; tool-use continuation rounds write a third row and increment by 3.

**4. Cost tracking via mpsc channel, not inline calls**
After each inference call, `ProductionInferenceAdapter` sends a `CostEvent { session_id, request_id, model_id, usage, cost, timestamp }` to `inference_client.cost_tx`. The `drain_cost_events` background task in `runtime.rs` reads from this channel and persists rows to `cost_records` via `CostStore::insert`. Two separate wiring points must both be implemented: emission in the inference adapter and draining in the runtime. A stub at either point silently suppresses all cost records.

**5. Fixed-point microdollars (u64) for all cost arithmetic**
All costs are represented as `u64` microdollars rather than `f64`. `Cost::as_microdollars()` is divided by `1_000_000.0` only when writing `cost_usd: f64` to `NewCostRecord`. This prevents floating-point accumulation errors when costs are summed or compared across many turns. Any new code that manipulates costs must use the designated cost types, not raw floats.

**6. `AssembledContext.content` is an opaque JSON-serialized `ContextPayload`**
`ProductionContextAdapter::assemble()` serializes the full `InferenceRequest` as a JSON string into `AssembledContext { estimated_tokens: u64, content: String }`. `ProductionInferenceAdapter` deserializes this string back to reconstruct the request before calling the provider. This boundary fully decouples context assembly from the inference subsystem — changes to `InferenceRequest` fields only require updating serialization and deserialization in their respective adapters.

---

## Gotchas

**7. Compaction threshold formula was inverted — fires every turn with default 0.8**
The original formula `(1.0 / compaction_threshold).round() as u32` gives `1` for the default `threshold = 0.8`, meaning compaction fires after every single turn. The correct formula is either `(100.0 * compaction_threshold).round() as u32` (threshold=0.8 → compact at 80 turns) or a token-fraction check using `budget_used / context_window >= threshold`. Tests that do not exercise compaction must either use a very low turn count, pre-seed a summary (which bypasses the trigger), or explicitly apply the corrected formula.

**8. Proactive compaction consumes one mock inference response**
When compaction triggers during context assembly, `generate_summary()` makes a real inference call using the same provider. In wiring tests backed by a `MockInferenceProvider` with a fixed response queue, this consumes one scripted response before the actual turn's inference call, leaving the turn without a response and causing a timeout. Tests that do not exercise compaction must prevent it from firing (pre-seed a summary or use a turn count below the threshold). Tests that do exercise compaction must queue one extra response for the summary call.

**9. `spawn_blocking` is required for all SQLite calls in async context**
`PersistenceLayer::connection()` returns a `MutexGuard<rusqlite::Connection>`. The guard is not `Send` and cannot be held across an `.await` point — doing so will deadlock or fail to compile. Every DB operation from async code (`TurnStore::persist_turn`, `CostStore::insert`, `SummaryStore::save`, `SessionStore::transition_status`) must be wrapped in `tokio::task::spawn_blocking`.

**10. `TrustLevel::Supervised` (the default) blocks tool execution indefinitely**
`WriteFile` and other tools with `requires_approval = true` wait for human approval via a 5-minute timeout when the session's trust level is `Supervised`. Because sessions are created `Supervised` by default, any test that exercises tool use will silently hang at the approval gate. Always set `TrustLevel::Trusted` via `TestHarnessBuilder::with_trust_level` for tests that include tool-use rounds; use `Supervised` only when specifically testing the approval flow.

**11. `TurnOutcome::Cancelled` emits `session_idle`, not `turn_complete`**
When a session is stopped (cancellation token fired), the agentic loop exits with `session_idle` (with `reason = "stopped_by_human"`) and does not emit a `turn_complete` streaming event and does not persist the in-progress assistant turn. Tests and WebSocket clients that wait for `turn_complete` after a stop will hang indefinitely. Subscribe to `ActivityEvent::SessionIdle` or check for `session_idle` in the WebSocket stream.

**12. `StoreMessageRouter::send_notify` does not emit `ActivityEvent`s**
Two message router objects exist: (1) `StoreMessageRouter` (the REST-facing API, used by `ApiState`), which does NOT emit `ActivityEvent::MessageSent` or `ActivityEvent::MessageDelivered`; (2) `runtime.rs`'s internal `route_message` (`pub(crate)`), which DOES emit these events. Tests that assert on WebSocket activity events after calling the REST notify endpoint will time out with no error. To test the activity event path, emit directly via `activity_tx` or trigger through the internal runtime path.

**13. Direct DB creation bypasses the in-memory session registry**
`SessionStore::create()` inserts a DB row but does not add the session to `SessionManager`'s in-memory `RwLock<HashMap>` registry. The `ensure_single_session()` startup helper and any test that seeds sessions via raw SQL share this problem. Subsequent calls to `activate_session()`, `session_detail()`, or any registry-dependent method will return `ActivationError::NotFound` or behave as if the session does not exist. Always use `SessionManager::create_session()`, which writes both the DB row and the registry entry.

**14. OpenAI function calling rejects bare object schemas without `"properties"`**
All tool `input_schema()` implementations that return `{"type": "object"}` must also include `"properties": {}` — even for parameter-less tools. The OpenAI API rejects schemas missing this field with `object schema missing properties`. Use the `all_standard_tool_schemas_are_openai_compatible` unit test as a regression gate.

**15. `ANTHROPIC_API_KEY` env var overrides Claude Max OAuth in worker subprocesses**
If `ANTHROPIC_API_KEY` is set in the shell (e.g., for local integration testing with a zero-credit account), it is propagated through `subprocess.Popen` to all worker processes, which then fail with "Credit balance is too low." Strip the variable explicitly when launching workers: `{k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}`.

---

## Patterns

**16. Wiring tests are the canonical integration gate for the agentic loop**
Seven tests in `tests/wiring/` exercise the full stack (DB → context assembly → inference → tool dispatch → persistence → shutdown) using `TestHarness` with `MockInferenceProvider`. They cover: startup sequence, tool result flow, context assembly with seeded turns, cost event persistence, compaction cycle, graceful shutdown. All 7 must pass before any agentic loop change is considered auditable. Run with `cargo test --test wiring`.

**17. Unique session names via `AtomicU64` in the test harness**
Multiple test harness instances sharing the same SQLite database (e.g., in crash-recovery tests that restart the runtime) will collide on the default session name, producing `NameConflict` errors. Add a global `AtomicU64` counter in `TestHarnessBuilder::build` and append its value to the session name to guarantee uniqueness across all instances in a process.

---

## Testing

**18. Poll `turn_count >= before + 2` as the per-round completion signal**
The correct harness wait pattern after sending a prompt is: sample `before = sessions.turn_count`, send the prompt, poll until `turn_count >= before + 2`. This reflects the two DB writes (user turn + assistant turn) per inference round. Use `>= before + 3` for tool-use rounds that write a third tool-result turn. Do not use a fixed sleep — the count is the reliable signal.

**19. Small `model_context_window` is required to exercise `FractionThreshold` in tests**
The `FractionThreshold` compaction trigger computes `budget_used / model_context_window`. With the default `model_context_window = 200_000` tokens, 30 short mock turns (~450 tokens) produce a 0.23% ratio — far below any threshold. To exercise the compaction path in tests, set `model_context_window = 3_000` with `max_output_tokens = 256` in `SessionAssemblyConfig`. This makes 30 turns (~712 tokens) exceed the 10% threshold (300 tokens).

**20. Multi-round tool loops require `ActivityEvent::TurnCompleted` for end detection**
Polling `turn_count >= before + 2` works only for single-round turns. Multi-round tool-use loops (ToolUse → tool execution → next inference round) complete all continuation rounds inside a single `execute_turn_with_deps` call; the harness polling can fire while the loop is still on round 1. The correct end-of-loop signal is `ActivityEvent::TurnCompleted { stop_reason: "endturn" }`, which is only emitted after the entire multi-round turn cycle finishes.

**21. E2E tests need live inference smoke coverage**
Integration tests with mocks can pass while the live stack has basic errors. A dedicated smoke test (e.g., `tests/smoke/phase2.rs`) using real inference API calls with a cheap model catches wiring failures that mocks hide: boot sequence, notify end-to-end, request/reply end-to-end. The smoke job should run nightly, not on every commit.

**22. Per-commit vs nightly test split**
Backend wiring tests + fast E2E paths should run per-commit (target: ≤60s total). Slower E2E paths (multi-session WS, approval flows, compaction) and all smoke tests run nightly. Missing tests from the speed-budget table cause CI time to silently exceed the budget — maintain the table explicitly with per-test timing estimates.
