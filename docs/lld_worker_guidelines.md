# LLD Worker Guidelines

Guidelines for workers producing or revising Low-Level Design documents. Referenced from design task context.

---

## 1. Before Writing: Pre-Work

### 1.1 Read Shared Contracts First

If a **shared contracts document** exists for the design area (e.g., `shared-frontend-contracts.md`), read it before writing anything. Shared contracts define types, state models, and ownership boundaries that multiple LLDs depend on. Your LLD must consume these contracts, not reinvent them.

If no shared contracts doc exists and your LLD introduces types or state models that other LLDs will need to reference (discriminated unions, connectivity models, subscription ownership), flag this in your deliverables: "Proposed shared contract: [type/model]. Needs cross-LLD alignment before other LLDs can finalize."

### 1.2 Scope Check

A single LLD should cover **at most 5 major subsystems**. If the scope exceeds this, propose a split in the task closeout rather than writing a shallow LLD that spans too much. A deep, narrow LLD is more useful than a broad, thin one.

### 1.3 Forced Design Decisions

Every LLD must explicitly decide — not leave ambiguous — at least these questions where applicable:

- **Identity**: Are IDs persisted or runtime-only? Are they stable across reload? What operations generate vs preserve them?
- **State ownership**: For each piece of state, who owns it? Where is it stored? When is it cleared?
- **Lifecycle**: What happens on create, load, save, discard, delete, and error for the primary entities?
- **Boundaries**: What does this subsystem own vs delegate to adjacent subsystems? State the boundary explicitly, including the direction of the dependency.
- **Degraded modes**: What happens when the network is down, storage is unavailable, or a dependency returns an error?

If the task context includes a decision checklist, answer every item. Do not defer decisions to open questions unless genuinely blocked on external input.

---

## 2. During Writing: Structure

### 2.1 Cross-Reference Discipline

When introducing a concept in one section that is consumed by another section:
- **Name the dependency** explicitly (e.g., "§2.2 uses the ID model from §0.1").
- **Do not restate the full contract** — reference it. Restating creates two sources of truth that diverge.
- **When a concept appears in 3+ sections**, consider giving it its own subsection and having others reference it.

### 2.2 Code Examples

Code examples in an LLD are **contracts, not illustrations**. Implementers will treat them as the authoritative interface. Ensure:
- Type signatures are complete (no implicit `any` or missing fields).
- Return types are explicit.
- Comments describe *intent*, not mechanics.
- Operations that change multiple fields (e.g., swap changes both `content` and `paramVersion`) are shown as atomic.

---

## 3. During Revision: Trace Before Fixing

**This is the most important guideline for revision work.**

When fixing a review finding or making any change to an LLD:

1. **Before editing**: List every section that references or depends on the concept being changed. Read all of them.
2. **Group related findings**: If multiple findings stem from the same root cause, design one coherent fix that addresses all of them together. Do not fix them sequentially — sequential fixes to interconnected concepts create cross-fix contradictions.
3. **Push back on suggestions**: Review findings correctly identify gaps, but the *suggested fix* is not always the right remedy. Evaluate whether the suggestion creates new contradictions before applying it. The simpler fix that preserves existing invariants is usually better than the clever fix that requires cascading changes.
4. **After editing**: Re-read every dependent section to verify the change does not introduce contradictions. This step is not optional.

### 3.1 Common Revision Traps

- **Changing identity semantics** (e.g., "IDs are now runtime-only") without updating reconciliation, cache, and startup logic that depends on ID stability.
- **Broadening a state model** (e.g., "scroll is now content-scoped") without updating all operations that reference the old model (swap comments, trade-off rationale, etc.).
- **Adding an overlay/guard** without checking whether it blocks actions that should remain available (e.g., `inert` blocking local-only actions during offline).
- **Disabling an action in one section** (e.g., "discard is disabled offline") without updating the action gating table that says otherwise.

---

## 4. Review Tool Usage

### 4.1 Initial Review

After completing the first draft, run the review tool with the appropriate bundle preset:

```bash
python3 scripts/review_doc.py --input <doc-path> --bundle-preset <preset>
```

Address all critical and major findings before considering the LLD complete.

### 4.2 Re-Review Iteration

After addressing findings, run a re-review:

```bash
python3 scripts/review_doc.py --input <doc-path> --bundle-preset <preset> \
  --rereview-from <doc-path>.reviews.<preset>
```

**Iteration budget**: Continue review → fix → re-review cycles until either:
- No critical findings remain AND ≤3 major findings remain, OR
- 3 iterations have been completed (diminishing returns beyond this — remaining findings are better addressed during implementation).

### 4.3 Skipping Clean Lanes

If a re-review shows a reviewer lane with 0 remaining findings, skip it on subsequent re-reviews using `--reviewer <lane-name>` to run only the lanes that still have issues.

---

## 5. Follow-On Task Table

The **last section** of every LLD must be a structured follow-on table listing all implementation tasks needed to realize the design. This table is the contract between design and implementation — if work isn't in this table, it won't get built.

### 5.1 Format

```markdown
## Follow-On Tasks

| # | Title | Depends On | Repo | Template | Notes |
|---|-------|------------|------|----------|-------|
| 1 | Wire FooPanel registration in panel-registrations.ts | — | ecosystem | feature | |
| 2 | Implement FooPanel data fetching and rendering | 1 | ecosystem | feature | |
| 3 | Add FooPanel Storybook story | 2 | ecosystem | feature | |
| 4 | Vertical integration: Foo panel end-to-end | 1, 2, 3 | ecosystem | validation | Confirms user can see and use the feature |
```

**Required columns:**
- **Title**: Specific enough that a worker can act on it without re-reading the LLD. Bad: "Implement panel." Good: "Wire FooPanel registration in panel-registrations.ts with lazy loader."
- **Depends On**: Row numbers within the table (not task IDs — those don't exist yet). Use `—` for no dependencies.
- **Repo**: Target repository for the task.
- **Template**: One of `feature`, `bugfix`, `refactor`, `infrastructure`, `validation`, `cleanup`.

**Required final row:** Every table must end with a **vertical integration** task (template: `validation`) that confirms the feature works end-to-end from the user's perspective.

### 5.2 Task Creation

After the review is clean and the LLD is finalized, **create all follow-on tasks** using `task_quick.py` before marking the design task as done. Map table rows to task_quick flags:

```bash
python3 scripts/task_quick.py \
  --title "Wire FooPanel registration in panel-registrations.ts" \
  --repo ecosystem \
  --template feature \
  --series ECO \
  --initiative <initiative-from-design-task> \
  --depends-on <TASK-ID-of-row-dependency>
```

Record the created task IDs back into the LLD table (add a "Task ID" column) so the mapping is traceable.

### 5.3 Completeness Rule

Every piece of implementation work implied by the LLD body must appear in the follow-on table. If a section describes a component, a store, an API call, or a wiring step, there must be a corresponding row. Reviewers will check this.

---

## 6. Deliverables Checklist

Before marking the task as done:

- [ ] All forced design decisions (§1.3) are explicitly answered
- [ ] Cross-references are consistent (no contradictory statements across sections)
- [ ] Review tool has been run; critical findings are resolved
- [ ] Open questions are listed with owners and resolution venues
- [ ] Follow-on task table is complete (§5) with vertical integration row
- [ ] All follow-on tasks have been created in CENTRAL via task_quick.py (§5.2)
- [ ] Changelog entry describes what changed and why
- [ ] Version number is incremented
