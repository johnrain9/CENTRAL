# Capability Memory LLD 01: Canonical Schema, Transaction Rules, And Preflight Persistence

This document is LLD 01 for the capability memory system described in [capability_memory_hld.md](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md).

It defines:

- canonical DB tables
- write-path enforcement points
- transaction boundaries
- migration approach
- bootstrap/seeding behavior

This LLD intentionally does not define ranking details for overlap search. That belongs in the preflight/ranking LLD.

This revision incorporates self-critique of the first draft by:

- simplifying the v1 schema where possible
- defining capability ID rules
- extending novelty enforcement to material task updates
- tightening the preflight freshness contract
- defining a concrete mutation payload envelope for audited capability changes

This revision also incorporates the adversarial LLD review by:

- fixing the `supersede` mutation shape
- defining server-authored preflight trust boundaries
- constraining capability lifecycle and provenance
- making classification semantics explicit
- tightening material-update rules
- separating bootstrap/backfill behavior from ordinary duplicate blocking
- requiring observability for freshness rejection and override behavior

## Scope

This LLD covers:

- capability registry storage inside the CENTRAL SQLite DB
- persistent preflight metadata for task creation
- transactional rules for task creation and audit-accepted capability mutation
- bootstrap rules for initial capability seeding
- the minimum canonical preflight contract required for blocking enforcement
- the minimum idempotency and provenance rules required for safe capability mutation

This LLD does not yet cover:

- search/ranking algorithms
- UI/control-panel surfaces
- worker prompt contract details beyond persisted fields

This LLD does define the minimum bucket semantics and token contract needed so task admission is deterministic.

## Design Decisions

### 1. Capabilities Live In The Same Canonical DB

Capabilities are stored in the same SQLite DB as tasks.

They are:

- canonical
- included in snapshots
- restored with snapshots
- migrated with the same migration sequence as task tables

No sidecar database, local cache, or generated markdown is canonical.

### 2. Preflight Must Be Enforced In The Write Path

Task creation cannot rely only on `create_planner_task.py`.

Canonical enforcement must live in `central_task_db.py` task-creation logic so that all writers are subject to the same rule.

### 3. Capability Mutation Is Tied To Verification

Capability creation/update/deprecation does not happen as a detached post-step.

For audited work, capability mutation happens transactionally with audit acceptance.

For lower-trust paths, capability mutation is allowed only at lower verification levels defined below.

### 4. V1 Should Prefer A Smaller Trusted Surface

The v1 schema should optimize for trust and operability, not exhaustiveness.

Fields that are useful but not required for the first working version should default into `metadata_json` instead of becoming hard-required top-level columns immediately.

## Schema Additions

### Table: `capabilities`

Purpose:

- canonical registry of reusable current-system affordances

Columns:

- `capability_id` `TEXT PRIMARY KEY`
- `name` `TEXT NOT NULL`
- `summary` `TEXT NOT NULL`
- `status` `TEXT NOT NULL`
  - check in `('proposed', 'active', 'deprecated')`
- `kind` `TEXT NOT NULL`
  - examples: `planner_tool`, `operator_tool`, `runtime_behavior`, `schema_contract`, `workflow`, `reporting_surface`
- `scope_kind` `TEXT NOT NULL`
  - check in `('local', 'cross_repo_contract', 'workflow')`
- `owning_repo_id` `TEXT NOT NULL`
- `when_to_use_md` `TEXT NOT NULL`
- `do_not_use_for_md` `TEXT NOT NULL DEFAULT ''`
- `entrypoints_json` `TEXT NOT NULL DEFAULT '[]'`
- `keywords_json` `TEXT NOT NULL DEFAULT '[]'`
- `evidence_summary_md` `TEXT NOT NULL DEFAULT ''`
- `verification_level` `TEXT NOT NULL`
  - check in `('provisional', 'planner_verified', 'audited')`
- `verified_by_task_id` `TEXT`
- `replaced_by_capability_id` `TEXT`
- `created_at` `TEXT NOT NULL`
- `updated_at` `TEXT NOT NULL`
- `archived_at` `TEXT`
- `metadata_json` `TEXT NOT NULL DEFAULT '{}'`

Foreign keys:

- `owning_repo_id -> repos.repo_id`
- `verified_by_task_id -> tasks.task_id`
- `replaced_by_capability_id -> capabilities.capability_id`

Indexes:

- `(status, kind)`
- `(owning_repo_id, status)`
- `(verification_level, status)`

Notes:

- `summary` and `when_to_use_md` are mandatory because a capability must be understandable without chasing task history.
- `metadata_json` carries v1-adjacent optional fields such as:
  - `applicable_schema_version`
  - `applicable_runtime_generation`
  - `compatibility_notes`
  - `deprecation_notes`

### Table: `capability_affected_repos`

Purpose:

- represent cross-repo scope

Columns:

- `capability_id` `TEXT NOT NULL`
- `repo_id` `TEXT NOT NULL`
- `created_at` `TEXT NOT NULL`

Primary key:

- `(capability_id, repo_id)`

Foreign keys:

- `capability_id -> capabilities.capability_id`
- `repo_id -> repos.repo_id`

Indexes:

- `repo_id`

### Table: `capability_source_tasks`

Purpose:

- link capabilities to source tasks that created or materially changed them

Columns:

- `capability_id` `TEXT NOT NULL`
- `task_id` `TEXT NOT NULL`
- `relationship_kind` `TEXT NOT NULL`
  - check in `('created_by', 'updated_by', 'deprecated_by', 'superseded_by', 'seeded_from')`
- `created_at` `TEXT NOT NULL`

Primary key:

- `(capability_id, task_id, relationship_kind)`

Foreign keys:

- `capability_id -> capabilities.capability_id`
- `task_id -> tasks.task_id`

Indexes:

- `task_id`

### Table: `capability_events`

Purpose:

- append-only history for capability lifecycle changes

Columns:

- `event_id` `INTEGER PRIMARY KEY`
- `capability_id` `TEXT NOT NULL`
- `event_type` `TEXT NOT NULL`
- `actor_kind` `TEXT NOT NULL`
- `actor_id` `TEXT NOT NULL`
- `payload_json` `TEXT NOT NULL DEFAULT '{}'`
- `created_at` `TEXT NOT NULL`

Foreign keys:

- `capability_id -> capabilities.capability_id`

Indexes:

- `(capability_id, created_at)`
- `(event_type, created_at)`
- `(actor_id, created_at)`

Notes:

- `payload_json` must include a mutation digest or transition digest when emitted from a transactional capability mutation path.
- `capability_events` are append-only and are not the primary idempotency mechanism.

### Table: `task_creation_preflight`

Purpose:

- persist the novelty and overlap check that justified task creation

Columns:

- `preflight_id` `INTEGER PRIMARY KEY`
- `task_id` `TEXT NOT NULL`
- `task_version` `INTEGER NOT NULL`
- `preflight_revision` `TEXT NOT NULL`
- `preflight_token` `TEXT NOT NULL`
- `preflight_request_json` `TEXT NOT NULL`
- `preflight_response_json` `TEXT NOT NULL`
- `query_text` `TEXT NOT NULL`
- `classification` `TEXT NOT NULL`
  - check in `('new', 'follow_on', 'extends_existing', 'supersedes', 'duplicate_do_not_create')`
- `novelty_rationale` `TEXT NOT NULL`
- `override_reason` `TEXT`
- `override_kind` `TEXT NOT NULL DEFAULT 'none'`
  - check in `('none', 'weak_overlap', 'strong_overlap_privileged', 'bootstrap_bypass')`
- `related_task_ids_json` `TEXT NOT NULL DEFAULT '[]'`
- `related_capability_ids_json` `TEXT NOT NULL DEFAULT '[]'`
- `matched_task_ids_json` `TEXT NOT NULL DEFAULT '[]'`
- `matched_capability_ids_json` `TEXT NOT NULL DEFAULT '[]'`
- `blocking_bucket` `TEXT NOT NULL`
  - check in `('none', 'weak_overlap', 'strong_overlap', 'duplicate')`
- `strong_overlap_count` `INTEGER NOT NULL DEFAULT 0`
- `override_allowed` `INTEGER NOT NULL DEFAULT 0`
- `performed_at` `TEXT NOT NULL`
- `performed_by` `TEXT NOT NULL`
- `metadata_json` `TEXT NOT NULL DEFAULT '{}'`

Foreign keys:

- `task_id -> tasks.task_id`

Indexes:

- `(task_id, task_version)`
- `(classification, performed_at)`
- `(blocking_bucket, performed_at)`

Notes:

- `preflight_revision` is a freshness marker from CENTRAL, not a user-facing value.
- `task_creation_preflight` is append-only by task version. It preserves create-time and material-update justifications separately.
- `preflight_request_json` and `preflight_response_json` are canonical serialized envelopes, not arbitrary debug blobs.
- `preflight_token` is CENTRAL-issued and binds the normalized request, response, and freshness marker together.

### Table: `capability_mutation_applications`

Purpose:

- idempotency ledger for Transaction B and other canonical capability mutation paths

Columns:

- `application_key` `TEXT PRIMARY KEY`
- `source_task_id` `TEXT NOT NULL`
- `source_task_version` `INTEGER NOT NULL`
- `mutation_digest` `TEXT NOT NULL`
- `applied_at` `TEXT NOT NULL`
- `actor_id` `TEXT NOT NULL`
- `outcome` `TEXT NOT NULL`
  - check in `('applied', 'replayed')`
- `metadata_json` `TEXT NOT NULL DEFAULT '{}'`

Foreign keys:

- `source_task_id -> tasks.task_id`

Indexes:

- `(source_task_id, source_task_version)`

## Capability ID Rules

Capability identity must be stable enough to prevent the registry from becoming an unstructured bag of notes.

V1 rules:

- `capability_id` is a canonical slug-like identifier
- it should describe the reusable behavior, not the implementation detail
- it must remain stable across compatible updates
- new IDs are created only when a genuinely new capability appears or an old capability is superseded

Examples:

- `operator_fail_task`
- `dispatcher_parked_task_visibility`
- `audit_auto_close_on_success`
- `worker_result_audit_verdict_contract`

Rules:

- create:
  - new `capability_id`
- update:
  - must reference existing `capability_id`
- deprecate:
  - must reference existing `capability_id`
- supersede:
  - creates new `capability_id` and links old row via `replaced_by_capability_id`

Capability IDs should be assigned by the control-plane mutation path, not freehand in arbitrary markdown.

### Optional Table: `capability_bootstrap_queue`

Purpose:

- represent seeded-but-not-yet-reviewed capability candidates during rollout

Columns:

- `candidate_id` `INTEGER PRIMARY KEY`
- `source_task_id` `TEXT`
- `candidate_payload_json` `TEXT NOT NULL`
- `seed_reason` `TEXT NOT NULL`
- `seeded_at` `TEXT NOT NULL`
- `seeded_by` `TEXT NOT NULL`
- `review_state` `TEXT NOT NULL DEFAULT 'pending'`

This table is optional. It is useful if Phase 0 seeding is expected to happen incrementally.

## Task Metadata Additions

The existing `tasks.metadata_json` should carry the following fields where relevant:

- `capability_impact`
  - `none | create | update | deprecate | supersede`
- `capability_ids`
  - list of referenced or mutated capability IDs
- `preflight_required`
  - boolean, default true for planner-created tasks
- `preflight_override`
  - optional summary if strong overlap was overridden

These metadata fields are not the canonical registry itself. They are task-local hints and provenance.

## Verification Levels

Capability records must carry one of:

- `provisional`
- `planner_verified`
- `audited`

Rules:

- audited implementation task accepted by audit:
  - capability may be `audited`
- non-audited implementation task:
  - capability may be at most `provisional` unless later upgraded
- planner or backfill workflow without audit acceptance:
  - capability may be `planner_verified` only through explicit planner verification path

V1 recommendation:

- keep capability activation strict
- prefer fewer higher-trust active capability entries over many weak ones

## Capability Lifecycle State Rules

The capability lifecycle must be constrained explicitly.

### Legal `status` Values

- `proposed`
- `active`
- `deprecated`

### Legal `verification_level` Values

- `provisional`
- `planner_verified`
- `audited`

### Required Provenance

Each capability row must carry canonical provenance as:

- `verified_by_task_id`

And may additionally carry secondary provenance in `metadata_json`, such as:

- `seed_origin`
- `bootstrap_note`

### Legal Combinations

- `proposed`:
  - may be `provisional` or `planner_verified`
  - may not be `audited`
- `active`:
  - may be `planner_verified` or `audited`
  - may be `provisional` only during explicit bootstrap rollout, not steady-state
- `deprecated`:
  - may be `planner_verified` or `audited`
  - should not be `provisional`

### Additional Invariants

- `archived_at` must be null unless the row is no longer part of active registry operations
- `status = deprecated` should normally include either:
  - `replaced_by_capability_id`
  - or deprecation notes in `metadata_json`
- `replaced_by_capability_id` must refer to a distinct capability row
- `verified_by_task_id` must point to:
  - the accepted audit task for `verification_level = audited`
  - the explicit planner verification task for `verification_level = planner_verified`
  - the bootstrap/planner seed task for `verification_level = provisional`

### Transition Rules

- create:
  - starts as `proposed` or `active` depending on verification path
- audited acceptance:
  - may move `proposed -> active`
  - may move `active -> deprecated`
- planner verification:
  - may move `proposed -> active` only for explicitly allowed non-audited bootstrap/planner paths
- supersession:
  - old capability moves to `deprecated`
  - new capability becomes `active`

V1 rule:

- capability provenance is task-based, not event-based
- `capability_events` describe lifecycle history, but trust queries should anchor on `verified_by_task_id`

## Capability Mutation Payload Envelope

Audited capability mutation must use a concrete payload shape, even if richer field validation lands in a later LLD.

V1 envelope:

```json
{
  "capability_mutations": [
    {
      "action": "create|update|deprecate",
      "capability_id": "dispatcher_parked_task_visibility",
      "name": "Dispatcher parked task visibility",
      "summary": "Dispatcher status surfaces parked non-eligible tasks.",
      "kind": "reporting_surface",
      "scope_kind": "workflow",
      "owning_repo_id": "CENTRAL",
      "affected_repo_ids": ["CENTRAL"],
      "entrypoints": ["scripts/central_runtime.py status", "scripts/dispatcher_control.py status"],
      "when_to_use_md": "Use when triaging queue state and non-eligible work.",
      "do_not_use_for_md": "Do not treat as scheduler policy output.",
      "evidence_summary_md": "Verified by accepted audit task.",
      "verification_level": "audited",
      "replaced_by_capability_id": null,
      "metadata": {}
    },
    {
      "action": "supersede",
      "prior_capability_id": "old_capability_id",
      "replacement": {
        "capability_id": "new_capability_id",
        "name": "New capability name",
        "summary": "New capability summary",
        "kind": "workflow",
        "scope_kind": "cross_repo_contract",
        "owning_repo_id": "CENTRAL",
        "affected_repo_ids": ["CENTRAL", "Dispatcher"],
        "entrypoints": ["..."],
        "when_to_use_md": "Use the new capability",
        "do_not_use_for_md": "Do not use for old cases",
        "evidence_summary_md": "Verified by accepted audit task",
        "verification_level": "audited",
        "metadata": {}
      }
    }
  ]
}
```

Rules:

- `create` requires full capability body
- `update` requires `capability_id` plus changed fields
- `deprecate` requires `capability_id` plus optional deprecation notes in metadata
- `supersede` requires:
  - `prior_capability_id`
  - a full `replacement` object with a distinct `capability_id`
  - atomic application of:
    - old row `status -> deprecated`
    - old row `replaced_by_capability_id -> new capability`
    - new row creation as active/properly verified

This envelope should be stored in task/audit closeout metadata and applied transactionally on accepted audit.

### Canonical Mutation Provenance Rules

- for audited capability mutation:
  - `verified_by_task_id` must equal the accepted audit task ID
- for planner-verified mutation:
  - `verified_by_task_id` must equal the explicit planner verification task ID
- for bootstrap/provisional mutation:
  - `verified_by_task_id` must equal the seeding or backfill task ID

## Canonical Preflight Request/Response Contract

This LLD must define the minimum preflight contract because task admission depends on it.

### Preflight Request

Canonical normalized request shape:

```json
{
  "normalized_task_intent": {
    "title": "...",
    "summary": "...",
    "objective_md": "...",
    "scope_md": "...",
    "deliverables_md": "...",
    "acceptance_md": "...",
    "target_repo_id": "CENTRAL",
    "dependency_task_ids": ["..."],
    "task_type": "implementation"
  },
  "search_scope": {
    "repo_ids": ["CENTRAL"],
    "include_recent_done_days": 90,
    "include_active_tasks": true,
    "include_capabilities": true
  },
  "requested_by": "planner/coordinator"
}
```

Rules:

- the normalized task intent is the signed input
- cosmetic or non-material fields must not affect the request digest
- the request must include the exact repo/dependency intent that admission is checking

### Preflight Response

Canonical response shape:

```json
{
  "classification_options": ["new", "follow_on", "extends_existing", "supersedes"],
  "blocking_bucket": "none|weak_overlap|strong_overlap|duplicate",
  "strong_overlap_count": 0,
  "matched_task_ids": ["..."],
  "matched_capability_ids": ["..."],
  "override_allowed": false,
  "preflight_revision": "{...}",
  "issued_at": "...",
  "issued_by": "CENTRAL"
}
```

### Token Contract

`preflight_token` must be a CENTRAL-issued digest over:

- canonical normalized preflight request
- canonical preflight response
- issued timestamp
- issuer identity

Writers do not invent this token. They only replay it to the canonical create/update path.

### Minimum Blocking Semantics

This LLD does not define ranking formulas, but it does define minimum bucket semantics:

- `none`
  - no overlap strong enough to constrain creation
- `weak_overlap`
  - related prior work exists; creation allowed with explicit classification/rationale
- `strong_overlap`
  - highly similar prior task/capability exists; creation blocked unless privileged override is permitted
- `duplicate`
  - the intended work is materially redundant; ordinary creation is rejected

These buckets must be deterministic for the same normalized request and search scope.

## Classification Semantics

Persisted classification must mean something operationally.

### `new`

Meaning:

- creator believes no relevant existing task/capability already covers the intended work

Requirements:

- may have weak related matches
- may not have strong-overlap matches without override
- should not reference explicit superseded or extended targets

### `follow_on`

Meaning:

- task intentionally continues or completes prior work

Requirements:

- must reference at least one related task ID
- may reference zero or more capability IDs
- related task IDs must be persisted in `related_task_ids_json`, not inferred from matched lists

### `extends_existing`

Meaning:

- task builds on an existing capability rather than creating a new one from scratch

Requirements:

- must reference at least one related capability ID
- related capability IDs must be persisted in `related_capability_ids_json`

### `supersedes`

Meaning:

- task intends to replace an existing task pattern or capability

Requirements:

- must reference the prior task or capability being replaced
- must persist the exact replaced target in `related_task_ids_json` or `related_capability_ids_json`
- should produce an explicit supersession mutation if capability-affecting

### `duplicate_do_not_create`

Meaning:

- proposed work is redundant and should not produce a canonical new task

Requirements:

- task creation should be rejected in ordinary paths
- may be allowed only in explicit bootstrap/backfill/admin modes with reason codes

## Explicit Related References

Preflight matched results and creator-declared references are different things.

Rules:

- `matched_task_ids_json` and `matched_capability_ids_json` record what CENTRAL found
- `related_task_ids_json` and `related_capability_ids_json` record what the creator selected as the canonical relationship basis
- create/update validation must reject:
  - `follow_on` without `related_task_ids_json`
  - `extends_existing` without `related_capability_ids_json`
  - `supersedes` without at least one explicit related target

## Transaction Boundaries

### Transaction A: Task Creation With Preflight

Canonical steps in one DB transaction:

1. validate task-create payload
2. derive or resolve canonical preflight result inside the write path
3. validate that the provided preflight token/result is server-authored
4. acquire the SQLite write lock via `BEGIN IMMEDIATE`
5. recompute or verify freshness against current novelty-domain revision under that write lock
5. validate classification semantics and required references
6. reject if strong-overlap rules are violated
7. write task row
8. write execution settings
9. write dependencies
10. write `task_creation_preflight`
11. auto-create paired audit task if required
12. write task events
13. commit

Result:

- no task exists without persisted preflight metadata
- no preflight metadata exists without the created task
- no client-authored preflight can bypass canonical overlap logic
- the freshness comparison happens while holding the write-intent lock, so the validation/commit gap is closed in v1

### Transaction A2: Material Task Update With Preflight Refresh

Novelty checks must also apply when a task is materially changed after creation.

Canonical steps in one DB transaction:

1. detect whether updated fields materially change task intent
2. if not material, perform normal update
3. if material:
   - acquire `BEGIN IMMEDIATE`
   - require fresh preflight payload
   - validate freshness
   - update task row
   - append a new `task_creation_preflight` row for the new task version
   - write task event describing refreshed novelty classification
4. commit

Material task changes include:

- title
- summary
- objective
- scope
- deliverables
- target repo
- any dependency add/remove
- dependency kind change
- acceptance changes that materially alter intended outcome

This prevents bypassing redundancy checks by creating a vague task and later mutating it into duplicate work.

### Transaction B: Audit Acceptance With Capability Mutation

Canonical steps in one DB transaction:

1. validate audit task acceptance
2. validate proposed capability mutation set
3. validate capability lifecycle/state invariants
4. apply capability upserts/deprecations/supersessions
5. write capability source-task links
6. write capability events
7. update parent task capability metadata if needed
8. mark audit task done
9. mark parent task done
10. commit

Result:

- accepted audit and capability mutation cannot drift apart

Idempotency rule:

- Transaction B must be idempotent on:
  - `audit_task_id`
  - accepted parent version
  - accepted mutation payload digest

Exact replay of the same accepted audit mutation must succeed without duplicating rows or producing conflicting events.

V1 mechanism:

- compute `application_key = sha256(audit_task_id + parent_version + mutation_digest)`
- insert into `capability_mutation_applications` before applying row mutations
- if the same `application_key` already exists:
  - do not reapply row mutations
  - record replay outcome if needed
  - return success with the already-applied result

### Transaction C: Capability Upgrade From Lower Trust

If a provisional capability is later audited:

1. verify the audit task references the capability
2. upgrade verification level
3. update evidence fields
4. write source-task link and event
5. commit

## Enforced Write Path Changes

### `task-create`

New requirements:

- planner-created tasks must include canonical preflight result reference unless using an explicit privileged override mode
- canonical code validates freshness, trust, and classification before writing

Suggested interface change:

- `task-create --input ... --preflight-input ...`

or

- embed preflight block inside task-create payload and validate it in canonical code

Preferred v1:

- require one of:
  - embedded canonical `preflight_result`
  - or a `preflight_token` returned by `task-preflight`

Trust rule:

- the canonical path must not trust client-computed matches/classifications by themselves
- it must either:
  - recompute overlap inside the write path
  - or verify an opaque canonical token/result digest previously issued by CENTRAL

Create/update must also verify:

- the normalized request implied by the submitted task payload exactly matches the normalized request bound into `preflight_token`
- the selected classification is one of the `classification_options` returned by CENTRAL
- the declared related references satisfy the selected classification

### `task-update`

New requirement:

- material task updates must either:
  - include a refreshed preflight block
  - or be rejected

Non-material metadata-only updates may skip preflight refresh.

### `task-preflight`

`task-preflight` should become the canonical producer of preflight results.

Its output should include:

- `preflight_token`
- freshness marker
- matched tasks/capabilities
- overlap buckets
- allowed classifications
- whether strong-overlap override is even eligible

### `create_planner_task.py`

Changes:

- helper must run canonical preflight first
- helper must present overlap results
- helper must require classification and rationale
- helper must include preflight block when calling canonical create

### Direct Writers

Any other DB writer path must either:

- provide valid preflight metadata
- or use an explicit bypass path reserved for migration/bootstrap/admin workflows

## Freshness And Race Handling

### Freshness Marker

V1 should use a simple CENTRAL-local revision marker for novelty freshness.

Recommended approach:

- define `preflight_revision` as a canonical monotonic freshness token derived from CENTRAL DB state over the relevant novelty domains

V1 recommended implementation:

- use a novelty-domain-scoped structured object serialized as stable JSON, for example:

```json
{
  "search_scope": {
    "repo_ids": ["CENTRAL", "Dispatcher"],
    "keywords": ["audit", "preflight", "capability"]
  },
  "task_max_updated_at": "...",
  "task_event_max_id": 1234,
  "capability_max_updated_at": "...",
  "capability_event_max_id": 456
}
```

- compute this immediately before returning preflight results
- require exact match at create/update commit time

This is conservative and easy to reason about.

The ranking LLD may refine how much of the DB contributes to this token, but the exact-match freshness rule should remain simple in v1.

Practical refinement:

- the token should be scoped to the matched candidate set and declared search scope, not the entire global DB
- unrelated writes outside that scoped novelty domain must not invalidate the token

### Creation Rule

If the relevant freshness marker changed between preflight and attempted create:

- rerun preflight
- require the creator to confirm again

This is intentionally conservative.

### Strong Overlap Abort

If fresh preflight shows strong-overlap candidates and the creator classified as `new` without override:

- reject task creation

### Override Authority

V1 should distinguish:

- normal creator override:
  - may override weak overlap with rationale
- privileged planner/admin override:
  - may override strong overlap with explicit reason

Strong-overlap override should be rare and fully persisted in the preflight row.

Required observability:

- count stale-preflight rejections
- count strong-overlap overrides
- count privileged bypasses
- count bootstrap/backfill creates

## Migration Plan

### Migration Number

Add a new migration after `0002_audit_lifecycle_statuses.sql`, for example:

- `0003_capability_registry_and_preflight.sql`

### Migration Contents

The migration should:

- create `capabilities`
- create `capability_affected_repos`
- create `capability_source_tasks`
- create `capability_events`
- create `task_creation_preflight`
- add supporting indexes

No backfill should be done inside the schema migration itself.

### Backward Compatibility

Existing tasks remain valid.

New enforcement should initially be introduced in rollout phases:

- schema first
- advisory create flow second
- blocking enforcement later

Existing tasks do not need retroactive preflight rows for the migration to succeed.

However, capability search and bootstrap workflows may later derive candidate overlap knowledge from historical accepted tasks.

## Bootstrap And Seeding

The registry must not start empty and immediately become authoritative.

### Phase 0 Seeding Sources

Seed capability candidates from:

- accepted or high-confidence audited tasks
- backfilled tasks such as lifecycle and worker-report contract hardening
- known high-value operator/planner/runtime surfaces

Suggested initial seed owners:

- planner/coordinator for manual seed curation
- future audit/backfill worker tasks for bulk candidate generation

### Canonical Bootstrap Mutation Path

V1 must define a real lower-trust mutation path, not leave it implicit.

Allowed bootstrap/planner mutation paths:

- `planner_verified` mutation
- `provisional` bootstrap seed mutation

Required transaction shape:

1. validate explicit bootstrap/planner mode
2. validate capability row shape and scope invariants
3. write or update capability row at allowed verification level
4. write `capability_source_tasks`
5. write `capability_events`
6. persist bootstrap/planner reason in metadata and event payload
7. commit

Disallowed in v1:

- creating `audited` capabilities outside accepted audit Transaction B

### Initial Verification State

Seeded capabilities should start as either:

- `audited` if backed by accepted audited work
- `planner_verified` if deliberately seeded from known current state without fresh audit

Avoid mass-creating `active provisional` capabilities.

### Duplicate Handling During Seeding

If multiple seed candidates appear to describe the same capability:

- do not auto-create multiple active capability rows
- queue for planner curation or merge logic in the bootstrap workflow

### Bootstrap And Backfill Classification Rules

Bootstrap/backfill must be explicit, not treated as ordinary duplicate creation.

Allowed behavior:

- a backfill/bootstrap task may use:
  - `follow_on`
  - `extends_existing`
  - or explicit bootstrap/admin bypass

Disallowed behavior:

- ordinary duplicate rejection should not block a canonical historical backfill task whose purpose is audit/provenance recovery

Persistence requirements:

- bootstrap/backfill rows must record:
  - `override_reason`
  - `metadata_json.bootstrap_mode = true`
  - whether the record should influence future novelty search immediately or only after verification

### Preflight Enforcement During Bootstrap

Until registry coverage is acceptable:

- preflight should persist results
- exact duplicates may be blocked
- weaker overlaps should warn, not hard-block

Bootstrap rows must persist:

- `override_kind = bootstrap_bypass`
- explicit reason
- selected related references if any
- whether the capability should influence future search immediately

## Failure Handling

### Missing Capability Mutation On Accepted Audit

If an accepted audit indicates capability impact but no capability mutation payload is present:

- fail the acceptance transaction
- require the audit or follow-up to provide the mutation payload

### Stale Preflight On Create

If preflight is stale:

- reject create with a structured error
- require rerun

### Untrusted Preflight Token

If the canonical path cannot verify that the preflight result/token was issued by CENTRAL:

- reject create/update
- do not write task or preflight rows

### Contradictory Capability Update

If a mutation attempts to:

- update a deprecated capability incorrectly
- supersede a capability without provenance
- assign impossible repo scope

Then reject transaction and leave both task and registry unchanged.

### Missing Preflight On Material Update

If a task update materially changes intent but does not include refreshed preflight metadata:

- reject update
- leave task unchanged

## Initial CLI And API Surface

This LLD assumes future additions such as:

- `capability-list`
- `capability-show`
- `capability-search`
- `task-preflight`

But the key v1 change is not new surface area alone. It is canonical storage and enforced persistence in the existing creation path.

Suggested additions for implementation:

- `task-preflight`
- `capability-list`
- `capability-show`
- `capability-search`
- `capability-apply-mutations` (internal helper path, not necessarily a public end-user command)

## Scope Invariants

Capability scope must be validated, not just described.

### `scope_kind = local`

Requirements:

- `affected_repo_ids` must contain exactly one repo
- that repo must equal `owning_repo_id`

### `scope_kind = cross_repo_contract`

Requirements:

- `affected_repo_ids` must contain at least two repos
- `owning_repo_id` must be included in `affected_repo_ids`

### `scope_kind = workflow`

Requirements:

- `affected_repo_ids` must contain at least `owning_repo_id`
- may contain multiple repos when the workflow spans systems

Enforcement point:

- scope invariants are validated in the canonical create/update/mutation code before join-table writes are applied
- join-table replacement must be atomic with the parent capability row update
- invalid scope payloads reject the whole transaction

## Open Questions For Later LLDs

- exact ranking and search strategy for overlap detection
- how much of preflight is lexical versus structured
- whether capability bootstrap candidates need their own queue table
- whether initiative/grouping metadata should also influence overlap ranking

## Recommended Next Step

The next LLD should define:

- `task-preflight` request/response contract
- overlap ranking rules
- freshness marker computation
- create-path enforcement and override semantics
