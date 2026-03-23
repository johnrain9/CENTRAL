# Message Router — Ecosystem Insights

Extracted from worker compaction summaries (25 sessions). Covers messaging patterns, fan-out, chain safety, pod boundary enforcement, and pub/sub. See also: `cross-cutting.md`.

---

## Architecture

**1. Three interaction patterns share chain tracking and budget infrastructure**
- **Notify** — fire-and-forget delivery, no reply expected.
- **Request/Reply** — blocking pattern with BFS deadlock prevention.
- **Broadcast** — channel-based pub/sub delivery to all channel subscribers.
- **HumanBroadcast** (`MessagePattern::HumanBroadcast`) — added for `POST /api/pods/{id}/directive`; uses the same delivery infrastructure.

**2. Broadcast counts as 1 fan-out slot regardless of subscriber count**
`FanOutTracker` (`src/message_router/fan_out.rs`) uses a sliding-window rate limiter (default: 20 messages per 60-second window). A broadcast to 250 subscribers consumes exactly 1 slot against the sender's window. Cascade risk is handled by `total_message_count` on `ChainContext`, not by fan-out counting. Counting per-subscriber was a design error corrected in the LLD.

**3. `total_message_count` still increments per subscriber on broadcast**
Even though fan-out counts as 1 slot, `total_message_count` increments once per subscriber delivery. A broadcast to 100 subscribers costs all 100 of the default 100-message chain budget. Size channels accordingly or raise `max_total_messages` for broadcast-heavy pods.

**4. BFS cycle detection runs before request/reply delivery**
The system walks the in-flight chain graph before dispatching any request/reply message, detecting potential deadlock cycles. `PendingReplies` (`src/message_router/request_reply.rs`) tracks in-flight chains in memory; `reply_to: Option<MessageId>` on `SendRequest` links a reply to the originating message.

**5. Chain safety limits and defaults**
`ChainContext` tracks depth, duration, cost, and total message count per chain with these defaults:
```rust
ChainLimits { max_depth: 10, max_duration: 300s, max_cost: $0.01, max_total_messages: 100 }
```
Budget reservation is atomic via `Mutex` on `ChainContext`. The RAII guard pattern reserves ephemerally before inference and commits or releases on completion.

**6. Pod boundaries are enforced at three points**
`check_pod_boundary()` is called in `send_notify`, `send_request`, and `broadcast` in `src/message_router/mod.rs`. The pod registry is an in-memory `RwLock<HashMap<PodId, PodRecord>>` loaded at startup. Pod assignment is immutable — once a session's `pod_id` is set, routing decisions on pod membership are stable for the lifetime of the session.

**7. Only the runtime-internal `route_message` emits `ActivityEvent`s**
`StoreMessageRouter::send_notify` (the REST-facing API) does NOT emit `ActivityEvent::MessageSent` or `ActivityEvent::MessageDelivered`. Only `runtime.rs`'s `pub(crate)` `route_message` function emits these. Tests asserting on activity events after a REST notify will time out. See `cross-cutting.md` §12.

**8. `sender_intent` captures why the sender is sending**
`SendRequest.sender_intent: Option<String>` is distinct from message content — it records the sender's motivation. Stored as `sender_intent TEXT` in the `messages` table and indexed by FTS5. Bounded to 500 characters; falls back to `None` if omitted.

**9. Two-channel WebSocket architecture: streaming vs. activity**
`StreamingEvent` (high-frequency: tokens, tool use) and `ActivityEvent` (lower-frequency: session lifecycle, message events) are broadcast on separate tokio channels. The frontend subscribes to each independently. Conflating them into a single handler is a recurring source of missed events.

**10. Reaction rules provide event-driven routing**
Reaction rules subscribe to the `ActivityEvent` tokio broadcast channel. On trigger match, the engine fires an action (`CreateTask`, `SendMessage`, `AlertOperator`, `BroadcastToChannel`). Loop prevention uses a `reaction_depth` field on events (max depth 3). Audits are the canonical example: "on `task_completed` → create review task assigned to reviewer."

**11. `MessageDelivered` carries full sender context for graph visualization**
`ActivityEvent::MessageDelivered` includes `sender_id`, `sender_name`, `recipient_id`, `recipient_name`, `subject`, and `pattern`. This allows building session communication graphs without cross-event correlation. The pre-fix version only had `message_id` and `recipient_id`, making broadcast edge tracking impossible.

**12. Cross-pod and ecosystem-level channels are not yet implemented**
The existing system is pod-scoped only. Channel names need only be unique within a pod (enforced by a partial index on `(name, pod_id)`). Cross-pod and ecosystem-level channels are a known missing infrastructure piece.

---

## Gotchas

**1. Fan-out per-subscriber counting exhausts large channel budgets**
If `FanOutTracker` is reverted to count each subscriber as 1 slot, a broadcast to any moderately-sized channel immediately exhausts the rate limit. Broadcast must always count as 1 slot regardless of subscriber count.

**2. Large broadcasts can exhaust the entire chain message budget in one shot**
See Architecture §3 above. Even though fan-out is 1 slot, `total_message_count` increments per delivery. Broadcasting to 100 subscribers costs all 100 of a chain's default message budget in a single operation.

**3. `ActivationError` variants evolved — exhaustive matches must be updated**
Adding `NotFound`, `InvalidState`, or `Config` to `ActivationError` silently breaks all existing exhaustive match arms. Unhandled variants in the web layer caused 404 responses instead of 202 during prior development. Always add new error variants to all match sites simultaneously.

**4. `TurnOutcome::Cancelled` does not emit `turn_complete`**
When a session is stopped, the agentic loop emits `session_idle` with `reason="stopped_by_human"`, not `turn_complete`. Tests waiting for `turn_complete` after a stop will time out. See `cross-cutting.md` §11.

**5. Chain context is ephemeral — lost on crash**
In-flight chain state is held in memory only. On crash, any message in-flight is not re-delivered (at-most-once semantics). Design consumers to be idempotent where possible.

**6. Deferred `OnceLock` init for circular Arc refs**
SessionManager and message router components have circular Arc dependencies resolved via `OnceLock`, wired at startup step 10.5. Components must not be used before this wiring step. See `cross-cutting.md` §2.

---

## Patterns

**13. RAII budget reservation**
Reserve budget before inference, commit actuals on success, release on failure via RAII guard drop:
```rust
let _guard = chain_ctx.reserve_budget(estimated_cost)?;
let result = inference_provider.complete(...).await?;
persistence.record_actual_cost(result.actual_cost).await?;
```

**14. Pod-scoped discovery injection at the tool layer**
`discover_for_session(caller_id, query)` injects the caller's `pod_id` into the discovery query. AI-initiated discovery is always pod-scoped without requiring the AI to specify its pod. Administrative callers use `discover(query)` with an explicit `DiscoveryQuery`. Keep these two paths separate to preserve pod isolation.

**15. Subject-based pre-screening before routing**
`src/message_router/pre_screen.rs` returns `PreScreenResult` synchronously with no inference call. Use this path to filter messages by tag overlap before queuing for delivery. Test independently of the full delivery pipeline.

**16. Reaction rules as first-class routing**
Subscribe to `ActivityEvent` broadcast for event-driven side effects. Use `reaction_depth` (max 3) to prevent cascade loops. Audits, integrator notifications, and "new tool available" announcements all fit this pattern.

---

## Testing

**17. Test suite structure**
```
cargo test --lib          # unit tests in source files
cargo test --test wiring  # full runtime, mock inference
cargo test --test e2e     # full stack, mock inference
cargo test --test smoke   # live API, real model (cheap)
cargo test --test live    # real model, sensitive to safety filters
```

**18. `TestHarness` uses the production startup path**
`TestHarnessBuilder` calls `EcosystemRuntime::new()`, the same path as production. This catches wiring failures (wrong Arc, missing OnceLock init) that unit tests cannot.

**19. Testing `ActivityEvent`s — use `activity_tx` directly**
`StoreMessageRouter::send_notify` does NOT emit `ActivityEvent::MessageSent`. To test the WebSocket activity event path, emit directly:
```rust
fixture.runtime.human_interface().activity_tx.send(ActivityEvent::MessageSent { ... })
```

**20. Testing session stop — wait for `session_idle`**
`TurnOutcome::Cancelled` does not emit `turn_complete`. Subscribe to activity events and wait for `session_idle` with `reason="stopped_by_human"`.

**21. Testing approval timeout**
Create a standalone `ApprovalRouter` with a short timeout (2s) outside the fixture. Spawn the request, do not respond, assert `ApprovalDecision::Timeout`. Verify `pending_requests()` is empty after expiry.

**22. Pre-screening is testable without inference**
`src/message_router/pre_screen.rs` returns `PreScreenResult` synchronously. Test message routing policy independently of the inference provider.

| File | Purpose |
|------|---------|
| `src/message_router/mod.rs` | Pod boundary enforcement (3 call sites) |
| `src/message_router/broadcast.rs` | Broadcast delivery, fan-out counting |
| `src/message_router/chain.rs` | ChainContext, ChainLimits |
| `src/message_router/fan_out.rs` | FanOutTracker (sliding window) |
| `src/message_router/pre_screen.rs` | Subject/tag filtering |
| `src/message_router/request_reply.rs` | PendingReplies, BFS cycle detection |
| `src/message_router/types.rs` | SendRequest, MessagePattern, MessageRouterConfig |
| `src/persistence/channels.rs` | ChannelRecord (pod_id), SubscriptionRecord |
| `src/persistence/messages.rs` | messages/message_deliveries tables, sender_intent |
