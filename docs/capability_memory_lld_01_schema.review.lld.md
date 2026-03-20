**Verdict**
Not implementation-ready. The document leaves core contracts ambiguous at the points that matter most for correctness: how preflight results are trusted, how capability lifecycle states are constrained, how supersession is represented, and how rollout/backfill avoid poisoning or deadlocking the system.

**Findings**
1. **Supersede Payload Cannot Encode Both Sides**  
Severity: `critical`.  
Why: The rules say `supersede` creates a new capability ID and links the old row via `replaced_by_capability_id`, but the envelope only shows one mutation object with one `capability_id`. That object cannot unambiguously represent both the old capability being superseded and the new replacement body.  
Citations: Doc lines 266-268, 332-361.  
Consequence: Different implementations will either mutate the wrong row, invent an out-of-band second object, or split supersession into a non-atomic two-step flow.  
Needed: Define `supersede` as either `prior_capability_id` plus a `replacement` object, or as an explicit two-record mutation shape with atomic validation rules.

2. **Preflight Trust Boundary Is Missing**  
Severity: `high`.  
Why: The LLD says enforcement must live in the canonical write path, but Transaction A only validates that a caller supplied preflight exists and is fresh. It never says the canonical code recomputes overlap results or verifies that the submitted classification/matches/rationale are tied to a server-generated result.  
Citations: Doc lines 53-58, 367-381, 450-463, 479-482, 495-516. Local corroboration: [central_task_db.py#L3801](/Users/paul/projects/CENTRAL/scripts/central_task_db.py#L3801), [central_task_db.py#L4390](/Users/paul/projects/CENTRAL/scripts/central_task_db.py#L4390), [create_planner_task.py#L84](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py#L84), [create_planner_task.py#L219](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py#L219).  
Consequence: Any direct writer can fabricate a favorable preflight payload with a current freshness token and still appear compliant, which defeats the main control.  
Needed: Make preflight server-authored. Either recompute it inside `task-create`/`task-update`, or require an opaque canonical preflight result ID/hash that covers query, matches, classification, override, and revision.

3. **Capability Lifecycle And Provenance Are Underconstrained**  
Severity: `high`.  
Why: The schema exposes `status`, `verification_level`, `replaced_by_capability_id`, and `archived_at`, but the document never defines legal combinations or transitions. It also drops the HLD’s explicit validity/provenance requirement (`valid_from_event_id` or equivalent), so there is no canonical boundary for when a capability became true.  
Citations: Doc lines 83-105, 304-325, 356-361, 639-647. Local corroboration: [capability_memory_hld.md#L210](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L210), [capability_memory_hld.md#L222](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L222), [capability_memory_hld.md#L229](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L229).  
Consequence: You can persist nonsense states such as `proposed + audited`, `deprecated` without replacement/evidence, or `active` with `archived_at` set, and readers will disagree on what is current.  
Needed: Define the lifecycle state machine explicitly and enforce it in mutation validation. Add a canonical provenance boundary such as `valid_from_event_id` or an accepted-audit event reference.

4. **Preflight Classification Is Just A Label**  
Severity: `high`.  
Why: `new`, `follow_on`, `extends_existing`, `supersedes`, and `duplicate_do_not_create` are persisted, but the document does not define what each classification requires or permits. The only hard rule is “strong overlap + classified `new` without override => reject.”  
Citations: Doc lines 213-225, 521-545, 615-621.  
Consequence: Two compliant implementations can attach totally different behavior to the same classification, making the stored preflight row useless for automation, auditing, and analytics.  
Needed: Define per-class invariants: required matched task/capability references, whether creation is allowed, whether override is legal, and what extra writes are mandatory.

5. **Material Update Enforcement Is Not Deterministic**  
Severity: `high`.  
Why: Transaction A2 depends on detecting whether an update “materially change[s] task intent,” but the listed fields are partial and the hardest case, “major dependency changes,” is undefined. Existing task updates already allow a much larger mutable surface.  
Citations: Doc lines 388-414, 465-473. Local corroboration: [central_task_db.py#L1590](/Users/paul/projects/CENTRAL/scripts/central_task_db.py#L1590), [central_task_db.py#L1729](/Users/paul/projects/CENTRAL/scripts/central_task_db.py#L1729).  
Consequence: Duplicate-introducing edits will slip through some writers, while benign edits will be blocked by others.  
Needed: Define a deterministic materiality contract: exact fields that always require refresh, exact dependency-change rules, and whether the check is based on field presence, semantic diff, or an explicit caller flag.

6. **Scope And Affected-Repo Rules Are Missing**  
Severity: `high`.  
Why: The HLD says a capability must declare both `owning_repo_id` and `affected_repo_ids`, but the LLD never defines enforceable invariants for `scope_kind`. “Impossible repo scope” is mentioned only as a rejection example, not as a contract.  
Citations: Doc lines 90-92, 128-152, 341-343, 639-646. Local corroboration: [capability_memory_hld.md#L195](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L195), [capability_memory_hld.md#L204](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L204).  
Consequence: The registry can contain `local` capabilities affecting the wrong repo set, or `cross_repo_contract` capabilities with only one repo, which breaks repo-scoped search and overlap reasoning.  
Needed: Define exact invariants per `scope_kind` and enforce them during mutation, including whether `owning_repo_id` must always appear in `affected_repo_ids`.

7. **Lower-Trust Mutation Paths Are Referenced But Not Defined**  
Severity: `high`.  
Why: The document allows `provisional` and `planner_verified` capabilities, but only defines concrete transactions for audited acceptance and later upgrade-from-lower-trust. There is no canonical transaction for creating or updating lower-trust rows in the first place.  
Citations: Doc lines 63-66, 314-320, 416-442, 599-605.  
Consequence: Teams will invent ad hoc planner or bootstrap write paths, expanding the trusted surface in exactly the way the LLD claims to avoid.  
Needed: Either define one canonical lower-trust mutation transaction with authority/evidence rules, or remove those states from v1.

8. **Audit Acceptance Is Not Idempotent**  
Severity: `high`.  
Why: Transaction B is append-heavy (`capability_source_tasks`, `capability_events`) but does not define retry semantics or an idempotency key. If the caller loses the commit response and retries, some writes may conflict and others may duplicate.  
Citations: Doc lines 416-442, 153-204. Local corroboration: [central_task_db.py#L1766](/Users/paul/projects/CENTRAL/scripts/central_task_db.py#L1766), [central_task_db.py#L1792](/Users/paul/projects/CENTRAL/scripts/central_task_db.py#L1792), [central_task_db.py#L1830](/Users/paul/projects/CENTRAL/scripts/central_task_db.py#L1830).  
Consequence: Operators cannot safely retry an accepted audit after an ambiguous failure; manual repair becomes part of the happy path.  
Needed: Define idempotency on `audit_task_id` plus accepted version/outcome, and make capability mutation application return success on exact replay instead of partially failing.

9. **Bootstrap/Backfill Collides With Duplicate Blocking**  
Severity: `high`.  
Why: The rollout section says exact duplicates may still be blocked during bootstrap, but the repo already has a backfill flow for creating canonical tasks after implementation has already landed. Those tasks are intentionally duplicative relative to historical work.  
Citations: Doc lines 572-580, 582-622. Local corroboration: [create_planner_task.py#L98](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py#L98), [create_planner_task.py#L181](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py#L181), [create_planner_task.py#L238](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py#L238).  
Consequence: Rollout can block the very historical tasks needed to seed the registry, or force operators into undocumented overrides that blur real duplicates with bootstrap/backfill.  
Needed: Add explicit bootstrap/backfill semantics: allowed classification, bypass reason, persistence rules, and whether those records influence future duplicate metrics/search.

10. **Freshness Is Over-Broad And Not Observable Enough To Operate**  
Severity: `medium`.  
Why: The recommended freshness token is effectively global (`task_max_updated_at`, `task_event_max_id`, `capability_max_updated_at`, `capability_event_max_id`) with exact-match enforcement. Any unrelated change can invalidate pending work, and the LLD does not require metrics/events to measure stale rejects or override rates before switching from advisory to blocking.  
Citations: Doc lines 495-519, 523-528, 572-577.  
Consequence: Busy systems will generate constant preflight churn, encouraging bypass usage, and rollout decisions will be made without evidence.  
Needed: Scope the token to the searched novelty domain and require explicit instrumentation for stale-preflight rejects, strong-overlap overrides, and bypass usage.

**Top Risks**
- The registry will accumulate internally inconsistent capability rows because lifecycle, provenance, and supersession are not constrained tightly enough.
- Duplicate prevention will either be bypassed trivially or become operationally hostile due to untrusted preflight payloads and over-broad freshness invalidation.
- Bootstrap will stall or distort the data set because historical backfill is not separated cleanly from real duplicate task creation.

**Open Questions**
- What exact canonical artifact proves a preflight result came from CENTRAL and was not fabricated by the client?
- What are the legal transitions between `proposed`, `active`, `deprecated` and between `provisional`, `planner_verified`, `audited`?
- How is `supersede` encoded atomically: which field names identify the old capability, the new capability, and the required provenance links?
- Which task-update diffs are always material, and what downstream writes are mandatory for each classification?
- How are backfill/bootstrap tasks created without either being blocked as duplicates or polluting future novelty signals?