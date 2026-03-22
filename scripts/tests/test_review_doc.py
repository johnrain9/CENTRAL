#!/usr/bin/env python3
"""Coverage for review_doc bundle review helpers."""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import review_doc  # type: ignore


class ReviewDocBundleTests(unittest.TestCase):
    def test_design_ui_preset_uses_expected_provider_split(self) -> None:
        args = argparse.Namespace(bundle_config=None, bundle_preset="design-ui")
        bundle_name, reviewers = review_doc.resolve_bundle_reviewers(args)

        self.assertEqual(bundle_name, "design-ui")
        self.assertEqual([reviewer.provider for reviewer in reviewers], ["codex", "codex", "opus"])
        self.assertEqual([reviewer.model for reviewer in reviewers], ["gpt-5.4", "gpt-5.4", "opus"])

    def test_frontend_hld_preset_uses_expected_provider_split(self) -> None:
        args = argparse.Namespace(bundle_config=None, bundle_preset="frontend-hld")
        bundle_name, reviewers = review_doc.resolve_bundle_reviewers(args)

        self.assertEqual(bundle_name, "frontend-hld")
        self.assertEqual(
            [reviewer.name for reviewer in reviewers],
            [
                "client_architecture_shape",
                "experience_state_coverage",
                "system_contracts_and_delivery",
            ],
        )
        self.assertEqual([reviewer.provider for reviewer in reviewers], ["codex", "codex", "opus"])
        self.assertEqual([reviewer.model for reviewer in reviewers], ["gpt-5.4", "gpt-5.4", "opus"])

    def test_frontend_lld_preset_uses_expected_provider_split(self) -> None:
        args = argparse.Namespace(bundle_config=None, bundle_preset="frontend-lld")
        bundle_name, reviewers = review_doc.resolve_bundle_reviewers(args)

        self.assertEqual(bundle_name, "frontend-lld")
        self.assertEqual(
            [reviewer.name for reviewer in reviewers],
            [
                "client_contracts_and_state_machine",
                "interaction_and_edge_state_coverage",
                "integration_and_delivery_reality",
            ],
        )
        self.assertEqual([reviewer.provider for reviewer in reviewers], ["codex", "codex", "opus"])

    def test_parse_bundle_config_requires_exactly_three_reviewers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="review_doc_config_") as tmpdir:
            config_path = Path(tmpdir) / "bundle.toml"
            config_path.write_text(
                "\n".join(
                    [
                        'name = "design-ui-custom"',
                        "",
                        "[[reviewers]]",
                        'name = "one"',
                        'provider = "codex"',
                        'lens = "first lens"',
                        "",
                        "[[reviewers]]",
                        'name = "two"',
                        'provider = "opus"',
                        'lens = "second lens"',
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit) as exc:
                review_doc.parse_bundle_config(config_path)

        self.assertIn("exactly 3 reviewers", str(exc.exception))

    def test_select_bundle_reviewers_returns_requested_subset(self) -> None:
        reviewers = [
            review_doc.BundleReviewer(name="visual_design_critique", provider="codex", lens="visual", model="gpt-5.4"),
            review_doc.BundleReviewer(name="ux_product_critique", provider="codex", lens="ux", model="gpt-5.4"),
            review_doc.BundleReviewer(name="implementation_system_reality", provider="opus", lens="system", model="opus"),
        ]

        selected = review_doc.select_bundle_reviewers(reviewers, ["implementation_system_reality"])

        self.assertEqual([reviewer.name for reviewer in selected], ["implementation_system_reality"])

    def test_print_bundle_reviewers_lists_names_and_providers(self) -> None:
        reviewers = [
            review_doc.BundleReviewer(name="visual_design_critique", provider="codex", lens="visual", model="gpt-5.4"),
            review_doc.BundleReviewer(name="ux_product_critique", provider="codex", lens="ux", model="gpt-5.4"),
            review_doc.BundleReviewer(name="implementation_system_reality", provider="opus", lens="system", model="opus"),
        ]
        stream = io.StringIO()
        with mock.patch("sys.stdout", stream):
            review_doc.print_bundle_reviewers("design-ui", reviewers)

        output = stream.getvalue()
        self.assertIn("Bundle preset: design-ui", output)
        self.assertIn("visual_design_critique (codex, model=gpt-5.4)", output)
        self.assertIn("implementation_system_reality (opus, model=opus)", output)

    def test_print_bundle_reviewers_lists_frontend_hld_names(self) -> None:
        args = argparse.Namespace(bundle_config=None, bundle_preset="frontend-hld")
        bundle_name, reviewers = review_doc.resolve_bundle_reviewers(args)
        stream = io.StringIO()
        with mock.patch("sys.stdout", stream):
            review_doc.print_bundle_reviewers(bundle_name, reviewers)

        output = stream.getvalue()
        self.assertIn("Bundle preset: frontend-hld", output)
        self.assertIn("client_architecture_shape (codex, model=gpt-5.4)", output)
        self.assertIn("system_contracts_and_delivery (opus, model=opus)", output)

    def test_resolve_context_artifacts_embeds_parent_review_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="review_doc_parent_review_") as tmpdir:
            tmp = Path(tmpdir)
            parent_doc = tmp / "frontend_hld.md"
            adjacent_doc = tmp / "streaming_lld.md"
            context_doc = tmp / "backend_addendum.md"
            review_dir = tmp / "frontend_hld.rereviews.frontend_hld"
            review_dir.mkdir()
            parent_doc.write_text("# Frontend HLD\n", encoding="utf-8")
            adjacent_doc.write_text("# Streaming LLD\n", encoding="utf-8")
            context_doc.write_text("# Backend Addendum\n", encoding="utf-8")
            (review_dir / "summary.rereview.md").write_text("# Re-review Summary\n", encoding="utf-8")

            artifacts = review_doc.resolve_context_artifacts(
                context_files=[context_doc],
                parent_docs=[parent_doc],
                adjacent_docs=[adjacent_doc],
                parent_review_dirs=[review_dir],
            )

        labels = [artifact.label for artifact in artifacts]
        self.assertIn(f"Context file: {context_doc}", labels)
        self.assertIn(f"Parent document: {parent_doc}", labels)
        self.assertIn(f"Adjacent document: {adjacent_doc}", labels)
        self.assertTrue(any(label.startswith("Parent review summary: ") for label in labels))

    def test_reviewer_descriptor_formats_lane_identity(self) -> None:
        reviewer = review_doc.BundleReviewer(
            name="implementation_system_reality",
            provider="opus",
            lens="system",
            model="opus",
        )
        self.assertEqual(
            review_doc.reviewer_descriptor(reviewer),
            "implementation_system_reality (opus, model=opus)",
        )

    def test_build_codex_command_can_force_high_effort(self) -> None:
        command = review_doc.build_codex_command(
            cwd=Path("/tmp/work"),
            output_path=Path("/tmp/out.md"),
            profile=None,
            model="gpt-5.4",
            reasoning_effort="high",
            sandbox="read-only",
            skip_git_repo_check=True,
        )

        self.assertIn("--model", command)
        self.assertIn("gpt-5.4", command)
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertIn("--skip-git-repo-check", command)

    def test_build_claude_command_sets_high_effort(self) -> None:
        command = review_doc.build_claude_command(executable="claude", model="opus", effort="high")
        self.assertEqual(command[:2], ["claude", "-p"])
        self.assertIn("--model", command)
        self.assertIn("opus", command)
        self.assertIn("--effort", command)
        self.assertIn("high", command)

    def test_run_claude_strips_anthropic_api_key(self) -> None:
        args = argparse.Namespace(model=None, print_command=False)
        completed = mock.Mock(returncode=0, stdout="# Findings\n", stderr="")
        env = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "test-key"}

        with tempfile.TemporaryDirectory(prefix="review_doc_claude_env_") as tmpdir:
            output_path = Path(tmpdir) / "review.md"
            with mock.patch.dict(review_doc.os.environ, env, clear=True):
                with mock.patch.object(review_doc, "resolve_executable", return_value="claude"):
                    with mock.patch.object(review_doc.subprocess, "run", return_value=completed) as run_mock:
                        review_doc.run_claude(
                            args,
                            "review prompt",
                            cwd=Path(tmpdir),
                            output_path=output_path,
                            model_override="opus",
                            effort="high",
                        )
            self.assertTrue(output_path.is_file())

        launched_env = run_mock.call_args.kwargs["env"]
        self.assertNotIn("ANTHROPIC_API_KEY", launched_env)
        self.assertEqual(launched_env["PATH"], "/usr/bin")

    def test_parse_and_dedupe_findings_merges_similar_issues(self) -> None:
        visual_review = """
1. Severity: major
   Location: Hero section
   Issue: The primary CTA is visually buried beneath decorative copy, so the page hierarchy does not support the main action.
   Suggested fix: Increase CTA prominence and reduce competing copy weight.
"""
        ux_review = """
1. Severity: critical
   Location: Hero section
   Issue: The main call to action is buried below decorative marketing text, which weakens the primary task flow.
   Suggested fix: Move the CTA higher and simplify the supporting copy so the action is obvious immediately.
"""

        findings = review_doc.parse_review_findings(visual_review, "visual")
        findings.extend(review_doc.parse_review_findings(ux_review, "ux"))
        merged = review_doc.dedupe_findings(findings)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].severity, "critical")
        self.assertEqual(set(merged[0].reviewers), {"visual", "ux"})

    def test_build_rereview_prompt_embeds_prior_lane_feedback(self) -> None:
        reviewer = review_doc.BundleReviewer(
            name="implementation_system_reality",
            provider="opus",
            lens="system lens",
            model="opus",
        )
        with tempfile.TemporaryDirectory(prefix="review_doc_rereview_prompt_") as tmpdir:
            doc_path = Path(tmpdir) / "design.md"
            doc_path.write_text("# Revised\nLine two\n", encoding="utf-8")
            prompt = review_doc.build_rereview_prompt(
                input_path=doc_path,
                reviewer=reviewer,
                context_artifacts=[],
                prior_reviewer_output="# Prior Findings\n- old issue\n",
                prior_summary="# Summary\n- cross-provider note\n",
            )

        self.assertIn("blind adversarial re-review", prompt)
        self.assertIn("Resolution Audit", prompt)
        self.assertIn("Must Fix Now", prompt)
        self.assertIn("Defer To Other Doc", prompt)
        self.assertIn("Needs Prototype", prompt)
        self.assertIn("Status: resolved|partially_resolved|unresolved|not_applicable", prompt)
        self.assertIn("# Prior Findings", prompt)
        self.assertIn("# Summary", prompt)

    def test_parse_rereview_findings_reads_bucket_categories(self) -> None:
        text = """
# Verdict
Needs another pass.

# Must Fix Now
1. Severity: critical
   Location: Hero
   Issue: CTA hierarchy still hides the main action.
   Suggested fix: Promote the CTA and reduce competing copy.

# Defer To Other Doc
1. Severity: minor
   Location: Serialization notes
   Issue: Arrangement persistence format is underspecified here.
   Suggested fix: Move persistence details into the frontend architecture doc.
"""
        findings = review_doc.parse_rereview_findings(text, "visual_design_critique")
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0].category, "must_fix_now")
        self.assertEqual(findings[1].category, "defer_to_other_doc")

    def test_parse_rereview_audit_items_reads_resolution_audit_section(self) -> None:
        text = """
# Verdict
Needs another pass.

# Resolution Audit
1. Status: unresolved
   Original issue: Hero hierarchy buried the CTA.
   Current assessment: The CTA is still visually weak after the revision.
   What still needs to change: Increase CTA prominence and reduce competing copy.

# Remaining Issues
1. Severity: major
   Location: Hero
   Issue: CTA still lacks prominence.
   Suggested fix: Increase contrast and spacing.
"""
        items = review_doc.parse_rereview_audit_items(text, "visual_design_critique")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, "unresolved")
        self.assertEqual(items[0].reviewer, "visual_design_critique")
        self.assertIn("CTA", items[0].original_issue)

    def test_build_bundle_summary_groups_by_severity(self) -> None:
        reviewers = [
            review_doc.BundleReviewer(name="visual", provider="codex", lens="visual", model="gpt-5.4"),
            review_doc.BundleReviewer(name="ux", provider="codex", lens="ux", model="gpt-5.4"),
            review_doc.BundleReviewer(name="system", provider="opus", lens="system", model="opus"),
        ]
        findings = [
            review_doc.ParsedFinding(
                severity="major",
                location="Checkout flow",
                issue="Loading state is missing during payment confirmation.",
                suggested_fix="Add an explicit in-progress payment state.",
                reviewers=["ux"],
            ),
            review_doc.ParsedFinding(
                severity="nit",
                location="Hero section",
                issue="Headline feels generic.",
                suggested_fix="Rewrite the headline with a clearer point of view.",
                reviewers=["visual"],
            ),
        ]

        summary = review_doc.build_bundle_summary(
            input_path=Path("/tmp/doc.md"),
            bundle_name="design-ui",
            reviewers=reviewers,
            parsed_findings=findings,
            audit_items=[],
            failed_reviewers=[],
            missing_reviewers=[],
            rereview=False,
        )

        self.assertIn("# Cross-Provider Review Summary: design-ui", summary)
        self.assertIn("## By Review Lane", summary)
        self.assertIn("### visual (codex, model=gpt-5.4)", summary)
        self.assertIn("### ux (codex, model=gpt-5.4)", summary)
        self.assertIn("### system (opus, model=opus)", summary)
        self.assertIn("Corroborated by: none", summary)
        self.assertIn("## By Severity", summary)
        self.assertIn("## Major", summary)
        self.assertIn("## Nit", summary)
        self.assertIn("Lanes: ux (codex, model=gpt-5.4)", summary)

    def test_run_review_bundle_can_rerun_one_reviewer_and_reuse_existing_outputs(self) -> None:
        args = argparse.Namespace(
            bundle_config=None,
            bundle_preset="design-ui",
            reviewer=["implementation_system_reality"],
            output_dir=None,
            parent_doc=[],
            adjacent_doc=[],
            parent_review_dir=[],
            profile=None,
            model=None,
            sandbox="read-only",
            skip_git_repo_check=False,
            print_command=False,
            rereview_from=None,
        )

        with tempfile.TemporaryDirectory(prefix="review_doc_bundle_partial_") as tmpdir:
            tmp = Path(tmpdir)
            doc_path = tmp / "design.md"
            context_path = tmp / "requirements.md"
            doc_path.write_text("# Design\nbody\n", encoding="utf-8")
            context_path.write_text("# Requirements\n", encoding="utf-8")
            output_dir = doc_path.parent / f"{doc_path.stem}.reviews.design_ui"
            output_dir.mkdir()
            (output_dir / "visual_design_critique.codex.md").write_text(
                "1. Severity: major\n"
                "   Location: Hero\n"
                "   Issue: Visual hierarchy is too flat.\n"
                "   Suggested fix: Increase contrast between headline and supporting copy.\n",
                encoding="utf-8",
            )
            (output_dir / "ux_product_critique.codex.md").write_text(
                "1. Severity: minor\n"
                "   Location: Checkout\n"
                "   Issue: Loading state is missing.\n"
                "   Suggested fix: Add a loading state.\n",
                encoding="utf-8",
            )

            def fake_run_bundle_reviewer(*_call_args, **kwargs):
                reviewer = kwargs["reviewer"]
                output_path = review_doc.output_path_for_reviewer(kwargs["output_dir"], reviewer)
                output_path.write_text(
                    "1. Severity: critical\n"
                    "   Location: Navigation\n"
                    "   Issue: Keyboard navigation order is undefined.\n"
                    "   Suggested fix: Specify tab order and focus states.\n",
                    encoding="utf-8",
                )
                return reviewer.name, output_path, 0

            with mock.patch.object(review_doc, "run_bundle_reviewer", side_effect=fake_run_bundle_reviewer) as run_mock:
                exit_code = review_doc.run_review_bundle(
                    args,
                    input_path=doc_path,
                    context_files=[context_path],
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(run_mock.call_count, 1)
            summary = (output_dir / "summary.md").read_text(encoding="utf-8")
            self.assertIn("Keyboard navigation order is undefined", summary)
            self.assertIn("Visual hierarchy is too flat", summary)
            self.assertIn("Loading state is missing", summary)
            self.assertNotIn("Missing reviewer outputs", summary)

    def test_run_review_bundle_rereview_uses_prior_outputs_and_writes_rereview_summary(self) -> None:
        args = argparse.Namespace(
            bundle_config=None,
            bundle_preset="design-ui",
            reviewer=["implementation_system_reality"],
            output_dir=None,
            parent_doc=[],
            adjacent_doc=[],
            parent_review_dir=[],
            profile=None,
            model=None,
            sandbox="read-only",
            skip_git_repo_check=False,
            print_command=False,
            rereview_from=None,
        )

        with tempfile.TemporaryDirectory(prefix="review_doc_bundle_rereview_") as tmpdir:
            tmp = Path(tmpdir)
            doc_path = tmp / "design.md"
            doc_path.write_text("# Revised Design\nbody\n", encoding="utf-8")
            old_review_dir = tmp / "prior.reviews.design_ui"
            old_review_dir.mkdir()
            (old_review_dir / "summary.md").write_text(
                "# Cross-Provider Review Summary: design-ui\n- Target document: prior\n",
                encoding="utf-8",
            )
            (old_review_dir / "implementation_system_reality.opus.md").write_text(
                "1. Severity: critical\n"
                "   Location: Navigation\n"
                "   Issue: Keyboard navigation order is undefined.\n"
                "   Suggested fix: Specify tab order and focus states.\n",
                encoding="utf-8",
            )
            args.rereview_from = str(old_review_dir)

            def fake_run_bundle_reviewer(*_call_args, **kwargs):
                reviewer = kwargs["reviewer"]
                output_path = review_doc.output_path_for_reviewer(
                    kwargs["output_dir"],
                    reviewer,
                    rereview=True,
                )
                output_path.write_text(
                    "# Verdict\n"
                    "Partially improved.\n\n"
                    "# Resolution Audit\n"
                    "1. Status: partially_resolved\n"
                    "   Original issue: Keyboard navigation order is undefined.\n"
                    "   Current assessment: Focus states are now specified, but tab order is still ambiguous.\n"
                    "   What still needs to change: Define deterministic tab order.\n\n"
                    "# Must Fix Now\n"
                    "1. Severity: major\n"
                    "   Location: Navigation\n"
                    "   Issue: Tab order remains ambiguous across the primary nav.\n"
                    "   Suggested fix: Define explicit tab sequencing.\n\n"
                    "# Defer To Other Doc\n\n"
                    "# Needs Prototype\n\n"
                    "# Open Questions\n"
                    "- None.\n",
                    encoding="utf-8",
                )
                return reviewer.name, output_path, 0

            with mock.patch.object(review_doc, "run_bundle_reviewer", side_effect=fake_run_bundle_reviewer) as run_mock:
                exit_code = review_doc.run_review_bundle(
                    args,
                    input_path=doc_path,
                    context_files=[],
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(run_mock.call_count, 1)
            rereview_dir = doc_path.parent / f"{doc_path.stem}.rereviews.design_ui"
            summary = (rereview_dir / "summary.rereview.md").read_text(encoding="utf-8")
            self.assertIn("# Cross-Provider Re-Review Summary: design-ui", summary)
            self.assertIn("## Resolution Audit Counts", summary)
            self.assertIn("## Must Fix Now", summary)
            self.assertIn("partially_resolved=1", summary)
            self.assertIn("Tab order remains ambiguous", summary)


if __name__ == "__main__":
    unittest.main()
