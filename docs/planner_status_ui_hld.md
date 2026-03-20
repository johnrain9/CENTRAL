# Planner Status UI HLD

## Purpose

Design a planner-first control surface for CENTRAL that makes it easy to:

- see what matters now
- understand dispatcher state at a glance
- inspect tasks and workers without drowning in flat task lists
- filter, sort, and drill into details on demand

This UI is for AI and human planners/coordinators. It is not a general project dashboard. It is an operations console for planning, dispatching, triage, and audit management.

## Problem

The current CLI and logs are useful but fragmented:

- planner state is spread across multiple commands
- dispatcher health and queue state are visible, but not easy to scan continuously
- task volume does not scale well in flat textual views
- failed audits, awaiting audits, parked work, and repo pressure require too much manual assembly
- live tails contain useful data, but too much visual noise

As task volume grows, the system needs a single control plane view that makes exceptions and actionable work obvious while still allowing detailed inspection.

## Goals

- Provide a dark-themed planner and dispatcher control panel.
- Make the default view answer:
  - what should I care about right now?
  - what is running?
  - what is eligible?
  - what is stuck?
  - what failed audit?
  - which repo is hot?
  - what is the dispatcher configured to do right now?
- Support dense scanning with details on demand.
- Support sorting and filtering across task metadata.
- Show worker-level operational data that matters in live use.
- Auto-refresh so the UI can be left open during active dispatch operations.

## Non-Goals

- Start/stop/restart dispatcher controls in v1.
- Full task editing in v1.
- A separate bespoke backend service in v1.
- Replacing canonical DB/CLI workflows. The UI is a new read surface first.
- Full websocket/subscription infra in v1.

## Ownership And System Boundary

- CENTRAL owns the UI and its read-model composition.
- CENTRAL DB remains the canonical source of truth for task state.
- Dispatcher remains the data plane for worker execution and runtime status emission.
- The UI is a read-focused control-plane surface in v1.

The UI should not introduce alternate task state, shadow scheduling rules, or UI-local truth. Any derived status shown in the UI must be explainable from canonical CENTRAL state plus dispatcher runtime data.

## Primary Users

- Planner/coordinator AI
- Human planner/coordinator
- Operator watching dispatcher behavior during active runs

## Design Principles

- Overview first, detail on demand.
- Exceptions before exhaustive lists.
- Dark theme by default.
- One flat giant task table is not the default experience.
- Live worker state must be visually prominent.
- Queue truth must be legible without reading logs.
- Labels must distinguish active faults from historical queue counts.

## Information Architecture

The UI should have four layers of information:

1. Global status
- dispatcher state
- capacity and worker settings
- top queue counts

2. Actionable frontier
- work that can run now
- work that needs planner/operator attention now

3. Structured backlog slices
- awaiting audit
- by repo
- recent changes

4. Detail on demand
- full task details
- full worker details
- linked audit/rework context
- recent events and artifacts

## Core Layout

Recommended v1 layout:

- top summary bar
- left/center main content with collapsible sections
- right-side detail drawer for selected task or worker

Alternative acceptable layout:

- two-column main view
- modal or expandable row for detail

The most important requirement is not the exact panel geometry. It is preserving scanability while making rich details available quickly.

## Top Summary Bar

Always visible. Must show:

- dispatcher state
- max workers
- active workers
- idle slots
- eligible task count
- awaiting audit count
- failed audit count
- blocked count
- stale worker/task count
- recent-changes count
- last refresh timestamp

Must also show current dispatcher settings:

- worker mode/backend
- default model
- claim policy
- capacity/backoff state if active

## Main Sections

### Active Workers

The most operationally important section during live dispatch.

Each worker row/card should show:

- task id
- repo
- task title
- worker/backend mode
- model
- started at
- elapsed runtime
- last heartbeat
- log file size
- recent log growth delta
- stale/flat indicator

This section should make it easy to spot:

- long-running workers
- flat/stale workers
- wrong-model workers
- noisy or fast-growing logs

### Actionable Now

Split into at least:

- eligible implementation tasks
- eligible audit tasks

Audit work should be visually distinct.

### Needs Attention

Must include:

- failed audits
- blocked tasks
- stale work
- runtime/planner mismatches
- pending review items

This section should surface exceptions first, not bury them below healthy queue state.

### Awaiting Audit

Show implementation tasks that are completed enough for audit but not yet accepted.

### By Repo

Repo breakdown is required.

Each repo grouping should show:

- active count
- eligible count
- awaiting audit count
- failed audit count
- blocked count
- running worker count

Each repo section should be expandable into a task list.

### Recent Changes

Show tasks or workers changed in the last N hours.

This helps planners resume context without rescanning the full system.

## Task Table / Row Model

Rows should be dense but readable.

Default visible fields:

- task id
- title
- repo
- task type
- priority
- planner status
- runtime status
- audit verdict
- dependency-blocked indicator
- updated age

Optional visible fields:

- initiative
- planner owner
- lease owner
- approval required

Expanded detail should show:

- summary
- objective
- acceptance
- testing
- dependencies
- linked audit
- linked rework
- recent events
- artifacts
- closeout or failure summary

## Sorting and Filtering

Must support sorting by:

- priority
- repo
- planner status
- runtime status
- task type
- audit verdict
- created time
- updated time
- age/staleness

Must support filtering by:

- repo
- planner status
- runtime status
- task type
- initiative
- audit state
- failed audit only
- awaiting audit only
- dependency blocked
- approval required

Saved filters are nice-to-have but not required in v1.

## Interaction Model

The UI should be optimized for:

- scan
- narrow
- inspect
- return

Recommended behaviors:

- collapsible sections
- row expansion or detail drawer
- sticky filter/sort controls
- clear visual state badges
- minimal click depth to see full task details

## Visual Design Direction

Dark theme by default.

Recommended style:

- charcoal / near-black background
- high-contrast text
- muted surfaces
- strong but restrained semantic accent colors

Suggested semantic palette:

- active/running: blue
- accepted/done: green
- awaiting audit/audit-related: teal
- warning/stale: amber
- failed: red
- blocked: orange-red
- neutral metadata: gray

Avoid a generic low-contrast admin-dashboard look. This should feel precise, dense, and deliberate.

## Refresh Model

V1 should auto-refresh.

Recommended policy:

- polling every 5-10 seconds
- visible last-refresh time
- manual refresh button also available

Reasoning:

- simpler and faster to ship than live subscriptions
- good enough for planner and dispatcher monitoring
- preserves a “live console” feel

Websocket/live push can come later if needed.

## Degraded And Failure States

The UI must handle partial truth and refresh failures explicitly.

Required states:

- loading
- empty
- stale data
- partial data available
- refresh failed
- dispatcher unavailable

Behavior expectations:

- show the age of the currently displayed data
- distinguish "no tasks" from "failed to load tasks"
- distinguish "dispatcher idle" from "dispatcher unreachable"
- preserve the last successful snapshot when refresh fails
- show a visible stale warning if auto-refresh stops succeeding

## Data Delivery Shape

V1 should prefer a thin read-model layer over direct ad hoc UI assembly from many independent shell calls.

The implementation should expose a small number of UI-oriented read payloads that aggregate existing CENTRAL data into stable sections such as:

- top summary
- active workers
- actionable now
- needs attention
- awaiting audit
- by repo
- recent changes

This does not require a new bespoke backend service in v1, but it does require deliberate read contracts so the UI is not tightly coupled to raw CLI text formatting.

## Data Sources

V1 should compose existing CENTRAL surfaces where possible:

- `view-planner-panel`
- `view-summary`
- `view-eligible`
- `view-review`
- `view-audits`
- `view-active`
- dispatcher status/runtime state surfaces

The UI should not invent alternate truth. CENTRAL DB remains canonical.

## Rollout Shape

Recommended rollout:

1. Read-only v1 using stable UI-oriented read payloads.
2. Validate the section hierarchy and refresh behavior with real planner use.
3. Add richer worker-health and drill-down affordances.
4. Later, add operator controls such as dispatcher start/stop/restart if still desired.

This keeps the first release focused on observability and planner decision quality before adding mutation risk.

## V1 Scope

Include:

- dark themed UI shell
- top summary bar
- active workers section
- actionable queue sections
- needs-attention section
- awaiting-audit section
- by-repo breakdown
- recent-changes section
- sortable/filterable task list
- detail drawer or expandable task detail
- auto-refresh

Exclude:

- dispatcher control actions
- in-UI task mutation flows
- advanced personalization
- push-based live transport

## Success Criteria

V1 is successful if a planner can answer, in under 30 seconds:

- what is running?
- what is eligible now?
- what should I intervene on?
- what audits failed?
- what repo is generating the most pressure?
- what model is each active worker using?
- whether a worker looks healthy or stale

## Risks

- too much information density without hierarchy
- confusing active-failure signals with historical counts
- making the UI a mirror of raw CLI output instead of a better control surface
- adding visual complexity without improving planner decisions
- accidental coupling to unstable text views instead of stable read payloads
- unclear degraded-state handling causing false operator confidence

## Recommended Next Step

After review, write an LLD that covers:

- page/component layout
- data contract per section
- polling strategy
- derived fields for worker log growth/staleness
- exact mapping from existing CLI/DB views to UI state
