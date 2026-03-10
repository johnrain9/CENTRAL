# CENTRAL Canonical Task System

This document defines the target canonical CENTRAL task model for scalable planning and dispatch.

## Direction

The canonical source of truth is a CENTRAL-managed SQLite database.

Target model:

- `CENTRAL` owns canonical planner task truth
- canonical task truth lives in SQLite, not flat files
- planner and operator tooling read and write structured task records
- markdown surfaces are generated summaries, exports, archives, or migration aids only

## Canonical References

- DB schema: [`central_task_db_schema.md`](/home/cobra/CENTRAL/docs/central_task_db_schema.md)
- runtime integration model: [`central_autonomy_integration.md`](/home/cobra/CENTRAL/docs/central_autonomy_integration.md)
- DB bootstrap and location contract: [`central_task_db_bootstrap.md`](/home/cobra/CENTRAL/docs/central_task_db_bootstrap.md)

## Non-Canonical Surfaces

These surfaces are not authoritative:

- `CENTRAL/tasks.md`
- `CENTRAL/tasks/*.md`
- repo-local boards
- exported task cards or reports

They may remain temporarily for migration or human-readable output, but planner truth does not live there.

## Required Capabilities

The canonical task system must support:

- indexed queries over large task sets
- dependency traversal without file parsing
- concurrent planner and worker activity
- durable assignment and event history
- generated summaries and exports
- clean integration with dispatcher/runtime state

## Source Of Truth Rule

If any markdown summary, export, or mirror disagrees with the DB, the DB wins.

## Migration Rule

Move from bootstrap markdown into DB-authored canonical tasks as quickly as practical.

Do not deepen the markdown-first model beyond transitional compatibility work.
