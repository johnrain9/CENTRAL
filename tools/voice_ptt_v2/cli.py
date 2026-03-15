from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.voice_ptt_v2.core.config import load_config
from tools.voice_ptt_v2.core.controller import TranscriptionController
from tools.voice_ptt_v2.core.logging_utils import configure_logging


DEFAULT_CONFIG_PATH = Path("/home/cobra/CENTRAL/tools/voice_ptt/config.toml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable voice transcription v2")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    transcribe = subparsers.add_parser("transcribe-file", help="Transcribe an existing audio file")
    transcribe.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to TOML config")
    transcribe.add_argument("--audio-file", type=Path, required=True, help="Audio file to transcribe")
    transcribe.add_argument("--platform", default="cli", help="Platform label for structured output")
    transcribe.add_argument("--metadata", default="", help="Optional JSON object string to merge into metadata")
    transcribe.add_argument("--result-file", type=Path, help="Write the JSON result to this file")
    transcribe.add_argument("--text-file", type=Path, help="Write transcript text to this file on success")
    transcribe.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)
    if args.command == "transcribe-file":
        return run_transcribe(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


def run_transcribe(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    metadata = json.loads(args.metadata) if args.metadata else {}
    controller = TranscriptionController(config)
    result = controller.transcribe_file(args.audio_file, platform=args.platform, metadata=metadata)
    rendered = json.dumps(result.to_dict(), indent=2 if args.pretty else None, sort_keys=args.pretty)
    if args.result_file:
        args.result_file.write_text(rendered + "\n", encoding="utf-8")
    if args.text_file and result.status == "ok":
        args.text_file.write_text(result.text, encoding="utf-8")
    print(rendered)
    return 0 if result.status == "ok" else 1
