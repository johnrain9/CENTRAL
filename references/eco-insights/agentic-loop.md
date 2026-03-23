# Agentic Loop — Ecosystem Insights

Extracted from worker compaction summaries (25 sessions). Generalizable patterns only — task-specific noise excluded. See also: `cross-cutting.md`.

---

## Architecture

**1. Stub/real split: `execute_turn` vs `execute_turn_with_deps`**
`AgenticLoopState::execute_turn` in `state.rs` is a shell stub that only performs state-machine label transitions and returns `TurnOutcome::Complete` without doing real work. The real 8-step cycle (cancel check → context assembly → budget check → inference slot → inference call → tool dispatch → persist turn → continuation decision) lives in `execute_turn_with_deps` in `turn_cycle.rs`. Production code checks `turn_deps.is_some()` in `run_session_loop_inner` and dispatches to `execute_turn_with_deps` directly; the stub is only reached when `turn_deps` is `None` (loop-control unit tests).

**2. Five-trait dependency injection via `TurnDeps`**
All external collaborators are injected as trait objects in a `TurnDeps` struct: `ContextInterface`, `InferenceInterface`, `ToolInterface`, `PersistenceInterface`, and `BudgetInterface`. Production adapters (`ProductionContextAdapter`, `ProductionInferenceAdapter`, `ProductionToolAdapter`, `ProductionPersistenceAdapter`, `SessionBudgetChecker`) live in `src/agentic_loop/adapters.rs` and are built by `activate_session` in `runtime.rs`. This allows `execute_turn_with_deps` to be unit-tested with mock implementations without bringing up the full runtime.

**3. Multi-round continuation is internal to a single `execute_turn_with_deps` call**
The session loop (`run_session_loop_inner`) calls `execute_turn_with_deps` once per human message. All multi-round continuation (ToolUse → tool execution → next inference round) loops inside that function — the outer session loop is never re-entered between rounds. The session loop only sees `Complete`, `Cancelled`, or `Error` as exit states.

**4. `BudgetReservation` is a RAII guard with a reconcile callback**
`BudgetInterface::reserve()` returns a `BudgetReservation` that must be reconciled after inference completes. `reservation.reconcile(tokens, usd)` invokes an optional callback (e.g., to update `SessionBudgetChecker`'s in-memory `Arc<AtomicU64>` accumulator). The RAII pattern ensures budget accounting always runs even if inference returns early or errors.

**5. Session status updates are load-bearing, not optional**
`update_session_status` in `runtime.rs` must call `SessionStore::transition_status` with a real DB write. The harness's `wait_for_session_not_active` polling loop reads this field; a no-op stub causes graceful-shutdown tests to hang indefinitely and never pass.

**6. Cost events flow asynchronously through an mpsc channel**
`ProductionInferenceAdapter` emits `CostEvent` via `inference_client.cost_tx` after each inference call. A background task (`drain_cost_events`) reads from this channel and writes to `cost_records` via `CostStore::insert` in `spawn_blocking`. Because drain is async, budget checkers must use an in-memory accumulator (updated synchronously via `BudgetReservation::reconcile`) rather than querying the DB. See `cross-cutting.md` §4 for the full wiring description.

**7. `ToolContext` carries `task_id` propagated from session state**
`ToolContext` includes `task_id: Option<String>` plumbed from `AgenticLoopState.task_id`. This lets task-bound tools (`task_report`) identify their task without an independent lookup. The field flows: `AgenticLoopState` → `ProductionToolAdapter::execute` → `ToolContext`.

---

## Gotchas

**8. `TurnOutcome::Cancelled` does not write a `turn_complete` event**
When the cancellation token fires, the loop exits with `SessionExitAction::Idle` and emits `session_idle` (with `reason = "stopped_by_human"`), but does not emit `turn_complete` and does not persist the in-progress assistant turn. Tests and WebSocket clients checking for full-turn completion must listen for `session_idle` instead.

**9. Compaction threshold formula fires every turn with default 0.8**
The formula `round(1.0 / threshold)` with the default `threshold = 0.8` yields `1`, meaning compaction triggers after every single turn. Tests expecting 2 inference calls then observe 3 because the extra call is the `generate_summary()` compaction invocation. See `cross-cutting.md` §7 for the correct formula and test mitigation strategies.

**10. `load_recent_turns` only returns finalized turns**
`TurnStore::load_recent_turns` filters on `AND is_complete = 1`. Turns written by `begin_turn_write` but not yet finalized are invisible to context assembly. If the finalize step is skipped or crashes, the context appears stale even though data was written.

**11. The stub `execute_turn` is kept for unit tests — never call it from production paths**
The original stub `AgenticLoopState::execute_turn` in `state.rs` must not be removed (unit tests reference it) and must not be reachable from production paths. The `run_session_loop_inner` check `turn_deps.is_some()` is the guard. Future developers who add new production entry points must maintain this guard.

**12. Unmatched `tool_use` blocks break context assembly**
Every `tool_use` content block must have exactly one `tool_result`. `compensate_pending_tools` generates synthetic error `tool_result` blocks for any `tool_use` calls that did not complete due to cancellation. If the return value of `compensate_pending_tools` is dropped rather than added to the turn builder, downstream context assembly will include unmatched blocks, which some providers reject outright.

---

## Patterns

**13. `tokio::select! { biased; ... }` for cancellation priority**
The agentic loop uses biased `select!` macros to check the cancellation token before processing new work, ensuring fast shutdown even under high load. Any new async hot-path added to the loop should follow this pattern and check the cancel token first.

**14. Context assembly reads from DB, not from in-flight state**
`ProductionContextAdapter::assemble` builds inference context by querying `TurnStore::load_recent_turns` and `SummaryStore::load_active_summaries`. It does not use any in-memory cache of the current turn. Tool results from the current round must be persisted to the DB before the next `assemble` call, or passed directly as parameters to the next inference call.

**15. `SessionBudgetChecker` uses average-cost projection**
Because `drain_cost_events` is async, future turn cost cannot be known at `reserve()` time. `SessionBudgetChecker` projects next-turn cost as `accumulated + (accumulated / turn_count)`. For the first turn (count = 0), projection is 0 and the turn is always allowed. This is a best-effort gate with bounded overshoot — at most one extra turn beyond the limit.

**16. Pod-scoped discovery injection at the adapter layer**
`ProductionToolAdapter::execute` automatically injects the caller session's `pod_id` into `DiscoveryQuery` when the session calls `discover_sessions`. Enforcement happens at the adapter layer, not in the AI's prompt — the AI cannot escape its pod by modifying the query.

---

## Testing

**17. Wiring tests are the canonical integration signal**
The 7 tests in `tests/wiring/` each construct `EcosystemRuntime` through the real `activate_session` path but inject `MockInferenceProvider` instead of a real provider. A failure in wiring tests — even if unit tests pass — means production turns will silently not do real work. Run with `cargo test --test wiring` as the minimum gate before any agentic loop change.

**18. `dispatch_prompt` must wait for `TurnCompleted`, not a fixed turn count**
The correct signal for a fully completed turn (including multi-round tool-use loops) is `ActivityEvent::TurnCompleted { stop_reason: "endturn" }`, emitted after the entire `execute_turn_with_deps` call finishes. Polling `turn_count >= before + 2` fires too early for multi-round turns. See `cross-cutting.md` §20.
