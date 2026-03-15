from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError as exc:  # pragma: no cover
    raise SystemExit("Python 3.11+ with tomllib is required") from exc


DEFAULT_CONFIG: dict[str, Any] = {
    "runtime": {
        "temp_dir": "/tmp/voice-ptt",
        "result_format": "json",
    },
    "transcription": {
        "backend": "openai",
        "timeout_seconds": 180,
        "language": "",
        "prompt": "",
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-transcribe",
            "api_key_env": "OPENAI_API_KEY",
            "api_key_file": "",
            "curl_path": "curl",
        },
        "command": {
            "shell_command": "",
            "trim_stdout": True,
        },
    },
    "platforms": {
        "linux": {
            "recording": {
                "input": "default",
                "sample_rate": 16000,
                "channels": 1,
                "temp_dir": "/tmp/voice-ptt",
                "ffmpeg_path": "ffmpeg",
            },
            "beep": {
                "paplay_path": "paplay",
                "start_hz": 880,
                "stop_hz": 660,
                "duration_ms": 140,
            },
            "paste": {
                "mode": "ctrl_shift_v",
                "delay_ms": 120,
            },
            "notify": {
                "enabled": True,
                "notify_send_path": "notify-send",
            },
            "hotkey": {
                "modifiers": ["Control", "Shift"],
                "key": "R",
            },
        },
        "windows": {
            "hotkey": "^+r",
            "ffmpeg_path": "ffmpeg.exe",
            "audio_input": "audio=Microphone",
            "temp_dir": "%TEMP%\\voice-ptt",
            "clipboard_paste": True,
            "paste_delay_ms": 100,
            "startup_mode": "startup_folder",
            "autohotkey_path": "AutoHotkey.exe",
            "python_path": "python.exe",
        },
        "wsl_bridge": {
            "enabled": False,
            "distribution": "",
            "python_path": "python3",
            "working_directory": "/home/cobra/CENTRAL",
        },
    },
}

LEGACY_LINUX_SECTIONS = ("recording", "beep", "paste", "notify", "hotkey")


def merge_dicts(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_config(path: Path) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    expanded = path.expanduser()
    if not expanded.exists():
        return config
    with expanded.open("rb") as handle:
        loaded = tomllib.load(handle)
    return normalize_config(config, loaded)


def normalize_config(config: dict[str, Any], loaded: dict[str, Any]) -> dict[str, Any]:
    config = merge_dicts(config, loaded)
    if "backend" in loaded:
        config["transcription"] = merge_dicts(config["transcription"], _normalize_legacy_backend(loaded["backend"]))
    for section in LEGACY_LINUX_SECTIONS:
        if section in loaded:
            config["platforms"]["linux"][section] = merge_dicts(config["platforms"]["linux"][section], loaded[section])
    if "wsl_bridge" in loaded:
        config["platforms"]["wsl_bridge"] = merge_dicts(config["platforms"]["wsl_bridge"], loaded["wsl_bridge"])
    return config


def _normalize_legacy_backend(legacy: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "backend": legacy.get("type", DEFAULT_CONFIG["transcription"]["backend"]),
        "timeout_seconds": legacy.get("timeout_seconds", DEFAULT_CONFIG["transcription"]["timeout_seconds"]),
    }
    if "openai" in legacy:
        normalized["openai"] = legacy["openai"]
    if "command" in legacy:
        normalized["command"] = legacy["command"]
    return normalized

