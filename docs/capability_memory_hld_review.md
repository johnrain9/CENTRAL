# Critical Review: Capability Memory HLD

Reviewed document: [`capability_memory_hld.md`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md)

## Verdict

`capability_memory_hld.md` identifies a real problem, but it is not ready to drive implementation yet. The largest gaps are around canonical ownership, enforcement at task-creation time, capability identity, and how this integrates with CENTRAL's current audit/task model without creating new drift or operator burden.

## Findings

### 1. Canonical ownership and transactional consistency are not defined

Relevant HLD sections:
- [`capability_memory_hld.md:41`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L41)
- [`capability_memory_hld.md:102`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L102)
- [`capability_memory_hld.md:113`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L113)
- [`capability_memory_hld.md:264`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L264)

Grounding in current CENTRAL:
- [`central_task_system.md:7`](/Users/paul/projects/CENTRAL/docs/central_task_system.md#L7)
- [`central_task_system.md:46`](/Users/paul/projects/CENTRAL/docs/central_task_system.md#L46)

Problem:
The HLD says CENTRAL adds "a second canonical layer alongside tasks", but it never states whether the capability registry lives in the same SQLite DB, follows the same durability/snapshot rules, or updates atomically with task/audit reconciliation. In the current CENTRAL model, the DB is the canonical source of truth and that rule is explicit. This HLD introduces another canonical surface without defining the consistency contract.

Why it matters:
If task closeout, audit acceptance, and capability updates are not one transactional unit, CENTRAL can end up in contradictory states:
- task and audit say the change is accepted
- capability registry is missing or stale
- preflight then gives wrong answers based on partial writes

What should change:
- State explicitly that the capability registry is part of the CENTRAL canonical SQLite model, not a sidecar index or generated artifact.
- Define the source-of-truth rule for capabilities.
- Define the transaction boundaries for:
  - task creation + novelty classification persistence
  - audit acceptance/failure + capability mutation
  - restore/snapshot/recovery behavior

### 2. "Mandatory preflight" is not enforceable as written and does not address duplicate-creation races

Relevant HLD sections:
- [`capability_memory_hld.md:46`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L46)
- [`capability_memory_hld.md:84`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L84)
- [`capability_memory_hld.md:197`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L197)
- [`capability_memory_hld.md:208`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L208)
- [`capability_memory_hld.md:240`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L240)

Grounding in current CENTRAL:
- [`central_task_cli.md:95`](/Users/paul/projects/CENTRAL/docs/central_task_cli.md#L95)
- [`central_task_cli.md:118`](/Users/paul/projects/CENTRAL/docs/central_task_cli.md#L118)
- [`create_planner_task.py:279`](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py#L279)

Problem:
The HLD says task creation "must" run preflight and the creator "must" classify the task, but it does not define where this is enforced. Today `task-create` and `create_planner_task.py` create canonical tasks directly. Nothing in the HLD defines:
- whether preflight is enforced in the write path or only as UI guidance
- how stale preflight results are detected
- what happens when two planners or planner-agents run the same preflight and both create near-duplicate tasks

Why it matters:
If preflight is only advisory, AI planners will eventually bypass it under time pressure. If it is enforced only in a client helper, other writers can skip it. If it is enforced without a race model, it still will not stop duplicates under concurrency.

What should change:
- Define a server-side enforcement point in the canonical task creation/update path.
- Require persisted preflight metadata on task creation:
  - preflight timestamp/version
  - matched task/capability IDs
  - creator classification
  - rationale / override reason
- Define how optimistic concurrency interacts with preflight so duplicate creation is checked against fresh state, not a stale search result.

### 3. The capability identity model is too weak to serve as canonical system memory

Relevant HLD sections:
- [`capability_memory_hld.md:117`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L117)
- [`capability_memory_hld.md:138`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L138)
- [`capability_memory_hld.md:224`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L224)
- [`capability_memory_hld.md:347`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L347)

Problem:
The proposed record shape is descriptive, but not strong enough to make capability records reliable over time. The examples mix several different kinds of truth:
- operator action
- runtime behavior
- workflow coupling
- schema contract

But the HLD does not define:
- when a change is an `update` versus a new capability
- whether capabilities are versioned per release/schema/runtime generation
- what evidence proves a capability is currently valid
- who owns the capability definition when multiple surfaces depend on it
- whether preflight should match on user-visible behavior, internal implementation, or both

Why it matters:
Without a stable identity model, the registry will become a bag of loosely related notes. That makes search noisy, overlap decisions inconsistent, and deprecation/supersession hard to trust.

What should change:
- Define capability grain explicitly.
- Add identity rules for:
  - new capability vs update
  - compatible evolution vs supersession
  - contract capabilities vs implementation details
- Add required evidence fields, not only prose fields.
- Add validity/version scope, for example schema version, runtime generation, or applicable system range.

### 4. The audit-based truth model conflicts with CENTRAL's current optional-audit task model

Relevant HLD sections:
- [`capability_memory_hld.md:68`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L68)
- [`capability_memory_hld.md:92`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L92)
- [`capability_memory_hld.md:132`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L132)
- [`capability_memory_hld.md:224`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L224)
- [`capability_memory_hld.md:281`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L281)

Grounding in current CENTRAL:
- [`central_task_cli.md:118`](/Users/paul/projects/CENTRAL/docs/central_task_cli.md#L118)
- [`create_planner_task.py:221`](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py#L221)
- [`create_planner_task.py:246`](/Users/paul/projects/CENTRAL/scripts/create_planner_task.py#L246)
- [`central_task_db.py:1443`](/Users/paul/projects/CENTRAL/scripts/central_task_db.py#L1443)

Problem:
The HLD assumes audits are the mechanism that grounds capability truth, but CENTRAL currently supports `audit_mode required|none`, and paired audits are auto-created only for planner-owned implementation tasks. The HLD does not say how capability truth is established for:
- research tasks
- planner tasks
- implementation tasks with `audit_mode=none`
- already-landed/backfilled work that may need capability backfill

Why it matters:
This creates a hidden architectural fork:
- either capabilities can only come from audited implementation tasks
- or the system needs another verification path

Right now the HLD says both "audits ground capability truth" and "do not force human approval of every capability change", but it never resolves the boundary.

What should change:
- Define an applicability matrix by task type and audit mode.
- Decide whether capability-affecting changes are allowed only on audited implementation tasks.
- If not, define the alternate verifier for non-audited work and how its trust level differs from audited capability entries.

### 5. The model does not work cleanly for cross-repo capabilities or shared contracts

Relevant HLD sections:
- [`capability_memory_hld.md:11`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L11)
- [`capability_memory_hld.md:117`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L117)
- [`capability_memory_hld.md:151`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L151)

Problem:
The motivating examples span CENTRAL, Dispatcher, and planner workflow, but the record model has only one `repo_id`. Several of the example capabilities are actually shared contracts or multi-repo behaviors, for example worker-result schema or audit-coupled lifecycle behavior.

Why it matters:
If a capability belongs to one repo in the model but is consumed by multiple repos in reality:
- preflight misses relevant overlap outside the chosen repo
- ownership becomes ambiguous
- updates can silently break downstream systems without updating related capability entries

What should change:
- Add first-class support for cross-repo scope.
- Distinguish:
  - owning repo
  - affected repos
  - contract scope vs local implementation scope
- Define how shared capabilities are searched during task preflight.

### 6. The rollout plan starts with an almost empty registry, which will make the mandatory preflight low-trust

Relevant HLD sections:
- [`capability_memory_hld.md:364`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L364)
- [`capability_memory_hld.md:366`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L366)
- [`capability_memory_hld.md:395`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L395)

Grounding in current CENTRAL:
- [`central_task_cli.md:163`](/Users/paul/projects/CENTRAL/docs/central_task_cli.md#L163)
- [`central_task_cli.md:204`](/Users/paul/projects/CENTRAL/docs/central_task_cli.md#L204)

Problem:
Phase 1 says "manual but structured capability entries" and then immediately recommends starting with registry schema plus preflight. That means the first version of preflight will consult a registry with very low coverage while CENTRAL already has meaningful task and audit history.

Why it matters:
Early false negatives are operationally dangerous here. If the first few preflights fail to surface capabilities people know exist, the system will lose credibility and planners will ignore it.

What should change:
- Add an explicit bootstrap/backfill phase for capability seeding.
- Seed initial capability candidates from accepted audits, backfilled tasks, and known high-value system surfaces.
- Keep preflight non-blocking until registry coverage reaches an explicit threshold.

### 7. The HLD has no measurable quality bar for preflight ranking, which is the core product behavior

Relevant HLD sections:
- [`capability_memory_hld.md:197`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L197)
- [`capability_memory_hld.md:204`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L204)
- [`capability_memory_hld.md:353`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L353)
- [`capability_memory_hld.md:377`](/Users/paul/projects/CENTRAL/docs/capability_memory_hld.md#L377)

Problem:
The HLD says preflight should be "cheap", "small", and "decision-oriented", but it does not define any quality targets for latency, precision, recall, or override rates. Search/ranking is not a detail here; it is the whole value proposition.

Why it matters:
Without measurable targets, the system can easily ship in one of two bad states:
- high-noise results that planners learn to ignore
- low-recall results that miss the exact redundant work the system exists to catch

What should change:
- Define evaluation criteria for preflight:
  - maximum response latency
  - maximum result set size
  - target precision/recall on a known set of historical tasks
  - override telemetry and review loop
- Require an offline evaluation set before blocking task creation based on overlap scoring.

## Top Risks

1. Capability truth drifts from task/audit truth because atomic ownership is not defined.
2. Mandatory preflight becomes advisory in practice and fails under concurrent planners.
3. The registry turns into untrusted metadata because capability identity and evidence are underspecified.
4. Audit-based verification creates blind spots or forces broader audit coupling than CENTRAL currently uses.
5. Early low-coverage rollout trains users and agents to ignore preflight results.

## Short Recommendation

Before writing LLDs, revise the HLD to answer four concrete questions:
- Where does canonical capability truth live and how is it updated atomically?
- How is preflight enforced in the write path, including concurrency races?
- What exactly is a capability record, and how does it evolve over time?
- Which task classes are allowed to create or modify capability truth, and how are they verified?
