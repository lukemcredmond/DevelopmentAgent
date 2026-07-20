"""Project-scoped (workspace-wide) user-injected command/test evidence."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend import state

MAX_PROJECT_EVIDENCE = 20
MAX_PROMPT_ENTRIES = 5
MAX_OUTPUT_IN_PROMPT = 1200
_SETTING_PREFIX = "project_tool_evidence:"


def _setting_key(project_id: Optional[str] = None) -> str:
    return f"{_SETTING_PREFIX}{project_id or state.CURRENT_PROJECT_ID}"


def list_project_evidence() -> List[Dict[str, Any]]:
    return list(state.PROJECT_TOOL_EVIDENCE)


def load_project_evidence(project_id: Optional[str] = None) -> None:
    raw = state.storage.get_setting(_setting_key(project_id))
    if not raw:
        state.PROJECT_TOOL_EVIDENCE = []
        return
    try:
        data = json.loads(raw)
        state.PROJECT_TOOL_EVIDENCE = data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        state.PROJECT_TOOL_EVIDENCE = []


def _persist() -> None:
    state.storage.set_setting(
        _setting_key(),
        json.dumps(state.PROJECT_TOOL_EVIDENCE),
    )


def inject_project_tool_evidence(
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_output: str,
    *,
    note: str = "",
) -> Dict[str, Any]:
    """Store workspace-wide evidence and append a Tools log row (no task id)."""
    from backend.services.logs import add_system_log
    from backend.services.tool_execution_service import append_user_tool_log_event

    log_meta = append_user_tool_log_event(
        tool_name=tool_name,
        tool_args=tool_args,
        tool_output=tool_output,
        task_id=None,
        run_id_prefix="project-inject",
    )

    entry_id = str(uuid.uuid4())[:12]
    command = str(tool_args.get("command") or "") if tool_name == "run_command" else ""
    entry = {
        "id": entry_id,
        "toolName": tool_name,
        "command": command,
        "toolArgs": dict(tool_args or {}),
        "toolOutput": str(log_meta.get("toolOutput") or "")[:8000],
        "note": (note or "").strip(),
        "outcome": log_meta.get("outcome") or "ok",
        "success": bool(log_meta.get("toolSuccess", True)),
        "timestamp": log_meta.get("timestamp")
        or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    state.PROJECT_TOOL_EVIDENCE.insert(0, entry)
    state.PROJECT_TOOL_EVIDENCE = state.PROJECT_TOOL_EVIDENCE[:MAX_PROJECT_EVIDENCE]
    _persist()

    preview = (command or tool_name)[:80]
    add_system_log(
        "User",
        "success" if entry["success"] else "warning",
        f"Project evidence injected — {preview}",
    )
    return entry


def delete_project_evidence(entry_id: str) -> bool:
    before = len(state.PROJECT_TOOL_EVIDENCE)
    state.PROJECT_TOOL_EVIDENCE = [
        e for e in state.PROJECT_TOOL_EVIDENCE if str(e.get("id")) != entry_id
    ]
    if len(state.PROJECT_TOOL_EVIDENCE) == before:
        return False
    _persist()
    return True


def clear_project_evidence() -> int:
    n = len(state.PROJECT_TOOL_EVIDENCE)
    state.PROJECT_TOOL_EVIDENCE = []
    _persist()
    return n


def format_project_evidence_for_prompt(limit: int = MAX_PROMPT_ENTRIES) -> str:
    """Compact block for sprint/agent prompts."""
    entries = state.PROJECT_TOOL_EVIDENCE[:limit]
    if not entries:
        return ""
    lines = [
        "=== PROJECT SHARED EVIDENCE (user-provided workspace results) ===",
        "Use this when relevant; it is not tied to a single card.",
    ]
    for e in entries:
        cmd = str(e.get("command") or e.get("toolName") or "command")
        note = str(e.get("note") or "").strip()
        out = str(e.get("toolOutput") or "")[:MAX_OUTPUT_IN_PROMPT]
        lines.append(f"\n--- {cmd} ({e.get('timestamp', '')}) ---")
        if note:
            lines.append(f"Note: {note}")
        lines.append(out)
    lines.append("")
    return "\n".join(lines)
