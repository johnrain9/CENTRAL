---
name: mobile-response
description: Compressed, small-screen-friendly output for when the user is on a mobile device via Termius or similar. Use this skill ONLY when the user explicitly says "on mobile", "mobile response", "mobile mode", "from my phone", or similar. Do NOT trigger for mobile app development, mobile testing, or any other use of the word "mobile" in a development context.
---

# Mobile Response Mode

The user is reading on a small phone screen via Termius. Every character counts.

## Output Rules

- Max 60 characters per line where possible
- Use short bullets, not paragraphs
- No tables wider than 3 columns
- No full file paths — use basenames or `~/` shorthand
- No code blocks longer than 8 lines
- Abbreviate common terms:
  - task → tsk, status → st, priority → p
  - running → run, failed → fail, done → ✓
  - CENTRAL-OPS → OPS
- Skip preamble — lead with the answer
- Group related info tight, one blank line between sections

## Task Status Format

When showing tasks, use this compact format:
```
OPS-77 p2 ✓  Result normalization fix
OPS-39 p11 run  Repo health CLI
```

## Dispatcher Status Format

```
Dispatcher: up pid=1234 3/3 workers
  OPS-39 run sonnet-4-6
  OPS-64 run sonnet-4-6
Mode: claude | Model: sonnet-4-6
```

## Summary Format

```
Portfolio: 55✓ 4todo 0blocked
Mismatches: 19 (cosmetic)
Workers: 3 run, 0 idle
Next eligible: OPS-67 p100
```

## When asked for details

Give one level deeper but still compact:
```
OPS-77 p2 ✓ bugfix
  Fixed claude result normalization
  Worker output → worker_result schema
  Tests: 4/4 pass
```

## Do NOT

- Show full JSON output
- Include file paths unless asked
- Use horizontal rules or headers heavier than ##
- Repeat what the user said
