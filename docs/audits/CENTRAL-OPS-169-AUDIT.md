# CENTRAL-OPS-169 Audit

## Verdict

Rework required.

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

## Finding

`refresh_session()` does not preserve the previous active session until a replacement seed succeeds, which is weaker than the accepted LLD contract.

Expected behavior from the LLD:

- [docs/session_persistent_workers_lld.md](/home/cobra/projects/CENTRAL/docs/session_persistent_workers_lld.md#L189): mark the current active session stale, run a new seed, and keep the stale session forkable until the replacement reaches `active`.
- [docs/session_persistent_workers_lld.md](/home/cobra/projects/CENTRAL/docs/session_persistent_workers_lld.md#L156): retire prior stale rows only when the new seed promotes to `active`.

Observed implementation:

- [scripts/session_manager.py](/home/cobra/projects/CENTRAL/scripts/session_manager.py#L333) retires all existing stale rows and demotes the active row before calling `seed_session()`.
- If `seed_session()` fails, the old active row stays `stale` and the previously stale rows are already gone.

## Runtime Evidence

Manual reproduction against a temporary SQLite DB with `subprocess.run` forced to return exit code `1`:

```text
RuntimeError seed failed
[{'session_id': 'old-active', 'status': 'stale'}]
```

This demonstrates that refresh mutates registry state before replacement seeding succeeds.

## Recommended Fix

Keep the old active session available until the replacement seed succeeds, then retire superseded stale rows and promote the new session in the success transaction. Add a regression test that forces seed failure during refresh and asserts that the previously forkable fallback state is preserved.
