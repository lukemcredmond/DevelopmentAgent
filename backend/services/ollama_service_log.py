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


@dataclass
class LogSource:
    kind: str
    path: Optional[str] = None
    command: Optional[List[str]] = None
    available: bool = True
    note: Optional[str] = None


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def resolve_log_source() -> LogSource:
    """Pick the best log source for the current platform."""
    if _which("ollama"):
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
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            log_path = Path(local) / "Ollama" / "server.log"
            if log_path.exists():
                return LogSource(kind="file", path=str(log_path), note="Windows server.log")
    if system == "Darwin":
        log_path = Path.home() / "Library" / "Logs" / "Ollama" / "server.log"
        if log_path.exists():
            return LogSource(kind="file", path=str(log_path), note="macOS server.log")
    tmp_log = Path("/tmp/ollama.log")
    if tmp_log.exists():
        return LogSource(kind="file", path=str(tmp_log), note="/tmp/ollama.log")
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        log_path = Path(local) / "Ollama" / "server.log"
        return LogSource(
            kind="file",
            path=str(log_path),
            available=log_path.exists(),
            note="Windows server.log (start Ollama to create)",
        )
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
        if not out.strip() and result.returncode != 0:
            return "", f"exit {result.returncode}"
        return out[-MAX_LOG_BYTES:], None
    except subprocess.TimeoutExpired:
        return "", "command timed out"
    except OSError as exc:
        return "", str(exc)


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

    if source.kind == "file" and source.path:
        content = _tail_file(source.path, lines)
        if not content:
            error = "Log file empty or not found"
    elif source.command:
        cmd = list(source.command)
        if source.kind == "ollama_cli":
            cmd = ["ollama", "logs", "-n", str(lines)]
        elif source.kind == "journalctl":
            cmd = ["journalctl", "-u", "ollama.service", "-n", str(lines), "--no-pager"]
        content, error = _run_command(cmd)

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
