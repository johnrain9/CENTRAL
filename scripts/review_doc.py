#!/usr/bin/env python3
"""Run adversarial document reviews via Codex or an external reviewer CLI."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None


MODE_FOCUS = {
    "hld": [
        "scope boundaries and ownership seams",
        "architectural consistency and invariants",
        "missing contracts between subsystems",
        "failure modes, rollback, and recovery behavior",
        "scalability, operability, and observability gaps",
        "migration, rollout, and adoption risk",
    ],
    "lld": [
        "schema, API, and state-machine correctness",
        "transactionality, concurrency, and idempotency",
        "edge cases, failure handling, and recovery",
        "backward compatibility and migration safety",
        "testability and operational instrumentation",
        "places where the low-level contract is ambiguous or contradictory",
    ],
    "requirements": [
        "missing or contradictory requirements",
        "ambiguous acceptance criteria",
        "unstated assumptions and hidden scope",
        "realistic operational constraints that are missing",
        "requirements that are not testable or not measurable",
    ],
    "investigation": [
        "unsupported conclusions",
        "weak evidence or missing data",
        "alternative explanations not considered",
        "premature design commitments",
        "open questions that block a justified decision",
    ],
    "generic": [
        "contradictions, underspecification, and weak assumptions",
        "missing decisions or requirements",
        "operational, migration, and testing gaps",
        "places where implementation teams could reasonably diverge",
    ],
}

CONTEXT_LEVEL_GUIDANCE = {
    "doc-only": (
        "Review only the target document. Do not inspect local repository files, source code, or other docs. "
        "If a finding depends on implementation context, call out the missing context explicitly instead of exploring."
    ),
    "targeted": (
        "Review the target document first, then use only the explicitly provided context files to confirm or sharpen findings. "
        "Do not inspect any other local repository files."
    ),
    "repo": (
        "Review the target document first. Then inspect local repository files selectively when repository context would materially "
        "change or sharpen a finding. Do not perform a broad codebase tour without a concrete reason."
    ),
}

MODE_BOUNDARY_GUIDANCE = {
    "hld": (
        "Keep the critique at the high-level design layer. Focus on architecture, ownership, contracts, system behavior, "
        "rollout, and operational risk. Do not drift into low-level schema, API, code-structure, or implementation details "
        "unless the document itself incorrectly depends on them."
    ),
    "lld": (
        "Keep the critique at the low-level design layer. Focus on concrete contracts, schemas, APIs, state transitions, "
        "concurrency, migrations, and testability. Do not drift into code-level implementation review or line-by-line "
        "coding suggestions unless the document itself incorrectly depends on them."
    ),
    "requirements": (
        "Keep the critique at the requirements layer. Focus on coverage, clarity, measurability, and contradictions. "
        "Do not drift into architecture or implementation design unless the requirements improperly hardcode them."
    ),
    "investigation": (
        "Keep the critique at the investigation layer. Focus on evidence quality, reasoning, alternatives, and decision readiness. "
        "Do not drift into prescribing detailed design or implementation unless the investigation claims those details are already justified."
    ),
    "generic": (
        "Match the critique depth to the document. Do not drift into lower-level design or implementation detail unless the document's claims require that level of scrutiny."
    ),
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Run an adversarial review against a design, requirements, or investigation document. "
            "The tool builds a critique prompt and sends it to Codex or another CLI."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scripts/review_doc.py --input docs/capability_memory_hld.md --mode hld\n"
            "  python3 scripts/review_doc.py --input docs/capability_memory_hld.md --mode hld --backend claude\n"
            "  python3 scripts/review_doc.py --input docs/my_lld.md --mode lld --context-level targeted \\\n"
            "    --context-file autonomy/dispatch.py --context-file autonomy/store.py\n"
            "  python3 scripts/review_doc.py --input docs/my_hld.md --mode hld --context-level repo\n"
            "  python3 scripts/review_doc.py --input docs/my_lld.md --mode lld --profile work --model gpt-5\n"
            "  python3 scripts/review_doc.py --input docs/reqs.md --mode requirements --backend prompt-only\n"
            "  python3 scripts/review_doc.py --input docs/my_doc.md --mode generic --backend external \\\n"
            "    --command-template 'my-review-cli --prompt-file {prompt_file} --output {output_file}'\n\n"
            "Modes:\n"
            "  hld            Critique architecture, ownership seams, failure modes, rollout, and system fit.\n"
            "  lld            Critique schemas, APIs, state machines, migrations, and implementation contracts.\n"
            "  requirements   Critique missing, ambiguous, or untestable requirements.\n"
            "  investigation  Critique unsupported conclusions, evidence quality, and open questions.\n"
            "  generic        Use the general adversarial review prompt.\n"
            "Context levels:\n"
            "  doc-only       Review only the input document. This is the default.\n"
            "  targeted       Review the document plus only the files passed via --context-file.\n"
            "  repo           Allow selective repository inspection from the working directory.\n"
        ),
    )
    parser.add_argument("--input", required=True, help="Path to the document to review.")
    parser.add_argument(
        "--mode",
        choices=sorted(MODE_FOCUS),
        default="generic",
        help="Review mode. Defaults to generic.",
    )
    parser.add_argument(
        "--backend",
        choices=("codex", "claude", "external", "prompt-only"),
        default="codex",
        help=(
            "Reviewer backend. 'codex' runs codex exec. "
            "'claude' runs Claude Code in print mode. "
            "'external' runs a custom command template. "
            "'prompt-only' only writes the generated prompt to the output file."
        ),
    )
    parser.add_argument(
        "--output",
        help="Where the review should be written. Defaults to <input>.review.<mode>.md",
    )
    parser.add_argument(
        "--cwd",
        help=(
            "Working directory for the reviewer. Defaults to the input document directory unless "
            "--context-level repo is selected, in which case it defaults to the nearest Git root."
        ),
    )
    parser.add_argument(
        "--context-level",
        choices=("doc-only", "targeted", "repo"),
        default="doc-only",
        help=(
            "How much local context the reviewer may use. "
            "'doc-only' reviews only the input doc. "
            "'targeted' allows only files passed via --context-file. "
            "'repo' allows selective repository inspection."
        ),
    )
    parser.add_argument(
        "--context-file",
        action="append",
        default=[],
        help=(
            "Additional file to include in a targeted review. Repeat for multiple files. "
            "These files are embedded in the prompt and are the only allowed local context when "
            "--context-level targeted is used."
        ),
    )
    parser.add_argument("--profile", help="Codex profile to use when --backend codex.")
    parser.add_argument("--model", help="Codex model to use when --backend codex.")
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default="read-only",
        help="Codex sandbox mode when --backend codex. Defaults to read-only.",
    )
    parser.add_argument(
        "--skip-git-repo-check",
        action="store_true",
        help="Pass through to Codex if the inferred cwd is not a Git repo.",
    )
    parser.add_argument(
        "--command-template",
        help=(
            "External backend command template. Must include {prompt_file} and {output_file}. "
            "Optional placeholders: {input_file}, {mode}, {cwd}. "
            "Example: 'my-review-cli --prompt-file {prompt_file} --output {output_file}'"
        ),
    )
    parser.add_argument(
        "--extra-instruction",
        action="append",
        default=[],
        help="Additional review instruction. Repeat for multiple lines.",
    )
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="Print the reviewer command before execution.",
    )
    return parser.parse_args(argv)


def resolve_input_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise SystemExit(f"input file not found: {path}")
    return path


def default_output_path(input_path: Path, mode: str) -> Path:
    return input_path.with_name(f"{input_path.stem}.review.{mode}{input_path.suffix or '.md'}")


def resolve_executable(name: str) -> str:
    resolved = shutil.which(name)
    if resolved:
        return resolved
    local_bin = Path.home() / ".local" / "bin" / name
    if local_bin.is_file():
        return str(local_bin)
    raise SystemExit(f"required executable not found on PATH: {name}")


def resolve_default_codex_model() -> str | None:
    if tomllib is None:
        return None
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.is_file():
        return None
    try:
        payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    model = payload.get("model")
    return str(model).strip() if model else None


def find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_cwd(input_path: Path, requested: str | None, context_level: str) -> tuple[Path, bool]:
    if requested:
        cwd = Path(requested).expanduser().resolve()
        if not cwd.exists():
            raise SystemExit(f"cwd does not exist: {cwd}")
        return cwd, not (cwd / ".git").exists()
    if context_level == "repo":
        git_root = find_git_root(input_path.parent)
        if git_root is not None:
            return git_root, False
    return input_path.parent, True


def resolve_context_files(values: list[str]) -> list[Path]:
    resolved: list[Path] = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"context file not found: {path}")
        resolved.append(path)
    return resolved


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def number_lines(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return "   1 |"
    return "\n".join(f"{index + 1:4d} | {line}" for index, line in enumerate(lines))


def format_context_files(context_files: list[Path]) -> str:
    sections: list[str] = []
    for path in context_files:
        sections.append(
            "\n".join(
                [
                    f"Context file: {path}",
                    "",
                    number_lines(read_text(path)),
                ]
            )
        )
    return "\n\n".join(sections)


def build_prompt(
    *,
    input_path: Path,
    mode: str,
    context_level: str,
    context_files: list[Path],
    extra_instructions: list[str],
) -> str:
    mode_focus = MODE_FOCUS[mode]
    numbered_doc = number_lines(read_text(input_path))
    extra_block = ""
    if extra_instructions:
        extra_lines = "\n".join(f"- {item.strip()}" for item in extra_instructions if item.strip())
        if extra_lines:
            extra_block = f"\nAdditional instructions:\n{extra_lines}\n"
    context_file_block = ""
    if context_files:
        context_file_block = (
            "\nExplicit context files included below. These are the only local files you may use in targeted mode:\n"
            f"{chr(10).join(f'- {path}' for path in context_files)}\n"
        )
    focus_lines = "\n".join(f"- {item}" for item in mode_focus)
    return (
        f"You are performing an adversarial review of a {mode.upper()} document.\n\n"
        "Your job is not to restate or summarize it. Your job is to find weaknesses, risks, contradictions, "
        "missing decisions, underspecified contracts, and ways the document could cause wasted work or bad outcomes.\n\n"
        "Explicitly look for important things the document does not say but should. "
        "Treat omissions, missing constraints, missing requirements, missing failure handling, and missing rollout or "
        "operational details as first-class findings, not as side notes.\n\n"
        "Review it like a skeptical senior engineer.\n\n"
        f"Mode boundary: {MODE_BOUNDARY_GUIDANCE[mode]}\n\n"
        f"Context level: {context_level}\n"
        f"Context guidance: {CONTEXT_LEVEL_GUIDANCE[context_level]}\n"
        f"{context_file_block}"
        "\n"
        "Focus especially on:\n"
        f"{focus_lines}\n"
        f"{extra_block}\n"
        "For each finding:\n"
        "1. Give it a short title.\n"
        "2. Assign severity: critical, high, medium, or low.\n"
        "3. Explain why it is a problem.\n"
        "4. Cite the relevant document line numbers and any local corroborating files if used.\n"
        "5. Describe the likely consequence if left unresolved.\n"
        "6. Recommend the decision, clarification, or design change needed.\n\n"
        "Prioritize high-severity findings first.\n"
        "Do not praise the document unless necessary for contrast.\n"
        "Do not rewrite the document.\n"
        "Do not give generic advice.\n"
        "Be concrete, critical, and specific.\n"
        "If something looks acceptable but depends on an unstated assumption, call that out explicitly.\n\n"
        "Output markdown only, with these sections:\n"
        "- Verdict\n"
        "- Context Used\n"
        "- Findings\n"
        "- Top Risks\n"
        "- Open Questions\n\n"
        f"Target document: {input_path}\n\n"
        "Document contents with line numbers:\n\n"
        f"{numbered_doc}\n"
        f"{chr(10) * 2 if context_files else ''}"
        f"{format_context_files(context_files) if context_files else ''}\n"
    )


def build_codex_command(
    *,
    cwd: Path,
    output_path: Path,
    profile: str | None,
    model: str | None,
    sandbox: str,
    skip_git_repo_check: bool,
) -> list[str]:
    command = ["codex", "exec", "-C", str(cwd), "-o", str(output_path), "--sandbox", sandbox]
    if profile:
        command.extend(["--profile", profile])
    if model:
        command.extend(["--model", model])
    if skip_git_repo_check:
        command.append("--skip-git-repo-check")
    command.append("-")
    return command


def build_claude_command(*, executable: str, model: str | None) -> list[str]:
    command = [
        executable,
        "-p",
        "Follow the full review instructions provided on stdin. Output markdown only.",
        "--output-format",
        "text",
        "--permission-mode",
        "plan",
    ]
    if model:
        command.extend(["--model", model])
    return command


def validate_command_template(template: str) -> None:
    for token in ("{prompt_file}", "{output_file}"):
        if token not in template:
            raise SystemExit(f"--command-template must include {token}")


def run_codex(args: argparse.Namespace, prompt: str, *, cwd: Path, output_path: Path, skip_git_repo_check: bool) -> int:
    resolve_executable("codex")
    model = args.model or resolve_default_codex_model()
    if os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED") == "1":
        raise SystemExit(
            "review_doc codex backend cannot run from this sandboxed session because outbound network is disabled "
            "(CODEX_SANDBOX_NETWORK_DISABLED=1). Run the script from a normal shell, or use --backend prompt-only."
        )
    command = build_codex_command(
        cwd=cwd,
        output_path=output_path,
        profile=args.profile,
        model=model,
        sandbox=args.sandbox,
        skip_git_repo_check=skip_git_repo_check or args.skip_git_repo_check,
    )
    if args.print_command:
        print(" ".join(shlex.quote(part) for part in command), file=sys.stderr)
    completed = subprocess.run(command, input=prompt, text=True, cwd=str(cwd))
    return completed.returncode


def run_claude(args: argparse.Namespace, prompt: str, *, cwd: Path, output_path: Path) -> int:
    command = build_claude_command(
        executable=resolve_executable("claude"),
        model=args.model,
    )
    if args.print_command:
        print(" ".join(shlex.quote(part) for part in command), file=sys.stderr)
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        cwd=str(cwd),
        capture_output=True,
    )
    if completed.returncode == 0:
        output_path.write_text(completed.stdout, encoding="utf-8")
    else:
        if completed.stdout:
            sys.stdout.write(completed.stdout)
        if completed.stderr:
            sys.stderr.write(completed.stderr)
    return completed.returncode


def run_external(args: argparse.Namespace, prompt: str, *, cwd: Path, input_path: Path, output_path: Path) -> int:
    if not args.command_template:
        raise SystemExit("--command-template is required when --backend external")
    validate_command_template(args.command_template)
    with tempfile.TemporaryDirectory(prefix="doc_review_") as tmpdir:
        prompt_path = Path(tmpdir) / "review_prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        rendered = args.command_template.format(
            prompt_file=str(prompt_path),
            output_file=str(output_path),
            input_file=str(input_path),
            mode=args.mode,
            cwd=str(cwd),
        )
        command = shlex.split(rendered)
        if args.print_command:
            print(" ".join(shlex.quote(part) for part in command), file=sys.stderr)
        completed = subprocess.run(command, cwd=str(cwd))
    return completed.returncode


def run_prompt_only(prompt: str, *, output_path: Path) -> int:
    output_path.write_text(prompt, encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = resolve_input_path(args.input)
    context_files = resolve_context_files(args.context_file)
    if args.context_level == "doc-only" and context_files:
        raise SystemExit("--context-file cannot be used with --context-level doc-only")
    if args.context_level == "targeted" and not context_files:
        raise SystemExit("--context-level targeted requires at least one --context-file")
    if args.context_level == "repo" and context_files:
        raise SystemExit("--context-file is only supported with --context-level targeted")
    output_path = Path(args.output).expanduser().resolve() if args.output else default_output_path(input_path, args.mode)
    cwd, inferred_skip_git = resolve_cwd(input_path, args.cwd, args.context_level)
    prompt = build_prompt(
        input_path=input_path,
        mode=args.mode,
        context_level=args.context_level,
        context_files=context_files,
        extra_instructions=args.extra_instruction,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.backend == "codex":
        return run_codex(args, prompt, cwd=cwd, output_path=output_path, skip_git_repo_check=inferred_skip_git)
    if args.backend == "claude":
        return run_claude(args, prompt, cwd=cwd, output_path=output_path)
    if args.backend == "external":
        return run_external(args, prompt, cwd=cwd, input_path=input_path, output_path=output_path)
    if args.backend == "prompt-only":
        return run_prompt_only(prompt, output_path=output_path)
    raise SystemExit(f"unsupported backend: {args.backend}")


if __name__ == "__main__":
    raise SystemExit(main())
