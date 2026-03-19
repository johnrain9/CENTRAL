# Document Review Tool

Use [`scripts/review_doc.py`](/Users/paul/projects/CENTRAL/scripts/review_doc.py) to run an adversarial review against a design, requirements, or investigation document.

The tool is intentionally small:
- prompt presets by document type
- `codex` as the default backend
- first-class `claude` support
- explicit context tiers so cheap reviews stay cheap
- one generic external-command backend for anything else
- review output written to a file

The CLI is meant to be self-discoverable:

```bash
python3 scripts/review_doc.py --help
```

The `--help` output now includes:
- mode descriptions
- backend descriptions
- example commands
- the required placeholders for external backends

## Default Usage

Run an HLD review with Codex:

```bash
python3 scripts/review_doc.py \
  --input docs/capability_memory_hld.md \
  --mode hld
```

That defaults to:
- backend: `codex`
- context level: `doc-only`
- sandbox: `read-only`
- cwd: input document directory
- output: `docs/capability_memory_hld.review.hld.md`

This default is intentionally cheap:
- the reviewer should critique only the document text
- it should not explore the repository
- if a finding depends on implementation details, the review should call out missing context instead of diving into code

Run the same review with Claude Code:

```bash
python3 scripts/review_doc.py \
  --input docs/capability_memory_hld.md \
  --mode hld \
  --backend claude
```

Notes for Claude:
- the tool runs `claude` in print mode
- it feeds the generated review prompt over stdin
- review output is captured and written to the output file by the wrapper script
- the wrapper falls back to `~/.local/bin/claude` if the current shell environment has not refreshed `PATH`

## Modes

Available modes:
- `hld`
- `lld`
- `requirements`
- `investigation`
- `generic`

Use `generic` when the document does not fit cleanly into the others.

## Context Levels

Use context levels to control cost and review depth:
- `doc-only`
- `targeted`
- `repo`

`doc-only` is the default and should usually be the first pass.

Use `targeted` when you want a design checked against a small set of real implementation files:

```bash
python3 scripts/review_doc.py \
  --input docs/my_lld.md \
  --mode lld \
  --context-level targeted \
  --context-file autonomy/dispatch.py \
  --context-file autonomy/store.py
```

Notes for `targeted`:
- you must pass at least one `--context-file`
- the tool embeds those files into the prompt
- the reviewer is instructed to use only those files and not roam the repo

Use `repo` only when you want a fuller implementation-fit review:

```bash
python3 scripts/review_doc.py \
  --input docs/my_hld.md \
  --mode hld \
  --context-level repo
```

Notes for `repo`:
- the default `cwd` becomes the nearest Git root
- the reviewer is allowed to inspect local files selectively
- this is the most expensive mode and should usually be a later pass

## Useful Flags

Pick a model or profile:

```bash
python3 scripts/review_doc.py \
  --input docs/my_doc.md \
  --mode lld \
  --profile work \
  --model gpt-5
```

Add extra instructions:

```bash
python3 scripts/review_doc.py \
  --input docs/my_doc.md \
  --mode hld \
  --extra-instruction "Be especially hard on rollback and migration risk." \
  --extra-instruction "Call out any place where ownership is ambiguous."
```

Print the exact backend command before execution:

```bash
python3 scripts/review_doc.py \
  --input docs/my_doc.md \
  --mode generic \
  --print-command
```

Recommended workflow:
- HLD, requirements, investigation: start with `doc-only`
- LLD: usually use `targeted`
- final implementation-fit review: use `repo` only when needed

## External Backend

If you want something other than `codex`, use the external backend with a command template.

The template must include:
- `{prompt_file}`
- `{output_file}`

Optional placeholders:
- `{input_file}`
- `{mode}`
- `{cwd}`

Example:

```bash
python3 scripts/review_doc.py \
  --input docs/my_doc.md \
  --mode hld \
  --backend external \
  --command-template 'my-review-cli --prompt-file {prompt_file} --output {output_file}'
```

This is the intended escape hatch for anything you call "cloud" without forcing this tool to know that CLI's exact contract.

## Prompt-Only Mode

If you only want the generated review prompt:

```bash
python3 scripts/review_doc.py \
  --input docs/my_doc.md \
  --mode requirements \
  --backend prompt-only \
  --output /tmp/review_prompt.md
```

This is useful for debugging prompts or pasting them into another system manually.
