# CENTRAL-OPS-169 Audit

## Verdict

Accepted.

## What Passed

- `scripts/session_manager.py` exists and covers the core registry APIs from the LLD.
- Both Claude runtime paths (`scripts/central_runtime.py` and `scripts/central_runtime_v2/backends.py`) consume `session_manager.get_fork_args()` and append `--resume ... --fork-session`.
- Targeted validation passed:
  - `pytest -q tests/test_session_manager.py tests/test_central_runtime_behavior.py scripts/tests/test_dispatcher.py`
  - `cargo build`
- A manual seeded-session proof used a fake `claude` binary and confirmed the real CLI contract:
  - subprocess `cwd` was the target repo root
  - the generated command included `--session-id`
  - the registry row recorded `status='active'`, `context_tokens`, and `seed_cwd`
- Refresh-failure behavior now matches the accepted LLD contract:
  - `refresh_session()` demotes the active row to `stale`, leaves prior stale fallbacks intact during the replacement attempt, and only retires stale rows when the new seed successfully promotes to `active`
  - the regression test `test_refresh_session_preserves_existing_fallbacks_when_seed_fails` passes

## Runtime Evidence

Manual proof against a temporary SQLite DB with a fake `claude` binary:

```text
{
  "session_id": "74c567b4-52c7-4ab0-94f7-fae6d8d48d03",
  "cwd": "/tmp/.../target-repo",
  "argv": [
    "--name",
    "target-repo-base",
    "--session-id",
    "74c567b4-52c7-4ab0-94f7-fae6d8d48d03",
    "--model",
    "claude-opus",
    "--dangerously-skip-permissions",
    "-p"
  ],
  "status": "active",
  "context_tokens": 15,
  "seed_cwd": "/tmp/.../target-repo"
}
```

Manual reproduction against a temporary SQLite DB with `subprocess.run` forced to return exit code `1`:

```text
{
  "error": "seed failed",
  "rows": [
    {"session_id": "active-1", "status": "stale"},
    {"session_id": "stale-1", "status": "stale"}
  ]
}
```

This demonstrates that the seed subprocess is anchored to `repo_root`, the registry tracks the seeded session correctly, and refresh failure preserves the previous forkable fallback state instead of retiring it prematurely.
