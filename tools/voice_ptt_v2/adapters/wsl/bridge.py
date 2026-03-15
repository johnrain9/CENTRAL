from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.voice_ptt_v2.core.config import load_config
from tools.voice_ptt_v2.core.controller import TranscriptionController
from tools.voice_ptt_v2.core.logging_utils import configure_logging


DEFAULT_CONFIG_PATH = Path("/home/cobra/CENTRAL/tools/voice_ptt/config.toml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WSL bridge for portable voice transcription v2")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to TOML config file")
    parser.add_argument("--request-file", default="-", help="JSON request file or - for stdin")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser


def _load_request(location: str) -> dict[str, object]:
    if location == "-":
        payload = sys.stdin.read()
    else:
        payload = Path(location).read_text(encoding="utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError("WSL bridge request must be a JSON object")
    return data


def _validate_request(request: dict[str, object]) -> tuple[Path, dict[str, object], str]:
    audio_path_value = str(request.get("audio_path", "")).strip()
    if not audio_path_value:
        raise RuntimeError("WSL bridge request must include a non-empty audio_path")
    metadata = request.get("metadata", {})
    if not isinstance(metadata, dict):
        raise RuntimeError("WSL bridge request metadata must be an object")
    requested_by = str(request.get("requested_by", "windows_host")).strip() or "windows_host"
    return Path(audio_path_value).expanduser(), metadata, requested_by


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose)
    request = _load_request(args.request_file)
    audio_path, metadata, requested_by = _validate_request(request)
    config = load_config(args.config)
    result = TranscriptionController(config).transcribe_file(
        audio_path,
        platform="wsl_bridge",
        metadata={
            "bridge_mode": "helper_only",
            "requested_by": requested_by,
            **metadata,
        },
    )
    print(json.dumps(result.to_dict()))
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
