## Verdict

Not ready for implementation. The document defines tables and transaction outlines, but several core low-level contracts are still ambiguous or contradictory at exactly the points that control correctness: token/digest trust, mutation semantics, provenance, versioning, bootstrap legality, and rollout behavior.

## Context Used

Only the target document text provided in the prompt, with its inline line numbers. No repository files, source code, database schema, or other docs were inspected.

## Findings

1. **Canonical digests are undefined**
- **Severity:** critical
- **Why it is a problem:** The design depends on digests and “canonical serialized envelopes” for trust, freshness, idempotency, and event provenance, but it never defines canonical JSON encoding, field ordering, null-vs-absent rules, array ordering, or the hash/signing algorithm for most of these values.
- **Citations:** Lines 222-223, 273-275, 563-565, 587-594, 879-895, 775
- **Likely consequence:** Different writers can produce different tokens/digests for the same logical payload, causing false stale/untrusted rejections, replay failures, or inconsistent acceptance behavior.
- **Needed change:** Define one canonical serialization and hashing/signing contract for `preflight_token`, `mutation_digest`, transition digests, and any request/response digest used in validation.

2. **Capability mutation semantics are underspecified**
- **Severity:** critical
- **Why it is a problem:** `update` requires only `capability_id` plus changed fields, and `deprecate` requires only `capability_id` plus optional notes. The doc does not say whether omitted fields are unchanged or cleared, whether `affected_repo_ids` is partial or replace-all, or what expected current status/version must be asserted before mutation.
- **Citations:** Lines 508-517, 745-780, 1151-1155
- **Likely consequence:** Sequential accepted audits can overwrite each other, join-table state can drift from the parent row, and implementations will invent incompatible patch behavior.
- **Needed change:** Define explicit mutation semantics: full replace vs patch, null handling, atomic join-table replacement rules, and required expected-current-state/version preconditions.

3. **Migration plan omits a required table**
- **Severity:** high
- **Why it is a problem:** `capability_mutation_applications` is part of the schema and is required by Transaction B for idempotency, but the migration contents do not create it.
- **Citations:** Lines 276-301, 773-780, 949-956
- **Likely consequence:** The prescribed audit-acceptance path cannot be implemented as designed after migration, or teams will add ad hoc idempotency outside the LLD.
- **Needed change:** Add `capability_mutation_applications` and its indexes to the migration plan, or revise Transaction B.

4. **Current-state provenance is ambiguous**
- **Severity:** high
- **Why it is a problem:** The row has a single `verified_by_task_id`, but the design also records multiple source tasks for create/update/deprecate/supersede. The doc says trust queries should anchor on `verified_by_task_id`, but it does not define what that field means after later audited updates, deprecations, or trust upgrades.
- **Citations:** Lines 167-172, 404-455, 521-528, 782-790
- **Likely consequence:** Consumers cannot reliably determine which task verified the current row contents, only that some task is attached to the row.
- **Needed change:** Define whether `verified_by_task_id` means latest verifier, creation verifier, or something else, and align that with `capability_source_tasks`.

5. **Task versioning is assumed, not specified**
- **Severity:** high
- **Why it is a problem:** The design uses `task_version` in persisted preflight rows and in audit-mutation idempotency, but it never defines when task versions increment, how updates compare against the current version, or how an audit proves it is accepting a specific immutable parent version.
- **Citations:** Lines 235, 265, 714-729, 764-769
- **Likely consequence:** Preflight rows can attach to the wrong logical version, and capability mutations can apply against stale parent task content.
- **Needed change:** Define task-version lifecycle, increment rules, and compare-and-set requirements for update and audit-acceptance paths.

6. **Bootstrap legality is contradictory**
- **Severity:** high
- **Why it is a problem:** Bootstrap/planner mutation paths are limited to `planner_verified` and `provisional`, audited creation outside Transaction B is disallowed, but seeded capabilities are also said to start as `audited` if backed by accepted audited work.
- **Citations:** Lines 422-425, 995-1013, 1016-1021
- **Likely consequence:** Historical backfill of already-audited capabilities has no single legal path, and different implementations will classify the same seed differently.
- **Needed change:** Choose one canonical backfill path for historically audited capabilities and state the exact allowed `status`/`verification_level` combinations for bootstrap.

7. **The preflight contract is internally inconsistent**
- **Severity:** high
- **Why it is a problem:** The create path may accept either embedded `preflight_result` or `preflight_token`, but only the token has a defined trust model. Separately, persisted `classification` allows `duplicate_do_not_create`, but the canonical response’s `classification_options` omits it. The doc also says this LLD defines the minimum preflight contract, then says the next LLD should define that contract.
- **Citations:** Lines 241-246, 530-532, 571-582, 809-825, 1166-1171
- **Likely consequence:** Writers cannot implement one consistent admission contract; some will trust raw embedded results, others will require tokens, and duplicate outcomes will not round-trip cleanly.
- **Needed change:** Make the preflight producer/consumer contract singular and complete in this LLD: token-only or signed embedded envelope, explicit classification model, and no deferred definition of already-required fields.

8. **Rollout phases conflict with the core invariant**
- **Severity:** high
- **Why it is a problem:** Transaction A states no task exists without persisted preflight metadata, but rollout says enforcement arrives later after an advisory phase. The doc does not define what happens to tasks created during that interim.
- **Citations:** Lines 688-713, 960-969
- **Likely consequence:** The system can permanently accumulate post-migration tasks without canonical preflight rows, undermining later assumptions and search/audit behavior.
- **Needed change:** Define phase-specific invariants and whether interim tasks must still persist advisory preflight rows, or explicitly mark them as legacy/non-canonical.

9. **Search inclusion rules for low-trust capabilities are missing**
- **Severity:** high
- **Why it is a problem:** Preflight admission depends on capability matches, but the doc never defines which capability states participate in search: `proposed`, `planner_verified`, bootstrap-created, `archived_at` rows, or “should influence future search immediately” cases.
- **Citations:** Lines 551-556, 1023-1066, 1128-1155
- **Likely consequence:** Low-trust seeded rows can either over-block legitimate work or be ignored when they should prevent duplicates.
- **Needed change:** Define deterministic search inclusion/exclusion rules by `status`, `verification_level`, bootstrap mode, and `archived_at`, preferably as canonical query-time behavior rather than metadata folklore.

10. **`task_creation_preflight` does not identify one canonical row per task version**
- **Severity:** medium
- **Why it is a problem:** The table is “append-only by task version,” but there is no uniqueness on `(task_id, task_version)`. That allows multiple competing preflight rows for the same admitted task version.
- **Citations:** Lines 233-266, 272, 727
- **Likely consequence:** Audit trails become ambiguous and downstream code has to guess which row justified the version.
- **Needed change:** Add a uniqueness constraint or explicitly define multiplicity and canonical selection rules.

11. **Idempotency only covers exact replay, not conflicting re-acceptance**
- **Severity:** high
- **Why it is a problem:** A second acceptance of the same audit task with a different mutation digest or parent version produces a different `application_key`. The doc never defines a one-shot acceptance guard that rejects that case.
- **Citations:** Lines 749-758, 764-780
- **Likely consequence:** One audit task can mutate the capability registry multiple times under divergent payloads if external state checks are weak or inconsistent.
- **Needed change:** Define audit acceptance as a single compare-and-set transition and reject any subsequent acceptance attempt for the same audit task regardless of digest.

## Top Risks

- The trust boundary is not actually specified tightly enough to be safe. Tokens, digests, and embedded preflight results are central to the design, but the canonical verification contract is incomplete.
- Capability state can drift silently. Mutation semantics, task versioning, and provenance are all too ambiguous for safe concurrent or sequential updates.
- Rollout/bootstrap behavior can fragment the registry early. The doc does not clearly define which seeded or advisory-era records are authoritative or searchable.

## Open Questions

- What exact canonical serialization and hashing/signing scheme defines `preflight_token`, `mutation_digest`, and transition digests?
- What does `verified_by_task_id` mean after a later audited update, deprecation, or trust upgrade?
- What is the required compare-and-set/version contract for task updates and audit acceptance?
- Which capability states are searchable for preflight blocking, especially during bootstrap and backfill?
- During advisory rollout, are post-migration tasks allowed to exist without `task_creation_preflight`, or must they still persist a canonical-but-non-blocking row?