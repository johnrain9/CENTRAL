#!/usr/bin/env python3
"""Tests for the document review helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_TOOL = REPO_ROOT / "scripts" / "review_doc.py"


def load_module():
    spec = importlib.util.spec_from_file_location("review_doc", REVIEW_TOOL)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load review_doc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReviewDocToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_module()

    def test_default_output_path_includes_mode_suffix(self) -> None:
        input_path = Path("/tmp/example.md")
        output_path = self.module.default_output_path(input_path, "hld")
        self.assertEqual(output_path, Path("/tmp/example.review.hld.md"))

    def test_build_claude_command_uses_print_mode_and_plan_permissions(self) -> None:
        command = self.module.build_claude_command(executable="/tmp/claude", model="sonnet")
        self.assertEqual(
            command,
            [
                "/tmp/claude",
                "-p",
                "Follow the full review instructions provided on stdin. Output markdown only.",
                "--output-format",
                "text",
                "--permission-mode",
                "plan",
                "--model",
                "sonnet",
            ],
        )

    def test_build_prompt_includes_mode_focus_and_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="review_doc_prompt_") as tmpdir:
            doc_path = Path(tmpdir) / "sample.md"
            doc_path.write_text("# Sample\nLine two\n", encoding="utf-8")
            prompt = self.module.build_prompt(
                input_path=doc_path,
                mode="lld",
                context_level="doc-only",
                context_files=[],
                extra_instructions=["Be strict about migrations."],
            )
        self.assertIn("adversarial review of a LLD document", prompt)
        self.assertIn("Context level: doc-only", prompt)
        self.assertIn("Review only the target document", prompt)
        self.assertIn("important things the document does not say but should", prompt)
        self.assertIn("Keep the critique at the low-level design layer", prompt)
        self.assertIn("Do not drift into code-level implementation review", prompt)
        self.assertIn("schema, API, and state-machine correctness", prompt)
        self.assertIn("Be strict about migrations.", prompt)
        self.assertIn("- Context Used", prompt)
        self.assertIn("   1 | # Sample", prompt)
        self.assertIn("   2 | Line two", prompt)

    def test_build_prompt_targeted_embeds_only_explicit_context_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="review_doc_targeted_") as tmpdir:
            doc_path = Path(tmpdir) / "sample.md"
            context_path = Path(tmpdir) / "context.py"
            doc_path.write_text("design\n", encoding="utf-8")
            context_path.write_text("def run():\n    return True\n", encoding="utf-8")
            prompt = self.module.build_prompt(
                input_path=doc_path,
                mode="hld",
                context_level="targeted",
                context_files=[context_path],
                extra_instructions=[],
            )
        self.assertIn("Context level: targeted", prompt)
        self.assertIn("Keep the critique at the high-level design layer", prompt)
        self.assertIn("Do not drift into low-level schema, API, code-structure, or implementation details", prompt)
        self.assertIn("Do not inspect any other local repository files", prompt)
        self.assertIn(f"Context file: {context_path}", prompt)
        self.assertIn("   1 | def run():", prompt)

    def test_resolve_cwd_uses_doc_directory_unless_repo_mode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="review_doc_cwd_") as tmpdir:
            root = Path(tmpdir).resolve()
            doc_dir = root / "docs"
            doc_dir.mkdir()
            doc_path = doc_dir / "sample.md"
            doc_path.write_text("text\n", encoding="utf-8")
            (root / ".git").mkdir()
            doc_only_cwd, doc_only_skip = self.module.resolve_cwd(doc_path, None, "doc-only")
            repo_cwd, repo_skip = self.module.resolve_cwd(doc_path, None, "repo")
        self.assertEqual(doc_only_cwd, doc_dir)
        self.assertTrue(doc_only_skip)
        self.assertEqual(repo_cwd, root)
        self.assertFalse(repo_skip)

    def test_cli_prompt_only_writes_prompt_to_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="review_doc_cli_") as tmpdir:
            doc_path = Path(tmpdir) / "sample.md"
            output_path = Path(tmpdir) / "sample.review.generic.md"
            doc_path.write_text("hello\nworld\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(REVIEW_TOOL),
                    "--input",
                    str(doc_path),
                    "--mode",
                    "generic",
                    "--backend",
                    "prompt-only",
                    "--output",
                    str(output_path),
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertTrue(output_path.is_file())
            prompt = output_path.read_text(encoding="utf-8")
            self.assertIn("Output markdown only", prompt)
            self.assertIn("Context level: doc-only", prompt)
            self.assertIn("Target document:", prompt)
            self.assertIn("   1 | hello", prompt)

    def test_help_includes_examples_and_modes(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REVIEW_TOOL), "--help"],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("Examples:", result.stdout)
        self.assertIn("Modes:", result.stdout)
        self.assertIn("Context levels:", result.stdout)
        self.assertIn("claude", result.stdout)
        self.assertIn("--context-level", result.stdout)
        self.assertIn("--context-file", result.stdout)
        self.assertIn("prompt-only", result.stdout)
        self.assertIn("--command-template", result.stdout)

    def test_external_backend_requires_prompt_and_output_placeholders(self) -> None:
        with self.assertRaises(SystemExit):
            self.module.validate_command_template("my-cli --prompt-file {prompt_file}")

    def test_cli_rejects_targeted_without_context_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="review_doc_targeted_cli_") as tmpdir:
            doc_path = Path(tmpdir) / "sample.md"
            output_path = Path(tmpdir) / "sample.review.generic.md"
            doc_path.write_text("hello\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(REVIEW_TOOL),
                    "--input",
                    str(doc_path),
                    "--mode",
                    "generic",
                    "--backend",
                    "prompt-only",
                    "--context-level",
                    "targeted",
                    "--output",
                    str(output_path),
                ],
                cwd=str(REPO_ROOT),
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires at least one --context-file", result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
