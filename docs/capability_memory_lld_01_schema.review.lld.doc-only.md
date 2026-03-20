## Verdict
This LLD is not implementation-ready. The core enforcement mechanism is still missing a canonical contract for what preflight actually signs, several state/provenance rules are not representable or enforceable with the proposed schema, and the transaction/idempotency story is incomplete enough to risk duplicate registry mutations and inconsistent task admission.

## Context Used
Only the supplied contents of `/Users/paul/projects/CENTRAL/docs/capability_memory_lld_01_schema.md` were reviewed, using the provided line numbers. No repository files, source code, or other documents were inspected.

## Findings

### 1. Preflight has no canonical signed input/output contract
**Severity:** critical  
**Why:** The write path is supposed to verify a server-authored `preflight_token` or result digest, but the schema only persists `query_text`, classification, rationale, match lists, and a freshness marker. It does not persist the normalized task fields that were checked, the search scope, the allowed classifications, the override eligibility, or the token/digest that was actually verified.  
**Lines:** 223-235, 527-530, 552-559, 633-643, 658-666, 691-712  
**Consequence:** You cannot later prove what input was evaluated, whether the committed task still matches the checked intent, or whether CENTRAL actually authorized the create/update. That makes both enforcement and incident review unverifiable.  
**Recommendation:** Define a normative preflight request/response schema and persist the exact normalized input snapshot plus the server-issued token/digest, scope, allowed classifications, and override eligibility.

### 2. Blocking on “strong overlap” depends on an undefined contract
**Severity:** high  
**Why:** This LLD intentionally defers ranking details, but task creation is rejected based on “strong overlap” and `strong_overlap_count`. There is no normative definition of what makes a match “strong,” what buckets exist, or what determinism is required across writers.  
**Lines:** 13, 219-235, 531, 658-666, 726-741, 934-946  
**Consequence:** Different implementations can make different blocking decisions for the same input. Tests will be unstable, rollout behavior will drift, and users will see nondeterministic admission/rejection.  
**Recommendation:** Either define the minimum blocking contract here, or remove hard enforcement from this LLD until the overlap bucket semantics are fixed elsewhere.

### 3. Transaction B idempotency is required but not implementable from the schema
**Severity:** high  
**Why:** The document requires exact replay of accepted audit mutations to succeed without duplicate rows or conflicting events, but `capability_events` has only an auto-increment PK and no idempotency key. `capability_source_tasks` partially dedupes some links, but not event emission or parent/audit completion side effects.  
**Lines:** 190-214, 576-603  
**Consequence:** Retries after partial failures will either duplicate lifecycle history or fail inconsistently. Audit acceptance becomes unsafe under any retry/replay condition.  
**Recommendation:** Add an explicit idempotency mechanism keyed by `audit_task_id`, accepted parent version, and payload digest, and define replay behavior for each mutated table.

### 4. Freshness validation has a race window with no locking/isolation rule
**Severity:** high  
**Why:** The create path validates freshness against the “current novelty-domain revision,” but the document does not specify when that revision is recomputed relative to acquiring the write lock. With token-based preflight, a concurrent writer can change the novelty domain between validation and commit unless this check is done under the correct SQLite transaction mode.  
**Lines:** 524-538, 683-724  
**Consequence:** Stale creates can still slip through, or valid creates can fail nondeterministically under contention. The exact failure mode will depend on implementation details the LLD leaves open.  
**Recommendation:** Specify the required isolation behavior and when the revision comparison happens relative to `BEGIN IMMEDIATE` or equivalent write-lock acquisition.

### 5. Classification semantics require references that the schema does not capture
**Severity:** high  
**Why:** `follow_on`, `extends_existing`, and `supersedes` require explicit prior task/capability references, but the proposed storage only has matched-result arrays and task-local `capability_ids` hints. There is no canonical field for the creator-selected related task IDs or the specific capability/task being superseded.  
**Lines:** 223-235, 301-312, 462-519, 527-535  
**Consequence:** Implementations will either infer intent from search matches or bury the real reference in ad hoc JSON. That loses provenance and makes validation subjective.  
**Recommendation:** Add canonical persisted fields for declared `related_task_ids` and `related_capability_ids`, separate from matched search results, and require an explicit superseded target.

### 6. Capability provenance is internally inconsistent
**Severity:** high  
**Why:** A capability row must have either `verified_by_task_id` or metadata provenance like `valid_from_event_id`/`seed_origin`, while separate source-task links and events also exist. The document never decides whether `verified_by_task_id` points to the implementation task, the audit task, or some other verifier. `valid_from_event_id` is recommended even though the event model is not defined tightly enough to support it.  
**Lines:** 108-121, 163-205, 324-330, 352-364, 576-612  
**Consequence:** Different writers will encode different provenance stories for equivalent mutations. Later upgrades, audits, and trust queries will disagree on what actually verified a capability.  
**Recommendation:** Choose one canonical provenance model. Either make verification event-based with a defined event schema, or define `verified_by_task_id` precisely and separate creator/updater/auditor provenance fields.

### 7. Scope invariants are declared but not enforceable as written
**Severity:** high  
**Why:** The document says capability scope “must be validated,” but the schema stores affected repos in a join table and only lists basic FKs/indexes in the migration. There is no specified enforcement point for cardinality/inclusion rules like “local must have exactly one affected repo equal to owner.”  
**Lines:** 138-161, 447-458, 758-767, 907-930  
**Consequence:** Invalid scope rows can be persisted, and downstream search or UI logic will silently operate on impossible capability definitions.  
**Recommendation:** Define where scope validation happens on create/update and how join-table diffs are applied; if DB-level enforcement is expected, specify triggers or equivalent constraints.

### 8. Planner-verified/bootstrap mutation paths are allowed but not specified
**Severity:** high  
**Why:** The design explicitly allows lower-trust capability mutation and planner verification, but only audited acceptance and later audited upgrade have concrete transaction definitions. Bootstrap/backfill also needs persisted bypass state and optional queueing, but the queue table itself is optional and no canonical transaction/API is defined for these modes.  
**Lines:** 73-76, 281-297, 324-330, 387-397, 818-839  
**Consequence:** The first non-audited implementation will invent its own rules for capability creation/update/deprecation, which is exactly where registry quality will degrade fastest.  
**Recommendation:** Either ban non-audited mutation in v1, or add a normative planner/bootstrap mutation transaction with required persisted fields, visibility rules, and review/merge behavior.

### 9. Material updates overwrite the only preflight record
**Severity:** medium  
**Why:** `task_creation_preflight` is keyed by `task_id`, and Transaction A2 updates that row in place after material intent changes. That destroys the original create-time justification even though the table is described as the persisted check that justified task creation.  
**Lines:** 215-249, 546-560, 574  
**Consequence:** After a few material edits, there is no durable record of why the task was initially admitted or how its novelty classification changed over time.  
**Recommendation:** Make preflight records append-only per task revision, or add a history table plus an explicit pointer to the current preflight.

### 10. The recommended freshness token is so broad that it will cause false staleness
**Severity:** medium  
**Why:** The token includes max task/capability timestamps and event IDs across the novelty search scope, and commit requires an exact match. Any unrelated write in that scope invalidates pending creates and updates. That is especially risky during bootstrap or in busy repos where events are frequent.  
**Lines:** 691-715, 719-724, 840-847  
**Consequence:** Users will be forced into repeated preflight reruns for unrelated activity, which increases friction, raises override pressure, and makes the system look flaky.  
**Recommendation:** Narrow the revision to the actual candidate set or normalized search inputs, or explicitly bound the search scope tightly enough that exact-match invalidation remains usable.

## Top Risks
- Task admission can become nondeterministic because the document blocks on “strong overlap” without defining the overlap contract or the signed preflight payload.
- Capability registry history can become corrupt under retries because Transaction B requires idempotency that the schema cannot currently enforce.
- Provenance will fragment across `verified_by_task_id`, source-task links, events, and seed metadata, making trust level and ownership queries unreliable.

## Open Questions
- What exact normalized task fields are part of the canonical preflight input, and what portion of that input must be signed and persisted?
- What precise bucket/threshold definition makes a match “strong” enough to block creation?
- In audited flows, is `verified_by_task_id` the implementation task, the audit task, or something else?
- What is the canonical planner/bootstrap mutation API and transaction boundary for non-audited capability changes?
- Should preflight persistence be append-only per task revision, or is losing prior justifications considered acceptable?
- How is the novelty revision checked under SQLite concurrency so the validation/commit gap is actually closed?