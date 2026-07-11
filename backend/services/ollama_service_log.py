"""Read and stream native Ollama server logs (CLI, journalctl, or log files)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

MAX_LOG_BYTES = 200_000
MAX_LINE_LEN = 4000

_ollama_logs_cli_supported_cache: Optional[bool] = None


@dataclass
class LogSource:
    kind: str
    path: Optional[str] = None
    command: Optional[List[str]] = None
    available: bool = True
    note: Optional[str] = None


def reset_log_source_cache() -> None:
    """Clear cached CLI capability probe (for tests)."""
    global _ollama_logs_cli_supported_cache
    _ollama_logs_cli_supported_cache = None


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _windows_server_log_path() -> Optional[Path]:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    return Path(local) / "Ollama" / "server.log"


def _darwin_server_log_path() -> Path:
    return Path.home() / "Library" / "Logs" / "Ollama" / "server.log"


def _linux_server_log_path() -> Path:
    return Path("/tmp/ollama.log")


def _is_unknown_logs_command(text: str) -> bool:
    lowered = text.lower()
    return "unknown command" in lowered and "log" in lowered


def _ollama_logs_cli_supported() -> bool:
    """True when the installed Ollama CLI exposes `ollama logs` (added in recent releases)."""
    global _ollama_logs_cli_supported_cache
    if _ollama_logs_cli_supported_cache is not None:
        return _ollama_logs_cli_supported_cache

    ollama = _which("ollama")
    if not ollama:
        _ollama_logs_cli_supported_cache = False
        return False

    try:
        result = subprocess.run(
            [ollama, "logs", "-n", "1"],
            capture_output=True,
            text=True,
            timeout=8,
            encoding="utf-8",
            errors="replace",
        )
        combined = (result.stdout or "") + (result.stderr or "")
        if _is_unknown_logs_command(combined):
            _ollama_logs_cli_supported_cache = False
            return False
        _ollama_logs_cli_supported_cache = True
        return True
    except (subprocess.TimeoutExpired, OSError):
        _ollama_logs_cli_supported_cache = False
        return False


def _file_source(path: Path, note: str, *, available: Optional[bool] = None) -> LogSource:
    exists = path.exists()
    return LogSource(
        kind="file",
        path=str(path),
        available=exists if available is None else available,
        note=note,
    )


def _platform_file_source(*, prefer_existing: bool = True) -> Optional[LogSource]:
    system = platform.system()
    if system == "Windows":
        win_path = _windows_server_log_path()
        if win_path is not None:
            if not prefer_existing or win_path.exists():
                return _file_source(
                    win_path,
                    "Windows server.log",
                    available=win_path.exists(),
                )
    elif system == "Darwin":
        mac_path = _darwin_server_log_path()
        if not prefer_existing or mac_path.exists():
            return _file_source(mac_path, "macOS server.log", available=mac_path.exists())
    linux_path = _linux_server_log_path()
    if system == "Linux" and (not prefer_existing or linux_path.exists()):
        return _file_source(linux_path, "/tmp/ollama.log", available=linux_path.exists())
    return None


def resolve_log_source() -> LogSource:
    """Pick the best log source for the current platform."""
    file_source = _platform_file_source(prefer_existing=True)
    if file_source is not None and file_source.available:
        return file_source

    if _ollama_logs_cli_supported():
        return LogSource(
            kind="ollama_cli",
            command=["ollama", "logs", "-n", "50"],
            note="ollama logs CLI",
        )

    system = platform.system()
    if system == "Linux" and _which("journalctl"):
        return LogSource(
            kind="journalctl",
            command=["journalctl", "-u", "ollama.service", "-n", "50", "--no-pager"],
            note="systemd journal (ollama.service)",
        )

    file_fallback = _platform_file_source(prefer_existing=False)
    if file_fallback is not None:
        if not file_fallback.available:
            file_fallback.note = f"{file_fallback.note} (start Ollama to create)"
        return file_fallback

    return LogSource(
        kind="unavailable",
        available=False,
        note="Install Ollama or start the service to view server logs",
    )


def _tail_file(path: str, lines: int) -> str:
    p = Path(path)
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > MAX_LOG_BYTES:
        text = text[-MAX_LOG_BYTES:]
    all_lines = text.splitlines()
    return "\n".join(all_lines[-lines:])


def _run_command(cmd: List[str], *, timeout: int = 15) -> Tuple[str, Optional[str]]:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        out = (result.stdout or "") + (result.stderr or "")
        if _is_unknown_logs_command(out):
            return "", "ollama logs command not supported by this Ollama version"
        if not out.strip() and result.returncode != 0:
            return "", f"exit {result.returncode}"
        return out[-MAX_LOG_BYTES:], None
    except subprocess.TimeoutExpired:
        return "", "command timed out"
    except OSError as exc:
        return "", str(exc)


def _read_from_source(source: LogSource, lines: int) -> Tuple[str, Optional[str], LogSource]:
    if source.kind == "file" and source.path:
        content = _tail_file(source.path, lines)
        error = None if content else "Log file empty or not found"
        return content, error, source

    if not source.command:
        return "", source.note, source

    cmd = list(source.command)
    if source.kind == "ollama_cli":
        cmd = ["ollama", "logs", "-n", str(lines)]
    elif source.kind == "journalctl":
        cmd = ["journalctl", "-u", "ollama.service", "-n", str(lines), "--no-pager"]

    content, error = _run_command(cmd)
    if source.kind == "ollama_cli" and (error or _is_unknown_logs_command(content)):
        fallback = _platform_file_source(prefer_existing=False)
        if fallback is not None and fallback.path:
            file_content = _tail_file(fallback.path, lines)
            file_error = None if file_content else "Log file empty or not found"
            return file_content, file_error, fallback
    return content, error, source


def read_service_log_snapshot(lines: int = 50) -> Dict[str, Any]:
    """Return recent server log lines and metadata."""
    lines = max(1, min(lines, 500))
    source = resolve_log_source()
    content = ""
    error: Optional[str] = None

    if source.kind == "unavailable":
        return {
            "available": False,
            "source": source.kind,
            "path": source.path,
            "note": source.note,
            "lines": [],
            "text": "",
            "error": source.note,
        }

    content, error, source = _read_from_source(source, lines)

    text = content or ""
    line_list = [ln[:MAX_LINE_LEN] for ln in text.splitlines() if ln.strip()]
    return {
        "available": bool(line_list) or source.available,
        "source": source.kind,
        "path": source.path,
        "note": source.note,
        "lines": line_list[-lines:],
        "text": "\n".join(line_list[-lines:]),
        "error": error,
    }


def _stream_subprocess(cmd: List[str]) -> Generator[str, None, None]:
    proc: Optional[subprocess.Popen[str]] = None
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if not proc.stdout:
            yield "data: [error] no stdout from log process\n\n"
            return
        for line in proc.stdout:
            if line:
                if _is_unknown_logs_command(line):
                    yield "data: [error] ollama logs command not supported — using log file instead\n\n"
                    return
                yield f"data: {line.rstrip()}\n\n"
    except OSError as exc:
        yield f"data: [error] {exc}\n\n"
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()


def _stream_file_tail(path: str, poll_sec: float = 1.0) -> Generator[str, None, None]:
    p = Path(path)
    pos = p.stat().st_size if p.exists() else 0
    while True:
        try:
            if not p.exists():
                time.sleep(poll_sec)
                continue
            size = p.stat().st_size
            if size < pos:
                pos = 0
            with p.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(pos)
                chunk = fh.read(MAX_LOG_BYTES)
                pos = fh.tell()
            if chunk:
                for line in chunk.splitlines():
                    if line.strip():
                        yield f"data: {line.rstrip()}\n\n"
        except OSError as exc:
            yield f"data: [error] {exc}\n\n"
            return
        time.sleep(poll_sec)


def stream_service_logs(lines: int = 50) -> Generator[str, None, None]:
    """SSE generator for live Ollama server logs."""
    snapshot = read_service_log_snapshot(lines)
    yield f"event: meta\ndata: {snapshot.get('source', 'unknown')}|{snapshot.get('note', '')}\n\n"
    for line in snapshot.get("lines") or []:
        yield f"data: {line}\n\n"

    source = resolve_log_source()
    if source.kind == "ollama_cli":
        yield from _stream_subprocess(["ollama", "logs", "-f", "-n", str(lines)])
    elif source.kind == "journalctl":
        yield from _stream_subprocess(
            ["journalctl", "-u", "ollama.service", "-f", "-n", str(lines), "--no-pager"]
        )
    elif source.kind == "file" and source.path:
        yield from _stream_file_tail(source.path)
    else:
        yield "data: [info] Live follow unavailable — refresh snapshot manually\n\n"
