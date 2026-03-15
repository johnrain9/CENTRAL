from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import fcntl
import logging
import os
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.voice_ptt_v2.core.backends import command_path, dependency_checks, shutil_which
from tools.voice_ptt_v2.core.config import load_config
from tools.voice_ptt_v2.core.controller import TranscriptionController
from tools.voice_ptt_v2.core.logging_utils import configure_logging


DEFAULT_CONFIG_PATH = Path("/home/cobra/CENTRAL/tools/voice_ptt/config.toml")
LOCK_PATH = Path("/tmp/voice-ptt.lock")

SHIFT_MASK = 1
LOCK_MASK = 2
CONTROL_MASK = 4
MOD2_MASK = 16
KEYPRESS = 2
SELECTION_CLEAR = 29
SELECTION_REQUEST = 30
SELECTION_NOTIFY = 31
CURRENT_TIME = 0
PROP_MODE_REPLACE = 0
GRAB_MODE_ASYNC = 1

XK_SHIFT_L = 0xFFE1
XK_CONTROL_L = 0xFFE3
XK_INSERT = 0xFF63
XK_R = ord("r")
XK_V = ord("v")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def notify(config: dict[str, Any], title: str, message: str, urgency: str = "normal") -> None:
    notify_cfg = config["platforms"]["linux"].get("notify", {})
    if not notify_cfg.get("enabled", True):
        return
    binary = command_path(str(notify_cfg.get("notify_send_path", "notify-send")))
    if not shutil_which(Path(binary).name) and not Path(binary).exists():
        return
    try:
        subprocess.Popen(
            [binary, "--urgency", urgency, title, message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return


def generate_tone(ffmpeg_path: str, output_path: Path, frequency_hz: int, duration_ms: int) -> None:
    if output_path.exists():
        return
    ensure_dir(output_path.parent)
    duration = max(duration_ms, 50) / 1000.0
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency_hz}:duration={duration}",
        str(output_path),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@dataclass
class RecordingState:
    process: subprocess.Popen[bytes]
    audio_path: Path
    started_at: float


class XKeyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("x_root", ctypes.c_int),
        ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


class XSelectionRequestEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("owner", ctypes.c_ulong),
        ("requestor", ctypes.c_ulong),
        ("selection", ctypes.c_ulong),
        ("target", ctypes.c_ulong),
        ("property", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
    ]


class XSelectionEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("requestor", ctypes.c_ulong),
        ("selection", ctypes.c_ulong),
        ("target", ctypes.c_ulong),
        ("property", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
    ]


class XEvent(ctypes.Union):
    _fields_ = [
        ("type", ctypes.c_int),
        ("xkey", XKeyEvent),
        ("xselectionrequest", XSelectionRequestEvent),
        ("xselection", XSelectionEvent),
        ("pad", ctypes.c_long * 24),
    ]


class X11Controller:
    def __init__(self) -> None:
        x11_name = ctypes.util.find_library("X11")
        xtst_name = ctypes.util.find_library("Xtst")
        if not x11_name or not xtst_name:
            raise RuntimeError("X11/XTest libraries are not available")
        self.x11 = ctypes.cdll.LoadLibrary(x11_name)
        self.xtst = ctypes.cdll.LoadLibrary(xtst_name)
        self._bind()
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError("Unable to open DISPLAY for global hotkey control")
        self.root = self.x11.XDefaultRootWindow(self.display)
        self.window = self.x11.XCreateSimpleWindow(self.display, self.root, 0, 0, 1, 1, 0, 0, 0)
        self.atom_clipboard = self.x11.XInternAtom(self.display, b"CLIPBOARD", 0)
        self.atom_primary = self.x11.XInternAtom(self.display, b"PRIMARY", 0)
        self.atom_targets = self.x11.XInternAtom(self.display, b"TARGETS", 0)
        self.atom_utf8 = self.x11.XInternAtom(self.display, b"UTF8_STRING", 0)
        self.atom_text = self.x11.XInternAtom(self.display, b"TEXT", 0)
        self.atom_string = self.x11.XInternAtom(self.display, b"STRING", 0)
        self.atom_atom = self.x11.XInternAtom(self.display, b"ATOM", 0)
        self.keycode_r = self.x11.XKeysymToKeycode(self.display, XK_R)
        if not self.keycode_r:
            raise RuntimeError("Unable to resolve keycode for hotkey")
        self.ignored_modifier_masks = [0, LOCK_MASK, MOD2_MASK, LOCK_MASK | MOD2_MASK]
        for extra_mask in self.ignored_modifier_masks:
            self.x11.XGrabKey(
                self.display,
                self.keycode_r,
                CONTROL_MASK | SHIFT_MASK | extra_mask,
                self.root,
                0,
                GRAB_MODE_ASYNC,
                GRAB_MODE_ASYNC,
            )
        self.x11.XFlush(self.display)
        self.clipboard_text = ""

    def _bind(self) -> None:
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self.x11.XDefaultRootWindow.restype = ctypes.c_ulong
        self.x11.XCreateSimpleWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        self.x11.XCreateSimpleWindow.restype = ctypes.c_ulong
        self.x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.x11.XInternAtom.restype = ctypes.c_ulong
        self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XKeysymToKeycode.restype = ctypes.c_uint
        self.x11.XGrabKey.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self.x11.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_ulong]
        self.x11.XPending.argtypes = [ctypes.c_void_p]
        self.x11.XPending.restype = ctypes.c_int
        self.x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(XEvent)]
        self.x11.XSetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        self.x11.XChangeProperty.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int,
        ]
        self.x11.XSendEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_long,
            ctypes.POINTER(XEvent),
        ]
        self.x11.XFlush.argtypes = [ctypes.c_void_p]
        self.x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.x11.XDestroyWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]

    def hotkey_pressed(self, event: XEvent) -> bool:
        normalized = event.xkey.state & ~(LOCK_MASK | MOD2_MASK)
        return event.type == KEYPRESS and event.xkey.keycode == self.keycode_r and normalized == (CONTROL_MASK | SHIFT_MASK)

    def set_clipboard_text(self, text: str) -> None:
        self.clipboard_text = text
        self.x11.XSetSelectionOwner(self.display, self.atom_clipboard, self.window, CURRENT_TIME)
        self.x11.XSetSelectionOwner(self.display, self.atom_primary, self.window, CURRENT_TIME)
        self.x11.XFlush(self.display)

    def handle_event(self, event: XEvent) -> None:
        if event.type == SELECTION_REQUEST:
            self._handle_selection_request(event.xselectionrequest)
        elif event.type == SELECTION_CLEAR:
            return

    def _handle_selection_request(self, request: XSelectionRequestEvent) -> None:
        property_atom = request.property or request.target
        response = XEvent()
        response.xselection.type = SELECTION_NOTIFY
        response.xselection.display = request.display
        response.xselection.requestor = request.requestor
        response.xselection.selection = request.selection
        response.xselection.target = request.target
        response.xselection.time = request.time
        response.xselection.property = 0

        if request.target == self.atom_targets:
            atoms = (ctypes.c_ulong * 3)(self.atom_utf8, self.atom_text, self.atom_string)
            payload = ctypes.cast(atoms, ctypes.POINTER(ctypes.c_ubyte))
            self.x11.XChangeProperty(
                self.display,
                request.requestor,
                property_atom,
                self.atom_atom,
                32,
                PROP_MODE_REPLACE,
                payload,
                len(atoms),
            )
            response.xselection.property = property_atom
        elif request.target in (self.atom_utf8, self.atom_text, self.atom_string):
            data = self.clipboard_text.encode("utf-8")
            array_type = ctypes.c_ubyte * max(len(data), 1)
            payload_buffer = array_type.from_buffer_copy(data or b"\0")
            payload = ctypes.cast(payload_buffer, ctypes.POINTER(ctypes.c_ubyte))
            self.x11.XChangeProperty(
                self.display,
                request.requestor,
                property_atom,
                request.target,
                8,
                PROP_MODE_REPLACE,
                payload,
                len(data),
            )
            response.xselection.property = property_atom

        self.x11.XSendEvent(self.display, request.requestor, 0, 0, ctypes.byref(response))
        self.x11.XFlush(self.display)

    def paste_from_clipboard(self, mode: str) -> None:
        combos = {
            "ctrl_shift_v": ([XK_CONTROL_L, XK_SHIFT_L], XK_V),
            "ctrl_v": ([XK_CONTROL_L], XK_V),
            "shift_insert": ([XK_SHIFT_L], XK_INSERT),
        }
        if mode not in combos:
            raise RuntimeError(f"Unsupported paste mode: {mode}")
        modifiers, keysym = combos[mode]
        modifier_keycodes = [self.x11.XKeysymToKeycode(self.display, value) for value in modifiers]
        keycode = self.x11.XKeysymToKeycode(self.display, keysym)
        if not keycode or any(not code for code in modifier_keycodes):
            raise RuntimeError("Unable to resolve keycodes for synthetic paste")
        for code in modifier_keycodes:
            self.xtst.XTestFakeKeyEvent(self.display, code, 1, CURRENT_TIME)
        self.xtst.XTestFakeKeyEvent(self.display, keycode, 1, CURRENT_TIME)
        self.xtst.XTestFakeKeyEvent(self.display, keycode, 0, CURRENT_TIME)
        for code in reversed(modifier_keycodes):
            self.xtst.XTestFakeKeyEvent(self.display, code, 0, CURRENT_TIME)
        self.x11.XFlush(self.display)
        self.x11.XSync(self.display, 0)

    def close(self) -> None:
        for extra_mask in self.ignored_modifier_masks:
            self.x11.XUngrabKey(self.display, self.keycode_r, CONTROL_MASK | SHIFT_MASK | extra_mask, self.root)
        self.x11.XDestroyWindow(self.display, self.window)
        self.x11.XCloseDisplay(self.display)


class LinuxVoicePttDaemon:
    def __init__(self, config_path: Path, x11_factory: type[X11Controller] = X11Controller) -> None:
        self.config_path = config_path
        self.config = load_config(config_path)
        self.platform_config = self.config["platforms"]["linux"]
        self.temp_dir = Path(self.platform_config["recording"]["temp_dir"])
        ensure_dir(self.temp_dir)
        self.logger = logging.getLogger("voice_ptt_v2.linux")
        self.transcription_controller = TranscriptionController(self.config, logger=self.logger)
        self.action_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.recording: RecordingState | None = None
        self.is_transcribing = False
        self.stop_requested = False
        self.lock_file = LOCK_PATH.open("w")
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("voice-ptt daemon is already running") from exc
        self.x11 = x11_factory()
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        self.logger.info("Received signal %s; shutting down", signum)
        self.stop_requested = True

    def ensure_beeps(self) -> tuple[Path, Path]:
        ffmpeg_path = command_path(str(self.platform_config["recording"]["ffmpeg_path"]))
        beep_cfg = self.platform_config["beep"]
        start_path = self.temp_dir / "start-beep.wav"
        stop_path = self.temp_dir / "stop-beep.wav"
        generate_tone(ffmpeg_path, start_path, int(beep_cfg["start_hz"]), int(beep_cfg["duration_ms"]))
        generate_tone(ffmpeg_path, stop_path, int(beep_cfg["stop_hz"]), int(beep_cfg["duration_ms"]))
        return start_path, stop_path

    def play_beep(self, beep_path: Path) -> None:
        paplay_path = command_path(str(self.platform_config["beep"]["paplay_path"]))
        subprocess.Popen([paplay_path, str(beep_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def build_record_command(self, audio_path: Path) -> list[str]:
        recording_cfg = self.platform_config["recording"]
        return [
            command_path(str(recording_cfg["ffmpeg_path"])),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "pulse",
            "-i",
            str(recording_cfg["input"]),
            "-ac",
            str(recording_cfg["channels"]),
            "-ar",
            str(recording_cfg["sample_rate"]),
            str(audio_path),
        ]

    def start_recording(self) -> None:
        if self.recording:
            self.logger.warning("Recording start ignored; already recording")
            return
        if self.is_transcribing:
            notify(self.config, "Voice PTT busy", "Transcription still running")
            return
        start_beep, _ = self.ensure_beeps()
        self.play_beep(start_beep)
        audio_path = self.temp_dir / f"capture-{time.strftime('%Y%m%d-%H%M%S')}.wav"
        command = self.build_record_command(audio_path)
        self.logger.info("Starting recording: %s", shlex.join(command))
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.recording = RecordingState(process=process, audio_path=audio_path, started_at=time.time())
        notify(self.config, "Voice PTT", "Recording started")

    def stop_recording(self) -> None:
        if not self.recording:
            self.logger.warning("Recording stop ignored; nothing active")
            return
        recording = self.recording
        self.recording = None
        process = recording.process
        self.logger.info("Stopping recording after %.2fs", time.time() - recording.started_at)
        if process.stdin:
            try:
                process.stdin.write(b"q")
                process.stdin.flush()
            except OSError:
                process.terminate()
        try:
            _, stderr_data = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr_data = process.communicate(timeout=5)
        _, stop_beep = self.ensure_beeps()
        self.play_beep(stop_beep)
        if process.returncode != 0:
            message = stderr_data.decode("utf-8", errors="ignore").strip() or "ffmpeg returned a non-zero status"
            raise RuntimeError(f"Recording failed to stop cleanly: {message}")
        if not recording.audio_path.exists() or recording.audio_path.stat().st_size == 0:
            raise RuntimeError("Recording did not produce an audio file")
        self.start_transcription(recording.audio_path)

    def start_transcription(self, audio_path: Path) -> None:
        self.is_transcribing = True
        notify(self.config, "Voice PTT", "Transcribing...")

        def worker() -> None:
            result = self.transcription_controller.transcribe_file(
                audio_path,
                platform="linux",
                metadata={"source": "desktop_hotkey"},
            )
            action = "transcript_ready" if result.status == "ok" else "transcript_error"
            self.action_queue.put((action, result))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def process_actions(self) -> None:
        while True:
            try:
                action, payload = self.action_queue.get_nowait()
            except queue.Empty:
                return
            self.is_transcribing = False
            if action == "transcript_ready":
                self.handle_transcript(payload)
            elif action == "transcript_error":
                self.handle_error(str(payload.error))

    def handle_transcript(self, result: Any) -> None:
        transcript = str(result.text)
        self.logger.info("Transcript ready (%d chars) via %s", len(transcript), result.backend)
        self.x11.set_clipboard_text(transcript)
        delay = max(int(self.platform_config["paste"]["delay_ms"]), 0) / 1000.0
        if delay:
            time.sleep(delay)
        self.x11.paste_from_clipboard(str(self.platform_config["paste"]["mode"]))
        notify(self.config, "Voice PTT", "Transcript pasted")

    def handle_error(self, message: str) -> None:
        self.logger.error(message)
        notify(self.config, "Voice PTT error", message, urgency="critical")

    def check_recording_health(self) -> None:
        if not self.recording:
            return
        return_code = self.recording.process.poll()
        if return_code is None:
            return
        stderr_data = self.recording.process.stderr.read().decode("utf-8", errors="ignore").strip() if self.recording.process.stderr else ""
        self.recording = None
        self.handle_error(stderr_data or f"Recorder exited unexpectedly with status {return_code}")

    def run(self) -> None:
        self.logger.info("voice-ptt v2 Linux adapter started with config %s", self.config_path)
        try:
            while not self.stop_requested:
                while self.x11.x11.XPending(self.x11.display):
                    event = XEvent()
                    self.x11.x11.XNextEvent(self.x11.display, ctypes.byref(event))
                    if self.x11.hotkey_pressed(event):
                        try:
                            if self.recording:
                                self.stop_recording()
                            else:
                                self.start_recording()
                        except Exception as exc:  # pylint: disable=broad-except
                            self.handle_error(str(exc))
                    else:
                        self.x11.handle_event(event)
                self.process_actions()
                self.check_recording_health()
                time.sleep(0.05)
        finally:
            if self.recording:
                try:
                    self.stop_recording()
                except Exception as exc:  # pylint: disable=broad-except
                    self.handle_error(str(exc))
            self.x11.close()
            self.lock_file.close()


def run_self_check(config_path: Path) -> int:
    config = load_config(config_path)
    linux_cfg = config["platforms"]["linux"]
    failures: list[str] = []
    warnings: list[str] = []
    checks = {
        "ffmpeg": command_path(str(linux_cfg["recording"]["ffmpeg_path"])),
        "paplay": command_path(str(linux_cfg["beep"]["paplay_path"])),
    }
    checks.update({name: path for name, path in dependency_checks(config).items() if name != "backend"})
    for name, resolved in checks.items():
        exists = bool(shutil_which(Path(resolved).name) or Path(resolved).exists())
        print(f"{name}: {'ok' if exists else 'missing'} ({resolved})")
        if not exists:
            failures.append(f"missing dependency: {name}")
    display = os.environ.get("DISPLAY", "")
    xauthority = os.environ.get("XAUTHORITY", "")
    wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
    print(f"display: {display or 'missing'}")
    print(f"xauthority: {xauthority or 'missing'}")
    print(f"wayland_display: {wayland_display or 'missing'}")
    if not display:
        failures.append("DISPLAY is not set")
    try:
        controller = X11Controller()
    except Exception as exc:  # pylint: disable=broad-except
        print(f"x11: failed ({exc})")
        failures.append("x11 unavailable")
        if display:
            warnings.append(
                "DISPLAY is set but X11/XWayland access still failed; the daemon likely lacks access to the active desktop "
                "socket or Xauthority cookie"
            )
            if not xauthority:
                warnings.append(
                    "XAUTHORITY is not set; if the session uses an Xauthority cookie, export it before starting the daemon"
                )
    else:
        print("x11: ok")
        controller.close()
    backend_name = str(config["transcription"]["backend"])
    print(f"backend: {backend_name}")
    if backend_name == "openai":
        openai_cfg = config["transcription"]["openai"]
        api_key_env = str(openai_cfg.get("api_key_env", "OPENAI_API_KEY"))
        api_key = os.environ.get(api_key_env, "").strip()
        api_key_file = str(openai_cfg.get("api_key_file", "")).strip()
        api_key_file_path = Path(api_key_file).expanduser() if api_key_file else None
        if api_key:
            print(f"openai_api_key: ok ({api_key_env})")
        elif api_key_file_path and api_key_file_path.exists():
            print(f"openai_api_key_file: ok ({api_key_file_path})")
        else:
            print("openai_api_key: missing")
            warnings.append(
                "OpenAI API key not configured; transcription requests will fail until api_key_env or api_key_file is set"
            )
    if failures:
        print("self-check failed:")
        for failure in failures:
            print(f" - {failure}")
        if warnings:
            print("warnings:")
            for warning in warnings:
                print(f" - {warning}")
        return 1
    if warnings:
        print("warnings:")
        for warning in warnings:
            print(f" - {warning}")
    print("self-check passed")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Linux desktop adapter for portable voice transcription v2")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="Path to TOML config file")
    parser.add_argument("--self-check", action="store_true", help="Validate dependencies and X11 access, then exit")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)
    if args.self_check:
        return run_self_check(args.config)
    try:
        daemon = LinuxVoicePttDaemon(args.config)
    except Exception as exc:  # pylint: disable=broad-except
        logging.getLogger("voice_ptt_v2.linux").error("Unable to start Linux adapter: %s", exc)
        return 1
    daemon.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
