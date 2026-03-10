# CENTRAL Multi-Repo Task Board

Last sync: 2026-03-09T00:00:00-07:00

## Tracked Repos
- photo_auto_tagging: /home/cobra/photo_auto_tagging
- aimSoloAnalysis: /home/cobra/aimSoloAnalysis
- video_queue: /home/cobra/video_queue
- video_wall: /home/cobra/video_wall
- tts: /home/cobra/tts

## Repo Snapshots
- photo_auto_tagging | branch=main | last_commit=8b1a6ab 2026-03-07 Stabilize V2 review and gallery flows
- aimSoloAnalysis | branch=feature/task-p0-12-evidence-plumbing | last_commit=54804fb 2026-03-08 chore: commit all current workspace changes | dirty=true (`TASKS.md` modified, `docs/wsl2_native_js_ui_design.md` untracked)
- video_queue | branch=main | last_commit=2dfe1c9 2026-02-28 feat(ui-v2): deliver /v2 svelte app, contracts, and validation
- video_wall | branch=main | last_commit=8ffd834 2026-02-17 Implement video wall transport, animated previews, modal player, and source filtering
- tts | non-git workspace | local TTS setup (Kokoro + XTTS) | key scripts: `run_kokoro.py`, `run_xtts.py`

## Source Task Files
- /home/cobra/photo_auto_tagging/tasks.md
- /home/cobra/photo_auto_tagging/docs/ui_redesign_tasks.md
- /home/cobra/aimSoloAnalysis/TASKS.md
- /home/cobra/video_queue/tasks.md
- /home/cobra/video_wall/tasks.md
- /home/cobra/tts (no `tasks.md` found yet)

## Portfolio Status Summary
- photo_auto_tagging/tasks.md: total=262 done=238 todo=24
- aimSoloAnalysis/TASKS.md: done=57 in_progress=1 todo=21
- video_queue/tasks.md: done=26 in_progress=3 todo=21
- video_wall/tasks.md: done=1 in_progress=0 todo=0
- photo_auto_tagging/docs/ui_redesign_tasks.md: checkboxes total=178 done=0 todo=178
- tts: no `tasks.md` / task index detected

## Active Task Queue (Imported)

### photo_auto_tagging (open from tasks.md)
- [todo] GF-POST-UI-03 - Implement explicit operating modes and mutation disclosures in UI
- [todo] GF-POST-UI-04 - Add preflight panels and decision-transparency coverage for generate/review/finalize
- [todo] GF-POST-UI-05 - Stabilize Playwright performance gate execution in local dependency-shim environments
- [todo] VQ-UI2-01 - Freeze API contract and V2 architecture spec before coding
- [todo] VQ-UI2-02 - Scaffold SvelteKit app with `/v2` subpath-safe build configuration
- [todo] VQ-UI2-03 - Mount V2 static app in FastAPI with correct route precedence
- [todo] VQ-UI2-04 - Build typed API client and shared frontend types
- [todo] VQ-UI2-05 - Implement polling/store strategy with backoff and visibility awareness
- [todo] VQ-UI2-06 - Phase 1 UI: StatusBar and QueuePanel (card rows + filtering + sorting)
- [todo] VQ-UI2-07 - Queue performance hardening (virtualization and lazy detail)
- [todo] VQ-UI2-08 - Phase 2 UI: SubmitPanel shell, workflow tabs, and dynamic param fields
- [todo] VQ-UI2-09 - Phase 2 UI: DropZone and presets integration
- [todo] VQ-UI2-10 - Phase 3 UI: JobDetail panel, prompt list, and log viewer
- [todo] VQ-UI2-11 - Workspace state, shortcuts, and interaction polish
- [todo] VQ-UI2-12 - Parity test suite, rollout gates, and rollback runbook

### aimSoloAnalysis (open from `TASKS.md`)
- [in_progress] XRK R&D notes (PROGRESS_AIMSOLO_XRK.txt) - continue in parallel
- [todo] Store raw arrays (compressed blobs) for full channel fidelity
- [todo] Add lean-angle proxy (from lateral accel + GPS radius) with quality gating
- [todo] Add light brake/throttle detection (turn/lean dependent) to synthesis
- [todo] Decode hGPS 56-byte record format
- [todo] Validate CRC16 trailer and timebase mapping
- [todo] Map hCHS fields to data types + sample rates
- [todo] Ingestion time benchmark
- [todo] Product-behavior assertion suite + golden scenario drift checks
- [todo] TASK-P0-09 - Upgrade coaching copy from consistency-only cues to explicit did-vs-should turn-in delta with causal rationale and concrete marker guidance
- [todo] TASK-P0-10 - Freeze top-insight did-vs-should payload contract (`did`, `should`, `because`, `success_check`) and null/fallback behavior
- [todo] TASK-P0-11 - Implement deterministic coaching copy policy for did-vs-should delta + causal rationale + measurable validation wording
- [todo] TASK-P0-12 - Ensure evidence plumbing always provides target/reference turn-in, rider average, and recent-lap turn-in history with graceful degradation
- [todo] TASK-P0-13 - Add golden behavior tests for did-vs-should coaching scenarios
- [todo] TASK-P0-14 - Gate did-vs-should coaching quality in eval scorecard
- [todo] TASK-PLAT-01 - Replace PowerShell bootstrap with native Python bootstrap refresh for WSL2/Linux planning flows
- [todo] TASK-PLAT-02 - Document and validate native WSL2 run/eval workflow for backend, frontend, and planner operations
- [todo] TASK-UI-10 - Freeze rewritten-UI API and payload contract for import/summary/insights/compare/map, including not-ready/error states
- [todo] TASK-UI-11 - Freeze modern JS trackside UI architecture and visual system for P0 flow
- [todo] TASK-UI-12 - Scaffold `ui-v2/` modern JS frontend shell and route skeleton for import/summary/insights/compare/corner
- [todo] TASK-UI-13 - Implement rewritten Insights experience with top-1 dominance and structured did-vs-should rendering
- [todo] TASK-UI-14 - Upgrade frontend evaluation harness for rewritten UI quality gates

### aimSoloAnalysis Summary
- Status: active repo with planner-owned dirty state (`TASKS.md` modified, `docs/wsl2_native_js_ui_design.md` untracked) on branch `feature/task-p0-12-evidence-plumbing`.
- Main risk: the WSL2/UI rewrite is valid, but it must not jump ahead of the coaching-contract chain. `TASK-P0-10` is the first hard gate for both P0 coaching quality and UI contract freeze.
- Design-intake dispatch order: `TASK-P0-10` -> `TASK-P0-11` + `TASK-P0-12` -> `TASK-P0-13` -> `TASK-P0-14` -> `TASK-PLAT-01` -> `TASK-PLAT-02` -> `TASK-UI-10` -> `TASK-UI-11` -> `TASK-UI-12` -> `TASK-UI-13` -> `TASK-UI-14`.
- Next-action rule: do not start `TASK-UI-12` scaffold work until native WSL2 bootstrap exists and the rewritten UI contract/architecture are frozen.

### video_queue (not done from tasks.md)
- [in_progress] T01: Test Infrastructure Upgrade
- [todo] T02: Shared Job Submission Service Extraction
- [todo] T03: API/CLI Submit Parity and Contract Tests
- [todo] T04: Database Boundary Hardening
- [todo] T05: Worker Decomposition Into Explicit State Transitions
- [todo] T06: Path and Environment Service Extraction
- [todo] T07: API Modularization (Routers + Services)
- [todo] T21 (Legacy D, Phase 2): Dynamic Arbitrary Stage Count ("Add Stage")
- [todo] T08: Workflow Metadata/UI Hint Contract
- [todo] T09: Frontend Refactor (Module Split + Error Resilience)
- [todo] T10: End-to-End Reliability and Failure Injection Suite
- [todo] T11: Documentation and Operational Playbooks
- [todo] T12: Final Regression Gate and Release Readiness
- [todo] T13: Persist UI Options Across Refresh and Reopen
- [todo] T14: Add Single I2V Tab With One-Image Input
- [todo] T15: Investigate and Fix `Cancel` Reliability
- [todo] T16: Show Matchable Task IDs and Collapsible Prompt Details in UI
- [in_progress] T25: Queue-Owned Input Staging for Durable Execution Paths
- [todo] T26: Upload Filename Policy: Preserve Original Name + Suffix on Collision
- [todo] T27: Input Duplication Policy and Configurable Staging Scope
- [todo] T28: Staging Retention and Cleanup Policy
- [todo] T32: Add Image Generation Mode (T2I + I2I) to Queue
- [todo] T33: Workspace Isolation and Visual Separation Upgrade
- [in_progress] T49: End-to-End Batch Acceptance and Quality Sign-off

### video_wall (not done from tasks.md)

### tts (tracking bootstrapped)
- [todo] Create `/home/cobra/tts/tasks.md` with `Txx` task format so it can join centralized status sync.

## Design Intake Queue (New)

## Planner-Owned Dispatch System Tasks

- [todo] AUT-OPS-01 - Install and expose the canonical `autonomy` console script so operator/planner skills can use `autonomy ...` directly without repo-local module fallback.
  - scope: `photo_auto_tagging` env/bootstrap and shell integration
  - why: current working path is `python -m autonomy.cli ...`; this is operationally correct but not the polished control-plane contract.
- [todo] AUT-OPS-02 - Define planner-owned task ingestion/update workflow from repo-local `tasks.md` and `CENTRAL/tasks.md` into the autonomy DB.
  - scope: decomposition rules, ownership rules, promotion to `pending`, and when central vs autonomy DB is source of truth
  - why: user should rarely create/update dispatch tasks manually; planner should do that work.
- [todo] AUT-OPS-03 - Update autonomy skills and repo docs to reflect real bootstrap/install behavior and the `dispatcher` launcher path.
  - scope: `autonomy-operator`, `autonomy-planner`, `autonomy-triage`, and `photo_auto_tagging/docs/autonomy_skills/*`
  - why: current skill text assumes canonical `autonomy` CLI availability, but local bootstrap still depends on repo runtime setup.
- [todo] AUT-OPS-04 - Add review/approval operating runbook for `pending_review`, failure triage, retry, and stale-review clearing.
  - scope: approval cadence, rejection/reset rules, and required operator evidence on closeout
  - why: dispatcher runtime without a crisp review workflow will accumulate stale review debt.
- [todo] AUT-OPS-05 - Decide and document source-of-truth migration from repo `tasks.md` boards to autonomy DB-backed planning.
  - scope: migration phases, mirror rules, drift resolution, and rollback path
  - why: planner currently tracks work in markdown boards; autonomy should eventually become the primary execution surface.
- Worker-ready task packet: [dispatch_system_tasks.md](/home/cobra/CENTRAL/dispatch_system_tasks.md)

### Intake 2026-02-28: `video_queue_auto_prompt_design.md`
- Source: `/home/cobra/photo_auto_tagging/docs/video_queue_auto_prompt_design.md`
- Primary target repo: `video_queue` (`/home/cobra/video_queue`)
- Secondary touchpoints: `photo_auto_tagging` (if we later mirror capability in GenFlow/PhotoQuery surfaces)
- Status: `converted` (imported to `video_queue/tasks.md` as `T40`-`T49` on 2026-02-28; current state: `T40`-`T48` done, `T49` in_progress)
- Sync note: updated design details (two-stage LM Studio flow, Stage 1 Qwen3-VL-8B-NSFW-Caption + Stage 2 Dolphin-Mistral-24B, requests-only dependency model, and caption-first iteration loop) are reflected in `T40`, `T41`, `T42`, `T45`, `T47`, `T48`, and `T49`.
- Sync note: latest doc refinements are captured in planning tasks:
  - renumbered split-prompt subsections (`11.3/11.4/11.5`) reflected in task decomposition
  - UI timestamp baseline aligned to `1.5s` / `3s` for 81-frame/24fps defaults
  - split-prompt UI terminology uses `Clip N` labels
  - API contract includes required `workflow_name` for split-mode/timing detection

Proposed execution slices:
- [done] VQ-AP-01: Add `video_queue/auto_prompt/` two-stage module (`generator.py`, `prompts.py`, optional `cache.py`) for caption->motion flow.
- [done] VQ-AP-02: Add LM Studio connectivity/capability detection and graceful `503` handling when unavailable.
- [done] VQ-AP-03: Add stage-selectable `POST /api/auto-prompt` endpoint (`caption|motion|both`) with captions reuse support.
- [done] VQ-AP-04: Add `per_file_params` support in `prompt_builder.build_prompts()` and job create schema.
- [done] VQ-AP-05: Add UI prompt mode model + two-stage auto panel states (caption stage, motion stage, apply-ready).
- [done] VQ-AP-06: Add progress UX + Stage 2-only regenerate/apply/clear controls for fast iteration.
- [done] VQ-AP-07: Add unit + integration tests for stage behavior, LM Studio failures, and per-file prompt override path.
- [done] VQ-AP-08: Add CLI/dev harness for stage-selectable runs and cached-caption Stage 2 iteration.
- [done] VQ-AP-09: Add docs/runbook for LM Studio setup, two-stage prompts, requests-only dependency model, and operator flow.
- [in_progress] VQ-AP-10: Run end-to-end acceptance on real 20-50 image batches, including Stage 2-only rerun evidence.

Repo-local canonical task IDs:
- `T40` Auto-Prompt Two-Stage LM Studio Generator Skeleton
- `T41` LM Studio Connectivity and Capability Gate
- `T42` Two-Stage Auto-Prompt API Endpoint (`POST /api/auto-prompt`)
- `T43` Per-File Prompt Overrides in Prompt Builder and Job Schema
- `T44` Prompt Mode Contract (`manual`, `per-image manual`, `per-image auto`)
- `T45` UI Two-Stage Auto-Prompt Panel
- `T46` Auto-Prompt Test Matrix (unit + integration + regression)
- `T47` Auto-Prompt Docs and Operator Runbook
- `T48` Auto-Prompt CLI Dev Harness for Prompt Iteration
- `T49` End-to-End Batch Acceptance and Quality Sign-off

## Imported Task Index

### photo_auto_tagging/tasks.md (all heading tasks)
- [done] I2V-00 - Environment bootstrap and baseline test pass
- [done] I2V-01 - Lock baseline behavior snapshot
- [done] I2V-02 - Add schema migration for image metadata
- [done] I2V-03 - Populate image metadata during scan/index
- [done] I2V-04 - Add metadata backfill command
- [done] I2V-05 - Add filter model and helpers in services layer
- [done] I2V-06 - Wire filters into `cmd_run` and `cmd_test`
- [done] I2V-07 - Wire filters into keeper and quality recommendation flows
- [done] I2V-08 - Expose filtering options in CLI and UI
- [done] I2V-09 - Add i2v template definitions
- [done] I2V-10 - Add `run-templates` command
- [done] I2V-11 - Add template picker to UI Operations tab
- [done] I2V-12 - Add i2v quality convenience commands
- [done] I2V-13 - Update docs and operator guidance
- [done] I2V-14 - Full regression pass and sign-off
- [done] RF-01 - Spot-check CLI/UI coverage for i2v feature completeness
- [done] RF-02 - Extract shared dim-mismatch/index-rebuild helper
- [done] RF-03 - Add metadata filter params to `cmd_quality_candidates`
- [done] RF-04 - Optimize `cmd_meta_scan` transaction strategy
- [done] RF-05 - Document `upsert_scan_state` full-scan assumption
- [done] RF-06 - Plan `services.py` modular split (deferred execution)
- [done] UI-OPS-01 - Add visual hierarchy to Operations tab sections
- [done] UI-OPS-02 - Wrap metadata filter controls in accordions across tabs
- [done] UI-OPS-03 - Move Meta Scan into an Index Tools grouping
- [done] UI-OPS-04 - Collapse low-frequency Operations controls
- [done] UI-OPS-05 - Reduce `build_app` monolith with per-tab builders
- [done] UI-OPS-06 - Add UI layout regression checks for new hierarchy
- [done] UI-VIS-01 - Add visual test task scope and dependency bootstrap
- [done] UI-VIS-02 - Capture deterministic screenshots in pytest
- [done] UI-VIS-03 - Add analysis-first assertions for no-baseline mode
- [done] UI-VIS-04 - Add optional baseline diff mode with bootstrap workflow
- [done] UI-VIS-05 - Run and verify targeted tests
- [done] UI-VIS-06 - Human screenshot review acceptance gate
- [done] UX-01 - Terminology normalization pass (High priority)
- [done] UX-02 - Change default landing experience (High priority)
- [done] UX-03 - Add strong empty states and next-action CTAs (High priority)
- [done] UX-04 - Preserve large single-image display contract (High priority, non-regression)
- [done] UX-05 - Add directory picker for roots with text fallback (Medium-high priority)
- [done] UX-06 - Update visual review checklist for all tabs (Medium priority)
- [done] UPA-00 - Re-run baseline tests and screenshot capture before changes
- [done] UPA-01 - Reorder top-level tabs to match workflow
- [done] UPA-02 - Consolidate Status, Calibration Status, and Errors into Diagnostics
- [done] UPA-03 - Improve Start Here density and navigability
- [done] UPA-04 - Reduce default JSON clutter using collapsed output sections
- [done] UPA-05 - Operations clarity pass (labels, output placement, slug management)
- [done] UPA-06 - Review Queue empty-state correctness and status-row polish
- [done] UPA-07 - Test tab readability and control-row responsiveness
- [done] UPA-08 - Quality workflow sequencing and advanced-control reduction
- [done] UPA-09 - Keeper simplification and safer root-configuration UX
- [done] UPA-10 - Image Search control prioritization pass
- [done] UPA-11 - Rename Audit tab to Data and Recovery (or equivalent)
- [done] UPA-12 - Errors diagnostics usability improvements
- [done] UPA-13 - Visual QA + sign-off pass for product-analysis changes
- [done] UPA-14 - Add keyboard shortcuts for high-frequency review actions
- [done] UPA-15 - Add human-readable action result messages across tabs
- [done] UPA-16 - Surface progress updates for long-running operations
- [done] UPA-17 - Upgrade Start Here into a live dashboard
- [done] UPA-18 - Add richer review context in Review Queue
- [done] UPA-19 - Add Undo Last Decision for review workflows
- [done] UPA-20 - Improve visual encoding for Quality star ratings
- [done] UPA-21 - Add integrated UX telemetry/checklist for flow-state features
- [done] PC-00 - Baseline validation and optional dependency bootstrap
- [done] PC-01 - Add `image_crops` schema migration
- [done] PC-02 - Add crop configuration model and defaults
- [done] PC-03 - Implement person detection probe helper
- [done] PC-04 - Implement crop generation utility
- [done] PC-05 - Extend `cmd_meta_scan` to generate and persist crops
- [done] PC-06 - Use crop embeddings by default in index pipeline
- [done] PC-07 - Add crop/index freshness diagnostics
- [done] PC-08 - Show crop as primary image in review-style tabs
- [done] PC-09 - Promote keeper crops by default with override
- [done] PC-10 - UI controls for crop-aware indexing and roots
- [done] PC-11 - CLI coverage for import/export and multi-user learnings safety
- [done] PC-12 - Tests and visual QA for person-crop flows
- [done] PC-13 - Documentation and rollout guidance
- [done] KI-00 - Keeper isolated index design spec
- [done] KI-01 - Add config support for isolated keeper runtime
- [done] KI-02 - Route keeper commands to keeper runtime + add `keeper-index`
- [done] KI-03 - Route keeper UI actions to keeper runtime
- [done] KI-04 - Ensure Gradio file access allows keeper image paths
- [done] KI-05 - Documentation + sample config updates
- [done] KI-06 - Polished release design tasks (not implemented in this milestone)
- [done] KM-00 - Keeper Meta Scan design spec
- [done] KM-01 - Add keeper-meta-scan UI action and controls
- [done] KM-02 - Route keeper-meta-scan handler to isolated keeper service
- [done] KM-03 - Shared-mode guard and operator messaging
- [done] KM-04 - Keeper meta-scan result messaging and next-step guidance
- [done] KM-05 - Cross-store safety and lock behavior regression pass
- [done] KM-06 - Documentation updates for keeper crop workflow
- [done] KM-07 - Polished release design tasks (not implemented in this milestone)
- [done] PH-00 - Keeper reranker diagnostics for model mismatch and fallback
- [done] PH-01 - Harden UI allowed-path computation against closed DB handles
- [done] PH-02 - CLIP-only defaults and legacy config normalization coverage
- [done] PH-03 - README hardening runbook updates
- [done] UR-01 - Phase 1: Button visual hierarchy (review tabs)
- [done] UR-02 - Phase 2: Layout tightening for review flow
- [done] UR-03 - Phase 3: Keeper tab restructuring
- [done] UR-04 - Phase 4: Image Search/Test tab polish
- [done] UR-05 - Phase 5: Tab consolidation decision (design-only)
- [done] UR-06 - Phase 6: Batch/grid review mode
- [done] UR-07 - Phase 7: Neighbor tag suggestions
- [done] UR-08 - Phase 8: Thumbnail filmstrip navigator
- [done] UR-09 - Phase 9: Click-to-zoom image inspection
- [done] UR-10 - Phase 10: Dark theme refinement
- [done] UR-11 - Phase 11: Long-operation progress indicators
- [done] UR-12 - Phase 12: Tagging while browsing (Test/Image Search)
- [done] UR-13 - Phase 13: Saved views/presets
- [done] UR-14 - Phase 14: Drag-and-drop image input
- [done] UR-15 - Phase 15: ComfyUI bridge (initial A/B scope)
- [done] UR-16 - Phase 16: Folder watch + UI notification
- [done] UR-17 - Final redesign acceptance gate
- [done] UR-18 - Persistent action output history (Keeper + shared pattern)
- [done] GF-UI-01 - Establish GenFlow design system tokens and component primitives
- [done] GF-UI-02 - Add deterministic screenshot capture for all core GenFlow views
- [done] GF-UI-03 - Add automated visual quality checks (no-baseline + diff mode)
- [done] GF-UI-04 - Add mandatory human screenshot review checklist for GenFlow milestones
- [done] GF-UI-05 - Define UX performance budgets and enforce with tests
- [done] GF-UI-06 - Add visual + UX release gate to Definition of Done
- [done] GF-CORE-01 - Add feature-to-task traceability matrix
- [done] GF-CORE-02 - Implement adapter contract test harness
- [done] GF-CORE-03 - Canonical path normalization across Linux/WSL/Windows
- [done] GF-CORE-04 - File-serving and allowed-path safety hardening
- [done] GF-CORE-05 - Per-user configuration and data-root isolation
- [done] GF-CORE-06 - Bootstrap/setup command for reproducible per-user onboarding
- [done] GF-CORE-07 - Generation lifecycle event log and restart recovery
- [done] GF-CORE-08 - Backup/restore and portability for learnings and metadata
- [done] GF-CORE-09 - Evaluation integrity: holdout/test isolation and leakage guards
- [done] GF-CORE-10 - Failure-injection and resilience test suite
- [done] GF-CORE-11 - Comprehensive end-to-end matrix (platform + profile + scale)
- [done] GF-CORE-12 - Definition-of-Done expansion for comprehensive testing
- [done] GF-CORE-13 - Model cache, download, and offline-mode reliability
- [done] GF-SHIP-DESIGN-01 - Runtime orchestration and process-topology design
- [done] GF-SHIP-01 - Implement orchestrator supervisor (`genflow up/down/status`)
- [done] GF-SHIP-DESIGN-02 - VRAM-aware execution/resource arbitration design
- [done] GF-SHIP-02X - Implement VRAM/CPU arbitration per design spec document
- [done] GF-SHIP-02A - Implement runtime resource broker and model lease API
- [done] GF-SHIP-02B - Implement automatic model load/unload sequencing across phases
- [done] GF-SHIP-02C - Add VRAM telemetry, budgets, and admission control
- [done] GF-SHIP-02D - Live GPU benchmark and CPU fallback validation
- [done] GF-SHIP-DESIGN-06 - Workflow selection/routing design for existing Flux and Wan pipelines
- [done] GF-SHIP-DESIGN-03 - Workflow state machine and transaction-boundary design
- [done] GF-SHIP-03 - Replace scaffold UI handlers with real command bus + backend actions
- [done] GF-SHIP-04 - Implement real adapter integrations (photoquery/video_queue/ComfyUI/video_wall)
- [done] GF-SHIP-05 - End-to-end source->generate->review->finalize flow implementation
- [done] GF-SHIP-DESIGN-04 - UX flow and persistent operation-log design
- [done] GF-SHIP-06 - Implement persistent operation logs + timeline UI
- [done] GF-SHIP-07 - Recipe/prompt library as real versioned assets
- [done] GF-SHIP-DESIGN-05 - Evaluation and learning-loop policy design
- [done] GF-SHIP-08 - Implement learning-loop execution and calibration visibility
- [done] GF-SHIP-09 - Shipping-grade documentation and operational runbooks
- [done] GF-SHIP-10 - End-to-end live integration matrix and release gate
- [done] GF-POST-UI-01 - Screenshot every top-level GenFlow tab and audit against product requirements
- [done] GF-POST-UI-02 - Manual UI invocation validation across all primary actions
- [todo] GF-POST-UI-03 - Implement explicit operating modes and mutation disclosures in UI
- [todo] GF-POST-UI-04 - Add preflight panels and decision-transparency coverage for generate/review/finalize
- [todo] GF-POST-UI-05 - Stabilize Playwright performance gate execution in local dependency-shim environments
- [done] GF-RECOV-01 - Convert user-reported UX/runtime failures into explicit tracked tasks
- [done] GF-RECOV-02 - Make Generate tab operational (real actions + full common settings surface)
- [done] GF-RECOV-03 - Fix Review candidate loading semantics and queue navigation
- [done] GF-RECOV-04 - Reduce timeline noise with tab-scoped history views
- [done] GF-RECOV-05 - Improve Library operability (presets, slug dropdown, learning clarity)
- [done] GF-RECOV-DESIGN-01 - Evaluation gap analysis and anti-regression strategy
- [done] GF-RECOV-06 - Apply new evaluation strategy to current GenFlow UI and publish evidence
- [done] GF-GAL-DESIGN-01 - Define scalable gallery architecture and UX
- [done] GF-GAL-01 - Add command-bus gallery listing APIs
- [done] GF-GAL-02 - Add `Gallery Images` tab with thumbnail browsing
- [done] GF-GAL-03 - Add `Gallery Video` tab with video-wall integration
- [done] PQ-PATH-DESIGN-01 - Define canonical path/index/sync strategy (DO FIRST)
- [done] PQ-SYNC-01 - Add automatic recursive one-way sync from `D:\\V` to `/home/cobra/dPics/dV`
- [done] PQ-REVIEW-UX-01 - Move candidate queue controls to Review Queue top in collapsible section
- [done] PQ-CROP-INDEX-01 - Evaluate and decide crop-before-index workflow
- [done] PQ-IDX-01 - Replace NOT IN placeholder pattern in upsert_scan_state with temp table
- [done] PQ-IDX-02 - Defer metadata probing to changed files only in scan_roots
- [done] PQ-IDX-03 - Batch changed_files query instead of N individual SELECTs
- [done] PQ-IDX-04 - Batch upsert_scan_state with executemany
- [done] PQ-IDX-05 - Integrate crop generation into cmd_index when crops enabled
- [done] PQ-IDX-06 - Add path-drift health check to pq status and UI startup
- [done] PQ-IDX-07 - Execute main store migration (Level 1 reconcile)
- [done] PQ-INDEX-UX-01 - Create dedicated `Index` tab with strict main vs keeper separation
- [done] PQ-REVIEW-CONSISTENCY-01 - Audit Review Queue/Quality/Keeper/Test for interaction consistency and publish standard
- [done] PQ-IMAGE-SEARCH-01 - Add text-only search mode to Image Search tab (no rating workflow required)
- [done] PQ-GALLERY-FAVORITES-01 - Design favorites model and Gallery tab (slug grid + favorites view)
- [done] PQ-GAL2-01 - Mount gallery API routes on Gradio's FastAPI app
- [done] PQ-GAL2-02 - Serve static gallery page and implement masonry grid
- [done] PQ-GAL2-03 - Implement filter bar (slug selection, text search, sort, quality filters)
- [done] PQ-GAL2-04 - Implement lightbox with full-screen view and navigation
- [done] PQ-GAL2-05 - Implement AI slug tagging in lightbox
- [done] PQ-GAL2-06 - Implement quality rating in lightbox
- [done] PQ-GAL2-07 - Implement favorite toggle (grid + lightbox)
- [done] PQ-GAL2-08 - Implement bulk selection and bulk tagging
- [done] PQ-GAL2-09 - Implement image info panel and copy path
- [done] PQ-GAL2-10 - Add gallery link to Gradio app and integration polish
- [todo] VQ-UI2-01 - Freeze API contract and V2 architecture spec before coding
- [todo] VQ-UI2-02 - Scaffold SvelteKit app with `/v2` subpath-safe build configuration
- [todo] VQ-UI2-03 - Mount V2 static app in FastAPI with correct route precedence
- [todo] VQ-UI2-04 - Build typed API client and shared frontend types
- [todo] VQ-UI2-05 - Implement polling/store strategy with backoff and visibility awareness
- [todo] VQ-UI2-06 - Phase 1 UI: StatusBar and QueuePanel (card rows + filtering + sorting)
- [todo] VQ-UI2-07 - Queue performance hardening (virtualization and lazy detail)
- [todo] VQ-UI2-08 - Phase 2 UI: SubmitPanel shell, workflow tabs, and dynamic param fields
- [todo] VQ-UI2-09 - Phase 2 UI: DropZone and presets integration
- [todo] VQ-UI2-10 - Phase 3 UI: JobDetail panel, prompt list, and log viewer
- [todo] VQ-UI2-11 - Workspace state, shortcuts, and interaction polish
- [todo] VQ-UI2-12 - Parity test suite, rollout gates, and rollback runbook
- [done] PQ-UI2-01 - Finalize V2 implementation spec and endpoint contract
- [done] PQ-UI2-02 - Build API route package and app mount entrypoint
- [done] PQ-UI2-03 - Add first-class image-serving endpoint for V2
- [done] PQ-UI2-04 - Scaffold SvelteKit V2 app with `/v2`-safe static build
- [done] PQ-UI2-05 - Mount V2 static app alongside V1 with safe fallback behavior
- [done] PQ-UI2-06 - Implement Dashboard page (Start Here + Operations replacement)
- [done] PQ-UI2-07 - Implement Gallery page in Svelte (no standalone phase)
- [done] PQ-UI2-08 - Build unified `ReviewWorkspace` in queue mode
- [done] PQ-UI2-09 - Add quality mode to unified `ReviewWorkspace`
- [done] PQ-UI2-10 - Add keeper mode to unified `ReviewWorkspace` with promote flow
- [done] PQ-UI2-11 - Add test mode to unified `ReviewWorkspace` (preview-only)
- [done] PQ-UI2-12 - Implement Search page (text and image search modes)
- [done] PQ-UI2-13 - Implement Settings page (index, recovery, diagnostics)
- [done] PQ-UI2-14 - Parity gates, performance validation, and deprecation rollout plan

### video_queue/tasks.md (all Txx tasks)
- [done] T00: Baseline Behavior Lock and Refactor Guardrails
- [in_progress] T01: Test Infrastructure Upgrade
- [todo] T02: Shared Job Submission Service Extraction
- [todo] T03: API/CLI Submit Parity and Contract Tests
- [todo] T04: Database Boundary Hardening
- [todo] T05: Worker Decomposition Into Explicit State Transitions
- [todo] T06: Path and Environment Service Extraction
- [todo] T07: API Modularization (Routers + Services)
- [done] T18 (Legacy A): Add New 2-Pass Split-Prompt Workflow
- [done] T19 (Legacy B): Add New 3-Pass Workflow (Extended Length)
- [done] T20 (Legacy C): UI Support for New Stage-Prompt Workflows
- [todo] T21 (Legacy D, Phase 2): Dynamic Arbitrary Stage Count ("Add Stage")
- [done] T22 (Legacy E): Source Image Upscale Mode for I2V Prep
- [todo] T08: Workflow Metadata/UI Hint Contract
- [todo] T09: Frontend Refactor (Module Split + Error Resilience)
- [todo] T10: End-to-End Reliability and Failure Injection Suite
- [todo] T11: Documentation and Operational Playbooks
- [todo] T12: Final Regression Gate and Release Readiness
- [todo] T13: Persist UI Options Across Refresh and Reopen
- [todo] T14: Add Single I2V Tab With One-Image Input
- [todo] T15: Investigate and Fix `Cancel` Reliability
- [todo] T16: Show Matchable Task IDs and Collapsible Prompt Details in UI
- [done] T17: Default Batch Input Directory to `/home/cobra/ComfyUI/input`
- [done] T23: Queue UX/Visibility Redesign (Research-Backed)
- [done] T24: Queue UX P0 Implementation (Status Controls, Actionable Sort, Safe Actions)
- [in_progress] T25: Queue-Owned Input Staging for Durable Execution Paths
- [todo] T26: Upload Filename Policy: Preserve Original Name + Suffix on Collision
- [todo] T27: Input Duplication Policy and Configurable Staging Scope
- [todo] T28: Staging Retention and Cleanup Policy
- [done] T29: Full-Page UI Hierarchy and Visual Refresh
- [done] T30: Video I2V Multi-Image Drag/Drop with Thumbnails
- [done] T31: Multi-Tab Workspaces for Queue Control Panel
- [todo] T32: Add Image Generation Mode (T2I + I2I) to Queue
- [todo] T33: Workspace Isolation and Visual Separation Upgrade
- [done] T34: UI V2 Foundation Scaffold (`/v2` Svelte App)
- [done] T35: UI V2 Phase 1 - Status Bar + Queue Panel
- [done] T36: UI V2 Phase 2 - Submit Panel + Dynamic Params + Presets
- [done] T37: UI V2 Phase 3 - Job Detail + Log Viewer + Workspace Manager
- [done] T38: UI V2 Compatibility, Performance, and Accessibility Gate
- [done] T39: UI V2 Cutover Plan (`/v2` -> `/`, Legacy Fallback)
- [done] T40: Auto-Prompt Two-Stage LM Studio Generator Skeleton
- [done] T41: LM Studio Connectivity and Capability Gate
- [done] T42: Two-Stage Auto-Prompt API Endpoint (`POST /api/auto-prompt`)
- [done] T43: Per-File Prompt Overrides in Prompt Builder and Job Schema
- [done] T44: Prompt Mode Contract (`manual`, `per-image manual`, `per-image auto`)
- [done] T45: UI Two-Stage Auto-Prompt Panel
- [done] T46: Auto-Prompt Test Matrix (unit + integration + regression)
- [done] T47: Auto-Prompt Docs and Operator Runbook
- [done] T48: Auto-Prompt CLI Dev Harness for Prompt Iteration
- [in_progress] T49: End-to-End Batch Acceptance and Quality Sign-off

### video_wall/tasks.md (all Txx tasks)
- [done] T00: Favorites Mode Design Doc (Product + Technical)

### photo_auto_tagging/docs/ui_redesign_tasks.md (phase headers)
- ## Phase 1: Button Visual Hierarchy (all review tabs)
- ## Phase 2: Layout Tightening (review tabs)
- ## Phase 3: Keeper Tab Restructuring
- ## Phase 4: Image Search & Test Tab Polish
- ## Phase 5: Tab Consolidation (optional, evaluate after Phase 4)
- ## Phase 6: Batch/Grid Review Mode
- ## Phase 7: Tag Suggestions from Neighbors
- ## Phase 8: Thumbnail Filmstrip Navigator
- ## Phase 9: Image Zoom
- ## Phase 10: Dark Theme Refinement
- ## Phase 11: Progress Animation
- ## Phase 12: Tagging While Browsing
- ## Phase 13: Saved Views / Presets
- ## Phase 14: Drag-and-Drop from External
- ## Phase 15: ComfyUI Bridge
- ## Phase 16: Folder Watch + Notification
- ## Final Acceptance

## Notes
- Duplication exists by design in this first import pass (for example, video_queue-related planning appears in both photo_auto_tagging and video_queue task files).
- Use repo-local tasks.md as source of truth for status updates until migration is complete.
- Dispatch contract: repo=<repo_name> do task Txx (or source ID for non-Txx tasks).
