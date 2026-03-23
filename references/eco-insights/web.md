# Web Layer — Ecosystem Insights

Extracted from worker compaction summaries (25 sessions). Covers the axum HTTP+WebSocket server, route registration, pod operations, and frontend E2E testing. See also: `cross-cutting.md`.

---

## Architecture

**1. Axum HTTP + WebSocket server with a single `ApiState`**
The web layer is an axum server started via `start_web_server()` in `src/web/server.rs`. All handlers share `ApiState`, which holds `session_id` (legacy single-session handle), `persistence: Arc<PersistenceLayer>`, `session_manager: Arc<SessionManager>`, and `store_router: Arc<StoreMessageRouter>`. Adding a new dependency to handlers means extending `ApiState::new()`'s parameter list and all call sites (harness setup, wiring tests, smoke tests).

**2. Two-channel WebSocket architecture: streaming vs. activity**
`StreamingEvent` (flat JSON, high-frequency token events) delivers real-time AI output to the focused session view. `ActivityEvent` (envelope format `{"type": ..., "ts": ..., "session_id": ..., "payload": {...}}`) delivers lifecycle events (turn completions, messaging, approval requests, status changes). These must use separate subscription paths (`subscribe_session` vs `subscribe_activity`); conflating them into one handler is a recurring source of missed events.

**3. `StoreMessageRouter` is distinct from the internal runtime router**
`src/message_router/MessageRouter` (alias `StoreMessageRouter`) is the REST-accessible API. The `pub(crate)` `route_message` function inside `runtime.rs` is the lower-level delivery path and the only source of `ActivityEvent::MessageSent/MessageDelivered`. Calling `StoreMessageRouter::send_notify` via REST does NOT emit `ActivityEvent` entries. See `cross-cutting.md` §12.

**4. Route registration order matters**
All routes are wired in `router()` returning an `axum::Router`. The compare endpoint `GET /api/pods/compare` must be registered before `GET /api/pods/:id` to prevent the parameter segment from matching "compare". Messaging routes live under `/api/messages/notify` and `/api/channels`.

**5. `ensure_single_session()` creates a DB record but bypasses the in-memory registry**
The legacy single-session startup path calls `SessionStore::create()` directly (not `SessionManager::create_session()`), so the session exists in the DB but NOT in the registry. Subsequent `activate_session()` calls return `ActivationError::NotFound`. See `cross-cutting.md` §13.

**6. Pod mutations must go through `SessionManager`, not `PodStore` directly**
`SessionManager` holds an in-memory pod registry. Pod creation and updates must call `SessionManager::create_pod`/`update_pod`, which write to the DB and update the in-memory map. Calling `PodStore` directly keeps the registry stale, causing `PodNotFound` errors on session creation even when the DB has the record.

**7. `SessionDetail` in `api.rs` is a separate struct from `runtime.rs`'s `SessionDetail`**
`api.rs` exposes `pod_id: Option<String>` and `pod_name: Option<String>` (looked up separately via a second `PodStore` call because the runtime struct only carries `pod_id`). The `session_detail_to_view()` helper converts between the two; the extra lookup is expected.

---

## Gotchas

**8. Playwright glob `**/api/**` intercepts Vite TypeScript source files**
In frontend E2E tests, the glob pattern `"**/api/**"` matches `src/api/sessions.ts` and other source files, causing MIME type errors when Vite serves them as modules. Use the regex `/^https?:\/\/[^/]+\/api\//` which anchors matching to the URL path root.

**9. `ApprovalOverlay` only fires its alert query when `wsState === "connected"`**
The frontend component gates the `GET /api/alerts?is_read=false` query on `wsState === "connected"`. In tests, if the MockWebSocket hasn't fired `onopen`, the query never runs and the overlay never renders. Tests must fire `onopen` on the mock WS before asserting on approval UI elements.

**10. `TurnOutcome::Cancelled` does not emit a `turn_complete` WebSocket event**
When a session is stopped mid-turn, the loop emits `session_idle` (with `reason: "stopped_by_human"`) but does NOT emit `turn_complete`. E2E tests that stop a session must wait for `session_idle`. See `cross-cutting.md` §11.

**11. `CatchUpBanner` fires on fresh page load with empty localStorage**
`useCatchUp` calls `shouldDisplayCatchUpSummary(durationMs, changes)` which returns true when `durationMs >= 5000 OR sessionChanges.length > 0`. On a fresh page load with no prior localStorage snapshot, every session appears as a change, so the banner fires. Reconnection tests must account for this.

**12. `dropIfConfirmed` requires timestamp parity between streaming and history endpoints**
The frontend streaming store clears the streaming buffer when `latestTurnCreatedAt >= completedAt`. Both timestamps must be ISO-8601 strings from the same server source. In E2E tests, `TURN_CREATED_AT` must be the same constant in both the `turn_complete` WS injection and the mock history API response.

**13. `axum::body` vs `hyper::body` for Axum 0.7**
Axum 0.7 uses `axum::body::to_bytes` directly; `hyper::body::to_bytes` is no longer a direct dependency and will cause compile failures. Any handler or test helper reading a response body must import from `axum::body`, not `hyper`.

---

## Patterns

**14. `WebTestFixture` is the standard full-stack test harness**
`WebTestFixture` brings up a full `EcosystemRuntime` + axum server on an ephemeral port. It provides a `reqwest::Client` for HTTP and a `WsClient` for WebSocket testing. `WsClient` exposes `wait_for_event(type)`, `collect_until(type)`, `subscribe_activity()`, `subscribe_session(id)`, and `close()`. Pass `vec![]` for mock inference responses when only testing HTTP endpoints.

**15. Pod session creation requires `model_id` in the request body**
When creating a session that belongs to a pod in E2E tests, the `POST /api/sessions` payload must include `"model_id": "mock-model"`. Omitting it causes 422 validation failures because `CreateSessionRequest::model_id` is required with no default.

**16. Search pagination uses the limit+1 trick with cursor encoding**
The `search()` handler fetches `limit + 1` results, checks `has_more = results.len() > limit`, and encodes `next_cursor` from the last result's `(created_at, source_id)` pair. Before this was implemented, `next_cursor: None, has_more: false` were hardcoded.

**17. `MessageDelivered` carries full sender context for graph visualization**
`ActivityEvent::MessageDelivered` includes `sender_id`, `sender_name`, `recipient_id`, `recipient_name`, `subject`, and `pattern`. Added specifically to support communication graph visualization ("Conversational Constellations"). The pre-fix version only had `message_id` and `recipient_id`, making broadcast edge tracking impossible.

**18. Pod ID and session ID format: `{prefix}_{uuid_v4}`**
Pod IDs are generated as `pod_{uuid_v4}` (40 chars) via `generate_pod_id()` in `api.rs` using `rand::Rng`. Session IDs follow the same convention. There is no DB auto-increment; the ID must be provided in `NewPod`/`NewSession` at creation time.

**19. `ignoreSnapshots: !!process.env.CI` hides visual baseline absence locally**
`frontend/playwright.config.ts` sets `ignoreSnapshots` in CI to avoid failures when no baseline exists. Locally, the first run of visual snapshot tests always fails — expected behavior resolved by running `npx playwright test --update-snapshots` once.

---

## Testing

**20. Playwright E2E uses MockWebSocket injected via `addInitScript`**
All browser-level WebSocket control is done by replacing `window.WebSocket` in `page.addInitScript()` before page navigation. The mock stores itself on `window._mockWs` and exposes `window.injectWsMessage(data)` and `window.simulateDisconnect()`. This avoids needing a real server for unit-level UI tests.

**21. Playwright strict mode selector gotchas**
Common patterns: (a) `getByText("Streaming")` matches both the streaming output label and the PromptInput disabled reason — scope to `page.locator('[data-testid="streaming-output"]').getByText("Streaming")`; (b) `getByText("retired")` matches the status pill and the prompt reason — use `{ exact: true }`; (c) session names appear in both sidebar and main grid — scope to `page.locator("main")` or `page.locator("aside")`.

**22. Session creation modal requires all three fields and keyboard submit**
`SessionConfigModal` validates `name` (1-100 chars), `idle_timeout_secs` (positive integer), and `system_prompt` (1-50000 chars). The reliable submit pattern is `page.getByRole("dialog").getByPlaceholder("auth-expert").press("Enter")` on the name input. `idle_timeout_secs` is required and easy to forget.

**23. E2E approval tests must bypass the agentic pipeline**
TrustLevel wiring hardcodes `trusted: true` in `ToolContext`, bypassing the approval gate in the agentic loop. E2E approval tests must call `ApprovalRouter::request()` directly (bypassing the normal prompt → tool_use → approval path) to exercise approval resolution and WebSocket event delivery.

**24. WebSocket reconnection tests require a 1-second delay budget**
The frontend WebSocket manager's `scheduleReconnect()` uses a 1-second first backoff before creating a new `WebSocket`. Reconnection E2E tests must use `timeout: 10_000` when asserting the "connected" state after simulating a disconnect.
