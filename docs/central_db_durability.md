# CENTRAL DB Durability And Sync

This document defines the durability model for the canonical CENTRAL SQLite task DB.

## Goals

- keep SQLite as the canonical planner store
- make planner state recoverable after machine loss, checkout loss, or local DB corruption
- make planner updates portable across operators without asking people to copy sqlite files by hand
- preserve auditability with immutable published snapshots and manifest metadata

## Chosen Model

The live writable DB remains local:

- canonical working DB: [`state/central_tasks.db`](/home/cobra/CENTRAL/state/central_tasks.db)

Durability and sync happen through published snapshot artifacts that are safe to commit and push:

- durability root: [`durability/central_db`](/home/cobra/CENTRAL/durability/central_db)
- immutable snapshots: [`durability/central_db/snapshots/`](/home/cobra/CENTRAL/durability/central_db/snapshots)
- latest pointer: [`durability/central_db/latest.json`](/home/cobra/CENTRAL/durability/central_db/latest.json)

Each published snapshot contains:

- `central_tasks.db`: a point-in-time SQLite backup generated from the live DB
- `manifest.json`: snapshot metadata, counts, task/version inventory, and planner/runtime digests

The DB remains canonical. The snapshot bundle is a durability and transport artifact, not a second source of truth.

## Normal Operator Workflow

1. Pull the latest repo changes.
2. Restore the latest published snapshot into the local working DB.
3. Make planner updates through `scripts/central_task_db.py`.
4. Publish a new snapshot after the update set that should be durable/shareable.
5. Commit and push the new snapshot bundle plus any related code or docs.

Commands:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-restore
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-create --note "planner sync after CENTRAL-OPS-26"
```

## Backup And Restore

Publish a durable snapshot:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-create \
  --note "post-planning handoff"
```

Inspect published snapshots:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-list
```

Restore the latest published snapshot into the default DB path:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-restore
```

Restore a specific snapshot into a clean DB path:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py snapshot-restore \
  --snapshot-id 20260310T000000Z-abcdef12 \
  --db-path /tmp/central_tasks_restored.db
```

When restoring over an existing DB, the CLI writes a pre-restore backup by default to a sibling `backups/` directory unless `--no-backup-existing` is passed.

## Recovery Expectations

- If the local `state/central_tasks.db` file is lost, restore from the latest published snapshot.
- If a planner makes a broad incorrect mutation, restore the most recent good snapshot, then re-apply any intended delta through the CLI.
- If an operator starts on a new machine or a clean checkout, restore the latest published snapshot before making planner changes.

## Auditability

Every published snapshot records:

- creation timestamp and actor ID
- immutable snapshot ID
- DB file size and SHA-256
- applied migrations
- repo IDs in scope
- task/version/status inventory
- planner-state and runtime-state digests

That metadata gives operators a reviewable handoff record without reintroducing markdown as canonical state.

## Tradeoffs

- This is a serialized git-backed handoff model, not multi-master replication. Operators should pull and restore before editing, then publish a fresh snapshot when handing off.
- Snapshot DB files are binary artifacts. The manifest provides the human/audit surface; the DB file is the restore surface.
- The live DB is still local for low-latency writes. Durability depends on operators publishing snapshots as part of normal planning handoff.
- If CENTRAL eventually needs higher-frequency multi-writer sync than git-backed snapshots can support, the next step should be a remote artifact or service-backed canonical transport, not a markdown rollback.
