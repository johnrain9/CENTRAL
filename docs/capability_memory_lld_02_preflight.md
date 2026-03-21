# Capability Memory LLD 02: Preflight Contract, Overlap Ranking, Freshness Tokens, And Create-Path Enforcement

This document is LLD 02 for the capability memory system described in [capability_memory_hld.md](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md) and [capability_memory_lld_01_schema.md](/Users/paul/projects/CENTRAL/docs/capability_memory_lld_01_schema.md).

It resolves the open questions intentionally deferred by LLD 01 and fully specifies the v1 preflight system.

It defines:

- the canonical `task-preflight` request/response contract
- the deterministic overlap ranking algorithm and bucket mapping
- freshness token computation and validation semantics
- create-path and material-update enforcement behavior
- override eligibility and required persisted override evidence

This LLD is implementation-driving. A worker should be able to implement the canonical preflight path, task-create enforcement, and override persistence from this document without making further design decisions.

## Scope

This LLD covers:

- canonical normalized input for `task-preflight`
- canonical search candidate set for tasks and capabilities
- scoring rules for:
  - exact duplicate
  - strong overlap
  - related capability
  - related recent work
- canonical response fields returned by `task-preflight`
- preflight token contents and freshness validation
- create and material-update rules for:
  - allow
  - warn
  - block
  - privileged override
- required audit trail when a block is overridden

This LLD does not change:

- capability schema defined in LLD 01
- audit-time capability mutation envelope defined in LLD 01
- UI presentation details for overlap results

## Design Goals

The v1 preflight design optimizes for:

- deterministic admission results for the same normalized task intent
- a small trusted surface in canonical task creation
- conservative duplicate blocking
- explicit creator intent when related prior work exists
- minimal false negatives for already-solved work

The v1 design intentionally tolerates some false positives in the `warn` path if that keeps `duplicate` and `strong_overlap` conservative and easy to reason about.

## Canonical Terms

### Candidate Types

Preflight ranks four kinds of novelty-domain candidates:

- active task
  - a task whose terminal status has not been reached
- recent accepted task
  - a done task accepted within the configured recent-work window
- active capability
  - a capability row with `status = active`
- deprecated capability
  - a capability row with `status = deprecated`

### Outcome Bands

Each candidate is assigned exactly one outcome band:

- `exact_duplicate`
- `strong_overlap`
- `related_capability`
- `related_recent_work`
- `non_actionable`

Only the highest band per candidate is retained.

### Admission Buckets

The canonical task-level result maps candidate bands into LLD 01 blocking buckets:

- `duplicate`
- `strong_overlap`
- `weak_overlap`
- `none`

## Normalized Task Intent

Preflight operates on a canonical normalized task intent derived from the proposed task payload.

### Required Input Fields

The normalized intent must contain:

- `title`
- `summary`
- `objective_md`
- `scope_md`
- `deliverables_md`
- `acceptance_md`
- `target_repo_id`
- `task_type`
- `dependency_task_ids`
- `dependency_kinds`

### Optional Input Fields

The normalized intent may contain:

- `parent_task_id`
- `initiative_key`
- `related_repo_ids`
- `requested_capability_ids`
- `requested_task_ids`
- `labels`

These optional fields participate in ranking only where explicitly stated below.

### Normalization Rules

The canonical writer must normalize the task intent before hashing or ranking.

Normalization rules:

- trim leading/trailing whitespace on all string fields
- collapse internal runs of whitespace to a single space for lexical comparison
- preserve original markdown in stored request JSON
- derive separate normalized plain-text forms of markdown fields for ranking
- lowercase comparison tokens
- remove punctuation-only tokens
- sort and deduplicate:
  - `dependency_task_ids`
  - `related_repo_ids`
  - `requested_capability_ids`
  - `requested_task_ids`
  - `labels`
- exclude non-material fields from normalization
  - examples: freeform notes, requester display text, CLI formatting flags

### Derived Comparison Fields

From the normalized intent, the preflight engine must derive:

- `intent_text`
  - concatenation of title, summary, objective, scope, deliverables, and acceptance plain text
- `intent_terms`
  - deduplicated lexical token set from `intent_text`
- `intent_fingerprint`
  - deterministic digest of:
    - normalized title
    - normalized summary
    - normalized objective plain text
    - normalized scope plain text
    - normalized deliverables plain text
    - normalized acceptance plain text
    - `target_repo_id`
    - `task_type`
    - sorted `dependency_task_ids`
- `repo_scope`
  - `target_repo_id` plus sorted `related_repo_ids`

`intent_fingerprint` is used only for exact-duplicate checks and token binding. It is not a global task identity.

## Canonical `task-preflight` Contract

### Request Shape

Canonical request JSON:

```json
{
  "normalized_task_intent": {
    "title": "Capability memory LLD-02",
    "summary": "Define task preflight, ranking, freshness, and create-time enforcement.",
    "objective_md": "...",
    "scope_md": "...",
    "deliverables_md": "...",
    "acceptance_md": "...",
    "target_repo_id": "CENTRAL",
    "task_type": "implementation",
    "dependency_task_ids": [],
    "dependency_kinds": {},
    "parent_task_id": null,
    "initiative_key": null,
    "related_repo_ids": [],
    "requested_capability_ids": [],
    "requested_task_ids": [],
    "labels": ["infrastructure"]
  },
  "search_scope": {
    "repo_ids": ["CENTRAL"],
    "include_active_tasks": true,
    "include_recent_done_days": 90,
    "include_capabilities": true,
    "include_deprecated_capabilities": true,
    "max_candidates_per_kind": 50
  },
  "request_context": {
    "requested_by": "planner/coordinator",
    "request_channel": "task-create",
    "is_material_update": false,
    "existing_task_id": null,
    "existing_task_version": null
  }
}
```

### Request Field Rules

Rules:

- `normalized_task_intent` is required and is the canonical input bound into the token
- `search_scope.repo_ids` defaults to:
  - `target_repo_id`
  - plus any `related_repo_ids`
- `include_active_tasks` must be true in canonical task creation
- `include_recent_done_days` defaults to `90`
- allowed range for `include_recent_done_days` is `30..180`
- `include_capabilities` must be true in canonical task creation
- `include_deprecated_capabilities` defaults to true
- `max_candidates_per_kind` defaults to `50` and may not exceed `200`
- `request_context.requested_by` is required for auditability but does not participate in ranking
- `request_context.request_channel` is required and allowed values are:
  - `task-create`
  - `task-update`
  - `planner-review`
  - `bootstrap`
- `existing_task_id` and `existing_task_version` are required when `is_material_update = true`

### Response Shape

Canonical response JSON:

```json
{
  "preflight_revision": {
    "algorithm_version": "capability-preflight-v1",
    "scope_fingerprint": "sha256:...",
    "task_domain": {
      "max_updated_at": "2026-03-20T18:42:11Z",
      "max_task_event_id": 1822
    },
    "capability_domain": {
      "max_updated_at": "2026-03-20T18:40:03Z",
      "max_capability_event_id": 114
    }
  },
  "issued_at": "2026-03-20T18:42:11Z",
  "issued_by": "CENTRAL",
  "classification_options": [
    "new",
    "follow_on",
    "extends_existing",
    "supersedes"
  ],
  "blocking_bucket": "strong_overlap",
  "override_allowed": true,
  "override_kind": "strong_overlap_privileged",
  "strong_overlap_count": 1,
  "duplicate_count": 0,
  "warning_count": 2,
  "candidates": [
    {
      "candidate_kind": "capability",
      "candidate_id": "worker_result_audit_verdict_contract",
      "band": "strong_overlap",
      "score": 89,
      "reason_codes": [
        "repo_scope_match",
        "shared_entrypoint_keywords",
        "deliverable_contract_match"
      ],
      "status": "active",
      "summary": "Worker result schema requires verdict and requirement checks."
    }
  ],
  "matched_task_ids": ["CENTRAL-OPS-12"],
  "matched_capability_ids": ["worker_result_audit_verdict_contract"],
  "related_task_ids_suggested": ["CENTRAL-OPS-12"],
  "related_capability_ids_suggested": ["worker_result_audit_verdict_contract"],
  "novelty_rationale_template": "New work touches an existing reusable contract and must be classified explicitly."
}
```

### Response Field Rules

Rules:

- `preflight_revision` is required and must be stable-serialized JSON
- `issued_at` is required and must be UTC RFC 3339
- `classification_options` is required and ordered by recommended creator choice first
- `blocking_bucket` is required and is derived from the highest candidate band
- `override_allowed` is required
- `override_kind` is required and must be one of:
  - `none`
  - `weak_overlap`
  - `strong_overlap_privileged`
  - `bootstrap_bypass`
- `strong_overlap_count` counts only `strong_overlap` candidates
- `duplicate_count` counts only `exact_duplicate` candidates
- `warning_count` counts `related_capability` plus `related_recent_work` candidates
- `candidates` must be sorted by:
  - descending `band` severity
  - descending `score`
  - ascending `candidate_id`
- `matched_task_ids` must include candidates in:
  - `exact_duplicate`
  - `strong_overlap`
  - `related_recent_work`
- `matched_capability_ids` must include candidates in:
  - `exact_duplicate`
  - `strong_overlap`
  - `related_capability`
- `related_*_ids_suggested` are CENTRAL suggestions only; they do not replace creator-declared references
- `novelty_rationale_template` is advisory text for UX and is not trusted at create time

## Candidate Search Set

The preflight engine must assemble candidates in the following order.

### Task Candidates

Include:

- active tasks in `search_scope.repo_ids`
- done tasks in `search_scope.repo_ids` completed within `include_recent_done_days`

Exclude:

- the same task version when `is_material_update = true`
- cancelled or rejected tasks that never represented accepted work
- tasks marked as non-material administrative records

### Capability Candidates

Include:

- active capabilities whose `owning_repo_id` or affected repo matches `search_scope.repo_ids`
- deprecated capabilities under the same scope when `include_deprecated_capabilities = true`

Exclude:

- `status = proposed` capabilities from blocking logic
- archived capabilities

`proposed` capabilities may appear only as informational `related_recent_work` candidates with a maximum score of `39`.

## Overlap Ranking Algorithm

The v1 algorithm is rule-based and deterministic. It does not use learned weights.

### Step 1: Compute Feature Flags Per Candidate

For each candidate, compute the following boolean features.

#### Shared Scope Features

- `repo_scope_match`
  - candidate repo scope intersects the request repo scope
- `same_target_repo`
  - candidate owning or target repo equals `target_repo_id`
- `dependency_reference_match`
  - request explicitly references candidate task ID or source task ID

#### Lexical Features

- `title_exact_match`
  - normalized title matches exactly
- `summary_exact_match`
  - normalized summary matches exactly
- `intent_fingerprint_match`
  - normalized intent fingerprint matches exactly
- `high_text_overlap`
  - Jaccard overlap of `intent_terms` is `>= 0.65`
- `moderate_text_overlap`
  - Jaccard overlap of `intent_terms` is `>= 0.40` and `< 0.65`
- `entrypoint_keyword_match`
  - at least two lexical tokens overlap with candidate entrypoints or capability keywords
- `deliverable_contract_match`
  - at least one deliverable/acceptance noun phrase overlaps with candidate summary, `when_to_use_md`, or recent-task title/summary

#### Lifecycle Features

- `candidate_is_active_task`
- `candidate_is_recent_done_task`
- `candidate_is_active_capability`
- `candidate_is_deprecated_capability`
- `candidate_verified_high_trust`
  - capability `verification_level in ('planner_verified', 'audited')`
- `candidate_completed_recently`
  - done task completed in the last `30` days

#### Intent Features

- `creator_marked_supersede_target`
  - candidate ID present in `requested_task_ids` or `requested_capability_ids` and classification intent is superseding
- `creator_marked_extension_target`
  - candidate capability present in `requested_capability_ids`
- `same_capability_surface`
  - candidate capability kind and overlapping entrypoints indicate the same reusable affordance
- `same_work_surface`
  - candidate task objective/scope indicates the same implementation surface even if work is incomplete

### Step 2: Assign Candidate Score

The candidate score is the sum of the following weights, capped at `100`.

#### Base Weights

- `repo_scope_match` = `10`
- `same_target_repo` = `10`
- `dependency_reference_match` = `10`
- `title_exact_match` = `25`
- `summary_exact_match` = `20`
- `intent_fingerprint_match` = `50`
- `high_text_overlap` = `25`
- `moderate_text_overlap` = `15`
- `entrypoint_keyword_match` = `10`
- `deliverable_contract_match` = `15`
- `candidate_is_active_task` = `20`
- `candidate_is_recent_done_task` = `12`
- `candidate_is_active_capability` = `20`
- `candidate_is_deprecated_capability` = `8`
- `candidate_verified_high_trust` = `8`
- `candidate_completed_recently` = `8`
- `creator_marked_supersede_target` = `12`
- `creator_marked_extension_target` = `12`
- `same_capability_surface` = `20`
- `same_work_surface` = `20`

#### Negative Adjustment

Apply a `-20` adjustment if:

- the candidate is a deprecated capability
- and `same_capability_surface` is false
- and `deliverable_contract_match` is false

This keeps unrelated historical capabilities from crowding the weak-overlap path.

### Step 3: Hard Classification Overrides

Before bucket mapping, apply the following hard rules.

#### Exact Duplicate

Classify as `exact_duplicate` if any of the following are true:

- `intent_fingerprint_match`
- `title_exact_match` and `summary_exact_match` and `same_target_repo`
- `same_capability_surface` and `title_exact_match` and candidate is an active capability with high trust

`exact_duplicate` candidates receive score `100`.

#### Strong Overlap

Classify as `strong_overlap` if not already an exact duplicate and any of the following are true:

- score `>= 75`
- candidate is an active capability and:
  - `same_capability_surface`
  - and `deliverable_contract_match`
- candidate is an active task and:
  - `same_work_surface`
  - and `high_text_overlap`
- candidate is a recent done task completed in the last `30` days and:
  - `same_work_surface`
  - and `deliverable_contract_match`

#### Related Capability

Classify as `related_capability` if not already higher severity and all of the following are true:

- candidate is a capability
- score `>= 40`
- at least one of:
  - `entrypoint_keyword_match`
  - `deliverable_contract_match`
  - `creator_marked_extension_target`

#### Related Recent Work

Classify as `related_recent_work` if not already higher severity and all of the following are true:

- candidate is a task
- score `>= 35`
- at least one of:
  - `moderate_text_overlap`
  - `dependency_reference_match`
  - `candidate_completed_recently`

#### Non-Actionable

Candidates not matching the above rules are `non_actionable` and omitted from persisted matched lists.

### Step 4: Task-Level Bucket Mapping

Map the highest candidate band to the preflight bucket:

- one or more `exact_duplicate` candidates:
  - `blocking_bucket = duplicate`
- otherwise one or more `strong_overlap` candidates:
  - `blocking_bucket = strong_overlap`
- otherwise one or more `related_capability` or `related_recent_work` candidates:
  - `blocking_bucket = weak_overlap`
- otherwise:
  - `blocking_bucket = none`

### Band Semantics

#### `exact_duplicate`

Meaning:

- the proposed task intent materially restates existing active work or an already-available capability

Admission effect:

- ordinary creation is blocked
- only bootstrap/admin bypass may proceed

#### `strong_overlap`

Meaning:

- the proposed task is not byte-for-byte duplicate but would likely duplicate existing active work or a current reusable capability unless explicitly justified

Admission effect:

- ordinary creation is blocked
- privileged override may proceed when allowed by enforcement rules below

#### `related_capability`

Meaning:

- a reusable capability exists in the same problem surface and the new task must be anchored to it if the work proceeds

Admission effect:

- creation is allowed
- creator must choose a non-`new` classification when the selected related capability is material to the work

#### `related_recent_work`

Meaning:

- recent task history touches the same implementation surface and should inform classification or decomposition

Admission effect:

- creation is allowed with warning
- creator should select `follow_on` or `supersedes` if appropriate

## Classification Options Returned By Preflight

The response must compute `classification_options` using the highest bucket and candidate mix.

Rules:

- `blocking_bucket = none`
  - options: `["new", "follow_on", "extends_existing", "supersedes"]`
- `blocking_bucket = weak_overlap`
  - if any `related_capability` candidate exists:
    - options: `["extends_existing", "follow_on", "supersedes", "new"]`
  - otherwise:
    - options: `["follow_on", "new", "supersedes", "extends_existing"]`
- `blocking_bucket = strong_overlap`
  - options: `["follow_on", "extends_existing", "supersedes"]`
  - omit `new`
- `blocking_bucket = duplicate`
  - options: `["duplicate_do_not_create"]`

The create path must reject any submitted classification not present in `classification_options`.

## Freshness Token Design

### Preflight Revision Structure

`preflight_revision` must encode the novelty-domain state used to produce the response.

Canonical stable JSON shape:

```json
{
  "algorithm_version": "capability-preflight-v1",
  "scope_fingerprint": "sha256:<digest>",
  "task_domain": {
    "max_updated_at": "<RFC3339 UTC timestamp or null>",
    "max_task_event_id": 1234
  },
  "capability_domain": {
    "max_updated_at": "<RFC3339 UTC timestamp or null>",
    "max_capability_event_id": 456
  }
}
```

### Scope Fingerprint

`scope_fingerprint` is `sha256` over stable JSON containing:

- normalized request `target_repo_id`
- sorted `search_scope.repo_ids`
- `include_recent_done_days`
- `include_active_tasks`
- `include_capabilities`
- `include_deprecated_capabilities`
- sorted IDs of actionable candidates returned by the query stage before scoring

Including the actionable candidate IDs prevents unrelated writes outside the candidate set from invalidating the token.

### Domain State Computation

At preflight time, compute:

- `task_domain.max_updated_at`
  - maximum `tasks.updated_at` across scoped task candidates
- `task_domain.max_task_event_id`
  - maximum task event ID across scoped task candidates
- `capability_domain.max_updated_at`
  - maximum `capabilities.updated_at` across scoped capability candidates
- `capability_domain.max_capability_event_id`
  - maximum capability event ID across scoped capability candidates

If a domain has no scoped candidates, use:

- `max_updated_at = null`
- max event ID = `0`

### Preflight Token Format

`preflight_token` must be:

- opaque to clients
- issued only by CENTRAL
- verifiable without trusting client-supplied matches

Canonical token payload before signing:

```json
{
  "version": "capability-preflight-token-v1",
  "request_sha256": "sha256:<digest>",
  "response_sha256": "sha256:<digest>",
  "preflight_revision_sha256": "sha256:<digest>",
  "issued_at": "2026-03-20T18:42:11Z",
  "issuer": "CENTRAL"
}
```

Canonical token value:

- `base64url(stable_json(token_payload)) + "." + hmac_sha256(server_secret, stable_json(token_payload))`

The server secret must live in CENTRAL control-plane configuration and must not be user-provided.

### What The Token Encodes

The token binds:

- the exact normalized request
- the exact canonical response
- the exact freshness revision
- the issuance time
- the issuer identity

### Token Validation At Create Time

Inside canonical create/update enforcement, CENTRAL must:

1. decode the token payload
2. verify signature using the server secret
3. verify `issuer = CENTRAL`
4. recompute stable JSON SHA-256 digests for:
   - submitted normalized request
   - submitted canonical response
   - submitted `preflight_revision`
5. reject if any digest mismatches
6. under `BEGIN IMMEDIATE`, recompute current `preflight_revision` for the same scoped candidate set
7. reject as stale if the recomputed revision differs exactly

The create path must not trust only the token payload. It must verify the response and revision presented alongside it.

### Token Lifetime

The token has no time-only expiration rule in v1.

Validity depends on:

- successful signature verification
- exact request/response binding
- exact freshness revision match at commit time

An old token may remain valid if and only if no relevant novelty-domain state changed.

## Create-Path Enforcement

### Required Inputs To Canonical `task-create`

Ordinary planner-created task creation must include:

- canonical task payload
- canonical normalized preflight request
- canonical preflight response
- `preflight_token`
- selected `classification`
- `novelty_rationale`
- explicit `related_task_ids`
- explicit `related_capability_ids`
- optional override block only when override is being exercised

### Required Inputs To Canonical `task-update`

Material updates must include the same preflight block, with:

- `request_context.is_material_update = true`
- `existing_task_id`
- `existing_task_version`

### Enforcement Order

Canonical enforcement order for create or material update:

1. normalize the submitted task payload
2. verify it exactly matches `normalized_task_intent`
3. verify token signature and request/response/revision digests
4. validate `classification` against `classification_options`
5. validate classification-specific related references from LLD 01
6. verify override payload if present
7. acquire `BEGIN IMMEDIATE`
8. recompute freshness revision for the same scope
9. reject if stale
10. if desired, recompute ranking and ensure the presented response still matches the canonical result under the current revision
11. apply bucket-specific enforcement
12. persist task row and `task_creation_preflight`
13. commit

Step 10 is recommended in v1 and mandatory if the implementation cannot cheaply prove that the stored response corresponds to the recomputed revision.

### Bucket-Specific Enforcement Matrix

#### `none`

Behavior:

- allow create/update
- no override permitted or needed

Persistence:

- `blocking_bucket = none`
- `override_kind = none`
- `override_allowed = 0`

#### `weak_overlap`

Behavior:

- allow create/update
- require explicit `classification`
- require non-empty `novelty_rationale`
- ordinary creator may proceed without privilege

Warnings:

- caller should be shown matching candidates before confirmation

Persistence:

- `blocking_bucket = weak_overlap`
- `override_kind = weak_overlap` only if the creator explicitly chooses to proceed despite a recommended non-`new` classification
- otherwise `override_kind = none`

`new` is allowed in weak-overlap only when:

- the submitted classification is present in `classification_options`
- and `novelty_rationale` explains why the related candidate does not cover the work

#### `strong_overlap`

Behavior:

- block ordinary create/update
- permit only privileged override

Privileged override eligibility:

- actor is planner/admin authority recognized by CENTRAL
- response field `override_allowed = true`
- response field `override_kind = strong_overlap_privileged`

Required privileged justification:

- why the candidate does not actually cover the intended work, or
- why parallel work is intentionally required, or
- why a superseding migration must proceed despite overlap

Persistence:

- `blocking_bucket = strong_overlap`
- `override_kind = strong_overlap_privileged` when exercised
- `override_allowed = 1`

Without override, canonical create/update must reject before writing any task rows.

#### `duplicate`

Behavior:

- reject ordinary create/update
- reject privileged strong-overlap override
- allow only explicit bootstrap/admin bypass mode

Allowed duplicate bypass cases:

- bootstrap provenance recovery
- historical backfill
- migration/admin repair

Persistence when bypassed:

- `classification = duplicate_do_not_create` for dry-run rejection
- for actual bootstrap/admin creation, the creator must use one of:
  - `follow_on`
  - `extends_existing`
  - `supersedes`
- `override_kind = bootstrap_bypass`

The ordinary planner path must never create a new task with `classification = duplicate_do_not_create`.

## Missing, Stale, And Mismatched Preflight

### Missing Preflight

If required preflight fields are missing:

- reject create/update
- do not write task or preflight rows

### Stale Preflight

If recomputed `preflight_revision` differs:

- reject with structured stale-preflight error
- require rerun of `task-preflight`

### Request Mismatch

If normalized task payload and submitted request differ:

- reject as untrusted preflight

Examples:

- title changed after preflight
- deliverables changed after preflight
- dependency set changed after preflight
- target repo changed after preflight

### Response Mismatch

If token verifies but submitted response does not match digests or canonical recomputation:

- reject as untrusted preflight

## Override Semantics And Recording

Overrides are canonical audit events, not ephemeral confirmations.

### When Override Is Considered To Have Happened

An override is considered exercised only when:

- `blocking_bucket` is `strong_overlap` or `duplicate`
- or `blocking_bucket` is `weak_overlap` and the creator proceeds against the top recommended classification

### Required Override Payload

When an override is exercised, the submitted create/update payload must include:

```json
{
  "override": {
    "override_kind": "weak_overlap|strong_overlap_privileged|bootstrap_bypass",
    "override_reason": "Human-readable justification.",
    "override_actor_id": "planner/coordinator",
    "override_authority": "ordinary_creator|planner_admin|bootstrap_admin",
    "acknowledged_candidate_ids": ["..."],
    "selected_related_task_ids": ["..."],
    "selected_related_capability_ids": ["..."]
  }
}
```

### Override Validation Rules

Rules:

- `override_kind` must equal the kind permitted by the response and enforcement path
- `override_reason` is required and must be non-empty
- `override_actor_id` is required
- `override_authority` is required and must match the caller's authenticated authority
- `acknowledged_candidate_ids` must contain every candidate ID in:
  - `exact_duplicate`
  - `strong_overlap`
  - and the top three `weak_overlap` warning candidates when `override_kind = weak_overlap`
- `selected_related_task_ids` and `selected_related_capability_ids` must be subsets of the matched candidate IDs plus explicit creator-entered references

### Required Persistence In `task_creation_preflight`

When an override is exercised, the `task_creation_preflight` row must persist:

- `override_reason`
- `override_kind`
- `override_allowed = 1`
- `metadata_json.override_actor_id`
- `metadata_json.override_authority`
- `metadata_json.acknowledged_candidate_ids`
- `metadata_json.override_timestamp`
- `metadata_json.override_request_channel`

If no override is exercised:

- `override_reason = null`
- `override_kind = none`
- `override_allowed` reflects whether a strong-overlap override would have been allowed, not whether it was used

### Required Task Metadata

When an override is exercised, `tasks.metadata_json` must include:

- `preflight_override.kind`
- `preflight_override.reason`
- `preflight_override.actor_id`
- `preflight_override.acknowledged_candidate_ids`

This duplicates the essential provenance into task-local metadata so operators can inspect the reason without joining immediately into preflight history.

## Persisted Preflight Row Requirements

When writing `task_creation_preflight`, the implementation must persist:

- the exact canonical request JSON
- the exact canonical response JSON
- the exact `preflight_token`
- the selected `classification`
- the submitted `novelty_rationale`
- `matched_task_ids_json`
- `matched_capability_ids_json`
- `related_task_ids_json`
- `related_capability_ids_json`
- `blocking_bucket`
- `strong_overlap_count`
- `override_allowed`
- `override_kind`
- `override_reason`
- any override metadata required above

Persistence must reflect the actual create/update decision, not only the raw preflight result.

## Error Contract

Canonical create/update must return structured machine-readable errors for:

- `preflight_missing`
- `preflight_untrusted`
- `preflight_stale`
- `preflight_classification_invalid`
- `preflight_related_references_invalid`
- `preflight_override_forbidden`
- `preflight_override_invalid`
- `preflight_duplicate_blocked`
- `preflight_strong_overlap_blocked`

Each error must include:

- `error_code`
- `message`
- `preflight_bucket`
- `matched_task_ids`
- `matched_capability_ids`
- `rerun_required`

## Observability

The canonical path must emit counters for:

- preflight requests issued
- preflight stale rejections
- preflight untrusted-token rejections
- duplicate blocks
- strong-overlap blocks
- weak-overlap proceeds
- strong-overlap privileged overrides
- duplicate bootstrap/admin bypasses
- material-update preflight rejections

The canonical path should also log:

- task ID or attempted task title
- request channel
- blocking bucket
- chosen classification
- override kind if any

## Resolved Open Questions From LLD 01

This LLD resolves all four open questions listed at the end of LLD 01.

### Exact Ranking And Search Strategy

Resolved by:

- defining the candidate search set
- defining deterministic feature extraction
- defining a fixed scoring table
- defining hard rules for:
  - exact duplicate
  - strong overlap
  - related capability
  - related recent work

### How Much Of Preflight Is Lexical Versus Structured

Resolved by:

- lexical comparison over normalized text and entrypoint keywords
- structured comparison over repo scope, dependencies, capability IDs, task IDs, lifecycle state, and capability trust level
- explicit feature weights showing both lexical and structured contribution

### Freshness Marker Computation

Resolved by:

- defining `preflight_revision`
- defining the scoped candidate-set fingerprint
- defining exact domain-state fields
- defining exact-match validation under `BEGIN IMMEDIATE`

### Create-Path Enforcement And Override Semantics

Resolved by:

- defining required inputs to `task-create` and `task-update`
- defining bucket-specific allow/warn/block behavior
- defining privileged override eligibility
- defining duplicate bootstrap/admin bypass
- defining required override payload and persisted fields

## Implementation Notes

Recommended implementation order:

1. implement normalized request builder
2. implement scoped candidate queries
3. implement deterministic scorer and bucket mapper
4. implement `task-preflight` response builder and token signer
5. enforce verification and freshness in canonical create/update
6. persist override evidence and observability counters

V1 implementation should favor correctness and stable serialization over query cleverness.
