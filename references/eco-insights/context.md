# Context Manager — Ecosystem Insights

Extracted from worker compaction summaries (25 sessions). Covers context assembly, compaction, summarization, and the token budget. See also: `cross-cutting.md`.

---

## Architecture

**1. Module structure under `src/context_manager/`**
Five submodules: `assembly.rs` (`assemble_context`, `AssemblyInput`, `AssembledContext`, `SessionAssemblyConfig`), `compaction.rs` (`FractionThreshold`, `turns_to_summarize`, `ContextMetrics`), `summary.rs` (`generate_summary`, `commit_compaction`, `SessionBudget`, `SUMMARY_SYSTEM_PROMPT`), `emergency.rs` (`emergency_compact`, `apply_emergency_config`), and `token_estimator.rs` (`estimate_tokens`, approximation: `(text.len() + 3) / 4`).

**2. Context assembly runs before every inference call**
The 8-step turn cycle in `execute_turn_with_deps` mandates assembly (step 2) before inference (step 5). `ProductionContextAdapter::assemble` loads session config, recent turns, and active summaries from the DB under the persistence mutex (dropped before any async call), optionally runs proactive compaction, then serializes an `InferenceRequest` into `AssembledContext.content`. The dependency ordering is implicit in the cycle documentation and not enforced by the type system.

**3. `AssembledContext.content` is a JSON-serialized `InferenceRequest`**
The `content: String` field carries a JSON `ContextPayload { model_id, session_id, system, messages, estimated_input_tokens }`. Code that produces or consumes `AssembledContext.content` must agree on this schema. See `cross-cutting.md` §6 for the full boundary description.

**4. Proactive compaction is triggered inside `assemble`, not by a separate scheduler**
The trigger check (`turns_to_summarize(candidate_turns, keep_recent: 10)`) runs on every `assemble` call. If it returns non-empty candidates and no summary already exists, `generate_summary()` is called synchronously (it makes an inference call) and the summary is persisted via `commit_compaction` before assembly continues. This means a compacting turn makes N+1 total inference calls.

**5. `generate_summary` is itself an inference call using `SUMMARY_SYSTEM_PROMPT`**
The `SUMMARY_SYSTEM_PROMPT` begins "You are summarizing…" and orders priorities: corrections > patterns/connections > decisions > state > findings > open questions. In tests, the wiring test `compaction_cycle` checks `requests[0].system` contains "You are summarizing" to confirm a compaction inference call was made.

**6. `FractionThreshold.should_compact` uses token fractions, not raw turn counts**
The formula is `adjusted_tokens > context_window * threshold_fraction`. With the default `model_context_window = 200_000` and small mock turns (~15 tokens each), even 30 seeded turns never trigger this threshold. To exercise `FractionThreshold` in tests, set `model_context_window = 3_000` with `max_output_tokens = 256` in `SessionAssemblyConfig`. See `cross-cutting.md` §19.

**7. Summaries carry turn-range metadata and are retrieved by `SummaryStore`**
A summary covers turns in a half-open range and is stored via `SummaryStore`. Once a summary exists for a session, `summaries.is_empty()` returns `false` and the proactive compaction trigger is bypassed — tests that pre-seed a summary are immune to the compaction formula bug.

**8. Emergency compaction is a separate code path from proactive compaction**
`trigger_emergency_compaction()` in `src/agentic_loop/compaction.rs` handles the `MaxTokens` response case. It is distinct from the proactive `FractionThreshold`-driven path in `ContextAdapter::assemble` and guarded by `emergency_attempted: bool` to prevent infinite loops.

**9. `SessionAssemblyConfig` drives the token budget for assembly**
Fields include `model_context_window`, `max_output_tokens`, and `compaction_threshold`. These values are sourced from the 4-tier config resolution (`session → pod → system → built-in`) and passed to `ProductionContextAdapter` at activation.

---

## Gotchas

**10. Compaction threshold formula fires every turn with default 0.8**
The formula `round(1.0 / threshold)` gives `compact_threshold = 1` for the default `threshold = 0.8`. With 5 seeded turns, compaction fires on the first `assemble` call and consumes the only mock response as a summary call, leaving the actual turn with no response. See `cross-cutting.md` §7 for the corrected formula.

**11. Proactive compaction consuming mock responses causes timeouts, not panics**
When `generate_summary()` takes the only available mock response, the actual turn never receives an inference result. The turn loop blocks, the harness timeout fires first, and the test fails with `observed=N, expected=N+2` (or similar). This is distinct from a `MockInference script exhausted` panic and can be harder to diagnose.

**12. `persistence_supplies_context` passes even with the compaction bug**
Pre-seeding a summary makes `summaries.is_empty()` false, bypassing the compaction trigger entirely. Tests with pre-seeded summaries are immune to the formula bug; tests without summaries are not. This asymmetry means some wiring tests can pass while others fail, making root-cause analysis harder.

**13. Context assembly ordering is not type-system-enforced**
If a caller skips assembly and passes a fabricated `AssembledContext`, inference will deserialize stale or garbage data. The dependency on step ordering (assembly before inference) is documented in the 8-step turn cycle but not enforced by Rust's type system.

**14. `execute_turn_with_deps` cancellation does not persist compensated tool results by default**
When `ToolDispatchOutcome::Cancelled` is returned, the compensation results from `compensate_pending_tools` are not written to the DB unless explicitly persisted on the cancellation path. Tests asserting tool result persistence after graceful shutdown require additional persistence calls.

---

## Patterns

**15. Load DB state under mutex, drop before async calls**
`ProductionContextAdapter::assemble` loads session/turns/summaries inside a `PersistenceLayer` mutex guard, then drops the guard before calling `generate_summary()` (which is async and makes an inference call). Re-acquire if needed for `commit_compaction`. This is the correct pattern for any context-manager operation that mixes DB reads with async calls.

**16. `turns_to_summarize(candidate_turns, keep_recent: 10)` gates proactive compaction**
Pass all candidate turns with a `keep_recent = 10` argument to preserve the most recent turns from summarization. If the returned vec is non-empty, trigger compaction. The batch size (10) controls how many turns roll into each summary; seeding fewer than 10 turns above the trigger threshold will not produce a compaction until the batch is full.

**17. `TurnRecord.prev_round_results` distinguishes turn types for persistence**
When persisting a turn, check `prev_round_results.is_empty()`: empty → write a human text message turn; non-empty → write a tool-results turn. This lets `ProductionPersistenceAdapter::persist_turn` write the correct DB message type for continuation rounds.

---

## Testing

**18. The 7 wiring tests and their compaction-specific coverage**
`compaction_cycle` checks that the first inference request has "You are summarizing" in its system prompt and that subsequent messages contain `[SUMMARY | turns: 1-20]`. `persistence_supplies_context` pre-seeds a summary to bypass compaction and isolates the test to a single inference call. Both tests require a small `model_context_window` (3,000 tokens) to make `FractionThreshold` fire. See `cross-cutting.md` §§16 and 19.

**19. Seeded turns must exceed `turns_to_summarize` batch size**
The `compaction_cycle` test must seed enough turns that `turns_to_summarize(candidate_turns, 10)` returns a non-empty batch. Seeding fewer than 10 turns above the trigger threshold will cause `turns_to_summarize` to return an empty vec, skipping compaction and failing the assertion.

**20. LLM-as-judge tests exist for summary quality**
A testing framework evaluates whether generated summaries preserve key information from the summarized turns. These tests are separate from functional correctness tests and are nightly-only, not per-commit.
