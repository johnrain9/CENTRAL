from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from tools.voice_ptt_v2.adapters import linux as linux_adapter


def write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 1600)


def write_config(path: Path, temp_dir: Path) -> None:
    path.write_text(
        textwrap.dedent(
            f"""
            [backend]
            type = "command"
            timeout_seconds = 30

            [backend.command]
            shell_command = "python3 -c \\"from pathlib import Path; import sys; p = Path(sys.argv[1]); print('transcribed:' + p.name + ':' + str(p.stat().st_size))\\" {{audio_path_quoted}}"
            trim_stdout = true

            [recording]
            input = "default"
            sample_rate = 16000
            channels = 1
            temp_dir = "{temp_dir}"
            ffmpeg_path = "ffmpeg"

            [beep]
            paplay_path = "paplay"
            start_hz = 880
            stop_hz = 660
            duration_ms = 140

            [paste]
            mode = "ctrl_shift_v"
            delay_ms = 0

            [notify]
            enabled = false

            [hotkey]
            modifiers = ["Control", "Shift"]
            key = "R"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


class FakeX11Controller:
    def __init__(self) -> None:
        self.clipboard_text = ""
        self.paste_modes: list[str] = []

    def set_clipboard_text(self, text: str) -> None:
        self.clipboard_text = text

    def paste_from_clipboard(self, mode: str) -> None:
        self.paste_modes.append(mode)

    def close(self) -> None:
        return


class FakeProcess:
    def __init__(self, audio_path: Path) -> None:
        self.audio_path = audio_path
        self.stdin = io.BytesIO()
        self.stderr = io.BytesIO()
        self.returncode = 0

    def communicate(self, timeout: int | None = None) -> tuple[bytes, bytes]:
        _ = timeout
        self.audio_path.write_bytes(b"RIFFfakewavdata")
        self.returncode = 0
        return b"", b""

    def poll(self) -> int | None:
        return None


class VoicePttV2Test(unittest.TestCase):
    def test_cli_transcribe_file_outputs_structured_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            audio_path = temp_root / "sample.wav"
            config_path = temp_root / "config.toml"
            write_wav(audio_path)
            write_config(config_path, temp_root)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.voice_ptt_v2",
                    "transcribe-file",
                    "--config",
                    str(config_path),
                    "--audio-file",
                    str(audio_path),
                    "--platform",
                    "test_cli",
                ],
                cwd=str(Path(__file__).resolve().parents[1]),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["platform"], "test_cli")
            self.assertIn("transcribed:sample.wav", payload["text"])

    def test_wsl_bridge_returns_helper_mode_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            audio_path = temp_root / "sample.wav"
            config_path = temp_root / "config.toml"
            request_path = temp_root / "request.json"
            write_wav(audio_path)
            write_config(config_path, temp_root)
            request_path.write_text(
                json.dumps(
                    {
                        "audio_path": str(audio_path),
                        "requested_by": "windows_wrapper",
                        "metadata": {"job": "bridge-test"},
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.voice_ptt_v2.adapters.wsl.bridge",
                    "--config",
                    str(config_path),
                    "--request-file",
                    str(request_path),
                ],
                cwd=str(Path(__file__).resolve().parents[1]),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["platform"], "wsl_bridge")
            self.assertEqual(payload["metadata"]["bridge_mode"], "helper_only")
            self.assertEqual(payload["metadata"]["job"], "bridge-test")

    def test_wsl_bridge_rejects_missing_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            config_path = temp_root / "config.toml"
            request_path = temp_root / "request.json"
            write_config(config_path, temp_root)
            request_path.write_text(json.dumps({"requested_by": "windows_wrapper"}), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tools.voice_ptt_v2.adapters.wsl.bridge",
                    "--config",
                    str(config_path),
                    "--request-file",
                    str(request_path),
                ],
                cwd=str(Path(__file__).resolve().parents[1]),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("audio_path", result.stderr)

    def test_linux_adapter_keeps_desktop_sequence_outside_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            lock_path = temp_root / "voice-ptt.lock"
            audio_config = temp_root / "config.toml"
            write_config(audio_config, temp_root)
            original_lock_path = linux_adapter.LOCK_PATH
            original_popen = linux_adapter.subprocess.Popen
            beeps: list[str] = []
            transcribed: list[Path] = []

            def fake_popen(command: list[str], **_: object) -> FakeProcess:
                return FakeProcess(Path(command[-1]))

            linux_adapter.LOCK_PATH = lock_path
            linux_adapter.subprocess.Popen = fake_popen  # type: ignore[assignment]
            try:
                daemon = linux_adapter.LinuxVoicePttDaemon(audio_config, x11_factory=FakeX11Controller)
                daemon.ensure_beeps = lambda: (temp_root / "start-beep.wav", temp_root / "stop-beep.wav")
                daemon.play_beep = lambda path: beeps.append(path.name)
                daemon.start_transcription = lambda audio_path: transcribed.append(audio_path)
                daemon.start_recording()
                self.assertIsNotNone(daemon.recording)
                daemon.stop_recording()
                self.assertEqual(beeps, ["start-beep.wav", "stop-beep.wav"])
                self.assertEqual(len(transcribed), 1)
                daemon.handle_transcript(SimpleNamespace(text="hello from linux", backend="command"))
                self.assertEqual(daemon.x11.clipboard_text, "hello from linux")
                self.assertEqual(daemon.x11.paste_modes, ["ctrl_shift_v"])
            finally:
                if "daemon" in locals():
                    daemon.x11.close()
                    daemon.lock_file.close()
                linux_adapter.LOCK_PATH = original_lock_path
                linux_adapter.subprocess.Popen = original_popen  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
