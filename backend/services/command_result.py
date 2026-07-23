"""Unified workspace command runner with structured diagnostics."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.config import LONG_COMMAND_TIMEOUT_SEC, TERMINAL_TIMEOUT_SEC
from backend.services.diagnostics_parser import parse_command_diagnostics, summarize_diagnostics
from backend.services.terminal_service import run_command

# Slow finite builds — use LONG_COMMAND_TIMEOUT_SEC (not background).
_LONG_COMMAND_MARKERS = (
    "build_runner",
    "flutter build",
    "flutter pub run build_runner",
    "dart run build_runner",
    "dotnet build",
    "dotnet publish",
    "gradle",
    "npm run build",
    "yarn build",
    "pnpm build",
    "cargo build",
    "cargo release",
    "mvn ",
    "maven ",
)


def is_long_running_command(command: str) -> bool:
    lower = (command or "").lower()
    return any(marker in lower for marker in _LONG_COMMAND_MARKERS)


def resolve_command_timeout(command: str, *, explicit: Optional[int] = None) -> int:
    """Pick shell timeout: explicit > long-command heuristic > workflow/default."""
    if explicit is not None and explicit > 0:
        return int(explicit)
    try:
        from backend.services.workflow_settings import get_workflow_settings

        base = int(get_workflow_settings().get("terminalTimeoutSec") or TERMINAL_TIMEOUT_SEC)
    except Exception:
        base = TERMINAL_TIMEOUT_SEC
    base = max(30, base)
    if is_long_running_command(command):
        return max(base, LONG_COMMAND_TIMEOUT_SEC)
    return base


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    outcome: str
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    @property
    def combined_output(self) -> str:
        parts = [part for part in (self.stdout, self.stderr) if part]
        return "\n".join(parts).strip() or "(no output)"

    @property
    def success(self) -> bool:
        return self.outcome == "ok"


def build_command_result(
    command: str,
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    duration_ms: int = 0,
) -> CommandResult:
    combined = "\n".join(part for part in (stdout, stderr) if part).strip() or "(no output)"
    diagnostics = parse_command_diagnostics(command, combined)
    from backend.agents.tool_outcomes import classify_run_command_outcome

    outcome = classify_run_command_outcome(command, exit_code, combined, diagnostics)
    summary = summarize_diagnostics(diagnostics)
    return CommandResult(
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        outcome=outcome,
        diagnostics=diagnostics,
        summary=summary,
    )


def run_workspace_command(command: str, timeout: Optional[int] = None) -> CommandResult:
    """Run a shell command in the workspace and return structured results."""
    effective = resolve_command_timeout(command, explicit=timeout)
    started = time.time()
    result = run_command(command, timeout=effective)
    duration_ms = int((time.time() - started) * 1000)
    return build_command_result(
        command,
        exit_code=int(result.get("returncode", -1)),
        stdout=result.get("stdout") or "",
        stderr=result.get("stderr") or "",
        duration_ms=duration_ms,
    )


def format_command_result_for_agent(result: CommandResult, *, max_output_chars: int = 8000) -> str:
    """Cursor-style agent output: header, summary, problems list, then raw output."""
    if result.outcome == "ok":
        header = f"[success exit {result.exit_code}]"
    elif result.outcome in ("lint_findings", "test_failed"):
        header = f"[findings exit {result.exit_code}]"
    else:
        header = f"[failed exit {result.exit_code}]"

    lines = [header, result.command]
    if result.summary:
        lines.append(f"Summary: {result.summary}")
    if result.diagnostics:
        lines.append("")
        lines.append("## Problems (fix these first)")
        for item in result.diagnostics[:100]:
            severity = item.get("severity", "info")
            file_path = item.get("file", "?")
            line = item.get("line", 0)
            column = item.get("column", 0)
            message = item.get("message", "")
            lines.append(f"- {file_path}:{line}:{column}  {severity}  {message}")
    lines.append("")
    lines.append("## Output")
    lines.append(result.combined_output[:max_output_chars])
    return "\n".join(lines)
