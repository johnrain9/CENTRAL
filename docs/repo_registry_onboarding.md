# Repo Registry Onboarding

Planner task creation and dispatch are registry-first in CENTRAL. If a repo matters operationally, register it canonically before creating or retargeting planner work to it.

## Required Workflow

1. Initialize or restore the CENTRAL DB.
2. Onboard the repo with the canonical registry command.
3. Verify the repo resolves to the expected canonical identity.
4. Create or update planner tasks only after the registry entry exists.
5. Dispatch from the task record that now carries the canonical `target_repo_id` and `target_repo_root`.

## Onboard A Repo

Preferred command:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-onboard \
  --repo-id PHOTO_AUTO_TAGGING \
  --repo-root /home/cobra/photo_auto_tagging \
  --display-name PHOTO_AUTO_TAGGING \
  --alias photo-auto-tagging
```

`repo-upsert` remains available as the lower-level equivalent:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-upsert \
  --repo-id PHOTO_AUTO_TAGGING \
  --repo-root /home/cobra/photo_auto_tagging
```

## Verify Identity

Use the registry itself as the lookup authority:

```bash
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-resolve --repo PHOTO_AUTO_TAGGING --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-resolve --repo photo-auto-tagging --json
python3 /home/cobra/CENTRAL/scripts/central_task_db.py repo-list
```

## Planner Rule

- `task-create` fails if `target_repo_id` and `target_repo_root` do not resolve to a registered repo.
- `task-update` fails if a repo retarget would introduce an unregistered repo, an unregistered alias, or a non-canonical repo root.
- Dispatch uses the canonical repo identity already stored on the task. There is no planner-side silent repo upsert during dispatch preparation.

## When A Command Fails

If planner commands report repo onboarding is required:

1. register the repo with `repo-onboard` or `repo-upsert`
2. confirm the alias/root resolves the way you expect with `repo-resolve`
3. rerun the planner command

Do not work around the error by inventing a new repo ID inside task JSON. The registry is the single source of truth for repo identity.
