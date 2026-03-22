#!/usr/bin/env python3
"""Run adversarial document reviews via Codex or an external reviewer CLI."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
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

SEVERITY_ORDER = {
    "critical": 0,
    "major": 1,
    "minor": 2,
    "nit": 3,
}

BUNDLE_PRESETS = {
    "design-ui": {
        "name": "design-ui",
        "reviewers": [
            {
                "name": "visual_design_critique",
                "provider": "codex",
                "model": "gpt-5.4",
                "lens": (
                    "Review the document as a visual and interaction design critic. Focus on hierarchy, layout, typography, "
                    "spacing, motion, tone, originality, and whether the design feels intentional or generic."
                ),
            },
            {
                "name": "ux_product_critique",
                "provider": "codex",
                "model": "gpt-5.4",
                "lens": (
                    "Review the document as a UX and product critic. Focus on task flows, information architecture, edge cases, "
                    "empty/error/loading states, and whether the workflows hold up under scrutiny."
                ),
            },
            {
                "name": "implementation_system_reality",
                "provider": "opus",
                "model": "opus",
                "lens": (
                    "Review the document as an implementation and systems critic. Focus on feasibility, accessibility, "
                    "component complexity, design-system fit, and where the design is likely to break when built. "
                    "Cross-reference the provided context files when useful."
                ),
            },
        ],
    },
    "frontend-hld": {
        "name": "frontend-hld",
        "reviewers": [
            {
                "name": "client_architecture_shape",
                "provider": "codex",
                "model": "gpt-5.4",
                "lens": (
                    "Review the document as a frontend architecture critic. Focus on app-shell shape, route or surface boundaries, "
                    "state ownership, component and module seams, interaction model coherence, and whether the client-side architecture "
                    "is likely to stay understandable as the product grows."
                ),
            },
            {
                "name": "experience_state_coverage",
                "provider": "codex",
                "model": "gpt-5.4",
                "lens": (
                    "Review the document as a user-experience systems critic. Focus on loading, empty, error, offline, permission, "
                    "responsive, accessibility, and perceived-performance states. Check whether the HLD covers the real user-facing "
                    "state model instead of only the happy path."
                ),
            },
            {
                "name": "system_contracts_and_delivery",
                "provider": "opus",
                "model": "opus",
                "lens": (
                    "Review the document as a frontend system-reality critic. Focus on backend and API contracts, data flow, caching, "
                    "synchronization, performance budgets, rollout constraints, observability, and where the proposed frontend architecture "
                    "is likely to break when integrated with real systems. Cross-reference the provided context files when useful."
                ),
            },
        ],
    },
    "frontend-lld": {
        "name": "frontend-lld",
        "reviewers": [
            {
                "name": "client_contracts_and_state_machine",
                "provider": "codex",
                "model": "gpt-5.4",
                "lens": (
                    "Review the document as a frontend low-level design critic. Focus on concrete client contracts, state-machine "
                    "correctness, schema and type boundaries, ownership of state and side effects, persistence semantics, and places "
                    "where the LLD remains ambiguous enough that implementers could diverge."
                ),
            },
            {
                "name": "interaction_and_edge_state_coverage",
                "provider": "codex",
                "model": "gpt-5.4",
                "lens": (
                    "Review the document as a detailed interaction-state critic. Focus on loading, empty, error, offline, reconnect, "
                    "stale-data, permission, responsive, and accessibility states, but only when they materially affect LLD behavior, "
                    "ownership, or state transitions."
                ),
            },
            {
                "name": "integration_and_delivery_reality",
                "provider": "opus",
                "model": "opus",
                "lens": (
                    "Review the document as an integration and delivery critic. Focus on backend/API alignment, streaming or persistence "
                    "contracts, performance and caching assumptions, rollout constraints, observability, and where the proposed LLD is "
                    "likely to fail in real integration. Cross-reference the provided context files when useful."
                ),
            },
        ],
    },
}

REREVIEW_CATEGORY_TITLES = {
    "must_fix_now": "Must Fix Now",
    "defer_to_other_doc": "Defer To Other Doc",
    "needs_prototype": "Needs Prototype",
}


@dataclass(frozen=True)
class BundleReviewer:
    name: str
    provider: str
    lens: str
    model: str


@dataclass
class ParsedFinding:
    severity: str
    location: str
    issue: str
    suggested_fix: str
    reviewers: list[str]
    category: str | None = None


@dataclass
class ParsedAuditItem:
    status: str
    original_issue: str
    current_assessment: str
    still_needed: str
    reviewer: str


@dataclass(frozen=True)
class ContextArtifact:
    label: str
    body: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Run an adversarial review against a design, requirements, or investigation document. "
            "The tool can run either a single reviewer or a blind cross-provider design bundle."
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
            "  python3 scripts/review_doc.py --input docs/design_doc.md --bundle-preset design-ui \\\n"
            "    --context-file docs/requirements.md --context-file docs/system_constraints.md\n"
            "  python3 scripts/review_doc.py --input docs/design_doc.md --bundle-preset design-ui --list-reviewers\n"
            "  python3 scripts/review_doc.py --input docs/frontend_hld.md --bundle-preset frontend-hld \\\n"
            "    --context-file docs/product_requirements.md --context-file docs/backend_hld.md\n"
            "  python3 scripts/review_doc.py --input docs/arrangement_engine_lld.md --bundle-preset frontend-lld \\\n"
            "    --parent-doc docs/frontend_hld.md --parent-review-dir docs/frontend_hld.rereviews.frontend_hld \\\n"
            "    --context-file docs/backend_persistence_design.md\n"
            "  python3 scripts/review_doc.py --input docs/design_doc.md --bundle-preset design-ui \\\n"
            "    --rereview-from docs/design_doc.reviews.design-ui --reviewer implementation_system_reality\n"
            "  python3 scripts/review_doc.py --input docs/my_doc.md --mode generic --backend external \\\n"
            "    --command-template 'my-review-cli --prompt-file {prompt_file} --output {output_file}'\n\n"
            "Modes:\n"
            "  hld            Critique architecture, ownership seams, failure modes, rollout, and system fit.\n"
            "  lld            Critique schemas, APIs, state machines, migrations, and implementation contracts.\n"
            "  requirements   Critique missing, ambiguous, or untestable requirements.\n"
            "  investigation  Critique unsupported conclusions, evidence quality, and open questions.\n"
            "  generic        Use the general adversarial review prompt.\n"
            "Bundle presets:\n"
            "  design-ui      Run 3 blind reviewers in parallel: visual design (codex), UX/product (codex),\n"
            "                 and implementation/system reality (opus), all at high effort.\n"
            "                 Reviewers: visual_design_critique, ux_product_critique,\n"
            "                 implementation_system_reality.\n"
            "  frontend-hld   Run 3 blind reviewers in parallel for frontend high-level design: client\n"
            "                 architecture shape (codex), experience state coverage (codex), and system\n"
            "                 contracts/delivery reality (opus), all at high effort.\n"
            "                 Reviewers: client_architecture_shape, experience_state_coverage,\n"
            "                 system_contracts_and_delivery.\n"
            "  frontend-lld   Run 3 blind reviewers in parallel for frontend low-level design: client\n"
            "                 contracts/state machine (codex), interaction/edge-state coverage (codex),\n"
            "                 and integration/delivery reality (opus), all at high effort.\n"
            "                 Reviewers: client_contracts_and_state_machine,\n"
            "                 interaction_and_edge_state_coverage, integration_and_delivery_reality.\n"
            "Context levels:\n"
            "  doc-only       Review only the input document. This is the default.\n"
            "  targeted       Review the document plus only the files passed via --context-file.\n"
            "  repo           Allow selective repository inspection from the working directory.\n"
        ),
    )
    parser.add_argument("--input", required=True, help="Path to the document to review.")
    parser.add_argument(
        "--bundle-preset",
        choices=tuple(sorted(BUNDLE_PRESETS)),
        help=(
            "Run a blind multi-review bundle instead of a single review. "
            "Available presets include design-ui and frontend-hld."
        ),
    )
    parser.add_argument(
        "--bundle-config",
        help=(
            "Path to a TOML bundle config that defines exactly 3 reviewers. "
            "Use instead of --bundle-preset for custom cross-provider review bundles."
        ),
    )
    parser.add_argument(
        "--output-dir",
        help=(
            "Directory for bundle reviewer outputs and the combined summary. "
            "Only used with --bundle-preset or --bundle-config."
        ),
    )
    parser.add_argument(
        "--reviewer",
        action="append",
        default=[],
        help=(
            "In bundle mode, run only the named reviewer. Repeat to run a subset. "
            "Names match the bundle reviewer names. Use --list-reviewers to print the exact names "
            "for the selected preset."
        ),
    )
    parser.add_argument(
        "--list-reviewers",
        action="store_true",
        help=(
            "In bundle mode, print the configured reviewer names and exit. "
            "Useful with --bundle-preset or --bundle-config."
        ),
    )
    parser.add_argument(
        "--rereview-from",
        help=(
            "In bundle mode, run a revision audit against a previous review directory. "
            "The tool will load the prior lane output and prior summary automatically."
        ),
    )
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
    parser.add_argument(
        "--parent-doc",
        action="append",
        default=[],
        help=(
            "Bundle-mode convenience flag for higher-level design docs that this document must align with. "
            "These files are embedded like context files."
        ),
    )
    parser.add_argument(
        "--adjacent-doc",
        action="append",
        default=[],
        help=(
            "Bundle-mode convenience flag for sibling docs or neighboring LLDs whose contracts should be checked "
            "for consistency. These files are embedded like context files."
        ),
    )
    parser.add_argument(
        "--parent-review-dir",
        action="append",
        default=[],
        help=(
            "Bundle-mode convenience flag for prior review or rereview directories whose summaries should be embedded "
            "as review context."
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


def resolve_directory_paths(values: list[str], *, label: str) -> list[Path]:
    resolved: list[Path] = []
    for value in values:
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise SystemExit(f"{label} directory not found: {path}")
        resolved.append(path)
    return resolved


def resolve_context_artifacts(
    *,
    context_files: list[Path],
    parent_docs: list[Path],
    adjacent_docs: list[Path],
    parent_review_dirs: list[Path],
) -> list[ContextArtifact]:
    artifacts: list[ContextArtifact] = []
    for path in context_files:
        artifacts.append(ContextArtifact(label=f"Context file: {path}", body=number_lines(read_text(path))))
    for path in parent_docs:
        artifacts.append(ContextArtifact(label=f"Parent document: {path}", body=number_lines(read_text(path))))
    for path in adjacent_docs:
        artifacts.append(ContextArtifact(label=f"Adjacent document: {path}", body=number_lines(read_text(path))))
    for review_dir in parent_review_dirs:
        rereview_summary = summary_path_for_bundle(review_dir, rereview=True)
        review_summary = summary_path_for_bundle(review_dir)
        summary_path = rereview_summary if rereview_summary.is_file() else review_summary
        if not summary_path.is_file():
            raise SystemExit(f"review summary not found in parent review dir: {review_dir}")
        artifacts.append(
            ContextArtifact(
                label=f"Parent review summary: {summary_path}",
                body=number_lines(read_text(summary_path)),
            )
        )
    return artifacts


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def number_lines(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return "   1 |"
    return "\n".join(f"{index + 1:4d} | {line}" for index, line in enumerate(lines))


def format_context_artifacts(artifacts: list[ContextArtifact]) -> str:
    sections: list[str] = []
    for artifact in artifacts:
        sections.append(
            "\n".join(
                [
                    artifact.label,
                    "",
                    artifact.body,
                ]
            )
        )
    return "\n\n".join(sections)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "reviewer"


def output_path_for_reviewer(output_dir: Path, reviewer: BundleReviewer, *, rereview: bool = False) -> Path:
    suffix = ".rereview.md" if rereview else ".md"
    return output_dir / f"{slugify(reviewer.name)}.{reviewer.provider}{suffix}"


def summary_path_for_bundle(output_dir: Path, *, rereview: bool = False) -> Path:
    return output_dir / ("summary.rereview.md" if rereview else "summary.md")


def resolve_bundle_output_dir(input_path: Path, requested: str | None, bundle_name: str, *, rereview: bool = False) -> Path:
    if requested:
        return Path(requested).expanduser().resolve()
    kind = "rereviews" if rereview else "reviews"
    return input_path.parent / f"{input_path.stem}.{kind}.{slugify(bundle_name)}"


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def normalize_heading(value: str) -> str:
    return normalize_text(value.lstrip("#").strip())


def significant_tokens(value: str) -> set[str]:
    stop_words = {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "into",
        "does",
        "not",
        "are",
        "below",
        "above",
        "main",
        "page",
        "section",
    }
    return {
        token
        for token in normalize_text(value).split()
        if len(token) >= 4 and token not in stop_words
    }


def severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.get(severity.lower(), len(SEVERITY_ORDER))


def choose_higher_severity(left: str, right: str) -> str:
    return left if severity_rank(left) <= severity_rank(right) else right


def parse_bundle_config(path: Path) -> tuple[str, list[BundleReviewer]]:
    if tomllib is None:
        raise SystemExit("bundle config requires Python 3.11+ with tomllib support")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"bundle config not found: {path}") from exc
    except Exception as exc:  # pragma: no cover - parse message depends on stdlib
        raise SystemExit(f"failed to parse bundle config {path}: {exc}") from exc

    name = str(payload.get("name") or path.stem).strip() or path.stem
    reviewers_payload = payload.get("reviewers")
    if not isinstance(reviewers_payload, list):
        raise SystemExit("bundle config must define [[reviewers]] entries")

    reviewers: list[BundleReviewer] = []
    for index, item in enumerate(reviewers_payload, start=1):
        if not isinstance(item, dict):
            raise SystemExit(f"bundle reviewer #{index} must be a table")
        provider = str(item.get("provider") or "").strip().lower()
        if provider not in {"codex", "opus"}:
            raise SystemExit(f"bundle reviewer #{index} has unsupported provider: {provider or '<empty>'}")
        lens = str(item.get("lens") or "").strip()
        if not lens:
            raise SystemExit(f"bundle reviewer #{index} is missing lens")
        reviewer_name = str(item.get("name") or f"reviewer_{index}").strip()
        model = str(item.get("model") or ("gpt-5.4" if provider == "codex" else "opus")).strip()
        reviewers.append(BundleReviewer(name=reviewer_name, provider=provider, lens=lens, model=model))

    if len(reviewers) != 3:
        raise SystemExit(f"bundle config must define exactly 3 reviewers, found {len(reviewers)}")
    return name, reviewers


def resolve_bundle_reviewers(args: argparse.Namespace) -> tuple[str, list[BundleReviewer]]:
    if args.bundle_config:
        return parse_bundle_config(Path(args.bundle_config).expanduser().resolve())
    if args.bundle_preset in BUNDLE_PRESETS:
        preset = BUNDLE_PRESETS[args.bundle_preset]
        return (
            str(preset["name"]),
            [
                BundleReviewer(
                    name=str(item["name"]),
                    provider=str(item["provider"]),
                    lens=str(item["lens"]),
                    model=str(item["model"]),
                )
                for item in preset["reviewers"]
            ],
        )
    raise SystemExit("bundle mode requires --bundle-preset or --bundle-config")


def select_bundle_reviewers(reviewers: list[BundleReviewer], selected_names: list[str]) -> list[BundleReviewer]:
    if not selected_names:
        return reviewers
    requested = {slugify(name) for name in selected_names}
    selected = [reviewer for reviewer in reviewers if reviewer.name in selected_names or slugify(reviewer.name) in requested]
    if not selected:
        available = ", ".join(reviewer.name for reviewer in reviewers)
        raise SystemExit(f"no matching reviewers for --reviewer. Available reviewers: {available}")
    missing = [
        name for name in selected_names
        if slugify(name) not in {slugify(reviewer.name) for reviewer in selected}
    ]
    if missing:
        available = ", ".join(reviewer.name for reviewer in reviewers)
        raise SystemExit(
            f"unknown reviewer name(s): {', '.join(missing)}. Available reviewers: {available}"
        )
    return selected


def print_bundle_reviewers(bundle_name: str, reviewers: list[BundleReviewer]) -> None:
    print(f"Bundle preset: {bundle_name}")
    for reviewer in reviewers:
        print(f"- {reviewer.name} ({reviewer.provider}, model={reviewer.model})")


def reviewer_descriptor(reviewer: BundleReviewer) -> str:
    return f"{reviewer.name} ({reviewer.provider}, model={reviewer.model})"


def resolve_rereview_dir(path_value: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    if not path.is_dir():
        raise SystemExit(f"rereview directory not found: {path}")
    return path


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
        f"{format_context_artifacts(resolve_context_artifacts(context_files=context_files, parent_docs=[], adjacent_docs=[], parent_review_dirs=[])) if context_files else ''}\n"
    )


def build_bundle_prompt(
    *,
    input_path: Path,
    reviewer: BundleReviewer,
    context_artifacts: list[ContextArtifact],
) -> str:
    numbered_doc = number_lines(read_text(input_path))
    context_block = ""
    if context_artifacts:
        context_block = (
            "\nExplicit context artifacts available to you and embedded below:\n"
            f"{chr(10).join(f'- {artifact.label}' for artifact in context_artifacts)}\n"
            f"\n{format_context_artifacts(context_artifacts)}\n"
        )
    return (
        "You are performing a blind adversarial review of a product or UI design document.\n\n"
        f"Reviewer name: {reviewer.name}\n"
        f"Provider role: {reviewer.provider}\n\n"
        "This review must be independent. You are not allowed to see or assume the output of any other reviewer.\n"
        "Use only the target document and the explicit context files embedded in this prompt. Do not inspect other repository files.\n\n"
        f"Your lens:\n{reviewer.lens}\n\n"
        "Review instructions:\n"
        "- Read the full document carefully before writing findings.\n"
        "- Be adversarial. The goal is to find problems, not praise the document.\n"
        "- Limit yourself to the top 15 findings, ranked by severity.\n"
        "- Prefer concrete, high-signal findings over generic commentary.\n"
        "- Use section references when possible. If the document has no suitable heading, cite a line range or line number.\n\n"
        "Output markdown only.\n"
        "Do not include a summary paragraph, praise, or filler.\n"
        "Write findings as a numbered list using exactly this field structure for every item:\n\n"
        "1. Severity: critical|major|minor|nit\n"
        "   Location: <section reference>\n"
        "   Issue: <what is wrong and why it matters>\n"
        "   Suggested fix: <specific corrective action>\n\n"
        "Target document:\n"
        f"- {input_path}\n"
        f"{''.join(['- ' + artifact.label + chr(10) for artifact in context_artifacts]) if context_artifacts else ''}\n"
        "Document contents with line numbers:\n\n"
        f"{numbered_doc}\n"
        f"{context_block}"
    )


def build_rereview_prompt(
    *,
    input_path: Path,
    reviewer: BundleReviewer,
    context_artifacts: list[ContextArtifact],
    prior_reviewer_output: str,
    prior_summary: str | None,
) -> str:
    numbered_doc = number_lines(read_text(input_path))
    context_block = ""
    if context_artifacts:
        context_block = (
            "\nExplicit context artifacts available to you and embedded below:\n"
            f"{chr(10).join(f'- {artifact.label}' for artifact in context_artifacts)}\n"
            f"\n{format_context_artifacts(context_artifacts)}\n"
        )
    prior_summary_block = ""
    if prior_summary:
        prior_summary_block = (
            "\nPrior cross-provider summary:\n\n"
            f"{prior_summary}\n"
        )
    return (
        "You are performing a blind adversarial re-review of a revised product or UI design document.\n\n"
        f"Reviewer name: {reviewer.name}\n"
        f"Provider role: {reviewer.provider}\n\n"
        "This is not a fresh first-pass review. Your job is to audit whether the revision resolved prior issues for your lane,\n"
        "identify which important prior concerns remain unresolved, and find any new problems introduced by the revision.\n"
        "Use only the revised target document, the explicit context files embedded in this prompt, your prior lane feedback,\n"
        "and the prior bundle summary if provided. Do not inspect other repository files.\n\n"
        f"Your lens:\n{reviewer.lens}\n\n"
        "Review instructions:\n"
        "- Read the full revised document carefully before writing.\n"
        "- Audit prior findings for your lane first.\n"
        "- Be adversarial. The goal is to detect unresolved issues, weak fixes, regressions, and new gaps.\n"
        "- Do not repeat issues that are clearly fixed.\n"
        "- Limit Must Fix Now, Defer To Other Doc, and Needs Prototype combined to the top 15 items.\n"
        "- Use section references when possible. If the document has no suitable heading, cite a line range or line number.\n\n"
        "Output markdown only, with exactly these sections:\n"
        "- Verdict\n"
        "- Resolution Audit\n"
        "- Must Fix Now\n"
        "- Defer To Other Doc\n"
        "- Needs Prototype\n"
        "- Open Questions\n\n"
        "For each item in Resolution Audit, use exactly this field structure:\n\n"
        "1. Status: resolved|partially_resolved|unresolved|not_applicable\n"
        "   Original issue: <prior issue summary>\n"
        "   Current assessment: <what changed and whether it actually resolves the problem>\n"
        "   What still needs to change: <specific remaining work, or 'none'>\n\n"
        "For each item in Must Fix Now, Defer To Other Doc, and Needs Prototype, use exactly this field structure:\n\n"
        "1. Severity: critical|major|minor|nit\n"
        "   Location: <section reference>\n"
        "   Issue: <what is wrong and why it matters>\n"
        "   Suggested fix: <specific corrective action>\n\n"
        "Classify each unresolved or newly introduced issue into exactly one of those three sections.\n"
        "Do not repeat the same issue across multiple sections.\n\n"
        "Target document:\n"
        f"- {input_path}\n"
        f"{''.join(['- ' + artifact.label + chr(10) for artifact in context_artifacts]) if context_artifacts else ''}\n"
        "\nRevised document contents with line numbers:\n\n"
        f"{numbered_doc}\n"
        f"{context_block}"
        "\nPrior lane feedback for this reviewer:\n\n"
        f"{prior_reviewer_output}\n"
        f"{prior_summary_block}"
    )


def parse_review_findings(
    text: str,
    reviewer_name: str,
    *,
    categorized_sections: dict[str, set[str]] | None = None,
) -> list[ParsedFinding]:
    findings: list[ParsedFinding] = []
    current: ParsedFinding | None = None
    current_field: str | None = None
    current_category: str | None = None

    def finalize() -> None:
        nonlocal current
        if current is None:
            return
        current.location = current.location.strip()
        current.issue = current.issue.strip()
        current.suggested_fix = current.suggested_fix.strip()
        if current.location and current.issue and current.suggested_fix:
            findings.append(current)
        current = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if categorized_sections is not None and stripped.startswith("#"):
            heading = normalize_heading(stripped)
            current_category = None
            for category, aliases in categorized_sections.items():
                if heading in aliases:
                    current_category = category
                    break
            continue
        match = re.match(r"^\s*\d+\.\s+Severity:\s*(critical|major|minor|nit)\s*$", line, re.IGNORECASE)
        if match:
            finalize()
            current = ParsedFinding(
                severity=match.group(1).lower(),
                location="",
                issue="",
                suggested_fix="",
                reviewers=[reviewer_name],
                category=current_category,
            )
            current_field = None
            continue
        if current is None:
            continue

        if stripped.startswith("Location:"):
            current.location = stripped.partition(":")[2].strip()
            current_field = "location"
            continue
        if stripped.startswith("Issue:"):
            current.issue = stripped.partition(":")[2].strip()
            current_field = "issue"
            continue
        if stripped.startswith("Suggested fix:"):
            current.suggested_fix = stripped.partition(":")[2].strip()
            current_field = "suggested_fix"
            continue
        if not stripped:
            current_field = None
            continue
        if current_field == "location":
            current.location = f"{current.location} {stripped}".strip()
        elif current_field == "issue":
            current.issue = f"{current.issue} {stripped}".strip()
        elif current_field == "suggested_fix":
            current.suggested_fix = f"{current.suggested_fix} {stripped}".strip()

    finalize()
    return findings


def parse_rereview_findings(text: str, reviewer_name: str) -> list[ParsedFinding]:
    categorized_sections = {
        "must_fix_now": {
            "must fix now",
            "remaining issues",
            "new regressions",
        },
        "defer_to_other_doc": {"defer to other doc"},
        "needs_prototype": {"needs prototype"},
    }
    return parse_review_findings(text, reviewer_name, categorized_sections=categorized_sections)


def parse_rereview_audit_items(text: str, reviewer_name: str) -> list[ParsedAuditItem]:
    items: list[ParsedAuditItem] = []
    current: ParsedAuditItem | None = None
    current_field: str | None = None
    in_resolution_audit = False

    def finalize() -> None:
        nonlocal current
        if current is None:
            return
        current.original_issue = current.original_issue.strip()
        current.current_assessment = current.current_assessment.strip()
        current.still_needed = current.still_needed.strip()
        if current.original_issue and current.current_assessment and current.still_needed:
            items.append(current)
        current = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        heading = stripped.lstrip("#").strip().lower()
        if heading == "resolution audit":
            in_resolution_audit = True
            finalize()
            continue
        if stripped.startswith("#") and heading != "resolution audit":
            if in_resolution_audit:
                finalize()
            in_resolution_audit = False
        if not in_resolution_audit:
            continue
        match = re.match(
            r"^\s*\d+\.\s+Status:\s*(resolved|partially_resolved|unresolved|not_applicable)\s*$",
            line,
            re.IGNORECASE,
        )
        if match:
            finalize()
            current = ParsedAuditItem(
                status=match.group(1).lower(),
                original_issue="",
                current_assessment="",
                still_needed="",
                reviewer=reviewer_name,
            )
            current_field = None
            continue
        if current is None:
            continue
        if stripped.startswith("Original issue:"):
            current.original_issue = stripped.partition(":")[2].strip()
            current_field = "original_issue"
            continue
        if stripped.startswith("Current assessment:"):
            current.current_assessment = stripped.partition(":")[2].strip()
            current_field = "current_assessment"
            continue
        if stripped.startswith("What still needs to change:"):
            current.still_needed = stripped.partition(":")[2].strip()
            current_field = "still_needed"
            continue
        if not stripped:
            current_field = None
            continue
        if current_field == "original_issue":
            current.original_issue = f"{current.original_issue} {stripped}".strip()
        elif current_field == "current_assessment":
            current.current_assessment = f"{current.current_assessment} {stripped}".strip()
        elif current_field == "still_needed":
            current.still_needed = f"{current.still_needed} {stripped}".strip()

    finalize()
    return items


def findings_are_duplicates(left: ParsedFinding, right: ParsedFinding) -> bool:
    left_issue = normalize_text(left.issue)
    right_issue = normalize_text(right.issue)
    if not left_issue or not right_issue:
        return False
    issue_similarity = SequenceMatcher(None, left_issue, right_issue).ratio()
    if normalize_text(left.location) == normalize_text(right.location):
        if issue_similarity >= 0.65:
            return True
        left_tokens = significant_tokens(left.issue)
        right_tokens = significant_tokens(right.issue)
        if left_tokens and right_tokens:
            overlap = len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
            if overlap >= 0.4:
                return True
    return issue_similarity >= 0.88


def dedupe_findings(findings: list[ParsedFinding]) -> list[ParsedFinding]:
    merged: list[ParsedFinding] = []
    for finding in findings:
        for existing in merged:
            if existing.category != finding.category:
                continue
            if findings_are_duplicates(existing, finding):
                existing.severity = choose_higher_severity(existing.severity, finding.severity)
                if len(finding.issue) > len(existing.issue):
                    existing.issue = finding.issue
                if len(finding.suggested_fix) > len(existing.suggested_fix):
                    existing.suggested_fix = finding.suggested_fix
                if len(finding.location) > len(existing.location):
                    existing.location = finding.location
                for reviewer in finding.reviewers:
                    if reviewer not in existing.reviewers:
                        existing.reviewers.append(reviewer)
                break
        else:
            merged.append(
                ParsedFinding(
                    severity=finding.severity,
                    location=finding.location,
                    issue=finding.issue,
                    suggested_fix=finding.suggested_fix,
                    reviewers=list(finding.reviewers),
                    category=finding.category,
                )
            )
    merged.sort(
        key=lambda item: (
            item.category or "",
            severity_rank(item.severity),
            normalize_text(item.location),
            normalize_text(item.issue),
        )
    )
    return merged


def build_bundle_summary(
    *,
    input_path: Path,
    bundle_name: str,
    reviewers: list[BundleReviewer],
    parsed_findings: list[ParsedFinding],
    audit_items: list[ParsedAuditItem],
    failed_reviewers: list[str],
    missing_reviewers: list[str],
    rereview: bool,
) -> str:
    reviewer_map = {reviewer.name: reviewer for reviewer in reviewers}
    title = "Cross-Provider Re-Review Summary" if rereview else "Cross-Provider Review Summary"
    lines = [
        f"# {title}: {bundle_name}",
        "",
        f"- Target document: {input_path}",
        f"- Reviewers: {', '.join(reviewer_descriptor(reviewer) for reviewer in reviewers)}",
    ]
    if failed_reviewers:
        lines.append(f"- Failed reviewers: {', '.join(failed_reviewers)}")
    if missing_reviewers:
        lines.append(f"- Missing reviewer outputs: {', '.join(missing_reviewers)}")
    if rereview:
        lines.extend(["", "## Resolution Audit Counts", ""])
        for reviewer in reviewers:
            reviewer_items = [item for item in audit_items if item.reviewer == reviewer.name]
            counts = {
                "resolved": sum(1 for item in reviewer_items if item.status == "resolved"),
                "partially_resolved": sum(1 for item in reviewer_items if item.status == "partially_resolved"),
                "unresolved": sum(1 for item in reviewer_items if item.status == "unresolved"),
                "not_applicable": sum(1 for item in reviewer_items if item.status == "not_applicable"),
            }
            lines.append(
                f"- {reviewer_descriptor(reviewer)}: "
                f"resolved={counts['resolved']}, "
                f"partially_resolved={counts['partially_resolved']}, "
                f"unresolved={counts['unresolved']}, "
                f"not_applicable={counts['not_applicable']}"
            )
        for category, heading in REREVIEW_CATEGORY_TITLES.items():
            category_items = [item for item in parsed_findings if item.category == category]
            lines.extend(["", f"## {heading}", ""])
            if not category_items:
                lines.append(f"No items classified as {heading.lower()}.")
                continue
            for index, item in enumerate(category_items, start=1):
                reviewers_str = ", ".join(
                    reviewer_descriptor(reviewer_map[name]) if name in reviewer_map else name
                    for name in item.reviewers
                )
                lines.extend(
                    [
                        f"{index}. Lanes: {reviewers_str}",
                        f"   Severity: {item.severity}",
                        f"   Location: {item.location}",
                        f"   Issue: {item.issue}",
                        f"   Suggested fix: {item.suggested_fix}",
                        "",
                    ]
                )
        resolved_items = [item for item in audit_items if item.status == "resolved"]
        lines.extend(["", "## Resolved", ""])
        if not resolved_items:
            lines.append("No prior findings were fully resolved.")
        else:
            for index, item in enumerate(resolved_items, start=1):
                lines.extend(
                    [
                        f"{index}. Lane: {item.reviewer}",
                        f"   Original issue: {item.original_issue}",
                        f"   Current assessment: {item.current_assessment}",
                        "",
                    ]
                )
    lines.extend(["", "## By Review Lane", ""])

    for reviewer in reviewers:
        lane_items = [item for item in parsed_findings if reviewer.name in item.reviewers]
        lines.extend([f"### {reviewer_descriptor(reviewer)}", ""])
        if not lane_items:
            lines.append("No findings attributed to this reviewer lane.")
            lines.append("")
            continue
        for index, item in enumerate(lane_items, start=1):
            corroborating = [name for name in item.reviewers if name != reviewer.name]
            corroborating_text = ", ".join(corroborating) if corroborating else "none"
            lines.extend(
                [
                    f"{index}. Severity: {item.severity}",
                    *(
                        [f"   Bucket: {REREVIEW_CATEGORY_TITLES.get(item.category, 'Uncategorized')}"]
                        if rereview
                        else []
                    ),
                    f"   Location: {item.location}",
                    f"   Issue: {item.issue}",
                    f"   Suggested fix: {item.suggested_fix}",
                    f"   Corroborated by: {corroborating_text}",
                    "",
                ]
            )

    lines.extend(["## By Severity", ""])
    if not parsed_findings:
        lines.append("No remaining or new findings were produced.")
        return "\n".join(lines) + "\n"
    for severity in ("critical", "major", "minor", "nit"):
        severity_items = [item for item in parsed_findings if item.severity == severity]
        if not severity_items:
            continue
        lines.extend([f"## {severity.title()}", ""])
        for index, item in enumerate(severity_items, start=1):
            reviewers_str = ", ".join(
                reviewer_descriptor(reviewer_map[name]) if name in reviewer_map else name
                for name in item.reviewers
            )
            lines.extend(
                [
                    f"{index}. Lanes: {reviewers_str}",
                    f"   Location: {item.location}",
                    f"   Issue: {item.issue}",
                    f"   Suggested fix: {item.suggested_fix}",
                    "",
                ]
            )
    return "\n".join(lines)


def build_codex_command(
    *,
    cwd: Path,
    output_path: Path,
    profile: str | None,
    model: str | None,
    reasoning_effort: str | None,
    sandbox: str,
    skip_git_repo_check: bool,
) -> list[str]:
    command = ["codex", "exec", "-C", str(cwd), "-o", str(output_path), "--sandbox", sandbox]
    if profile:
        command.extend(["--profile", profile])
    if model:
        command.extend(["--model", model])
    if reasoning_effort:
        command.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])
    if skip_git_repo_check:
        command.append("--skip-git-repo-check")
    command.append("-")
    return command


def build_claude_command(*, executable: str, model: str | None, effort: str | None = None) -> list[str]:
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
    if effort:
        command.extend(["--effort", effort])
    return command


def validate_command_template(template: str) -> None:
    for token in ("{prompt_file}", "{output_file}"):
        if token not in template:
            raise SystemExit(f"--command-template must include {token}")


def run_codex(
    args: argparse.Namespace,
    prompt: str,
    *,
    cwd: Path,
    output_path: Path,
    skip_git_repo_check: bool,
    model_override: str | None = None,
    reasoning_effort: str | None = None,
) -> int:
    resolve_executable("codex")
    model = model_override or args.model or resolve_default_codex_model()
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
        reasoning_effort=reasoning_effort,
        sandbox=args.sandbox,
        skip_git_repo_check=skip_git_repo_check or args.skip_git_repo_check,
    )
    if args.print_command:
        print(" ".join(shlex.quote(part) for part in command), file=sys.stderr)
    completed = subprocess.run(command, input=prompt, text=True, cwd=str(cwd))
    return completed.returncode


def run_claude(
    args: argparse.Namespace,
    prompt: str,
    *,
    cwd: Path,
    output_path: Path,
    model_override: str | None = None,
    effort: str | None = None,
) -> int:
    command = build_claude_command(
        executable=resolve_executable("claude"),
        model=model_override or args.model,
        effort=effort,
    )
    if args.print_command:
        print(" ".join(shlex.quote(part) for part in command), file=sys.stderr)
    worker_env = {key: value for key, value in os.environ.items() if key != "ANTHROPIC_API_KEY"}
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        cwd=str(cwd),
        capture_output=True,
        env=worker_env,
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


def run_bundle_reviewer(
    args: argparse.Namespace,
    *,
    input_path: Path,
    reviewer: BundleReviewer,
    context_artifacts: list[ContextArtifact],
    cwd: Path,
    output_dir: Path,
    skip_git_repo_check: bool,
    rereview_dir: Path | None,
) -> tuple[str, Path, int]:
    if rereview_dir is not None:
        prior_output_path = output_path_for_reviewer(rereview_dir, reviewer)
        if not prior_output_path.is_file():
            raise SystemExit(f"prior reviewer output not found for {reviewer.name}: {prior_output_path}")
        prior_summary_path = summary_path_for_bundle(rereview_dir)
        prior_summary = read_text(prior_summary_path) if prior_summary_path.is_file() else None
        prompt = build_rereview_prompt(
            input_path=input_path,
            reviewer=reviewer,
            context_artifacts=context_artifacts,
            prior_reviewer_output=read_text(prior_output_path),
            prior_summary=prior_summary,
        )
    else:
        prompt = build_bundle_prompt(input_path=input_path, reviewer=reviewer, context_artifacts=context_artifacts)
    output_path = output_path_for_reviewer(output_dir, reviewer, rereview=rereview_dir is not None)
    if reviewer.provider == "codex":
        return (
            reviewer.name,
            output_path,
            run_codex(
                args,
                prompt,
                cwd=cwd,
                output_path=output_path,
                skip_git_repo_check=skip_git_repo_check,
                model_override=reviewer.model,
                reasoning_effort="high",
            ),
        )
    return (
        reviewer.name,
        output_path,
        run_claude(
            args,
            prompt,
            cwd=cwd,
            output_path=output_path,
            model_override=reviewer.model,
            effort="high",
        ),
    )


def run_review_bundle(
    args: argparse.Namespace,
    *,
    input_path: Path,
    context_files: list[Path],
) -> int:
    bundle_name, configured_reviewers = resolve_bundle_reviewers(args)
    reviewers_to_run = select_bundle_reviewers(configured_reviewers, args.reviewer)
    rereview_from = getattr(args, "rereview_from", None)
    rereview_dir = resolve_rereview_dir(rereview_from) if rereview_from else None
    parent_docs = resolve_context_files(getattr(args, "parent_doc", []))
    adjacent_docs = resolve_context_files(getattr(args, "adjacent_doc", []))
    parent_review_dirs = resolve_directory_paths(getattr(args, "parent_review_dir", []), label="parent review")
    context_artifacts = resolve_context_artifacts(
        context_files=context_files,
        parent_docs=parent_docs,
        adjacent_docs=adjacent_docs,
        parent_review_dirs=parent_review_dirs,
    )
    output_dir = resolve_bundle_output_dir(
        input_path,
        args.output_dir,
        bundle_name,
        rereview=rereview_dir is not None,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    cwd = input_path.parent
    skip_git_repo_check = not (cwd / ".git").exists()
    results: list[tuple[str, Path, int]] = []

    with ThreadPoolExecutor(max_workers=len(reviewers_to_run)) as executor:
        futures = {
            executor.submit(
                run_bundle_reviewer,
                args,
                input_path=input_path,
                reviewer=reviewer,
                context_artifacts=context_artifacts,
                cwd=cwd,
                output_dir=output_dir,
                skip_git_repo_check=skip_git_repo_check,
                rereview_dir=rereview_dir,
            ): reviewer
            for reviewer in reviewers_to_run
        }
        for future in as_completed(futures):
            results.append(future.result())

    parsed_findings: list[ParsedFinding] = []
    audit_items: list[ParsedAuditItem] = []
    failed_reviewers: list[str] = []
    for reviewer_name, _output_path, returncode in sorted(results, key=lambda item: item[0]):
        if returncode != 0:
            failed_reviewers.append(reviewer_name)

    missing_reviewers: list[str] = []
    for reviewer in configured_reviewers:
        output_path = output_path_for_reviewer(output_dir, reviewer, rereview=rereview_dir is not None)
        if not output_path.is_file():
            missing_reviewers.append(reviewer.name)
            continue
        output_text = read_text(output_path)
        if rereview_dir is not None:
            parsed_findings.extend(parse_rereview_findings(output_text, reviewer.name))
            audit_items.extend(parse_rereview_audit_items(output_text, reviewer.name))
        else:
            parsed_findings.extend(parse_review_findings(output_text, reviewer.name))

    summary_path = summary_path_for_bundle(output_dir, rereview=rereview_dir is not None)
    summary_text = build_bundle_summary(
        input_path=input_path,
        bundle_name=bundle_name,
        reviewers=configured_reviewers,
        parsed_findings=dedupe_findings(parsed_findings),
        audit_items=audit_items,
        failed_reviewers=failed_reviewers,
        missing_reviewers=missing_reviewers,
        rereview=rereview_dir is not None,
    )
    summary_path.write_text(summary_text, encoding="utf-8")

    if failed_reviewers:
        print(f"bundle completed with reviewer failures: {', '.join(failed_reviewers)}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_reviewers:
        if not (args.bundle_preset or args.bundle_config):
            raise SystemExit("--list-reviewers requires --bundle-preset or --bundle-config")
        bundle_name, reviewers = resolve_bundle_reviewers(args)
        print_bundle_reviewers(bundle_name, reviewers)
        return 0
    input_path = resolve_input_path(args.input)
    context_files = resolve_context_files(args.context_file)
    if args.rereview_from and not (args.bundle_preset or args.bundle_config):
        raise SystemExit("--rereview-from requires --bundle-preset or --bundle-config")
    if (args.parent_doc or args.adjacent_doc or args.parent_review_dir) and not (args.bundle_preset or args.bundle_config):
        raise SystemExit("--parent-doc, --adjacent-doc, and --parent-review-dir require bundle mode")
    if args.bundle_preset or args.bundle_config:
        if args.output:
            raise SystemExit("--output cannot be used with bundle mode; use --output-dir instead")
        if args.backend != "codex":
            raise SystemExit("--backend is not used in bundle mode; reviewer providers come from the bundle config")
        if args.context_level == "repo":
            raise SystemExit("bundle mode only supports the target document plus explicit --context-file inputs")
        return run_review_bundle(args, input_path=input_path, context_files=context_files)
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
