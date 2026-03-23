## Architecture

**Panel tree model.** The arrangement engine represents layouts as a binary split tree where every node is either a `PanelLeaf` (carries typed `PanelParams`) or a `PanelSplit` (carries ratio and two children). Panel IDs are persisted in server-side arrangement definitions and must be preserved on same-definition reload — they are not regenerated at runtime. Only `createLeaf()` (used during splits or generated arrangements) produces fresh UUIDs.

**Three-layer arrangement state.** Arrangements exist at three distinct levels: server-persisted named definitions (source of truth), per-tab working copies in localStorage (namespaced by tab UUID stored in sessionStorage), and ephemeral view state (expansion, scroll, draft inputs). The working copy tracks a `dirty` flag and a `workingCopyOrigin` field (`definition` | `generated` | `deleted-upstream`). CacheEnvelope is a single JSON blob storing all three state facets together to prevent partial-write corruption.

**Panel content type registry.** Panel types are registered in a central registry mapping a `contentType` key to a renderer component plus required state surfaces (loading, empty, error, unavailable). `PanelParams` is a discriminated union keyed on `contentType` — never an opaque `Record<string, unknown>`. Every registered type must declare a `paramVersion` integer and an optional `migrateParams` function for forward compatibility.

**SubscriptionController as single authority.** A non-React module owns the desired session subscription set. It is the only code that calls `switchSet()`. The panel tree reconcile pass (`reconcilePanelView`) handles only view state restoration — it never independently manipulates subscriptions. This prevents double-subscription races when panels mount concurrently.

**Streaming pipeline separation.** High-frequency token events (`StreamingEvent`: TextDelta, ToolUseStart, ToolResult, TurnComplete) travel on the `SessionManager` broadcast channel. Dashboard-level events (`ActivityEvent`) travel on a separate `HumanInterface` channel. Mixing the two on one channel causes unnecessary React re-renders across all subscribers. Per-session `StreamBuffer` + `FlushScheduler` isolates re-renders to the active panel. See also: `web.md §2` for the server-side two-channel architecture.

**SessionProjection as unified read-model.** Each session's display data is a merge of the REST snapshot (loaded on mount) and live streaming tokens. `SessionProjection` is `useSyncExternalStore`-compatible and uses a version counter for change detection. Streaming buffers are dropped via `dropIfConfirmed` when the history refetch confirms the turn — the REST snapshot and streaming completion timestamps must use the same ISO-8601 source to avoid spurious retention.

**Router demotion.** TanStack Router (or any client router) is demoted to URL-sync only. It encodes the focused panel's `contentType + contentKey` for deep-linking but does not control layout. The arrangement store (Zustand) is the sole authority for which panels exist and what they display. This avoids race conditions where a route transition fights the arrangement engine for ownership of panel state.

**Catch-up subsystem ownership.** The catch-up / reconnection summary banner is owned by the Chrome & Overlays LLD, not the streaming store. `shouldDisplayCatchUpSummary` fires when `durationMs >= 5000` OR `sessionChanges.length > 0`. Because localStorage snapshot is empty on a fresh page load, all sessions appear as changes, so the banner shows correctly after first disconnect even with no prior snapshot.

**WebSocket route interception in Playwright.** See `web.md §8` for the canonical Playwright API route regex pattern and why the `**/api/**` glob breaks Vite module loading.

## Gotchas

**`**/api/**` glob matches Vite source files.** See `web.md §8`. This is the single most common Playwright test failure — every test that mocks API routes must use the regex form.

**Frontend tracked as orphaned gitlink.** If `frontend/` appears as a 160000-mode entry with no `.gitmodules` and no `.git` directory inside, it is an orphaned gitlink. Git will not let you commit changes to it. Fix: `git rm --cached frontend && git add frontend/` to re-track it as ordinary files.

**`dropIfConfirmed` requires identical timestamps.** See `web.md §12`. The streaming buffer drop logic compares `latestTurnCreatedAt` (WS `turn_complete` event) against `completedAt` (history API); both must come from the same server field rendered as ISO-8601.

**ActivityEvent `payload` field is optional.** Events without a `payload` field (e.g., experiment-related events) match the `"ts" in msg` routing guard and reach the activity handler. Accessing `event.payload.pod_id` crashes. Normalize at ingestion: `const normalized = { payload: {}, ...event }` before any field access.

**`SessionStatus` has two incompatible Rust types.** `persistence::sessions::SessionStatus` implements `FromStr` and `Display`. `session_manager::types::SessionStatus` is a plain enum with neither trait. Code that crosses the boundary needs inline conversion helpers — do not attempt to unify them without checking all call sites.

**Staleness threshold must be relative to constant.** `STALENESS_THRESHOLD_MS` in `freshness.ts` is a named export. Tests that hardcode absolute millisecond values (e.g., `isStale(15_000)`) break whenever the threshold changes. Always write test assertions as `Date.now() - STALENESS_THRESHOLD_MS - 1_000`.

**`SessionConfigModal` submit button can be below viewport.** The session creation form is tall enough that the submit button scrolls off screen on smaller displays. `force: true` on `.click()` does not help when the element is genuinely outside the viewport. Submit via `press("Enter")` on the name input field instead. Also: `idle_timeout_secs` is a required field validated server-side — omitting it in tests causes silent form rejection.

**Dialog `getByText` strict mode collisions.** `getByText("Create Session")` matches both the dialog heading `<h2>` and the submit `<button>`. Use `getByRole("dialog").getByRole("heading", { name: "Create Session" })` to target the heading specifically.

## Patterns

**Signal channel precedence (one meaning per visual channel).** Each visual property carries exactly one semantic meaning: hue encodes severity, motion encodes activity, and opacity/luminance encodes recency. Mixing meanings (e.g., shifting hue toward amber for both unread and high-severity) produces operator confusion at scale. A signal precedence table should be the first section of any ambient layer spec.

**Ambient awareness via CSS only.** Temperature, weathering, and edge glow effects are implemented as CSS classes driven by `data-age` attributes updated on a single 10-second JS interval. No per-token JS computation. The AmbientCoordinator enforces a total motion budget across all panels to prevent sensory overload when many sessions are active simultaneously.

**Composable arrangement persistence.** Named arrangements are server-persisted JSON definitions with optimistic locking (`version` integer). A working copy is stored in localStorage keyed by `activeDefinitionId + tabId`. On load, the startup validator walks the cached tree, verifies all content types exist in the registry, and checks binary-tree invariants before rendering from cache. If validation fails, the cache is discarded silently and the shell renders chrome-only.

**Two-level dirty guard on tree operations.** Level 1: if `dirty === true` (arrangement geometry changed), show Save/Discard/Cancel before any arrangement switch. Level 2: if `hasDirtyContentState()` is true (drafts, filters, unread markers), show a close-panel confirmation. `hasDirtyContentState()` is the correct check — not just `draftInput`, which misses other content-scoped state types.

**Content-scoped vs frame-scoped ephemeral state.** Frame-scoped state (keyed by `panelId` only): chrome collapse, panel expansion. Content-scoped state (keyed by `panelId + contentType + contentKey`): scroll position, draft input, filters, unread markers. Two panels showing the same session have independent content-scoped state. This distinction must be explicit in the store definition to prevent aliasing bugs.

**`refreshActiveArrangement()` vs `switchArrangement()`.** Remote arrangement updates (e.g., `ArrangementUpdated` WS event) must call `refreshActiveArrangement()`, which preserves focus and URL. `switchArrangement()` resets focus to the first leaf and performs a URL replace — correct for user-initiated navigation, wrong for background refresh of the same definition.

**BroadcastChannel race prevention.** Register the BroadcastChannel listener before issuing the tab-identity challenge. Include a 50ms acknowledgment window so that tabs starting up simultaneously don't both claim primary. Make the handler an `async` function.

**Optimistic hydration skips migration when versions differ.** The startup fast path reads the cached tree and renders skeleton panels immediately. If `schemaVersion` or `paramVersion` in the cache differs from the current registry values, skip the fast path entirely and fall through to full validation + migration. Do not attempt migration inside the optimistic path.

**MockWebSocket pattern for Playwright.** See `web.md §20` for the canonical mock implementation. The mock must auto-fire `onopen` to transition `wsState` to `"connected"`, which gates API queries like alert polling in `ApprovalOverlay`.

## Testing

**Wiring tests use the same startup path as production.** See `cross-cutting.md §16` (canonical wiring test gate) and `cross-cutting.md §18` (`turn_count >= before + 2` poll pattern).

**`wait_for_turn_target` must detect session cancellation.** See `cross-cutting.md §11`. The default harness `dispatch_prompt` only returns `Complete` or `Error`; a modified path is needed that detects session deactivation (`get_human_sender` returning `None`) and returns `Cancelled`.

**Compaction cycle tests require a small `model_context_window`.** See `cross-cutting.md §19`.

**E2E tests need live inference smoke coverage.** See `cross-cutting.md §21`.

**Per-commit vs nightly test split.** See `cross-cutting.md §22`.
