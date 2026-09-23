"""Subprocess output decoding, runtime state reservations, and tree cleanup."""

from __future__ import annotations

import locale
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable


ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
PROGRESS_LINE_RE = re.compile(
    r"^(?P<desc>[^|%\r\n]+?)\s*\|.*?\|\s*"
    r"(?P<percent>\d+%)\s*(?P<suffix>.*)$"
)


def build_encoding_candidates(*preferred: str | None) -> list[str]:
    candidates = list(preferred)
    candidates.extend(
        [
            locale.getpreferredencoding(False),
            getattr(sys.stdout, "encoding", None),
            "utf-8-sig",
            "utf-8",
            "gb18030",
            "gbk",
            "cp936",
        ]
    )
    unique = []
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        normalized = candidate.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(candidate)
    return unique


def decode_output_line(raw_line: bytes, encodings: Iterable[str]) -> str:
    candidates = list(encodings) or ["utf-8"]
    for encoding in candidates:
        try:
            return raw_line.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw_line.decode(candidates[0], errors="replace")


def clean_log_text(text: str) -> str:
    text = ANSI_ESCAPE_RE.sub("", str(text))
    return text.replace("\t", " ").replace("\r", "").rstrip("\r\n ")


def normalize_progress_log(text: str) -> str:
    if "%" not in text or "|" not in text:
        return text
    match = PROGRESS_LINE_RE.match(text.strip())
    if not match:
        return text
    description = " ".join(match.group("desc").split())
    percent = match.group("percent")
    suffix = " ".join(match.group("suffix").split())
    return f"{description} {percent} | {suffix}" if suffix else f"{description} {percent}"


def is_progress_log(text: str) -> bool:
    normalized = normalize_progress_log(text)
    return normalized != text or ("%" in text and "进度" in text)


class ProcessSupervisor:
    def __init__(
        self,
        *,
        processes: dict | None = None,
        running: dict | None = None,
        starting: dict | None = None,
        stop_requested: dict | None = None,
        state_lock=None,
        log_line: Callable[..., None] | None = None,
        log_system: Callable[[str], None] | None = None,
        platform: str | None = None,
        subprocess_module=subprocess,
    ):
        self.processes = processes if processes is not None else {}
        self.running = running if running is not None else {}
        self.starting = starting if starting is not None else {}
        self.stop_requested = (
            stop_requested if stop_requested is not None else {}
        )
        self.state_lock = state_lock or threading.RLock()
        self.log_line = log_line or (lambda *_args, **_kwargs: None)
        self.log_system = log_system or (lambda _message: None)
        self.platform = platform or sys.platform
        self.subprocess = subprocess_module

    @staticmethod
    def build_encoding_candidates(*preferred):
        return build_encoding_candidates(*preferred)

    @staticmethod
    def decode_output_line(raw_line, encodings):
        return decode_output_line(raw_line, encodings)

    @staticmethod
    def clean_log_text(text):
        return clean_log_text(text)

    @staticmethod
    def normalize_progress_log(text):
        return normalize_progress_log(text)

    @staticmethod
    def is_progress_log(text):
        return is_progress_log(text)

    def subprocess_window_kwargs(self):
        creationflags = 0
        startupinfo = None
        if self.platform == "win32":
            creationflags = getattr(
                self.subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
            creationflags |= getattr(self.subprocess, "CREATE_NO_WINDOW", 0)
            creationflags |= getattr(self.subprocess, "DETACHED_PROCESS", 0)
            startupinfo_factory = getattr(self.subprocess, "STARTUPINFO", None)
            if startupinfo_factory is not None:
                startupinfo = startupinfo_factory()
                startupinfo.dwFlags |= getattr(
                    self.subprocess,
                    "STARTF_USESHOWWINDOW",
                    0,
                )
                startupinfo.wShowWindow = 0
        return creationflags, startupinfo

    def _emit_buffer(self, buffer: bytes, source: str, encodings) -> None:
        if not buffer:
            return
        line = clean_log_text(decode_output_line(buffer, encodings))
        if line:
            self.log_line(
                source,
                line,
                replace_last=is_progress_log(line),
            )

    def stream_process_output(self, process, source: str, encodings) -> None:
        if not process or not process.stdout:
            return
        buffer = bytearray()
        read_available = getattr(process.stdout, "read1", None)
        while True:
            chunk = (
                read_available(4096)
                if read_available is not None
                else process.stdout.read(1)
            )
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode(encodings[0], errors="replace")
            for byte in chunk:
                if byte in (10, 13):
                    self._emit_buffer(bytes(buffer), source, encodings)
                    buffer.clear()
                else:
                    buffer.append(byte)
        self._emit_buffer(bytes(buffer), source, encodings)

    def run_logged_command(self, command, cwd, source="system", env=None) -> int:
        creationflags, startupinfo = self.subprocess_window_kwargs()
        process = self.subprocess.Popen(
            command,
            stdout=self.subprocess.PIPE,
            stderr=self.subprocess.STDOUT,
            cwd=cwd,
            text=False,
            creationflags=creationflags,
            startupinfo=startupinfo,
            env=env,
        )
        self.stream_process_output(
            process,
            source,
            build_encoding_candidates(
                locale.getpreferredencoding(False),
                "utf-8",
                "gb18030",
                "gbk",
            ),
        )
        return process.wait()

    def terminate_process_tree(self, process, label: str) -> bool:
        if not process or getattr(process, "pid", None) is None:
            return False
        if self.platform == "win32":
            creationflags, startupinfo = self.subprocess_window_kwargs()
            try:
                completed = self.subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=self.subprocess.DEVNULL,
                    stderr=self.subprocess.DEVNULL,
                    check=False,
                    creationflags=creationflags,
                    startupinfo=startupinfo,
                )
                if completed.returncode == 0:
                    return True
                self.log_system(
                    f"{label} 进程树终止返回码 {completed.returncode}，回退到普通终止"
                )
            except Exception as exc:
                self.log_system(
                    f"{label} 进程树终止失败，回退到普通终止: {exc}"
                )
        try:
            process.terminate()
            process.wait(timeout=5)
            return True
        except Exception:
            try:
                process.kill()
                return True
            except Exception as exc:
                self.log_system(f"{label} 强制终止失败: {exc}")
                return False

    def claim_start(self, runtime: str) -> bool:
        with self.state_lock:
            if self.running.get(runtime) or self.starting.get(runtime):
                return False
            self.stop_requested[runtime] = False
            self.starting[runtime] = True
            return True

    def mark_running(self, runtime: str, process) -> None:
        with self.state_lock:
            self.processes[runtime] = process
            self.starting[runtime] = False
            self.running[runtime] = True

    def mark_stopped(self, runtime: str, process=None) -> bool:
        with self.state_lock:
            current = self.processes.get(runtime)
            if process is not None and current is not None and current is not process:
                return False
            if (
                process is not None
                and current is None
                and self.starting.get(runtime)
            ):
                # The exiting process may have been detached by stop() already.
                # Do not let its late monitor callback release a new start claim.
                return False
            self.processes[runtime] = None
            self.running[runtime] = False
            self.starting[runtime] = False
            return True
