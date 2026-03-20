# Capability Memory And Redundancy Prevention HLD

This document defines the high-level design for a CENTRAL-native capability memory system that prevents redundant work, preserves awareness of reusable platform features, and scales planning beyond what one human or one AI can remember.

## Status

This HLD is intended to be implementation-driving.

It defines:

- canonical ownership
- enforcement boundaries
- capability identity rules
- verification rules
- rollout constraints

Focused LLDs should follow only after these contracts are accepted.

## Problem

The current task system is good at recording work, but it is not sufficient for remembering what now exists.

That creates a recurring failure mode:

- a task lands and changes CENTRAL, Dispatcher, or planner workflow
- the implementation exists in the codebase
- later, neither human nor AI remembers the new capability
- a new task is created to rebuild, replace, or work around something already solved

Task history alone does not solve this. A done task tells us what changed at one point in time. It does not reliably answer:

- what capability exists now
- when to use it
- what old pattern it replaced
- whether a newly proposed task is redundant

## Goals

- preserve memory of reusable capabilities introduced by tasks
- prevent redundant task creation before work enters the queue
- keep humans out of the critical path for routine novelty checks
- make capability awareness usable by AI-first workflows
- scale to dozens or hundreds of tasks without requiring global memory from any one planner
- integrate with existing CENTRAL task and audit lifecycle

## Non-Goals

- replace the canonical task DB
- turn the dispatcher into a planner
- force human approval of every task or every capability change
- create a general-purpose documentation portal
- block all duplicate work perfectly in v1

## Core Distinction

The system needs two kinds of memory:

- task system = memory of change
- capability registry = memory of current affordances

Tasks answer:

- what work was done
- by whom
- with what evidence

Capabilities answer:

- what the system can do now
- when that capability should be used
- what entrypoint or workflow exposes it
- what older pattern it replaces or deprecates

The design must preserve both without allowing them to drift.

## Canonical Ownership

### Source Of Truth Rule

Capability truth lives in the same canonical CENTRAL SQLite database as tasks.

There is no sidecar registry, generated index, or separate canonical file.

If any generated view, markdown, cache, or external summary disagrees with the DB, the DB wins.

### Durability Rule

Capabilities follow the same durability contract as canonical tasks:

- included in snapshots
- included in restore behavior
- included in migration/versioning discipline

### Transaction Rule

The following writes must be atomic at the DB level:

- task creation + persisted preflight metadata
- capability upsert + audit acceptance metadata when an audit accepts a capability-affecting change
- capability deprecation/supersession updates + source task linkage

This prevents contradictory states such as:

- task accepted, capability missing
- capability updated, audit missing
- preflight reading partial novelty metadata

## High-Level Architecture

### 1. Canonical Task DB

CENTRAL remains the source of truth for:

- implementation tasks
- audit tasks
- dependencies
- planner lifecycle
- runtime lifecycle
- closeout history
- task creation metadata

### 2. Canonical Capability Registry

CENTRAL adds capability tables to the same canonical DB.

A capability record represents a reusable current-system affordance, not a historical note.

Example capabilities:

- operator can explicitly fail a running task without immediate retry
- dispatcher status exposes parked non-eligible work
- audit-task runtime success auto-closes parent implementation task
- worker result schema requires verdict, requirement checks, and system-fit assessment

### 3. Task Creation Preflight

Task creation invokes a mandatory preflight inside the canonical write path.

That preflight searches for overlap against:

- active tasks
- recent accepted tasks
- active capabilities
- deprecated/superseded capabilities

### 4. Audit-Coupled Capability Verification

If a task changes reusable behavior, the audit verifies:

- whether capability impact exists
- whether the proposed capability mutation is correct
- whether the task is redundant with an existing capability

## Capability Identity Model

The review was right that descriptive fields alone are too weak. Capability identity must be stable enough to drive search and overlap decisions.

### What Counts As A Capability

A capability should be recorded only if it changes at least one of:

- operator workflow
- planner workflow
- reusable runtime behavior
- schema or contract behavior consumed by multiple tasks or repos
- reusable tooling or command surface
- generated reporting surface that operators or planners are expected to use

Do not create capability records for purely local implementation details.

### Capability Grain

A capability record describes a user-facing or operator-facing behavior/contract at the level where a planner or worker would decide to reuse it.

Good examples:

- `operator_fail_task`
- `dispatcher_parked_task_visibility`
- `audit_auto_close_on_success`
- `worker_result_audit_verdict_contract`

Bad examples:

- internal helper function names
- private refactors that do not change reusable behavior
- implementation notes without an entrypoint or usage rule

### New vs Update vs Supersede

- create a new capability when a new reusable behavior or contract appears
- update an existing capability when behavior evolves compatibly and the same entrypoint/intent remains valid
- supersede when users should stop using capability A and start using capability B
- deprecate when capability remains known but should no longer be used

### Capability Scope

A capability must declare both:

- `owning_repo_id`
- `affected_repo_ids`

This addresses cross-repo contracts such as CENTRAL/Dispatcher integration and shared schema behavior.

It must also declare:

- `scope_kind = local | cross_repo_contract | workflow`

### Capability Validity

Each capability record must include validity metadata:

- `status = proposed | active | deprecated`
- `valid_from_event_id` or equivalent task/audit provenance
- optional `replaced_by_capability_id`
- optional `applicable_schema_version`
- optional `applicable_runtime_generation`

Capabilities are not timeless prose. They are versioned operational truth.

### Required Evidence

A capability record is not valid with prose alone. It must be backed by:

- source task IDs
- verifying audit task ID or alternate verifier record
- entrypoints
- concise evidence summary

## Capability Record Model

At minimum, a capability record should contain:

- `capability_id`
- `name`
- `summary`
- `status`
- `kind`
- `scope_kind`
- `owning_repo_id`
- `affected_repo_ids`
- `entrypoints`
- `when_to_use`
- `do_not_use_for`
- `source_task_ids`
- `verified_by_task_id`
- `verification_level`
- `replaced_by_capability_id`
- `applicable_schema_version`
- `applicable_runtime_generation`
- `keywords`
- `evidence_summary`
- timestamps and versioning metadata

The registry must be queryable by:

- keywords
- repo ownership
- affected repo
- kind
- scope
- entrypoint
- source task
- active versus deprecated status

## Verification Model

The earlier HLD said “audits ground capability truth,” but that was underspecified. The rule needs an applicability matrix.

### Verification Levels

Capability records must carry an explicit verification level:

- `audited`
- `planner_verified`
- `provisional`

`audited` is the preferred and highest-trust state.

### Applicability Matrix

- audited implementation task:
  - may create, update, deprecate, or supersede capability records
  - verification level should be `audited`
- implementation task with `audit_required = false`:
  - may propose capability impact
  - capability may only become `provisional` unless a planner or later audit verifies it
- research/planning task:
  - may not directly create active capability truth
  - may propose candidate capabilities or deprecations
- backfilled already-landed work:
  - may create/update capability truth only through the paired backfill audit

This preserves the principle that durable reusable truth should normally be audit-grounded, while still allowing the system to represent lower-trust interim knowledge when necessary.

## Task Creation Preflight

### Enforcement Boundary

Preflight must be enforced in the canonical task creation write path, not only in a helper script or UI.

That means:

- `task-create` must require preflight metadata
- `create_planner_task.py` must invoke canonical preflight
- direct creation without preflight should be rejected unless an explicit privileged override path exists

### Persisted Preflight Metadata

Each created task must persist:

- preflight timestamp
- preflight query text
- matched task IDs
- matched capability IDs
- creator classification
- novelty rationale
- override reason if overlap was overridden

### Search Domains

The search should inspect:

- active implementation tasks
- active audit tasks
- recent accepted implementation tasks
- active capabilities
- recently deprecated or superseded capabilities
- optional task/event notes for exact commands, schema names, or feature names

### Output Size

The preflight result should return:

- at most a small ranked set
- grouped into exact duplicate, strong overlap, related capability, related recent work

This should be decision-oriented, not an unbounded dump.

### Classification

The creator must classify the task as one of:

- `new`
- `follow_on`
- `extends_existing`
- `supersedes`
- `duplicate_do_not_create`

And must record:

- `related_task_ids`
- `related_capability_ids`
- `novelty_rationale`

### Concurrency And Duplicate-Creation Races

The review correctly noted that mandatory preflight alone does not stop duplicate creation races.

The system must therefore:

- compute preflight against a fresh DB snapshot in the same logical creation flow
- persist the DB revision or equivalent freshness marker used for preflight
- reject or require retry if the underlying task/capability set changed materially before commit

For v1, this can be conservative:

- if strong-overlap candidates changed between preflight and commit, abort creation and rerun preflight

This is an optimistic concurrency rule for novelty, not just task versioning.

## Capability Impact Model

### Worker Role

Workers propose capability impact at closeout:

- `none`
- `create`
- `update`
- `deprecate`
- `supersede`

If not `none`, the worker proposes a capability draft or patch.

### Audit Role

Audits verify whether:

- capability impact was correctly identified
- the correct capability was updated
- the proposed capability record is accurate
- the task ignored or duplicated an existing capability

### Planner Role

Planners should not review every capability change manually.

Planners handle only:

- taxonomy conflicts
- ambiguous overlap cases
- multi-capability restructuring
- disputes between worker proposal and audit judgment

## Lifecycle Integration

### Before Task Creation

The planner or planner-agent must run canonical preflight.

Creation may be:

- blocked for strong duplicates
- warned but allowed for weaker overlaps
- forced only with recorded override rationale

### During Implementation

The implementation task proceeds normally.

Worker closeout includes:

- capability impact proposal
- capability draft/update proposal if applicable

### During Audit

The audit checks:

- was the correct thing built
- does the system now expose a reusable capability
- is the proposed capability record accurate
- was prior system capability ignored, duplicated, or superseded incorrectly

### On Audit Acceptance

If the task is capability-affecting and the audit accepts it:

- the capability registry mutation occurs atomically with acceptance
- the capability record becomes active or updated
- the implementation task closes normally

### On Audit Failure

If capability impact is missing or wrong:

- bounded metadata fixups may be applied during audit if policy allows
- otherwise the audit fails and follow-up work is created

## Rollout Plan

### Phase 0: Bootstrap And Coverage Seeding

The registry must not start empty and then immediately become authoritative.

Before blocking task creation based on capability overlap, CENTRAL should:

- seed capability candidates from accepted audits
- seed known high-value current surfaces
- seed key backfilled tasks such as audit lifecycle and dispatcher/operator tooling

Initial preflight should be advisory until coverage is judged sufficient.

### Phase 1: Canonical Registry And Manual Seeding

- add registry schema inside CENTRAL DB
- add capability search/list/show surfaces
- manually seed known high-value capabilities
- persist preflight metadata, but do not hard-block except for exact duplicates

### Phase 2: Enforced Preflight In Write Path

- require preflight metadata for task creation
- add optimistic concurrency freshness checks
- add override recording

### Phase 3: Capability Impact And Audit Integration

- worker proposes capability impact
- audits verify capability impact
- accepted audits mutate registry transactionally

### Phase 4: Capability-Aware Planner Surfaces

- control panel integration
- initiative/track integration
- recently added/deprecated capability surfacing

## Quality Bar For Preflight

The review correctly noted that search quality is the product here, not an implementation detail.

Before preflight becomes strongly blocking, define and measure:

- response latency target
- maximum default result count
- acceptable precision on historical duplicate and overlap examples
- acceptable recall on known “should have matched” examples
- override rate and override-review loop

For v1:

- keep results small
- prefer higher precision over aggressive low-confidence recall
- collect override telemetry before tightening enforcement

## Risks

### Registry Bloat

If every tiny change becomes a capability, the registry becomes noisy.

Mitigation:

- only record reusable or behavior-changing capabilities
- let audits reject low-value capability additions

### Stale Capability Entries

A capability registry is dangerous if it becomes untrustworthy.

Mitigation:

- same DB, same durability, same snapshots as tasks
- explicit status and validity metadata
- audit-backed verification where possible

### Preflight Distrust

If early preflight misses obvious capabilities, planners will ignore it.

Mitigation:

- bootstrap phase
- advisory rollout
- measurable ranking evaluation

### Cross-Repo Ambiguity

Shared contracts can drift if ownership is unclear.

Mitigation:

- separate owning repo from affected repos
- model scope kind explicitly

## Required LLDs

This HLD should be followed by focused LLDs:

1. Capability registry schema, transactional rules, and DB migration
2. Task creation preflight API, ranking logic, and concurrency model
3. Worker closeout contract for capability impact
4. Audit rules for capability verification and trust levels
5. Planner/operator surfaces for capability search and control-panel integration
6. Capability bootstrap and seeding workflow

## Recommendation

Proceed with HLD-first, then LLD-by-slice.

The first LLD should be the capability registry schema plus enforced preflight write-path contract, because those two pieces determine whether the system actually prevents redundant work rather than merely documenting it.
