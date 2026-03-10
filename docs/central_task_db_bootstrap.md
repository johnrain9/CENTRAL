# CENTRAL Task DB Bootstrap

This document defines where the canonical CENTRAL SQLite DB lives and how to initialize or upgrade it.

## Default Location

Default DB path:

- [`state/central_tasks.db`](/home/cobra/CENTRAL/state/central_tasks.db)

Default migration directory:

- [`db/migrations`](/home/cobra/CENTRAL/db/migrations)

Resolution order for DB path:

1. `--db-path`
2. `CENTRAL_TASK_DB_PATH`
3. default repo path above

## Bootstrap Command

Initialize or upgrade the DB with:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py init
```

This command:

- creates parent directories if needed
- creates the `schema_migrations` table if missing
- applies pending SQL migrations in version order
- is safe to run repeatedly

## Status Command

Inspect DB location, applied migrations, pending migrations, and tables with:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py status
python3 /home/cobra/CENTRAL/scripts/central_task_db.py status --json
```

## Temporary Or Test Databases

Use a temporary DB path for testing:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py init --db-path /tmp/central_tasks_test.db
```

This leaves the default repo DB untouched.

## Migration Contract

- Migrations live as explicit SQL files under [`db/migrations`](/home/cobra/CENTRAL/db/migrations)
- Applied migrations are recorded in `schema_migrations`
- A previously applied migration cannot change contents silently; checksum mismatch is treated as an error
- Schema upgrades happen through new migration files, not by replacing the DB file

## Current Scope

This bootstrap provides:

- canonical schema creation
- migration tracking
- repeatable DB initialization

This bootstrap does not yet provide:

- planner CRUD commands
- generated view commands
- dispatcher runtime mutations

Those arrive in later CENTRAL tasks.
