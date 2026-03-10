# CENTRAL Task Concurrency Model

This document defines the concurrency contract for multi-planner and multi-worker operation in the DB-canonical CENTRAL task system.

## Goals

The model must prevent:

- double-dispatch of the same task
- ambiguous ownership between planners and workers
- lost planner updates
- stale worker claims blocking throughput indefinitely

The model must support:

- multiple planners editing and sequencing tasks
- multiple workers claiming and executing tasks concurrently
- safe recovery from abandoned claims and partial failures
- fair dispatch across repos and priorities

## Write Responsibilities

Planner writes:

- task definition fields
- planner lifecycle state
- dependency edges
- priority and target repo
- assignment intent
- closeout reconciliation after worker completion

Worker writes:

- worker claim or lease state through runtime surfaces
- progress or heartbeat updates
- closeout payloads, test results, blockers, and artifact references

Dispatcher writes:

- worker claim creation
- lease renewal state
- runtime eligibility and claim transitions
- stale-lease recovery markers

Workers do not rewrite planner-owned definition fields directly.

## Concurrency Primitives

Use three mechanisms together:

- optimistic concurrency for planner-edited task records
- leases for worker claims
- append-only events for audit and recovery

Suggested record-level fields:

- `version` integer on planner-owned task rows
- `lease_owner`
- `lease_expires_at`
- `assignment_state`
- `last_heartbeat_at`

## Planner Edit Rule

- Planner updates must include the expected current `version`.
- The write succeeds only if the row version still matches.
- On success, increment `version`.
- On mismatch, reject the write and force the planner to reload current state.

This prevents silent last-writer-wins corruption between concurrent planners.

## Worker Claim And Lease Rule

Claim flow:

1. Dispatcher selects an eligible task.
2. Dispatcher atomically sets worker claim fields only if the task is still unclaimed and eligible.
3. Dispatcher records a lease expiration timestamp.
4. Worker heartbeats extend the lease while active.

Lease rules:

- one active worker lease per task
- leases are time-bounded
- heartbeat renewal must be explicit
- expired leases become reclaimable after stale-lease handling

## Assignment States

Planner-facing states:

- `todo`
- `in_progress`
- `blocked`
- `done`

Runtime-facing states:

- `queued`
- `claimed`
- `running`
- `pending_review`
- `failed`
- `timeout`
- `canceled`

The systems are linked but not identical:

- planner lifecycle answers "what should happen next?"
- runtime state answers "what is the worker doing right now?"

## Conflict Rules

Planner vs planner:

- optimistic concurrency on row version
- if one planner changes dependencies, ownership, or priority first, the second planner must reload before retrying

Planner vs worker:

- planner may update definition fields while a task is not actively leased
- planner should not mutate task definition fields during an active worker lease except for explicit override workflows
- emergency planner override must record an event and either cancel or drain the active lease first

Worker vs worker:

- impossible by contract through single-lease enforcement
- if a double claim is attempted, the second claim must fail atomically

## Stale Lease Recovery

A lease is stale when:

- `lease_expires_at` is in the past
- no heartbeat renewal has arrived within the allowed window

Recovery flow:

1. Mark the old lease stale.
2. Record a recovery event.
3. Return the task to a reclaimable runtime state.
4. Preserve prior worker artifacts and partial evidence for review.

Do not silently drop the old claim without an audit event.

## Retry, Timeout, And Reassignment

Retry:

- allowed after explicit failure or timeout classification
- planner or runtime recovery logic must record why the task is safe to retry

Timeout:

- timeout converts the active lease into a terminal runtime event
- planner decides whether to requeue, rescope, or block

Reassignment:

- planner changes `worker_owner` intent only when no active lease exists, or after explicit stale-lease recovery
- reassignment must leave an event trail

## Dispatch Fairness

Fairness policy should combine:

- task priority
- dependency eligibility
- per-repo queue pressure
- worker capacity and specialization

Minimum fairness rule:

- do not let one noisy repo monopolize all workers if other repos have eligible work of comparable priority

Suggested dispatch order:

1. eligible tasks only
2. sort by priority bucket
3. apply repo fairness rotation within a priority bucket
4. apply worker capability filters
5. claim atomically

## Three Required Race Outcomes

Double claim:

- two dispatchers try to claim the same task
- only one atomic claim succeeds
- the loser reloads eligible work

Planner conflict:

- planner A edits dependencies while planner B edits priority
- second write fails on version mismatch
- second planner reloads and reapplies intentionally

Stale worker lease:

- worker stops heartbeating
- lease expires
- dispatcher records stale lease and returns task to reclaimable state
- planner can inspect partial artifacts before retry or reassignment

## Implementation Guidance

Schema implications:

- task rows need version fields
- active lease data should be queryable without scanning event logs
- events remain the durable audit trail
- runtime links should support one planner task to one active runtime task lease at a time

This contract is intended to guide `CENTRAL-OPS-11` and later DB-native runtime integration work.
