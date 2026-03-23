# Session Manager — Ecosystem Insights

Extracted from worker compaction summaries across ECO tasks. Covers the in-memory registry, session lifecycle, config resolution, budget enforcement, and discovery. See also: `cross-cutting.md`.

---

## Architecture

**1. In-memory registry is the runtime source of truth**
`SessionManager` holds a `RwLock<HashMap<SessionId, SessionEntry>>` loaded from DB at startup. All runtime reads hit the registry; DB is written through on mutations. Never query the DB for live session state during normal operation.

**2. `count_active()` scans the registry, not an atomic counter**
`count_active()` scans the in-memory registry. An earlier design used an `active_count` atomic field; that was removed in Session Manager LLD v1.1. Any code or docs still referencing `active_count` are stale.

**3. Session lifecycle: `Idle → Active → Idle/Retired/Errored`**
Transitions are managed by `SessionStore::transition_status`. Sessions persist by default; retirement is explicit opt-in. The allowed graph is `Idle ↔ Active`, `* → Errored`, `Errored ↔ Active/Idle`, `Idle/Errored → Retired` (terminal).

**4. 4-tier config resolution: session → pod → system → built-in**
`SessionConfig::resolve()` accepts optional `pod_defaults`. Missing values fall through each tier in order. The function is pure — no DB access, no side effects — making it trivially testable by passing only the tiers you want to exercise.

**5. `activate_session` wires all production adapters**
It builds `LoopDependencies` including `TurnDeps` with five trait-object adapters: `ProductionContextAdapter`, `ProductionInferenceAdapter`, `ProductionToolAdapter`, `ProductionPersistenceAdapter`, and `SessionBudgetChecker`. Each bridges runtime-owned types to `turn_cycle` interfaces. See `cross-cutting.md` §1.

**6. `activation.rs` is `#[path]`-included into `runtime.rs`**
`use super::*` inside `activation.rs` resolves against `runtime.rs`'s scope. Adding imports to `activation.rs` directly may not work — add them in `runtime.rs` instead.

**7. Pod assignment is immutable after creation**
`session.pod_id` is set at `create_session` time and never updated. Routing and boundary decisions on pod membership are stable for the session's lifetime.

**8. Pod-scoped name uniqueness replaces global uniqueness**
The same session name is allowed in different pods. `SessionStore::name_taken_in_scope()` checks within the pod (or globally for unaffiliated sessions). The old `UNIQUE(name)` DB constraint was removed.

**9. `discover_for_session()` auto-injects the caller's pod**
The server enforces pod boundary: when an AI session calls discover, the server injects its `pod_id`. AIs cannot enumerate cross-pod sessions. `discover()` (unauthenticated path) accepts an explicit `DiscoveryQuery`. Keep these two paths separate.

**10. Session cap enforcement: reject, don't queue**
When the session limit is reached, `create_session` returns a cap error. Message Router handles upstream queuing at the message layer; Session Manager does not queue creation requests.

**11. In-memory pod registry mirrors the session registry**
`pods: RwLock<HashMap<PodId, PodRecord>>` is loaded alongside sessions at startup. `get_pod_id(session_id)` reads from the session registry (no DB call). `pod_session_config(pod_id)` reads from the pod registry (no DB call). Both are hot paths in activation.

---

## Gotchas

**12. `TrustLevel::Supervised` (the default) blocks tool execution indefinitely**
Sessions are created `Supervised` by default. Any tool with `requires_approval = true` (e.g., `WriteFile`) will block indefinitely waiting for human approval. This silently breaks multi-turn tool tests — the loop stalls at turn 2 with no error, just a timeout. See `cross-cutting.md` §10.

**13. Budget checking was a `NoopBudget` stub for a long time**
`activation.rs` originally wired `NoopBudget`, which never blocked execution. Budget enforcement only works after `SessionBudgetChecker` is wired at activation. Prior to this wiring, budget limits in config had no effect.

**14. `drain_cost_events` is async — DB budget queries race with it**
A budget check using `CostStore::total_by_session` directly will miss in-flight costs. Use an in-memory `Arc<AtomicU64>` accumulator (initialized from DB at activation, updated synchronously via `BudgetReservation::reconcile()`) instead.

**15. Tests seeding sessions via `SessionStore::create()` bypass the registry**
Direct DB inserts do not populate the in-memory `SessionEntry` map. Tests calling `session_manager.session_detail()` or `pod_session_config()` on a DB-seeded session will behave differently than sessions created through the manager. Prefer `SessionManager::create_session()` in tests.

**16. Discovery pagination was initially hardcoded**
The initial `discover()` implementation returned `next_cursor: None, has_more: false` unconditionally. Cursor-based pagination was added in LLD v1.1. Session-count-sensitive tests written before this change may assume unpaginated results.

---

## Patterns

**17. In-memory cost accumulator for budget enforcement**
`SessionBudgetChecker` holds `Arc<AtomicU64>` for accumulated microdollars and a turn counter. Initialized from `CostStore::total_by_session` at activation (before any turns run). Updated synchronously in `BudgetReservation::reconcile(actual_usd)` after each inference call. This avoids the async `drain_cost_events` race.

**18. Average-cost projection for next-turn budget gate**
Formula: `projected = accumulated + (accumulated / turn_count)`. For turn 0 (no history): project = 0, always allow. For subsequent turns: block if `projected > budget_usd`. This gives bounded overshoot (at most one extra turn's average cost beyond the limit).

**19. RAII `BudgetReservation` with reconcile callback**
`BudgetInterface::reserve()` returns a `BudgetReservation`. After inference, call `reservation.reconcile(actual_tokens, actual_usd)` to update the accumulator. Wire the update via `BudgetReservation::with_callback(Box<dyn FnOnce(f64) + Send>)` without exposing internal fields.

**20. Write-through cache discipline for the registry**
Every mutation to `SessionEntry` state (creation, activation, deactivation, status change) must update both the in-memory registry and the DB in that order. If the DB write fails, roll back the in-memory update. Never let them diverge.

**21. Cursor-based pagination for discovery**
`discover_sessions` uses `DiscoveryQuery` (with a cursor token) and returns `DiscoveryPage` (with `next_cursor`). Offset-based pagination is unstable under concurrent session creation — use a stable ordered column (`created_at` + `session_id`) as the cursor.

**22. Deferred refs pattern for circular initialization**
`SessionManager` stores six `OnceLock<Arc<_>>` refs (`inference_client`, `tool_executor`, `context_manager`, `message_router`, `self_ref`, `human_interface`) populated at startup step 10.5 via `set_deferred_refs`. See `cross-cutting.md` §2.

---

## Testing

**23. Set `TrustLevel::Trusted` for any tool-use test**
Without it, tools with `requires_approval = true` stall indefinitely. Use `TestHarnessBuilder::with_trust_level(TrustLevel::Trusted)`. Use `Supervised` only when specifically testing the approval flow.

**24. Wait on `ActivityEvent::TurnCompleted { stop_reason: "endturn" }` for multi-round loops**
`wait_for_turn_target` with `turn_count >= before + 2` corresponds to one inference round. Multi-round tool loops complete all continuation rounds inside a single `execute_turn_with_deps` call. See `cross-cutting.md` §20.

**25. 1 inference round = 2 DB turns**
`ProductionPersistenceAdapter::persist_turn` writes a user turn and an assistant turn per inference round. Tests checking `turn_count` should expect `+2` per round, not `+1`. See `cross-cutting.md` §18.

**26. Use `wait_for_not_active` for budget-exceeded tests**
When a budget limit is hit, the session transitions from Active to Idle (not Errored). Use `TestHarness::wait_for_not_active()` rather than checking for a specific terminal state.

**27. Seed budget cost sequences on `MockInferenceProvider`**
For budget enforcement tests, add `call_cost_sequence: Mutex<VecDeque<Cost>>` to the mock and populate with `set_call_cost_sequence()`. The mock pops costs in order, allowing precise control over per-call USD spend.

**28. Seeding via `SessionStore::create()` requires a follow-up registry hydration step**
If you must seed directly (e.g., for migration tests), call the equivalent of `session_manager.load_from_db()` or reconstruct `SessionEntry` and insert it into the registry before calling any registry-dependent method. Otherwise, assume the session doesn't exist from the manager's perspective.
