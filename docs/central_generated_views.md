# CENTRAL Generated Views And Operator Surfaces

This document defines the human-facing views that should be generated from the canonical CENTRAL task database.

## Canonical Rule

- the CENTRAL DB is the source of truth
- generated CLI tables, JSON exports, dashboards, and optional markdown exports are read models only
- humans may read generated views, but they should not edit them as the authoritative record

## Design Goals

Generated views must:

- answer the most common planner and operator questions quickly
- scale to hundreds or thousands of tasks without forcing users through one giant file
- make canonical-vs-derived status obvious
- support terminal, JSON, and dashboard-style consumption, with markdown only as an optional export
- remain cheap to refresh from DB state

Generated views must not:

- become a second hand-maintained planning surface
- require planners to duplicate canonical data into markdown
- hide runtime state that matters for dispatch or review

## Primary Operator Questions

The generated surfaces should answer:

1. What is the overall portfolio state right now?
2. What work is eligible to dispatch next?
3. What is blocked, and what unblocker is needed?
4. Which repos are overloaded or starving?
5. Which workers or planners currently hold assignments or leases?
6. Which tasks are aging in review, running too long, or drifting from expected progress?
7. What is the canonical handoff card for a specific task?

## Generated Surface Set

Use multiple generated views rather than a single giant board.

### 1. Portfolio Summary View

Purpose:

- fast top-level scan for planners and operators

Recommended contents:

- total task counts by planner status
- total task counts by runtime status
- top priority eligible tasks
- blocked-task count with oldest blocked age
- pending-review count with oldest review age
- per-repo queue pressure summary
- freshness timestamp: `generated_at`

Recommended surfaces:

- terminal summary command
- optional dashboard home page
- optional markdown snapshot export

### 2. Per-Repo Queue View

Purpose:

- answer "what is happening in repo X right now?"

Recommended contents:

- tasks grouped by target repo
- priority, planner status, runtime status
- dependency-blocked indicator
- current planner owner and worker owner
- active lease or runtime link if present
- short summary text rather than full task bodies

Recommended output forms:

- terminal table for interactive use
- optional JSON export for tooling
- optional markdown export for human sharing

### 3. Eligible Dispatch Queue View

Purpose:

- answer "what can a dispatcher or planner send next?"

Recommended contents:

- only dependency-satisfied, dispatchable tasks
- ordered by priority bucket, fairness policy, and repo rotation
- capability or execution-policy filters when available
- explicit reason when a near-top task is excluded

This view should be optimized for operational decisions, not archival browsing.

### 4. Blocked Tasks View

Purpose:

- keep blockers visible and actionable

Recommended contents:

- blocked task ID, title, target repo
- blocker summary
- blocking dependency or external need
- blocked age
- planner owner
- suggested unblocker action or next decision owner

This should be one of the highest-signal operational views.

### 5. Assignments And Leases View

Purpose:

- show who currently owns what

Recommended contents:

- planner assignments
- worker ownership intent
- active leases
- lease expiration and heartbeat age
- stale-lease warning state
- queue depth per worker where relevant

This view should join planner intent with runtime reality without collapsing them into one status field.

### 6. Review And Failure View

Purpose:

- answer "what needs human review or intervention?"

Recommended contents:

- pending review tasks ordered by age
- failed and timed-out runs
- retryability hint
- latest artifact or closeout reference
- escalation notes when present

### 7. Task Detail Card Export

Purpose:

- provide a human-readable handoff surface for one task when needed

Recommended contents:

- task ID, title, target repo, priority
- objective
- context
- scope boundaries
- deliverables
- acceptance
- testing expectations
- execution settings
- dependencies
- latest planner/runtime status summary

Formats:

- terminal or dashboard detail view for normal operation
- structured JSON export for machine handoff
- optional markdown card export for human reading or sharing

This export is a generated artifact from DB state. It replaces the long-term role of canonical markdown task files.

## Fate Of `tasks.md`

`tasks.md` should not remain the hand-maintained canonical board.

Recommended steady-state role if retained:

- keep `tasks.md` as an optional generated portfolio snapshot for humans
- keep it intentionally summary-level
- add a visible header that it is generated and non-canonical
- regenerate it from DB state on a predictable cadence and on-demand

What `tasks.md` should include in steady state:

- current generated timestamp
- top portfolio summary counts
- a short "active now" section
- compact CENTRAL canonical task-system section
- links or pointers to richer generated surfaces

What `tasks.md` should not try to do:

- list every task in full detail
- carry manual closeout notes
- act as the only operator surface for dispatch decisions

If even a thin generated `tasks.md` becomes noisy at scale, retire it and rely on CLI, dashboard, and JSON surfaces instead.

## Recommended Surface Map

Minimum required operator surfaces:

- `central task view summary`
- `central task view eligible`
- `central task view blocked`
- `central task view repo --repo <repo_id>`
- `central task view task-card --task <task_id>`
- JSON variants for each view where automation will consume them
- dashboard equivalents for summary, repo, blocked, review, and assignments views when a web surface exists

Optional export surfaces:

- `tasks.md`: generated landing page if a markdown landing page remains useful
- `generated/portfolio_summary.md`
- `generated/per_repo/<repo_id>.md`
- `generated/blocked_tasks.md`
- `generated/review_queue.md`
- `generated/assignments.md`
- `generated/task_cards/<task_id>.md`

The CLI, JSON, dashboard, and optional markdown exports should come from the same underlying read-model queries.

## Refresh And Update Rules

Use three refresh modes:

### On-demand

- any planner or operator may regenerate a view immediately after a meaningful update
- this is the normal mode for interactive use

### Event-driven

- update affected lightweight views after task create, status change, dependency change, assignment change, lease change, or review decision
- avoid regenerating every heavy view on every event if scale makes that expensive

### Scheduled

- regenerate a baseline snapshot on a fixed cadence for dashboard and cached export consumers
- recommended cadence: every 5 minutes for operational views, hourly for broad portfolio snapshots if event-driven generation is unavailable

Freshness rules:

- every generated view must include `generated_at`
- every generated view should include the source query scope or filter
- operator tooling should show stale warnings when freshness exceeds expected thresholds

## Non-Canonical Marking Rules

Every generated artifact should include a short banner such as:

- `Generated from CENTRAL DB. Do not edit manually.`

For CLI surfaces, print:

- source system
- generation time
- active filters

For markdown exports, include:

- generation timestamp
- command or surface name
- canonical task reference IDs

## Scaling Guidance

To stay useful at hundreds of tasks:

- default to filtered views, not full dumps
- paginate task-card lists and per-repo queues where needed
- sort blocked/review views by aging and severity
- cap landing-page sections to top N results with links to full exports
- keep full task bodies out of summary surfaces

## Operator Workflow Examples

Planner morning scan:

1. open portfolio summary
2. inspect blocked tasks view
3. inspect eligible dispatch queue
4. inspect review/failure view
5. open task-card export only for tasks being assigned or replanned

Dispatcher/operator scan:

1. inspect eligible queue
2. inspect assignments and leases
3. inspect review/failure queue
4. refresh per-repo queue if pressure looks uneven

## Answers To The Task Deliverables

Required generated views:

- portfolio summary
- per-repo queue
- eligible dispatch queue
- blocked tasks
- assignments and leases
- review and failure queue
- per-task detail card export

`tasks.md` role:

- retained as a generated landing page, not as canonical state

Worker handoff artifact:

- generated task-card export in markdown and JSON

Refresh model:

- on-demand plus event-driven updates where feasible
- scheduled fallback snapshots with explicit freshness markers
