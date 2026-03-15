from __future__ import annotations

import logging
import uuid
from pathlib import Path
from time import time
from typing import Any

from tools.voice_ptt_v2.core.backends import build_backend
from tools.voice_ptt_v2.core.contracts import SessionState, TranscriptionResult
from tools.voice_ptt_v2.core.logging_utils import log_event


class TranscriptionController:
    def __init__(self, config: dict[str, Any], logger: logging.Logger | None = None) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("voice_ptt_v2.core")
        self.backend = build_backend(config)

    def transcribe_file(
        self,
        audio_path: Path,
        platform: str,
        metadata: dict[str, Any] | None = None,
    ) -> TranscriptionResult:
        session = SessionState(
            session_id=str(uuid.uuid4()),
            platform=platform,
            audio_path=audio_path,
        )
        started_at = session.started_at
        merged_metadata = dict(metadata or {})
        merged_metadata["platform"] = platform
        log_event(
            self.logger,
            "transcription_started",
            session_id=session.session_id,
            platform=platform,
            backend=self.backend.name,
            audio_path=str(audio_path),
        )
        if not audio_path.exists():
            finished_at = time()
            error = f"Audio file does not exist: {audio_path}"
            log_event(
                self.logger,
                "transcription_failed",
                session_id=session.session_id,
                platform=platform,
                backend=self.backend.name,
                error=error,
            )
            return TranscriptionResult(
                status="error",
                session_id=session.session_id,
                platform=platform,
                backend=self.backend.name,
                audio_path=str(audio_path),
                error=error,
                metadata=merged_metadata,
                started_at=started_at,
                finished_at=finished_at,
            )
        try:
            response = self.backend.transcribe(audio_path)
        except Exception as exc:  # pylint: disable=broad-except
            finished_at = time()
            error = str(exc)
            log_event(
                self.logger,
                "transcription_failed",
                session_id=session.session_id,
                platform=platform,
                backend=self.backend.name,
                error=error,
            )
            return TranscriptionResult(
                status="error",
                session_id=session.session_id,
                platform=platform,
                backend=self.backend.name,
                audio_path=str(audio_path),
                error=error,
                metadata=merged_metadata,
                started_at=started_at,
                finished_at=finished_at,
            )
        finished_at = time()
        merged_metadata.update(response.metadata)
        log_event(
            self.logger,
            "transcription_finished",
            session_id=session.session_id,
            platform=platform,
            backend=self.backend.name,
            chars=len(response.text),
            duration_seconds=max(finished_at - started_at, 0.0),
        )
        return TranscriptionResult(
            status="ok",
            session_id=session.session_id,
            platform=platform,
            backend=self.backend.name,
            audio_path=str(audio_path),
            text=response.text,
            metadata=merged_metadata,
            started_at=started_at,
            finished_at=finished_at,
        )
