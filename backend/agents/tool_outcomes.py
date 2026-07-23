"""Tool invocation summaries and failure classification."""

import re
from typing import Any, Dict, List, Optional, Tuple

FILE_TOOLS: Dict[str, str] = {
    "read_file": "read",
    "write_file": "written",
    "apply_patch": "written",
    "run_test": "tested",
}


def file_path_from_tool(name: str, args: Dict[str, Any]) -> Optional[str]:
    """Extract workspace-relative path from a file-related tool invocation."""
    if name not in FILE_TOOLS:
        return None
    path = args.get("path") or args.get("test_script_path")
    if isinstance(path, str) and path.strip():
        return path.strip()
    return None


def file_action_for_tool(name: str) -> Optional[str]:
    return FILE_TOOLS.get(name)


def sanitize_tool_args_for_log(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Return a compact, log-safe view of tool arguments (no full file bodies)."""
    if name == "write_file":
        content = args.get("content")
        return {
            "path": args.get("path"),
            "contentLength": len(content) if isinstance(content, str) else 0,
        }
    if name == "apply_patch":
        old_t = args.get("old_text", "")
        new_t = args.get("new_text", "")
        return {
            "path": args.get("path"),
            "oldTextLength": len(old_t) if isinstance(old_t, str) else 0,
            "newTextLength": len(new_t) if isinstance(new_t, str) else 0,
        }
    if name in ("read_file", "run_test"):
        return {"path": args.get("path") or args.get("test_script_path")}
    if name == "run_command":
        cmd = args.get("command", "")
        return {"command": cmd[:200] if isinstance(cmd, str) else str(cmd)[:200]}
    if name == "update_board":
        return {"task_id": args.get("task_id"), "target_lane": args.get("target_lane")}
    if name in ("git_diff",):
        return {"path": args.get("path")} if args.get("path") else {}
    return {k: (v[:200] if isinstance(v, str) and len(v) > 200 else v) for k, v in args.items()}


def summarize_tool_args(name: str, args: Dict[str, Any]) -> str:
    """Human-readable one-line summary of tool arguments."""
    if name == "write_file":
        path = args.get("path", "?")
        content = args.get("content")
        n = len(content) if isinstance(content, str) else 0
        return f"{path} ({n} chars)"
    if name == "apply_patch":
        path = args.get("path", "?")
        old_t = args.get("old_text", "")
        n = len(old_t) if isinstance(old_t, str) else 0
        return f"{path} (replace {n} chars)"
    if name == "read_file":
        return str(args.get("path", "?"))
    if name == "run_test":
        return str(args.get("test_script_path", args.get("path", "?")))
    if name == "run_command":
        cmd = args.get("command", "")
        return cmd[:200] if isinstance(cmd, str) else str(cmd)[:200]
    if name == "update_board":
        return f"{args.get('task_id', '?')} → {args.get('target_lane', '?')}"
    if not args:
        return "(no args)"
    parts = []
    for key, val in list(args.items())[:4]:
        text = val if isinstance(val, str) else str(val)
        if len(text) > 80:
            text = text[:80] + "…"
        parts.append(f"{key}={text}")
    return ", ".join(parts)


_RUN_COMMAND_HEADER = re.compile(
    r"^\[(success|failed|findings) exit (-?\d+)\]",
    re.IGNORECASE | re.MULTILINE,
)

LINT_COMMAND_MARKERS = (
    "flutter analyze",
    "dart analyze",
    "npm run lint",
    "eslint",
    "ruff check",
    " pylint",
    "mypy",
)

TEST_COMMAND_MARKERS = (
    "flutter test",
    "pytest",
    "npm test",
    "npm run test",
    "jest",
    "cargo test",
    "go test",
)


def _is_lint_command(command: str) -> bool:
    lower = command.lower().strip()
    return any(marker in lower for marker in LINT_COMMAND_MARKERS)


def _is_test_command(command: str) -> bool:
    lower = command.lower().strip()
    return any(marker in lower for marker in TEST_COMMAND_MARKERS)


def parse_run_command_exit(output: str) -> Tuple[Optional[int], Optional[str]]:
    """Parse exit code and body from run_agent_command output."""
    if not output:
        return None, None
    match = _RUN_COMMAND_HEADER.search(output.strip())
    if not match:
        return None, output
    exit_code = int(match.group(2))
    body = output[match.end() :].strip()
    return exit_code, body


def _blocked_command_output(output: str) -> bool:
    lower = (output or "").lower()
    return (
        "chained or redirected commands are not allowed" in lower
        or "directory changes are not allowed" in lower
        or "command timed out" in lower
    )


def _execution_failed_by_output(
    output: str,
    *,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """True when the shell command did not run successfully (blocked, timeout, empty body)."""
    if diagnostics:
        return _blocked_command_output(output)

    if not output or not output.strip():
        return True
    if _blocked_command_output(output):
        return True
    if "## Problems" in output:
        return False

    lower = output.lower()
    exit_code, body = parse_run_command_exit(output)
    if exit_code is None:
        return output.startswith("Error") or "not registered" in lower

    if exit_code == 0:
        return False
    if exit_code < 0:
        return True

    body_stripped = (body or "").strip()
    if body_stripped == "(no output)":
        return True
    if len(body_stripped) <= 20:
        from backend.services.diagnostics_parser import parse_command_diagnostics

        if parse_command_diagnostics("", body_stripped):
            return False
        return True
    return False


def classify_run_command_outcome(
    command: str,
    exit_code: int,
    body: str,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Classify raw command results before agent formatting."""
    combined = body or ""
    if _blocked_command_output(combined):
        return "execution_failed"
    if exit_code < 0:
        return "execution_failed"

    diags = diagnostics or []
    if not diags and combined.strip() and combined.strip() != "(no output)":
        from backend.services.diagnostics_parser import parse_command_diagnostics

        diags = parse_command_diagnostics(command, combined)

    if diags:
        if _is_test_command(command):
            return "test_failed"
        return "lint_findings"

    if exit_code == 0:
        return "ok"
    if not combined.strip() or combined.strip() == "(no output)":
        return "execution_failed"

    cmd = (command or "").strip()
    if _is_test_command(cmd):
        return "test_failed"
    if _is_lint_command(cmd):
        return "lint_findings"
    return "lint_findings"


def classify_run_command(
    command: str,
    output: str,
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Classify run_command outcome: ok, lint_findings, test_failed, or execution_failed."""
    diags = diagnostics
    if diags is None and "## Problems" in output:
        exit_code, body = parse_run_command_exit(output)
        from backend.services.diagnostics_parser import parse_command_diagnostics

        diags = parse_command_diagnostics(command, body or output)

    if _execution_failed_by_output(output, diagnostics=diags):
        return "execution_failed"

    exit_code, body = parse_run_command_exit(output)
    if exit_code is None:
        return "ok"
    if diags is None:
        from backend.services.diagnostics_parser import parse_command_diagnostics

        diags = parse_command_diagnostics(command, body or output)
    return classify_run_command_outcome(command, exit_code, body or output, diags)


def format_run_command_output(command: str, returncode: int, body: str) -> str:
    """Build a classified run_command output string with structured problems."""
    from backend.services.command_result import build_command_result, format_command_result_for_agent

    result = build_command_result(
        command,
        exit_code=returncode,
        stdout=body or "",
        stderr="",
    )
    return format_command_result_for_agent(result)


def normalize_run_command_output(command: str, tool_output: str) -> str:
    """Wrap bare pasted command output with a classified header when missing."""
    stripped = (tool_output or "").strip()
    if not stripped:
        return format_run_command_output(command, -1, "(no output)")
    if _RUN_COMMAND_HEADER.search(stripped):
        return stripped
    returncode = 0
    if re.search(r"\b(error|warning|issues found)\b", stripped, re.IGNORECASE):
        returncode = 1
    return format_run_command_output(command, returncode, stripped)


def is_run_command_failure(output: str) -> bool:
    """True when run_command did not execute successfully (blocked, timeout, no output)."""
    return classify_run_command("", output) == "execution_failed"


def run_command_status_label(output: str, success: bool, command: str = "") -> str:
    """Human label for run_command log UI: OK, Findings, Tests failed, or Failed."""
    outcome = classify_run_command(command, output)
    exit_code, _ = parse_run_command_exit(output)
    if outcome == "ok":
        return "OK"
    if outcome == "lint_findings":
        return f"Findings (exit {exit_code})" if exit_code is not None else "Findings"
    if outcome == "test_failed":
        return f"Tests failed (exit {exit_code})" if exit_code is not None else "Tests failed"
    return "Failed"


def is_tool_failure(name: str, output: str) -> bool:
    """True when tool output indicates the invocation did not succeed."""
    if not output:
        return True
    if name == "run_command":
        return is_run_command_failure(output)
    lower = output.lower()
    if output.startswith("Error") or output.startswith("❌"):
        return True
    if "not registered" in lower:
        return True
    if "physical write failed" in lower:
        return True
    if "path escapes" in lower:
        return True
    if "validation failure" in lower:
        return True
    if lower.startswith("error:"):
        return True
    if "file missing at" in lower:
        return True
    if "user denied" in lower or "approval timed out" in lower:
        return True
    if name == "read_file" and lower.startswith("error: file"):
        return True
    return False


def format_tool_transcript_content(
    tool_name: str,
    args: Dict[str, Any],
    output: str,
    *,
    success: bool,
) -> str:
    """Single-line transcript summary for a tool invocation."""
    summary = summarize_tool_args(tool_name, args)
    status = "✓" if success else "✗"
    snippet = output[:300].replace("\n", " ")
    return f"{tool_name} → {summary} {status} {snippet}"
