## Verdict

The HLD is directionally coherent on UX intent, but it is not yet a safe design contract. It leaves several core architectural decisions unresolved: where the read model lives, who owns derived state, how canonical DB state and dispatcher runtime state are reconciled, what consistency guarantees the UI provides, and how degraded/partial data should affect operator decisions. In its current form, multiple teams could implement materially different systems that all appear compliant, with a high risk of false operational confidence and wasted rework.

## Context Used

Reviewed only the target document provided inline: [planner_status_ui_hld.md](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md). No repository files, source code, or other docs were inspected.

## Findings

### 1. No authoritative contract for joining canonical task state with live dispatcher state
**Severity:** critical

**Why it is a problem:**  
The document says CENTRAL DB is canonical for task state and dispatcher runtime data must also be shown, and that any derived status must be explainable from both sources. But it never defines the authority boundary when those sources disagree, nor the join key, reconciliation policy, or freshness expectations between them. This is the core architectural seam in the system.

**Relevant lines:**  
[planner_status_ui_hld.md:52](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L52), [planner_status_ui_hld.md:53](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L53), [planner_status_ui_hld.md:54](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L54), [planner_status_ui_hld.md:57](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L57), [planner_status_ui_hld.md:221](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L221), [planner_status_ui_hld.md:222](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L222), [planner_status_ui_hld.md:355](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L355), [planner_status_ui_hld.md:371](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L371)

**Likely consequence if unresolved:**  
Different views can show contradictory truth such as “eligible” and “running” for the same task, or a task can disappear from actionable sections due to reconciliation bugs. Operators will not know whether to trust planner state or runtime state, which undermines the purpose of the control surface.

**Recommended change:**  
Add an explicit cross-source consistency contract: identity keys, precedence rules for conflicting fields, acceptable lag/skew, and the exact invariant for when a task may appear in multiple sections versus when that is a bug.

---

### 2. Read-model ownership and execution boundary are undefined
**Severity:** high

**Why it is a problem:**  
The document rejects a bespoke backend service in v1 and also rejects ad hoc shell-call assembly, while requiring “stable UI-oriented read payloads.” That leaves the main architectural question unanswered: where the read-model composition actually runs and who owns it. Without that, the design boundary is not actionable.

**Relevant lines:**  
[planner_status_ui_hld.md:46](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L46), [planner_status_ui_hld.md:52](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L52), [planner_status_ui_hld.md:355](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L355), [planner_status_ui_hld.md:357](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L357), [planner_status_ui_hld.md:369](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L369)

**Likely consequence if unresolved:**  
Teams may independently build incompatible approaches: client-side composition, server-side composition inside an existing app, wrapper CLI endpoints, or direct DB-plus-runtime queries. That creates rework, inconsistent permissions models, and unstable contracts.

**Recommended change:**  
State the hosting model for v1 read-model composition explicitly: which existing process exposes the payloads, which team owns it, and which interfaces are stable for the UI versus internal-only.

---

### 3. The design does not define section membership invariants
**Severity:** high

**Why it is a problem:**  
The UI is organized around “active workers,” “actionable now,” “needs attention,” “awaiting audit,” “by repo,” and “recent changes,” but the document never specifies whether these are mutually exclusive partitions, overlapping projections, or priority-ordered views. The only guidance is UX-oriented.

**Relevant lines:**  
[planner_status_ui_hld.md:77](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L77), [planner_status_ui_hld.md:84](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L84), [planner_status_ui_hld.md:88](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L88), [planner_status_ui_hld.md:164](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L164), [planner_status_ui_hld.md:173](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L173), [planner_status_ui_hld.md:185](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L185), [planner_status_ui_hld.md:204](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L204)

**Likely consequence if unresolved:**  
Users will see duplicate tasks across sections without understanding whether that is intentional, while implementers will invent inconsistent inclusion rules. Counts in the summary bar may not reconcile with section counts, causing distrust and wasted debugging.

**Recommended change:**  
Define section semantics formally: whether each section is a slice, a priority queue, or an overlapping lens; whether counts must reconcile; and how a task with multiple applicable conditions is surfaced and labeled.

---

### 4. Operational failure handling is UI-local, not system-level
**Severity:** high

**Why it is a problem:**  
The document says to preserve the last successful snapshot and show stale warnings, but it does not define when stale data becomes unsafe for operational use, whether certain actions or interpretations should be blocked, or how partial truth affects derived counts and health indicators. This is especially risky for a console intended for live dispatch monitoring.

**Relevant lines:**  
[planner_status_ui_hld.md:334](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L334), [planner_status_ui_hld.md:336](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L336), [planner_status_ui_hld.md:347](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L347), [planner_status_ui_hld.md:352](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L352), [planner_status_ui_hld.md:353](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L353)

**Likely consequence if unresolved:**  
The UI can present stale or partial data with too much apparent precision, leading operators to make bad planning decisions, miss genuine incidents, or investigate ghosts caused by failed refreshes.

**Recommended change:**  
Define operational behavior for degraded modes: freshness thresholds, which sections become non-authoritative when one source is missing, whether counts are hidden versus shown as stale, and what explicit “unsafe to trust” states must exist.

---

### 5. “Eligible” is treated as a key control-plane concept but never defined
**Severity:** high

**Why it is a problem:**  
The summary bar, actionable sections, success criteria, and rollout all depend on knowing what is “eligible now,” yet the HLD never defines the eligibility contract at the design level or which source owns it. Since this is planner-facing, ambiguity here breaks the primary workflow.

**Relevant lines:**  
[planner_status_ui_hld.md:32](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L32), [planner_status_ui_hld.md:84](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L84), [planner_status_ui_hld.md:122](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L122), [planner_status_ui_hld.md:168](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L168), [planner_status_ui_hld.md:169](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L169), [planner_status_ui_hld.md:377](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L377), [planner_status_ui_hld.md:424](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L424)

**Likely consequence if unresolved:**  
Different payloads or screens will compute eligibility differently. The UI may highlight work that cannot actually run, or hide work that should be dispatched, directly harming planner effectiveness.

**Recommended change:**  
Add a high-level eligibility definition: owning subsystem, required inputs, and the invariant that the UI must surface owned eligibility decisions rather than recompute them independently.

---

### 6. Several headline metrics are undefined and therefore not safely comparable
**Severity:** high

**Why it is a problem:**  
The top summary bar requires counts for stale worker/task, recent changes, blocked, failed audit, awaiting audit, idle slots, and capacity/backoff state. The HLD never defines their time windows, thresholds, or authority. Those metrics are central to prioritization, so undefined semantics are a design flaw, not an implementation detail.

**Relevant lines:**  
[planner_status_ui_hld.md:114](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L114), [planner_status_ui_hld.md:126](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L126), [planner_status_ui_hld.md:127](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L127), [planner_status_ui_hld.md:135](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L135), [planner_status_ui_hld.md:206](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L206), [planner_status_ui_hld.md:447](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L447)

**Likely consequence if unresolved:**  
Trend comparisons and section prioritization become arbitrary. Two planners can interpret the same console differently, and future revisions will break historical expectations because the semantics were never fixed.

**Recommended change:**  
Define each required metric at the contract level: source of truth, threshold or lookback basis, and whether it is configurable or fixed in v1.

---

### 7. The document names ownership but not operational responsibility
**Severity:** medium

**Why it is a problem:**  
It says CENTRAL owns the UI and read-model composition, while dispatcher owns execution and runtime emission, but it does not assign who is responsible for correctness of cross-system views, incident response for bad data, or approval to change shared read contracts.

**Relevant lines:**  
[planner_status_ui_hld.md:50](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L50), [planner_status_ui_hld.md:52](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L52), [planner_status_ui_hld.md:54](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L54), [planner_status_ui_hld.md:359](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L359)

**Likely consequence if unresolved:**  
When the UI shows incorrect worker or queue state, teams will argue about whether the bug is in DB truth, runtime truth, or composition. Fixes will stall at ownership seams.

**Recommended change:**  
Add explicit operational ownership for each contract: task truth, runtime truth, composed UI truth, and who signs off on changes that affect planner-facing semantics.

---

### 8. Rollout strategy lacks migration and adoption safeguards
**Severity:** medium

**Why it is a problem:**  
The rollout says to validate with real planner use, but it does not define how the UI coexists with canonical CLI workflows during adoption, what discrepancies are expected, or how planners should arbitrate when UI and existing views disagree. Since the UI is read-only first, rollout correctness depends on trust calibration.

**Relevant lines:**  
[planner_status_ui_hld.md:47](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L47), [planner_status_ui_hld.md:385](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L385), [planner_status_ui_hld.md:389](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L389), [planner_status_ui_hld.md:394](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L394)

**Likely consequence if unresolved:**  
Planners may either ignore the UI because it is not yet trusted, or trust it prematurely and make decisions off mismatched read models. Either outcome reduces value and increases support burden.

**Recommended change:**  
Specify rollout guardrails: parallel-run expectations, discrepancy handling, who decides the UI is trustworthy enough for operational use, and what fallback remains canonical during the adoption period.

---

### 9. Scalability intent is stated, but no design constraints back it up
**Severity:** medium

**Why it is a problem:**  
The problem statement is explicitly about growth in task volume, but the HLD does not set any high-level limits or behavior expectations for large task counts, repo counts, or concurrent workers. “Dense scanning” and “sortable/filterable task list” are not a scalability design.

**Relevant lines:**  
[planner_status_ui_hld.md:20](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L20), [planner_status_ui_hld.md:24](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L24), [planner_status_ui_hld.md:37](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L37), [planner_status_ui_hld.md:38](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L38), [planner_status_ui_hld.md:408](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L408)

**Likely consequence if unresolved:**  
A v1 that works on small datasets can fail in the exact growth conditions that motivated it, forcing redesign of the section model, pagination strategy, or aggregation approach after adoption starts.

**Recommended change:**  
Add high-level scale assumptions and corresponding design constraints: expected order of magnitude, what must remain summary-only, and where the UI is allowed to defer detail loading versus requiring globally complete snapshots.

---

### 10. Observability requirements for the UI/read-model path are missing
**Severity:** medium

**Why it is a problem:**  
The HLD emphasizes operational visibility for planner and dispatcher behavior, but says nothing about how the UI and read-model layer themselves are monitored. If the read path becomes slow, skewed, or partially broken, the system loses trust without clear diagnosis.

**Relevant lines:**  
[planner_status_ui_hld.md:24](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L24), [planner_status_ui_hld.md:40](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L40), [planner_status_ui_hld.md:316](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L316), [planner_status_ui_hld.md:334](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L334), [planner_status_ui_hld.md:438](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L438)

**Likely consequence if unresolved:**  
Refresh problems or bad composition logic will surface only as user confusion. There will be no clear way to distinguish source instability from UI contract bugs.

**Recommended change:**  
Define minimum operability requirements for the read path: freshness monitoring, error-rate visibility, source-level partial failure attribution, and an explicit owner for those signals.

---

### 11. Security and access boundary are omitted for an operations console
**Severity:** medium

**Why it is a problem:**  
The UI surfaces task details, worker details, recent events, artifacts, closeout/failure summaries, model settings, and log-derived signals. The HLD says who uses it, but not who is allowed to use it or whether all users see the same operational data. For an audit and operations console, this is a design-level omission.

**Relevant lines:**  
[planner_status_ui_hld.md:12](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L12), [planner_status_ui_hld.md:59](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L59), [planner_status_ui_hld.md:93](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L93), [planner_status_ui_hld.md:234](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L234)

**Likely consequence if unresolved:**  
The easiest implementation will likely overexpose operational or audit data. Retrofitting role boundaries later will be expensive because section contracts and detail drawers will already assume unrestricted access.

**Recommended change:**  
State the access model for v1: whether this console is restricted to trusted operators only, whether all primary users share the same visibility, and which data classes are intentionally in scope.

---

### 12. Success criteria measure speed of answering questions, not correctness or trustworthiness
**Severity:** low

**Why it is a problem:**  
The success criteria focus on whether a planner can answer questions quickly, but not whether the answers are accurate, reconciled, and safe under degraded conditions. For an operations console, speed without correctness is a poor optimization target.

**Relevant lines:**  
[planner_status_ui_hld.md:419](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L419), [planner_status_ui_hld.md:421](/Users/paul/projects/CENTRAL/docs/planner_status_ui_hld.md#L421)

**Likely consequence if unresolved:**  
A visually effective but semantically unreliable UI could be declared successful. That sets the wrong acceptance bar for future rollout and control-plane expansion.

**Recommended change:**  
Add success criteria around correctness and operational trust: count reconciliation, source attribution, degraded-state clarity, and acceptable freshness bounds.

## Top Risks

- The largest risk is false confidence from unresolved reconciliation between CENTRAL DB state and dispatcher runtime state. The document explicitly wants a single control-plane view, but does not define how conflicting truths are merged.
- The second risk is architectural churn from the missing execution boundary for the read-model layer. “No bespoke backend” and “no ad hoc shell assembly” is not enough to align implementation.
- The third risk is planner confusion from undefined section semantics and metrics. The UI may look polished while remaining logically inconsistent.
- The fourth risk is unsafe degraded-mode behavior. Preserving last-known-good data without defining when it stops being trustworthy can actively mislead operators during incidents.
- The fifth risk is adoption failure. Without an explicit coexistence and discrepancy policy versus existing CLI workflows, the UI will either not gain trust or will gain it too early.

## Open Questions

- What subsystem is authoritative for each planner-facing concept: eligibility, blocked, awaiting audit, stale, recent change, and worker health?
- What is the exact reconciliation rule when task DB state and dispatcher runtime state disagree?
- Are the main sections intended to be partitions of the task space, overlapping lenses, or priority-ranked views?
- Where does the v1 read-model composition run, and who owns the stability of those payloads?
- What freshness/skew bounds are acceptable before the UI must stop presenting certain data as operationally trustworthy?
- During rollout, if the UI and existing CLI views disagree, which one is canonical for planner decisions and how is that discrepancy surfaced?
- Is the console assumed to be available only to trusted operators, or does it require explicit role-based visibility constraints in v1?
- What scale envelope is v1 expected to handle for tasks, repos, and workers without collapsing back into the flat-list problem the document is trying to solve?