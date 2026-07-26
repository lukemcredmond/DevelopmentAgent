"""Export step diagnostics / transcripts as JSONL for offline SFT (no in-app training)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend import state
from backend.config import diagnostics_dir


def _load_recent_step_files(limit: int = 50) -> List[Dict[str, Any]]:
    project_dir = diagnostics_dir(state.CURRENT_PROJECT_ID)
    if not project_dir.exists():
        return []
    matches: List[tuple[float, Path, Dict[str, Any]]] = []
    for file_path in project_dir.glob("step-*.json"):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                matches.append((file_path.stat().st_mtime, file_path, data))
        except Exception:
            continue
    matches.sort(key=lambda t: t[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for _, path, data in matches[: max(1, limit)]:
        data = dict(data)
        data["_filePath"] = str(path)
        out.append(data)
    return out


def step_trace_to_export_row(data: Dict[str, Any]) -> Dict[str, Any]:
    tools = data.get("toolsUsed") or []
    if not isinstance(tools, list):
        tools = list(tools) if tools else []
    events = data.get("events") or data.get("timeline") or []
    messages: List[Dict[str, Any]] = []
    if isinstance(events, list):
        for ev in events[-40:]:
            if not isinstance(ev, dict):
                continue
            messages.append(
                {
                    "role": ev.get("type") or ev.get("role") or "event",
                    "content": str(ev.get("detail") or ev.get("content") or "")[:500],
                }
            )
    return {
        "taskId": data.get("taskId") or "",
        "taskTitle": data.get("taskTitle") or "",
        "agent": data.get("agent") or "",
        "stopReason": data.get("exitReason") or data.get("stopReason") or "",
        "outcome": data.get("agentResultSnippet") or data.get("hint") or "",
        "tools": tools,
        "messages": messages,
        "durationMs": data.get("durationMs"),
        "ok": data.get("ok"),
        "filePath": data.get("_filePath") or data.get("filePath") or "",
    }


def export_training_jsonl(*, limit: int = 50) -> Dict[str, Any]:
    """
    Build JSONL text from recent step diagnostics for offline fine-tuning.
    Training is NOT run in AllHands — export only.
    """
    rows = [step_trace_to_export_row(d) for d in _load_recent_step_files(limit=limit)]
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    return {
        "ok": True,
        "count": len(lines),
        "projectId": state.CURRENT_PROJECT_ID,
        "jsonl": "\n".join(lines) + ("\n" if lines else ""),
        "note": "Export for offline fine-tuning — training not run in AllHands.",
    }
