"""Per-step sprint diagnostics — JSON files under ~/.allhands/diagnostics/."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from backend import state
from backend.config import diagnostics_dir
from backend.services.logs import add_system_log

MAX_FILES_PER_PROJECT = 50


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_task_slug(task_id: str) -> str:
    return re.sub(r"[^\w\-]", "_", task_id)[:40]


class StepDiagnosticsTracker:
    """Accumulates events for one manual sprint step and writes a JSON report."""

    def __init__(
        self,
        *,
        task_id: str,
        task_title: str,
        agent: str,
        lane_before: str,
        file_path: Path,
    ) -> None:
        self.trace_id = uuid.uuid4().hex[:8].upper()
        self.task_id = task_id
        self.task_title = task_title
        self.agent = agent
        self.lane_before = lane_before
        self.file_path = file_path
        self.started_at = _now_str()
        self.started_monotonic = datetime.now()
        self.ollama_calls: List[Dict[str, Any]] = []
        self.tools_log: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.tools_used: Set[str] = set()
        self.plan_rejections = 0
        self.text_rejections = 0
        self.llm_iterations_used = 0
        self.llm_iterations_max = 0
        self.tool_failures = 0

    def log_ollama_call(
        self,
        iteration: int,
        *,
        duration_ms: int,
        tool_calls: Optional[List[str]] = None,
        text_chars: int = 0,
        error: Optional[str] = None,
    ) -> None:
        self.llm_iterations_used = max(self.llm_iterations_used, iteration)
        self.ollama_calls.append(
            {
                "iteration": iteration,
                "durationMs": duration_ms,
                "toolCalls": tool_calls or [],
                "textChars": text_chars,
                "error": error,
            }
        )

    def set_llm_iterations_max(self, max_iterations: int) -> None:
        self.llm_iterations_max = max_iterations

    def log_tool(self, name: str, success: bool, summary: str) -> None:
        self.tools_used.add(name)
        if not success:
            self.tool_failures += 1
        self.tools_log.append(
            {
                "timestamp": _now_str(),
                "toolName": name,
                "success": success,
                "summary": summary[:300],
            }
        )

    def log_event(self, kind: str, message: str) -> None:
        if kind == "plan_rejected":
            self.plan_rejections += 1
        elif kind == "text_rejected":
            self.text_rejections += 1
        self.events.append(
            {
                "timestamp": _now_str(),
                "kind": kind,
                "message": message[:500],
            }
        )

    def _build_hint(self, exit_reason: str) -> str:
        hints = {
            "read_only_no_edits": (
                "Model read files but never called apply_patch/write_file. "
                "Check Model tab iteration 2+ or attach this JSON."
            ),
            "max_iterations": "Agent hit max LLM iterations without finishing edits.",
            "tool_failure_stop": "Tool failures exceeded the step limit.",
            "ollama_fallback": "Ollama was unavailable during the step.",
            "completed_text_only": (
                "Agent returned text without write tools while still In Progress."
            ),
            "plan_exhausted": "Multiple plan-only responses were rejected; no edits written.",
        }
        return hints.get(
            exit_reason,
            "See ollamaCalls and events in this file; attach when reporting issues.",
        )

    def finalize(
        self,
        *,
        exit_reason: str,
        lane_after: str,
        ok: bool,
        agent_result: Optional[str] = None,
        last_step_outcome: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ended = datetime.now()
        duration_ms = int((ended - self.started_monotonic).total_seconds() * 1000)
        payload: Dict[str, Any] = {
            "traceId": self.trace_id,
            "projectId": state.CURRENT_PROJECT_ID,
            "taskId": self.task_id,
            "taskTitle": self.task_title,
            "agent": self.agent,
            "startedAt": self.started_at,
            "endedAt": ended.strftime("%Y-%m-%d %H:%M:%S"),
            "durationMs": duration_ms,
            "exitReason": exit_reason,
            "laneBefore": self.lane_before,
            "laneAfter": lane_after,
            "toolsUsed": sorted(self.tools_used),
            "toolFailures": self.tool_failures,
            "planRejections": self.plan_rejections,
            "textRejections": self.text_rejections,
            "llmIterations": {
                "used": self.llm_iterations_used,
                "max": self.llm_iterations_max,
            },
            "ollamaCalls": self.ollama_calls,
            "toolsLog": self.tools_log,
            "events": self.events,
            "agentResultSnippet": (agent_result or "")[:500],
            "lastStepOutcome": last_step_outcome,
            "hint": self._build_hint(exit_reason),
            "filePath": str(self.file_path),
            "ok": ok,
        }
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        _prune_old_files(self.file_path.parent)
        tools_summary = ",".join(sorted(self.tools_used)) or "none"
        add_system_log(
            "System",
            "info",
            f"Step diagnostics: {self.file_path} (exit={exit_reason}, tools={tools_summary}, {duration_ms // 1000}s)",
        )
        return payload


def _prune_old_files(project_dir: Path) -> None:
    files = sorted(project_dir.glob("step-*.json"), key=lambda p: p.stat().st_mtime)
    while len(files) > MAX_FILES_PER_PROJECT:
        oldest = files.pop(0)
        try:
            oldest.unlink()
        except OSError:
            pass


def get_active_trace() -> Optional[StepDiagnosticsTracker]:
    return state.ACTIVE_STEP_DIAGNOSTICS


def start_step_trace(
    task_id: str,
    task_title: str,
    agent: str,
    lane: str,
) -> StepDiagnosticsTracker:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    slug = _safe_task_slug(task_id)
    project_dir = diagnostics_dir(state.CURRENT_PROJECT_ID)
    file_path = project_dir / f"step-{slug}-{stamp}.json"
    tracker = StepDiagnosticsTracker(
        task_id=task_id,
        task_title=task_title,
        agent=agent,
        lane_before=lane,
        file_path=file_path,
    )
    state.ACTIVE_STEP_DIAGNOSTICS = tracker
    add_system_log(
        "System",
        "info",
        f"Step diagnostics trace {tracker.trace_id} started — {file_path}",
    )
    return tracker


def log_ollama_call(
    iteration: int,
    *,
    duration_ms: int,
    tool_calls: Optional[List[str]] = None,
    text_chars: int = 0,
    error: Optional[str] = None,
) -> None:
    trace = get_active_trace()
    if trace:
        trace.log_ollama_call(
            iteration,
            duration_ms=duration_ms,
            tool_calls=tool_calls,
            text_chars=text_chars,
            error=error,
        )


def log_tool(name: str, success: bool, summary: str) -> None:
    trace = get_active_trace()
    if trace:
        trace.log_tool(name, success, summary)


def log_event(kind: str, message: str) -> None:
    trace = get_active_trace()
    if trace:
        trace.log_event(kind, message)


def set_llm_iterations_max(max_iterations: int) -> None:
    trace = get_active_trace()
    if trace:
        trace.set_llm_iterations_max(max_iterations)


def derive_exit_reason(
    *,
    agent_result: Optional[str],
    tools_used: Optional[Set[str]],
    lane_before: str,
    lane_after: str,
) -> str:
    tools = tools_used or set()
    if agent_result == "SIMULATION_FALLBACK":
        return "ollama_fallback"
    if agent_result and agent_result.startswith("Stopped:"):
        return "tool_failure_stop"
    if agent_result and agent_result.startswith("Max tool iterations"):
        return "max_iterations"
    if state.DEV_STEP_READ_ONLY_NO_EDITS:
        return "read_only_no_edits"
    trace = get_active_trace()
    if trace and trace.plan_rejections >= 2 and not (tools & {"write_file", "apply_patch"}):
        return "plan_exhausted"
    if tools & {"write_file", "apply_patch"}:
        return "completed_with_writes"
    if lane_before == lane_after == "In Progress" and agent_result:
        return "completed_text_only"
    return "fix_verify_done"


def finalize_active_step_trace(
    *,
    lane_after: str,
    agent_result: Optional[str] = None,
    tools_used: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:
    trace = get_active_trace()
    if not trace:
        return None
    outcome = state.LAST_STEP_OUTCOME
    ok = bool(outcome.get("ok")) if outcome else True
    exit_reason = derive_exit_reason(
        agent_result=agent_result or state.LAST_AGENT_STEP_RESULT,
        tools_used=tools_used or trace.tools_used,
        lane_before=trace.lane_before,
        lane_after=lane_after,
    )
    summary = trace.finalize(
        exit_reason=exit_reason,
        lane_after=lane_after,
        ok=ok,
        agent_result=agent_result or state.LAST_AGENT_STEP_RESULT,
        last_step_outcome=outcome,
    )
    state.LAST_STEP_DIAGNOSTICS = summary
    state.ACTIVE_STEP_DIAGNOSTICS = None
    return summary


def clear_active_step_trace() -> None:
    state.ACTIVE_STEP_DIAGNOSTICS = None
