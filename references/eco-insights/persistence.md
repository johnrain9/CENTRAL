# Persistence — Ecosystem Insights

Extracted from worker compaction summaries (25 sessions). Canonical reference for turn storage, cost records, session state, and the temporal store. See also: `cross-cutting.md`.

---

## Architecture

**1. Two-phase turn write: `begin_turn_write` then `finalize_turn`**
`TurnStore::persist_turn` splits each write into two SQL steps: `begin_turn_write` inserts the row with `is_complete = 0`, then `finalize_turn` sets `is_complete = 1` and atomically increments `sessions.turn_count`. Rows not yet finalized are invisible to `load_recent_turns` (which filters `AND is_complete = 1`). If the finalize step is skipped or crashes, the row persists but is never served to context assembly or harness polling.

**2. `sessions.turn_count` is the canonical completion signal**
`turn_count` in the `sessions` table is the only field that reliably indicates how many full turns have been persisted. The test harness polls `turn_count >= before + 2` after each prompt. This counter is incremented exactly once per `finalize_turn` call — two calls to `persist_turn` (user + assistant) produce `turn_count += 2`. See `cross-cutting.md` §18.

**3. Three DB row types per inference round**
One prompt round writes: (1) user turn from `record.input`, (2) assistant turn from `record.response`, and conditionally (3) a tool-result turn from `record.tool_results` when non-empty. Single-round turns (no tool use) produce exactly 2 rows and `turn_count += 2`. Tool-use continuation rounds produce 3 rows and `turn_count += 3`.

**4. `SessionStore::transition_status` is the only valid session status write path**
All session status mutations must go through this function. It validates the allowed state machine (`Idle ↔ Active`, `* → Errored`, `Idle/Errored → Retired`) before writing. Direct SQL updates to the status column can produce illegal state. The harness's `wait_for_session_not_active` depends on this write being real.

**5. `CostStore::insert` must run via `spawn_blocking` from async context**
Cost events arrive from an async inference stream but `CostStore::insert` is a synchronous SQLite call. It must be wrapped in `tokio::task::spawn_blocking`. An in-memory `Arc<AtomicU64>` accumulator (updated synchronously via `BudgetReservation::reconcile`) must be used for budget checks rather than re-querying the DB. See `cross-cutting.md` §9.

**6. `PricingTable::DEFAULT_PRICING` fallback for unknown models**
Unknown model strings fall back to Claude Sonnet 4 pricing rather than returning zero cost. Mock inference providers return `Cost::ZERO`, which bypasses this table — tests asserting non-zero costs must use `ProductionInferenceAdapter` with the real `PricingTable::compute_cost()`.

**7. `AssembledContext.content` is a JSON-serialized `ContextPayload`**
The context payload struct contains: `model_id`, `session_id`, `system`, `messages`, and `estimated_input_tokens`. Consumers must deserialize this string to access individual fields. See `cross-cutting.md` §6.

**8. Proactive compaction runs inside `ContextAdapter::assemble`, not as a separate step**
Compaction is triggered eagerly inside `ProductionContextAdapter::assemble()` before returning the assembled context. The trigger check runs on every call; if non-empty candidates are found, `generate_summary` is called synchronously and the summary is persisted before assembly continues. There is no separate compaction scheduler.

**9. `turns_to_summarize(candidate_turns, 10)` gates proactive compaction**
The compaction decision is delegated to this function with a batch size of 10. If it returns an empty vec, compaction is skipped. The batch size controls how many turns are rolled into each summary; seeding fewer than 10 turns above the trigger threshold will not produce a compaction until the batch is full.

**10. Sessions persist by default — not tied to process lifetime**
A session started by `SessionManager::create_session` persists in SQLite regardless of whether the owning process crashes. On restart, `load_recent_turns` and `load_active_summaries` reconstruct the full conversation context. Dead-session cleanup and compaction-on-load are necessary concerns; there is no automatic GC.

**11. Temporal store uses append-only writes with keyframe/delta snapshots**
The temporal persistence subsystem never overwrites or deletes event rows. New state is expressed as supersession records. Snapshots are keyframe/delta pairs: a full keyframe is written periodically, and deltas accumulate until the next keyframe. `temporal_derived_state` is a derived materialization persisted every 60 seconds and may lag behind raw events by up to one flush cycle.

**12. Per-producer sequence numbers use `AtomicU64` for ordering within a stream**
Each event producer holds a `producer_seq: AtomicU64` counter. Sequence numbers are authoritative for ordering within a producer's stream. Cross-producer ordering requires event timestamps plus sequence as a tie-break. The sequence counter resets to 0 on process restart — it is not globally monotonic across restarts.

---

## Gotchas

**13. Compaction threshold of 0.8 triggers after exactly 1 turn by default**
The formula `round(1.0 / compaction_threshold)` with `threshold = 0.8` gives `compact_threshold = 1`. Apply a floor — `compact_threshold.max(20u32)` — so compaction cannot fire until at least 20 turns have accumulated. See `cross-cutting.md` §7.

**14. A stub `drain_cost_events` silently discards all cost events**
If `drain_cost_events` is implemented as a no-op, all `CostEvent`s emitted by `ProductionInferenceAdapter` are lost with no error. The failure manifests as a zero-row assertion in `cost_events_persisted`, not as a panic — easy to misdiagnose as an inference emission problem.

**15. SQLite does not support nested transactions via helper function reuse**
Any helper that contains `BEGIN`/`COMMIT` must not be called from within an existing transaction. The inner `BEGIN` is treated as a no-op or raises an error. Inline the SQL from the helper directly into the outer transaction body.

**16. `load_recent_turns` only returns finalized rows (`is_complete = 1`)**
Rows written by `begin_turn_write` but not yet finalized are invisible. A compaction that runs immediately after a crash may miss the most recent un-finalized turn. This is generally the desired behavior (incomplete turns should not affect context).

---

## Patterns

**17. `spawn_blocking` wraps all synchronous SQLite calls in the async path**
Every call to a synchronous SQLite function (`TurnStore::persist_turn`, `CostStore::insert`, `SummaryStore::save`, `SessionStore::transition_status`) from async context must be wrapped in `tokio::task::spawn_blocking`. Calling these directly from an async function blocks the entire tokio thread. See `cross-cutting.md` §9.

**18. Event payloads are self-contained: names and tags embedded, not just IDs**
Temporal store events embed entity names and relevant tags directly in the payload rather than storing foreign keys. This enables reconstructions and replays without joining back to other tables that may have changed since the event was written.

**19. `tokio::broadcast` with primary/secondary subscription tiers for temporal events**
The Temporal Event Bus uses two priority tiers: primary subscribers receive events immediately, secondary subscribers receive from a secondary channel with a larger buffer and tolerate drops. This prevents slow secondary consumers from backpressuring the primary write path.

**20. `snapshot_version` as a read-modify-write guard**
`ExperimentStore::update_scores` uses a `snapshot_version` integer field that is incremented on each write. The update applies `WHERE snapshot_version = $expected_version` and fails if the version has changed, signaling the caller to retry. Without this guard, concurrent pod score updates produce last-write-wins corruption.

**21. Supersession model replaces hard deletes in the temporal store**
When state changes (e.g., a session is retired, a tag is removed), a new row is inserted with a `supersedes_id` pointing to the old row; the old row is never deleted or updated. Queries for current state filter on `WHERE superseded_by IS NULL`. Periodic archival of fully-superseded rows is required to bound table size.

---

## Testing

**22. Harness must poll `turn_count >= before + 2`, not `before + 1`**
Asserting `>= before + 1` would succeed after only the user turn is finalized, before the assistant turn is written, making the test race-prone. For tool-use rounds that write 3 turns, `>= before + 3` is the correct bound. See `cross-cutting.md` §18.

**23. Compaction wiring tests require a small `model_context_window`**
With the default `model_context_window = 200_000`, 30 seeded turns never trigger the token-fraction threshold. Set `model_context_window = 3_000` with `max_output_tokens = 256`. See `cross-cutting.md` §19.

**24. CLI-layer tests use in-memory SQLite with `run_cli()` calling `main()` directly**
Tests for persistence CLI commands call the module's `main()` function directly (not via subprocess) and capture `stdout`/`stderr` with `redirect_stdout`/`redirect_stderr`. The database is created in a temp file or `:memory:`. This pattern requires `pyproject.toml` to set `pythonpath = ["scripts"]` so imports resolve.

**25. `all_standard_tool_schemas_are_openai_compatible` as a regression guard**
A single test that asserts every `input_schema()` contains `"type": "object"` AND `"properties": {}` catches schemas that are valid JSON but rejected by OpenAI's API. This test is faster than a live API call and should be part of the standard unit test suite. See `cross-cutting.md` §14.

**26. Graceful shutdown tests require harness-level detection of `Cancelled` outcome**
`send_prompt_to_session` only returns `Complete` or `Error`. The harness must monitor the session activity stream for `session_idle` and map that to `Cancelled`. Without this, the test always receives `Complete` and the graceful shutdown path is untested.

**27. Seeded turns for compaction test must exceed the `turns_to_summarize` batch size**
Seeding fewer than 10 turns above the compaction trigger threshold causes `turns_to_summarize` to return an empty vec, skipping compaction and failing the assertion. The 30-turn seed in the test spec is sized to guarantee at least one full batch of 10.

**28. Temporal store reconstruction concurrency limit**
The Temporal Store enforces a maximum of 3 concurrent reconstruction jobs. Requests beyond this limit receive HTTP 429. Integration tests that spin up many replay clients in parallel must gate concurrency explicitly to avoid masking real 429-handling bugs.
