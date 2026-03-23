# Tool Executor — Architectural Insights

Extracted from compaction summaries (25 sessions). Covers tool dispatch, approval flow, claims, SSRF protection, and schema requirements. See also: `cross-cutting.md`.

---

## Architecture

**1. Dispatch is trait-object based via `TurnDeps`**
The turn cycle operates through five trait objects in `TurnDeps`. `ToolInterface::execute` is the boundary: `async fn execute(&self, session_id: &str, tool_call: &ToolCall) -> ToolResult`. The production adapter (`ProductionToolAdapter`) wraps `ToolExecutor::execute(ToolCallRequest, &ToolContext) -> ToolOutcome` and converts `ToolOutcome` to `ToolResult`. See `cross-cutting.md` §1.

**2. `ToolOutcome` has five structurally-enforced variants**
`Success { tool_use_id, content: Vec<ToolResultContent> }`, `Error { tool_use_id, message }`, `Blocked { tool_use_id, hook_name, reason }`, `Denied { tool_use_id, reason }`, `ClaimConflict { tool_use_id, .. }`. All outcomes are typed — dispatch is infallible by the type system.

**3. Execution order within `ToolExecutor`: claims → approval → hooks → execute**
This ordering is specified in `lld-tool-executor.md` v1.1 and enforced in `src/tool_executor/mod.rs`. A tool that passes the claims check but fails the approval gate is blocked before any external side effects occur.

**4. Six native tools are registered**
`GetFiles`, `WriteFile`, `LocalRun`, `Search`, `WebFetch`, `Think`. Registered via `ToolRegistry` in `src/tool_executor/registry.rs` and exposed through `ToolExecutor::all_definitions() -> Vec<ToolDefinition>` (a public wrapper over `ToolRegistry::all_definitions()`).

**5. `ToolContext` carries the permission surface per call**
`ToolContext { session_id, cwd: PathBuf, workspace_roots: Vec<PathBuf>, trusted: bool }`. The `trusted` flag is derived from `SessionConfig`'s `TrustLevel` (Trusted/Supervised); `ctx.trusted == false` triggers the approval gate.

**6. Approval is driven by a channel pair**
`ApprovalEvent::Requested(ApprovalRequest)` is sent via `approval_tx`; the tool call blocks waiting for a reply. `ApprovalRequest` carries `{ request_id, session_id, session_name, tool_name, tool_input, intent: Option<String>, timeout }`. Default timeout is 5 minutes.

**7. SSRF protection is post-DNS**
`WebFetch` resolves the hostname, then checks resolved IPs against a blocklist of private/loopback ranges. This prevents DNS rebinding bypasses — the block happens after resolution, not before.

**8. Workspace claims are block-only and component-aware**
Claims are advisory, time-bounded directory/file reservations for write conflict prevention. Read operations are never blocked. Overlap detection is component-aware (same component can overlap itself).

**9. Tool results from one round become the user-turn input for the next**
`TurnRecord` carries `prev_round_results: Vec<ToolResult>`. In `ProductionPersistenceAdapter::persist_turn`, if `prev_round_results.is_empty()` the user turn is written as a human text message; otherwise it is written as a `ToolResult` content block. This determines DB turn structure for continuation rounds.

**10. `ToolExecutor::all_definitions()` is a public wrapper over the private registry**
`ToolRegistry::all_definitions()` exists but was not originally exposed on `ToolExecutor`. The public wrapper `pub fn all_definitions(&self) -> Vec<ToolDefinition>` must be added explicitly; production adapters building tool lists for inference requests require it.

---

## Gotchas

**11. `TrustLevel::Supervised` (the default) blocks tool execution indefinitely**
`WriteFile::requires_approval()` returns `true` unconditionally. With the default `TrustLevel::Supervised`, calling `WriteFile` in a test blocks indefinitely (5-minute timeout) waiting for human approval. Tests must explicitly set `TrustLevel::Trusted`. See `cross-cutting.md` §10.

**12. Multi-round tool-use harness polling fires too early**
The harness's `dispatch_prompt` polls `turn_count >= before + 2`, which assumes single-round turns. Multi-round loops complete all continuation rounds inside a single `execute_turn_with_deps` call. The correct signal is `ActivityEvent::TurnCompleted { stop_reason: "endturn" }`. See `cross-cutting.md` §20.

**13. `compensate_pending_tools` return value must not be dropped**
The module-level `tool_dispatch::compensate_pending_tools` returns `Vec<ToolResult>` (correct for internal use), but earlier wrappers on `AgenticLoopState` dropped this result, violating INV-1 (every `tool_use` must have exactly one `tool_result`). The fix requires a `TurnBuilder` struct with `add_tool_results()` to accumulate compensated results for persistence.

**14. `emergency_attempted` flag is per `execute_turn` invocation, not per session**
It resets to `false` at the start of each `execute_turn` call. This prevents infinite compact-retry loops within a turn (MaxTokens → compact → retry → MaxTokens → `ContextExhausted`) but does not prevent storms across separate turns.

**15. All tool `input_schema()` implementations must include `"properties": {}`**
Returning `{"type": "object"}` without `"properties"` causes an `object schema missing properties` runtime rejection at the OpenAI provider layer. All 7 standard tool schemas were missing this field before the regression test was added. See `cross-cutting.md` §14.

**16. `OnceLock<Arc<T>>.set().expect()` requires `T: Debug`**
`SessionManager` stores six deferred refs via `OnceLock`; all wrapped types must implement `Debug` or `set().expect()` calls will fail to compile. This affected `ToolExecutor`, `HookCacheEntry` in `hooks.rs`, and `BudgetReservation` in `summary.rs`.

**17. `PathValidator` stores roots privately with no public accessor**
If the `workspace_roots` used to build the validator are needed later (e.g., for `ToolContext`), they must be stored separately on `SessionManager`. The validator itself cannot be queried for them.

**18. `dispatch_tools_sequential` cancellation path skips persistence**
When the cancellation token fires mid-tool, `dispatch_tools_sequential` returns `ToolDispatchOutcome::Cancelled { completed, compensated }` and `execute_turn_with_deps` returns `TurnOutcome::Cancelled` — skipping `persist_turn`. The `wiring_graceful_shutdown` test requires the compensation `ToolResult` to appear in the DB; this is a known gap requiring explicit persistence on the cancellation path.

---

## Patterns

**19. Adapter pattern bridges runtime types to `TurnDeps` trait objects**
`src/agentic_loop/adapters.rs` contains all production adapters. A `build_turn_deps(persistence, inference_client, tool_executor, workspace_roots, compaction_threshold) -> Arc<TurnDeps>` factory assembles them. See `cross-cutting.md` §1.

**20. `Option<Arc<TurnDeps>>` in `LoopDependencies` enables graceful stub fallback**
When `None`, the stub executes for loop-control unit tests that don't need real I/O. When `Some`, `run_session_loop_inner` calls `execute_turn_with_deps`. Existing tests set `turn_deps: None` on all constructors.

**21. Intent flows from stream consumer through `PendingTool` to `ApprovalRequest`**
The stream consumer extracts `intent: Option<String>` from tool calls and stores it on `PendingTool`. It is passed to `ApprovalRequest.intent` and propagated to the Persistence Layer and Human Interface. Intent is bounded to 500 characters and truncated if necessary.

**22. `ActivityEvent::TurnCompleted` encodes whether a round is terminal**
`stop_reason` is `"endturn"` for the final round and `"tooluse"` for intermediate tool-use rounds. Tests waiting for full loop completion must subscribe to `activity_tx` and filter for `stop_reason == "endturn"`. See `cross-cutting.md` §20.

**23. Deferred refs for circular initialization**
`SessionManager` stores six `OnceLock<Arc<_>>` refs populated at startup step 10.5 via `set_deferred_refs`. See `cross-cutting.md` §2.

---

## Testing

**24. Wiring tests use `EcosystemRuntime::new()` — the production startup path**
`TestHarness` in `tests/common/harness.rs` starts the real runtime. Deferred ref wiring, DB initialization, and the full `activate_session` path all execute for every wiring test. See `cross-cutting.md` §16.

**25. `MockInferenceProvider` is the primary test double for the turn cycle**
It records all `InferenceRequest`s to `recorded_requests` and returns scripted responses. Used in both `turn_cycle.rs` unit tests (via `TurnDeps` mock impl) and wiring tests (via `TestHarness`).

**26. `all_standard_tool_schemas_are_openai_compatible` is a systematic regression test**
Located in `src/tool_executor/mod.rs`. It calls `executor.all_definitions()`, iterates all tools, and asserts that any schema with `"type": "object"` also has a `"properties"` key. Added after a runtime crash exposed missing properties in all 7 standard tool schemas.

**27. Pre-existing test failures in `tool_executor` are environment-specific**
Tests for `path_validator`, `local_run`, `claims`, and `search` fail in sandboxed worker environments (no filesystem/port access) but pass locally. These are not regressions and should not block audits of unrelated changes.
