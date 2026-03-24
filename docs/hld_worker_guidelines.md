# HLD Worker Guidelines

Guidelines for workers producing or revising High-Level Design documents. Referenced from design task context.

---

## 1. Before Writing: Pre-Work

### 1.1 Read Upstream Docs First

If **product requirements**, **product design**, or **backend design** docs exist for the target area, read them before writing. An HLD that doesn't reference its upstream constraints will get flagged in review.

### 1.2 Scope Check

A single HLD should cover one **system** or **initiative** (e.g., "Frontend System", "Temporal Observability", "Pod Management Backend"). If it spans multiple unrelated systems, propose a split. Deep and focused beats broad and thin.

### 1.3 Forced Design Decisions

Every HLD must explicitly decide — not leave ambiguous — at least these questions where applicable:

- **Ownership boundaries**: For each subsystem, what does it own? What does it delegate? Which direction do dependencies flow?
- **State architecture**: Where does each category of state live? What is the source of truth for each?
- **Data flow**: How does data get from backend to screen (or vice versa)? What are the transport mechanisms?
- **Migration strategy**: How do we get from the current system to the target? What are the independently shippable steps?
- **Performance constraints**: What are the budgets (latency, frame rate, payload size, etc.) and what enforces them?

If the task context includes a decision checklist, answer every item. Do not defer decisions to open questions unless genuinely blocked on external input.

---

## 2. During Writing: Structure

### 2.1 LLD References Throughout

When describing a subsystem that needs its own LLD, mark it inline with:

```markdown
**LLD needed**: *LLD: [Name]* — [what the LLD must decide].
```

This makes LLD scope traceable from the HLD body. Every inline `**LLD needed**` marker must have a corresponding row in the required LLD table (§5).

### 2.2 Architectural Decisions

Use a numbered format (AD-1, AD-2, etc.) with:
- **Decision**: What was decided.
- **Current state**: What exists today.
- **Target state**: What we're building toward.
- **Why**: The reasoning. Trade-offs considered.

### 2.3 Cross-Reference Discipline

Same rules as LLD guidelines: name dependencies explicitly, don't restate contracts from other documents, give shared concepts their own subsection if referenced 3+ times.

---

## 3. During Revision: Trace Before Fixing

Same rules as the LLD Worker Guidelines (§3). When fixing a review finding:

1. Before editing: list every section that depends on the concept being changed.
2. Group related findings: design one coherent fix, not sequential patches.
3. Push back on suggestions that create new contradictions.
4. After editing: re-read every dependent section.

---

## 4. Review Tool Usage

Same protocol as LLD guidelines. Run the review tool with the appropriate bundle preset (e.g., `frontend-hld`):

```bash
python3 scripts/review_doc.py --input <doc-path> --bundle-preset <preset>
```

Iteration budget: review → fix → re-review until no critical findings and ≤3 major findings, or 3 iterations.

---

## 5. Required LLD Table

The **last section before the changelog** of every HLD must be a structured table listing all LLDs needed to realize the design. This table is the contract between high-level and low-level design — if an LLD isn't in this table, it won't get written.

### 5.1 Format

```markdown
## Required LLDs

| # | LLD Title | Covers | Priority | Depends On | Repo | Notes |
|---|-----------|--------|----------|------------|------|-------|
| 1 | LLD: Arrangement Engine | Panel tree, layout engine, persistence, keyboard nav | P0 | — | ecosystem | Shared shell contract with #2 |
| 2 | LLD: Streaming Architecture | SubscriptionController, buffers, reconnection | P0 | — | ecosystem | Shared shell contract with #1 |
| 3 | LLD: Router Migration | URL encoding, route-to-panel mapping, redirects | P1 | 1 | ecosystem | |
| 4 | LLD: Session Monitor | Tree model, virtualization, filtering, activity feed | P1 | 1, 2 | ecosystem | |
```

**Required columns:**
- **LLD Title**: The exact name of the LLD to produce. Must match inline `**LLD needed**` markers in the HLD body.
- **Covers**: Brief scope summary — what the LLD must decide.
- **Priority**: P0 (blocking), P1 (core), P2 (feature), P3 (extension).
- **Depends On**: Row numbers for LLDs that must be completed first. Use `—` for none.
- **Repo**: Target repository for the LLD design task.

### 5.2 Completeness Rule

Every inline `**LLD needed**` marker in the HLD body must have a corresponding row in this table. Reviewers will check this. If a section describes a subsystem complex enough to need its own design doc, it needs a row.

### 5.3 LLD Task Creation

After the review is clean and the HLD is finalized, **create design tasks for all required LLDs** using `task_quick.py` before marking the HLD task as done:

```bash
python3 scripts/task_quick.py \
  --title "LLD: Arrangement Engine" \
  --repo ecosystem \
  --template design \
  --series ECO \
  --initiative <initiative-from-hld-task> \
  --depends-on <TASK-ID-of-dependency-LLD> \
  --context "Scope from HLD §9.1: Panel tree model, layout engine, serialization, ..."
```

Record the created task IDs back into the table (add a "Task ID" column) so the mapping is traceable.

---

## 6. Deliverables Checklist

Before marking the task as done:

- [ ] All forced design decisions (§1.3) are explicitly answered
- [ ] Cross-references are consistent (no contradictory statements across sections)
- [ ] Every inline `**LLD needed**` marker has a row in the Required LLD table (§5)
- [ ] Required LLD table covers all subsystems that need detailed design
- [ ] Review tool has been run; critical findings are resolved
- [ ] Open questions are listed with owners and resolution venues
- [ ] All LLD design tasks have been created in CENTRAL via task_quick.py (§5.3)
- [ ] Changelog entry describes what changed and why
- [ ] Version number is incremented
