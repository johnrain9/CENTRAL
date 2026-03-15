from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import time
from typing import Any


@dataclass(slots=True)
class SessionState:
    session_id: str
    platform: str
    audio_path: Path
    started_at: float = field(default_factory=time)


@dataclass(slots=True)
class BackendResponse:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TranscriptionResult:
    status: str
    session_id: str
    platform: str
    backend: str
    audio_path: str
    text: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def duration_seconds(self) -> float:
        return max(self.finished_at - self.started_at, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "session_id": self.session_id,
            "platform": self.platform,
            "backend": self.backend,
            "audio_path": self.audio_path,
            "text": self.text,
            "error": self.error,
            "metadata": self.metadata,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
        }

