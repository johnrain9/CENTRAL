---
name: doc-review-bundles
description: Use when reviewing design docs, frontend HLDs, or frontend LLDs with CENTRAL's `scripts/review_doc.py`, including cross-provider bundle presets, rerunning a single reviewer, and rereviewing a revised document against prior review outputs.
---

# Document Review Bundles

Use this skill for CENTRAL's document review workflow driven by `python3 scripts/review_doc.py`.

## When To Use It

- Reviewing a product or UI design doc with mixed Codex and Opus reviewers
- Reviewing a frontend HLD with the `frontend-hld` preset
- Reviewing a frontend LLD with the `frontend-lld` preset
- Rerunning one reviewer from an existing bundle
- Rereviewing a revised document against a prior review directory

## Quick Start

List reviewers for a preset:

```bash
python3 scripts/review_doc.py \
  --input path/to/doc.md \
  --bundle-preset design-ui \
  --list-reviewers
```

Run the default product/UI design bundle:

```bash
python3 scripts/review_doc.py \
  --input path/to/design_doc.md \
  --bundle-preset design-ui \
  --context-file path/to/requirements.md \
  --context-file path/to/companion_design.md
```

Run the frontend HLD bundle:

```bash
python3 scripts/review_doc.py \
  --input path/to/frontend_hld.md \
  --bundle-preset frontend-hld \
  --context-file path/to/product_requirements.md \
  --context-file path/to/backend_hld.md
```

Run the frontend LLD bundle against parent docs:

```bash
python3 scripts/review_doc.py \
  --input path/to/arrangement_engine_lld.md \
  --bundle-preset frontend-lld \
  --parent-doc path/to/frontend_hld.md \
  --parent-review-dir path/to/frontend_hld.rereviews.frontend_hld \
  --adjacent-doc path/to/streaming_architecture_lld.md \
  --context-file path/to/backend_persistence_design.md
```

## Presets

- `design-ui`
  - `visual_design_critique` -> Codex / GPT-5.4
  - `ux_product_critique` -> Codex / GPT-5.4
  - `implementation_system_reality` -> Opus
- `frontend-hld`
  - `client_architecture_shape` -> Codex / GPT-5.4
  - `experience_state_coverage` -> Codex / GPT-5.4
  - `system_contracts_and_delivery` -> Opus
- `frontend-lld`
  - `client_contracts_and_state_machine` -> Codex / GPT-5.4
  - `interaction_and_edge_state_coverage` -> Codex / GPT-5.4
  - `integration_and_delivery_reality` -> Opus

All bundle reviewers run at high effort.

## Important Flags

- `--reviewer <name>` reruns only one reviewer or a subset from a preset
- `--rereview-from <review_dir>` audits a revised document against a prior review directory
- `--parent-doc <path>` embeds higher-level design docs as context
- `--adjacent-doc <path>` embeds neighboring docs for contract consistency checks
- `--parent-review-dir <dir>` embeds the prior summary from a related review/rereview directory

## Rerun And Rereview

Rerun one reviewer:

```bash
python3 scripts/review_doc.py \
  --input path/to/doc.md \
  --bundle-preset design-ui \
  --reviewer implementation_system_reality \
  --context-file path/to/requirements.md
```

Rereview a revised doc:

```bash
python3 scripts/review_doc.py \
  --input path/to/revised_doc.md \
  --bundle-preset design-ui \
  --rereview-from path/to/original_doc.reviews.design_ui
```

Rereview output is written under `<doc>.rereviews.<preset>/`.

## Output Expectations

- Fresh bundle output includes one file per reviewer plus `summary.md`
- Rereview output includes one file per reviewer plus `summary.rereview.md`
- Rereview summaries include:
  - `Resolution Audit Counts`
  - `Must Fix Now`
  - `Defer To Other Doc`
  - `Needs Prototype`
  - `Resolved`

## Reduced Codex Mode (temporary — until 2026-03-25 22:24 MT)

Codex quota is constrained. Run only 1 Codex + 1 Opus reviewer per preset (drop the 2nd Codex lane). Use `--reviewer` to select:

```bash
# design-ui: keep visual_design_critique + implementation_system_reality
python3 scripts/review_doc.py --input doc.md --bundle-preset design-ui \
  --reviewer visual_design_critique \
  --reviewer implementation_system_reality ...

# frontend-hld: keep client_architecture_shape + system_contracts_and_delivery
python3 scripts/review_doc.py --input doc.md --bundle-preset frontend-hld \
  --reviewer client_architecture_shape \
  --reviewer system_contracts_and_delivery ...

# frontend-lld: keep client_contracts_and_state_machine + integration_and_delivery_reality
python3 scripts/review_doc.py --input doc.md --bundle-preset frontend-lld \
  --reviewer client_contracts_and_state_machine \
  --reviewer integration_and_delivery_reality ...
```

**Remove this section once Codex quota recovers.**

## Notes

- Bundle mode uses only the target doc plus explicit context artifacts. It should not roam the repo.
- Prefer rereview over a fresh second-pass review when the document changed moderately rather than being fully rewritten.
- If the user is unsure which reviewer names exist, run `--list-reviewers`.
