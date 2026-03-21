# Capability Memory LLD 03: Worker Closeout Emission Contract

This document defines the worker-side closeout contract for capability memory.

It builds on:

- [capability_memory_hld.md](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md)
- [capability_memory_lld_01_schema.md](/Users/paul/projects/CENTRAL/docs/capability_memory_lld_01_schema.md)

LLD-01 defines the canonical capability schema, verification levels, scope invariants, and mutation payload envelope. This LLD defines when workers must emit a capability record proposal, what exact payload shape they must emit at closeout, how scope is classified, what audit enforcement does when the record is absent, and the exact prompt text needed to elicit reliable worker output.

The contract is intentionally machine-readable enough to embed directly in a worker prompt.

## Scope

This LLD covers:

- worker closeout emission requirements for capability-affecting tasks
- required versus optional capability mutation fields at worker closeout
- `scope_kind` decision rules and validation guidance
- the worker-visible mutation envelope that references the canonical LLD-01 shape
- audit enforcement behavior when a required capability proposal is missing
- exact prompt additions for implementation workers

This LLD does not change:

- the canonical DB schema
- the authoritative mutation envelope defined by LLD-01
- canonical transaction boundaries

## Design Goals

- make worker output reliable enough for prompt-driven automation
- avoid forcing capability records for tasks that cannot legitimately produce one
- keep worker closeout distinct from canonical audit acceptance
- make omission failures deterministic and auditable

## Core Principle

Workers do not directly create canonical capability truth.

Workers emit a capability mutation proposal inside closeout metadata when the task type and outcome require one. The proposal is later validated and applied according to the LLD-01 audit-coupled write path and verification rules.

## Task Type Emission Policy

### Emission Categories

The worker must classify the completed task into exactly one of the following categories for capability closeout purposes.

#### `must_emit`

The worker must emit a capability mutation proposal when the completed work introduces, changes, deprecates, or supersedes reusable behavior.

Task kinds in this category:

- `feature`
- `fix`
- `refactor_with_behavioral_change`

Interpretation rules:

- `feature` means a new reusable behavior, command surface, workflow, contract, reporting surface, or operator/planner affordance now exists.
- `fix` means an existing reusable behavior changed materially enough that the capability record should be created, updated, deprecated, or superseded.
- `refactor_with_behavioral_change` means the task was labeled or initially framed as a refactor, but the landed result changed external behavior, contract semantics, workflow expectations, or reuse guidance.

#### `may_emit`

The worker may emit a capability mutation proposal when the task itself is not authoritative implementation work but still discovered a valid candidate capability or deprecation that should be reviewed later.

Task kinds in this category:

- `design`
- `research`

Interpretation rules:

- emitted payloads from these tasks are proposals only
- they must not be treated as direct authorization to create `active` canonical truth
- they should normally use lower-trust verification paths described by the HLD and LLD-01

#### `must_not_emit`

The worker must not emit a capability mutation proposal when the task changes no reusable behavior and the closeout is purely operational or incidental.

Task kinds in this category:

- `admin_only`
- `config_only`

Interpretation rules:

- `admin_only` includes queue hygiene, bookkeeping, retries, ownership updates, or status correction with no reusable behavior change.
- `config_only` includes environment-only or local-configuration-only adjustments that do not introduce or materially change a reusable system capability or contract.

### Behavioral Override Rule

The category is determined by landed behavior, not by the task title alone.

If a task was titled as a refactor, cleanup, or migration but it changed reusable behavior, the worker must treat it as `refactor_with_behavioral_change` and emit a proposal.

If a nominal feature or fix landed with no reusable behavior change, the worker must explicitly state `capability_emission_required = false` and explain why in closeout.

## Worker Closeout Contract

Worker closeout must include a capability block in a machine-readable form.

### Canonical Closeout Fields

Required fields in worker closeout metadata:

- `task_type_category`
- `capability_emission_required`
- `capability_emission_reason`
- `capability_mutations`

Rules:

- `task_type_category` must be one of `must_emit`, `may_emit`, `must_not_emit`
- `capability_emission_required` must be `true` or `false`
- `capability_emission_reason` must be a short deterministic explanation
- `capability_mutations` must always be present as an array
- when no proposal is emitted, `capability_mutations` must be `[]`
- when `task_type_category = must_emit`, `capability_emission_required` must be `true` unless the worker explicitly determined no reusable behavior changed
- when `capability_emission_required = true`, `capability_mutations` must contain at least one valid mutation object

### Machine-Readable Worker Payload

Worker closeout must embed this block verbatim in its structured closeout JSON:

```json
{
  "capability_closeout": {
    "task_type_category": "must_emit|may_emit|must_not_emit",
    "capability_emission_required": true,
    "capability_emission_reason": "short explanation",
    "capability_mutations": []
  }
}
```

The worker may include additional closeout fields outside `capability_closeout`, but the field names above must remain exact.

## Capability Mutation Proposal Contract

LLD-01 remains the canonical definition of the mutation payload envelope. LLD-03 constrains what the worker must supply at closeout.

### Supported Actions

Worker proposals may use only:

- `create`
- `update`
- `deprecate`
- `supersede`

### Required Versus Optional Fields

#### Common Required Fields

Every emitted mutation object must include:

- `action`

Additional required fields depend on action.

#### `create`

Required fields:

- `action`
- `capability_id`
- `name`
- `summary`
- `kind`
- `scope_kind`
- `owning_repo_id`
- `affected_repo_ids`
- `entrypoints`
- `when_to_use_md`
- `do_not_use_for_md`
- `evidence_summary_md`
- `verification_level`

Optional fields:

- `replaced_by_capability_id`
- `metadata`

Rules:

- workers should normally omit `replaced_by_capability_id` for `create` unless the create is the replacement side of a larger supersession already represented elsewhere
- `metadata` should default to `{}` if the worker has nothing extra to supply

#### `update`

Required fields:

- `action`
- `capability_id`

Optional fields:

- any changed capability body field from the `create` shape
- `metadata`

Rules:

- workers should include only changed fields plus any fields needed to make scope intent unambiguous
- if the update changes repo scope, the worker must provide `scope_kind`, `owning_repo_id`, and `affected_repo_ids` together

#### `deprecate`

Required fields:

- `action`
- `capability_id`

Optional fields:

- `metadata`
- `evidence_summary_md`

Rules:

- deprecation notes should live in `metadata`
- if the worker already knows the replacement capability, prefer `supersede` over a bare `deprecate`

#### `supersede`

Required fields:

- `action`
- `prior_capability_id`
- `replacement`

The `replacement` object must include the full `create` required field set:

- `capability_id`
- `name`
- `summary`
- `kind`
- `scope_kind`
- `owning_repo_id`
- `affected_repo_ids`
- `entrypoints`
- `when_to_use_md`
- `do_not_use_for_md`
- `evidence_summary_md`
- `verification_level`

Optional fields inside `replacement`:

- `metadata`

Rules:

- `replacement.capability_id` must differ from `prior_capability_id`
- workers must not use `supersede` unless they intend the old capability to become deprecated and point to the replacement

### Verification Level Rules For Worker Proposals

Workers must propose the most defensible verification level, but the audit path remains authoritative.

Proposal guidance:

- audited implementation work with required audit: use `audited`
- implementation work that is not audit-backed yet: use `provisional`
- design or research candidate proposals: use `provisional`
- planner-verified backfill or explicit planner verification paths: use `planner_verified` only when the task actually represents that verification path

Workers must not claim `planner_verified` or `audited` unless the surrounding task flow actually supports that level under the HLD and LLD-01 rules.

## Scope Classification Rules

Workers must classify `scope_kind` using the decision tree below.

### Decision Tree

Apply these questions in order:

1. Does the reusable behavior or contract apply to exactly one repo, with no cross-repo consumer contract and no multi-system workflow expectation?
   - yes: `scope_kind = local`
   - no: continue
2. Does the capability primarily describe a contract, interface, schema, or behavior that must line up across two or more repos?
   - yes: `scope_kind = cross_repo_contract`
   - no: continue
3. Does the capability primarily describe an operator or planner workflow, command flow, or end-to-end runtime behavior that may span one or more systems?
   - yes: `scope_kind = workflow`
   - no: continue
4. Fallback:
   - if the worker cannot justify `cross_repo_contract`, choose `local` for one-repo behavior and `workflow` for operational flow behavior

### Scope Invariants

The worker proposal must obey the LLD-01 invariants:

- `local`
  - `affected_repo_ids` must contain exactly one repo
  - that repo must equal `owning_repo_id`
- `cross_repo_contract`
  - `affected_repo_ids` must contain at least two repos
  - `owning_repo_id` must be included in `affected_repo_ids`
- `workflow`
  - `affected_repo_ids` must contain at least `owning_repo_id`
  - it may contain multiple repos when the workflow spans systems

### Classification Examples

- a CENTRAL-only command or reporting surface: `local`
- a schema contract consumed by CENTRAL and Dispatcher: `cross_repo_contract`
- a task lifecycle or operator flow spanning audit and runtime behavior: `workflow`

## Worker Envelope Format

The worker proposal must be nested under `capability_closeout.capability_mutations` and the mutation objects must match the LLD-01 envelope shape.

Expected closeout structure:

```json
{
  "capability_closeout": {
    "task_type_category": "must_emit",
    "capability_emission_required": true,
    "capability_emission_reason": "Task changed reusable audit/closeout behavior.",
    "capability_mutations": [
      {
        "action": "create",
        "capability_id": "worker_closeout_capability_contract",
        "name": "Worker closeout capability contract",
        "summary": "Workers emit a structured capability closeout block for capability-affecting tasks.",
        "kind": "schema_contract",
        "scope_kind": "workflow",
        "owning_repo_id": "CENTRAL",
        "affected_repo_ids": ["CENTRAL"],
        "entrypoints": ["worker prompt closeout contract", "audit closeout parser"],
        "when_to_use_md": "Use when implementing or auditing worker closeout output.",
        "do_not_use_for_md": "Do not use for task types that change no reusable behavior.",
        "evidence_summary_md": "Implemented by the completed task and intended for audit validation.",
        "verification_level": "audited",
        "metadata": {}
      }
    ]
  }
}
```

Worker-level requirements:

- field names must match exactly
- arrays must be valid JSON arrays
- omitted optional fields should be absent rather than set to placeholder strings
- free-text explanations should stay concise and deterministic enough for audit review

## Audit Enforcement

### Audit Inputs

Audit must evaluate:

- task outcome and landed behavior
- worker closeout classification
- whether reusable capability impact exists
- whether the proposed mutation payload is present and structurally sufficient

### Missing Required Capability Proposal

If the task is capability-affecting and falls into `must_emit`, but worker closeout lacks a usable capability proposal:

- audit must treat the closeout as incomplete
- audit must not accept the task while the required capability mutation payload is absent
- acceptance transaction must fail or remain blocked until a valid payload is supplied

This aligns with LLD-01, which requires acceptance failure when accepted audit indicates capability impact but no capability mutation payload is present.

### Invalid Capability Proposal

If worker closeout includes a proposal but it is structurally invalid or contradicts scope/provenance rules:

- audit must reject the invalid payload
- bounded metadata correction may be applied during audit only if policy allows
- otherwise audit fails and follow-up work is created

Examples of invalidity:

- impossible `scope_kind` and `affected_repo_ids` combination
- `supersede` without a full replacement body
- `verification_level` incompatible with the task path
- missing required fields for the chosen action

### Non-Required Categories

If the task falls into `may_emit` and the worker omits a proposal:

- audit may still note a missed candidate capability
- omission alone does not force failure unless audit determines the task actually belongs in `must_emit`

If the task falls into `must_not_emit`:

- audit should confirm no reusable capability impact exists
- stray proposals may be discarded or called out as incorrect classification

## Prompt Contract

This section is self-contained and intended to be embedded directly into worker prompts.

### Exact Prompt Addition

Use the following text verbatim in implementation-worker prompts:

```text
Capability closeout contract:

1. Classify the completed task for capability emission as exactly one of:
   - must_emit: feature, fix, or refactor with behavioral change
   - may_emit: design or research
   - must_not_emit: admin-only or config-only

2. Base the classification on landed behavior, not just the task title.

3. Your closeout JSON must include this exact object:
   "capability_closeout": {
     "task_type_category": "must_emit|may_emit|must_not_emit",
     "capability_emission_required": true,
     "capability_emission_reason": "short explanation",
     "capability_mutations": []
   }

4. If the task introduced, changed, deprecated, or superseded reusable behavior, you must set "capability_emission_required": true and include at least one mutation object in "capability_mutations".

5. Allowed mutation actions are:
   - create
   - update
   - deprecate
   - supersede

6. Mutation object requirements:
   - create requires: action, capability_id, name, summary, kind, scope_kind, owning_repo_id, affected_repo_ids, entrypoints, when_to_use_md, do_not_use_for_md, evidence_summary_md, verification_level
   - update requires: action, capability_id, plus changed fields
   - deprecate requires: action, capability_id
   - supersede requires: action, prior_capability_id, and replacement with the full create field set

7. scope_kind rules:
   - local: exactly one affected repo and it must equal owning_repo_id
   - cross_repo_contract: two or more affected repos and owning_repo_id must be included
   - workflow: at least owning_repo_id, optionally multiple repos for end-to-end workflow behavior

8. Choose scope_kind with this order:
   - one-repo behavior only -> local
   - multi-repo contract/schema/interface -> cross_repo_contract
   - operator/planner/runtime workflow -> workflow

9. verification_level proposal rules:
   - audited for audit-backed implementation work
   - provisional for non-audited implementation work, research, or design proposals
   - planner_verified only when the task actually represents planner verification/backfill

10. If no capability proposal is required, set "capability_mutations": [] and explain why in "capability_emission_reason".

11. Do not omit the capability_closeout block. Audit may fail capability-affecting work when the required mutation payload is missing.
```

### Prompt Embedding Notes

Prompt generators should treat the text above as normative.

Required properties of the embedded contract:

- self-contained
- exact field names preserved
- no dependency on surrounding prose to understand required actions
- safe to include in JSON-oriented or schema-oriented worker prompts

## Machine-Readable Summary

The following summary is provided for direct prompt or parser embedding.

```json
{
  "capability_closeout_contract": {
    "task_type_categories": {
      "must_emit": ["feature", "fix", "refactor_with_behavioral_change"],
      "may_emit": ["design", "research"],
      "must_not_emit": ["admin_only", "config_only"]
    },
    "closeout_fields": {
      "required": [
        "task_type_category",
        "capability_emission_required",
        "capability_emission_reason",
        "capability_mutations"
      ],
      "optional": []
    },
    "mutation_actions": {
      "create": {
        "required": [
          "action",
          "capability_id",
          "name",
          "summary",
          "kind",
          "scope_kind",
          "owning_repo_id",
          "affected_repo_ids",
          "entrypoints",
          "when_to_use_md",
          "do_not_use_for_md",
          "evidence_summary_md",
          "verification_level"
        ],
        "optional": ["replaced_by_capability_id", "metadata"]
      },
      "update": {
        "required": ["action", "capability_id"],
        "optional": [
          "name",
          "summary",
          "kind",
          "scope_kind",
          "owning_repo_id",
          "affected_repo_ids",
          "entrypoints",
          "when_to_use_md",
          "do_not_use_for_md",
          "evidence_summary_md",
          "verification_level",
          "replaced_by_capability_id",
          "metadata"
        ]
      },
      "deprecate": {
        "required": ["action", "capability_id"],
        "optional": ["evidence_summary_md", "metadata"]
      },
      "supersede": {
        "required": ["action", "prior_capability_id", "replacement"],
        "replacement_required": [
          "capability_id",
          "name",
          "summary",
          "kind",
          "scope_kind",
          "owning_repo_id",
          "affected_repo_ids",
          "entrypoints",
          "when_to_use_md",
          "do_not_use_for_md",
          "evidence_summary_md",
          "verification_level"
        ],
        "replacement_optional": ["metadata"]
      }
    },
    "scope_kind_decision_order": [
      "single_repo_behavior_only => local",
      "multi_repo_contract_schema_interface => cross_repo_contract",
      "operator_planner_runtime_workflow => workflow"
    ],
    "audit_enforcement": {
      "missing_required_payload": "block_or_fail_audit_acceptance",
      "invalid_payload": "reject_payload_and_fail_or_request_follow_up",
      "missing_optional_payload_for_may_emit": "allowed_unless_audit_reclassifies_to_must_emit"
    }
  }
}
```

## Acceptance Mapping

This LLD satisfies the requested acceptance points by defining:

- which task types must emit, may emit, and must not emit capability records
- required and optional fields for worker-side mutation proposals
- `scope_kind` rules with a deterministic decision tree
- the worker-visible payload envelope aligned to LLD-01
- audit enforcement when required capability closeout is missing
- a self-contained prompt contract with exact text additions
