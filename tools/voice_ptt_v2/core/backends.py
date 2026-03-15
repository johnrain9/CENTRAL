from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Protocol

from tools.voice_ptt_v2.core.contracts import BackendResponse


class TranscriptionBackend(Protocol):
    name: str

    def transcribe(self, audio_path: Path) -> BackendResponse:
        """Transcribe the supplied audio file."""


def command_path(name: str) -> str:
    if os.path.isabs(name):
        return name
    resolved = shutil_which(name)
    return resolved or name


def shutil_which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


class OpenAITranscriptionBackend:
    name = "openai"

    def __init__(self, config: dict[str, Any], timeout_seconds: int) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    def transcribe(self, audio_path: Path) -> BackendResponse:
        api_key = os.environ.get(str(self.config.get("api_key_env", "OPENAI_API_KEY")), "")
        api_key_file = str(self.config.get("api_key_file", "")).strip()
        if not api_key and api_key_file:
            api_key = Path(api_key_file).expanduser().read_text(encoding="utf-8").strip()
        if not api_key:
            raise RuntimeError("OpenAI backend selected but no API key was found")
        url = str(self.config["base_url"]).rstrip("/") + "/audio/transcriptions"
        command = [
            command_path(str(self.config.get("curl_path", "curl"))),
            "--silent",
            "--show-error",
            "--fail-with-body",
            url,
            "-H",
            f"Authorization: Bearer {api_key}",
            "-F",
            f"file=@{audio_path}",
            "-F",
            f"model={self.config['model']}",
            "-F",
            "response_format=json",
        ]
        prompt = str(self.config.get("prompt", "")).strip()
        if prompt:
            command.extend(["-F", f"prompt={prompt}"])
        language = str(self.config.get("language", "")).strip()
        if language:
            command.extend(["-F", f"language={language}"])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "curl failed"
            raise RuntimeError(f"OpenAI transcription request failed: {message}")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenAI response was not valid JSON: {result.stdout.strip()}") from exc
        transcript = str(payload.get("text", "")).strip()
        if not transcript:
            raise RuntimeError("OpenAI transcription response did not contain text")
        return BackendResponse(text=transcript, metadata={"response": payload})


class CommandTranscriptionBackend:
    name = "command"

    def __init__(self, config: dict[str, Any], timeout_seconds: int) -> None:
        self.config = config
        self.timeout_seconds = timeout_seconds

    def transcribe(self, audio_path: Path) -> BackendResponse:
        template = str(self.config.get("shell_command", "")).strip()
        if not template:
            raise RuntimeError("Command backend selected but shell_command is empty")
        rendered = template.format(
            audio_path=str(audio_path),
            audio_path_quoted=shlex.quote(str(audio_path)),
        )
        result = subprocess.run(
            rendered,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "command backend failed"
            raise RuntimeError(f"Command transcription failed: {message}")
        transcript = result.stdout
        if self.config.get("trim_stdout", True):
            transcript = transcript.strip()
        if not transcript:
            raise RuntimeError("Command backend returned an empty transcript")
        return BackendResponse(text=transcript)


def build_backend(config: dict[str, Any]) -> TranscriptionBackend:
    transcription = config["transcription"]
    timeout_seconds = int(transcription.get("timeout_seconds", 180))
    backend_name = str(transcription.get("backend", "openai"))
    if backend_name == "openai":
        backend_config = dict(transcription.get("openai", {}))
        backend_config.setdefault("prompt", transcription.get("prompt", ""))
        backend_config.setdefault("language", transcription.get("language", ""))
        return OpenAITranscriptionBackend(backend_config, timeout_seconds)
    if backend_name == "command":
        return CommandTranscriptionBackend(dict(transcription.get("command", {})), timeout_seconds)
    raise RuntimeError(f"Unsupported backend type: {backend_name}")


def dependency_checks(config: dict[str, Any]) -> dict[str, str]:
    checks = {
        "backend": str(config["transcription"].get("backend", "openai")),
    }
    backend_name = checks["backend"]
    if backend_name == "openai":
        checks["curl"] = command_path(str(config["transcription"]["openai"].get("curl_path", "curl")))
    return checks

