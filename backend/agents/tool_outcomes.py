"""Tool invocation summaries and failure classification."""

from typing import Any, Dict, Optional

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
        return cmd[:120] if isinstance(cmd, str) else str(cmd)[:120]
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


def is_tool_failure(name: str, output: str) -> bool:
    """True when tool output indicates the invocation did not succeed."""
    if not output:
        return True
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
    if "[failed exit" in lower:
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
