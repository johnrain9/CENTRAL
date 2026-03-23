# Inference Subsystem — Ecosystem Insights

Extracted from worker compaction summaries (25 sessions). Covers the inference client, provider abstraction, cost flow, retry logic, and session lifecycle. See also: `cross-cutting.md`.

---

## Architecture

**1. `InferenceProvider` trait abstracts all backends**
Anthropic, OpenAI, Gemini, and Bedrock are all pluggable backends behind the `InferenceProvider` trait. The provider is called via `InferenceProvider::stream(request, cancel) -> Result<StreamHandle, InferenceError>`. Streaming is the universal transport — all providers return a `StreamHandle` and consume it before surfacing a `ConsumedStream { response, usage }`.

**2. `ProductionInferenceAdapter` bridges the provider to the turn cycle**
The turn cycle's `InferenceInterface` is implemented by `ProductionInferenceAdapter`, which holds an `Arc<InferenceClient>`. It deserializes `AssembledContext.content` back to an `InferenceRequest`, calls the provider, consumes the stream, computes cost via `PricingTable::compute_cost`, emits a `CostEvent` to `inference_client.cost_tx`, and returns the result. The `request_id` must be captured from the `StreamHandle` before `consume_stream` takes ownership of it.

**3. `PricingTable::DEFAULT_PRICING` provides fallback for unknown models**
Unknown model strings fall back to Claude Sonnet 4 pricing ($3/Mtok input, $15/Mtok output) rather than erroring or returning zero. `MockInferenceProvider.estimate_cost()` returns `Cost::ZERO`, so cost assertions in tests must use `ProductionInferenceAdapter` with the real `PricingTable::compute_cost()` — not the mock's estimate.

**4. Session status is a validated state machine**
Valid transitions: `Idle ↔ Active`, `* → Errored`, `Errored ↔ Active/Idle`, `Idle/Errored → Retired` (terminal). `SessionStore::transition_status` enforces these in SQL and returns an error for invalid transitions. Always use this function rather than direct SQL updates to session status.

**5. Crash recovery on startup discards incomplete turns**
On `EcosystemRuntime` startup, any turn row with `is_complete = 0` is deleted and the affected session's `turn_count` is reconciled. This implements at-most-once delivery: after a crash, incomplete turns are discarded rather than re-replayed. Context assembly then sees a clean history.

**6. Retry logic covers only pre-stream error classes**
`execute_with_retry` retries three error classes with exponential backoff: `Throttled`, `ServerError`, `NetworkError`. Errors during stream consumption (after the connection is established) are not retried. Application-level errors like `MaxTokens` trigger emergency compaction rather than retry.

**7. MaxTokens triggers emergency compaction with a single-attempt guard**
A `MaxTokens` response triggers `emergency_compact()` and a single retry within the same turn. The `emergency_attempted: bool` flag on `AgenticLoopState` is reset at the start of each `execute_turn` call and set to `true` after emergency compaction runs. A second `MaxTokens` within the same turn returns `TurnOutcome::Error(ContextExhausted)` rather than looping again.

**8. Prefix cache stability is a first-class design concern**
Context assembly ensures the system prompt + summaries portion of the assembled context is byte-identical across consecutive inference calls for the same session (requirement CM-13). This maximizes provider-side cache hit rates. Any change to summary ordering or formatting invalidates the cache and should be flagged as a potential performance regression.

**9. Summary quality is maintained via a 6-priority prompt ordering**
`SUMMARY_SYSTEM_PROMPT` instructs the model to capture in order: (1) corrections, (2) patterns/connections, (3) decisions, (4) state, (5) findings, (6) open questions. The summary is the primary mechanism for long-context coherence — the architecture bets on summary-compressed history preserving judgment rather than RAG over raw turns.

---

## Gotchas

**10. Gemini wraps output in markdown fences; 429s exit with code 0**
The Gemini CLI wraps its JSON output in markdown code fences that must be stripped before parsing. Additionally, Gemini 429 rate-limit errors exit with code 0 (not non-zero), so detection must be string-based rather than exit-code-based.

**11. OpenAI newer models require `max_completion_tokens`, not `max_tokens`**
The `src/inference/openai/convert.rs` wire struct must use `max_completion_tokens` for models that reject the old field name. Tool input schemas sent to OpenAI function calling must also include a `"properties"` field even for parameter-less tools (see `cross-cutting.md` §14).

**12. `drain_cost_events` and `update_session_status` are independent stubs**
Both functions were absent simultaneously during early development. `update_session_status` calls `SessionStore::transition_status`; `drain_cost_events` calls `CostStore::insert` on each drained event. Their failures manifest differently (status-polling hang vs. zero cost records) but they must both be wired together before wiring tests pass end-to-end.

**13. `PersistenceLayer::connection()` cannot be held across `.await`**
The `MutexGuard` returned by `connection()` is not `Send`. Any DB operation in async code must complete before the next yield point, or be wrapped in `spawn_blocking`. See `cross-cutting.md` §9.

**14. Tool cancellation must be detected before tool execution, not after**
If the `CancellationToken` is already set when `dispatch_tools_sequential` is reached, the function must detect this at entry before spawning the tool process. A post-completion cancel check results in `TurnOutcome::Complete` instead of `TurnOutcome::Cancelled`.

---

## Patterns

**15. Adapter pattern concentrates all type conversion in one place**
Rather than exposing infrastructure types to the turn cycle, each is wrapped in a thin adapter that implements the corresponding `*Interface` trait. This concentrates all async/sync bridging and type conversion in `adapters.rs`, keeping `turn_cycle.rs` fully unit-testable.

**16. INV-1: Every `tool_use` must have exactly one `tool_result`**
If a turn is cancelled mid-dispatch, `compensate_pending_tools()` generates synthetic error results (`"Tool cancelled: {tool_name}"`) for all unresolved tool calls, which are persisted as a user turn. This preserves the invariant even across abrupt cancellations.

---

## Testing

**17. `MockInferenceProvider` uses a pre-queued script (`VecDeque`)**
Each inference call pops the next response. If the script is exhausted, the mock panics. Unexpected extra inference calls (e.g., from spurious compaction) appear as panics rather than assertion errors — if a test panics unexpectedly, check whether an unintended compaction consumed a response slot.

**18. Wiring tests are the only integration coverage for the adapter layer**
The 7 tests in `tests/wiring/` cover startup sequence, tool result flow, context assembly with seeded turns, cost event persistence, graceful shutdown, compaction cycle, and persistence-supplied context. See `cross-cutting.md` §16.

**19. Live provider tests are gated on environment**
OpenAI live tests check for `OPENAI_API_KEY` or `~/.ecosystem/config.toml` at `[inference.openai].api_key`. Tests are skipped (not failed) when neither is present. Use this same pattern for any new live provider tests to avoid CI failures while enabling local integration testing.
