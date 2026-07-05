"""Per-project workflow settings and sprint summary persistence."""

import json
from typing import Any, Dict, List

from backend import state
from backend.config import MAX_SPRINT_STEPS

DEFAULT_WORKFLOW_SETTINGS: Dict[str, Any] = {
    "requireBacklogApproval": False,
    "requireCodeReview": False,
    "definitionOfDone": [],
    "maxSprintSteps": MAX_SPRINT_STEPS,
    "maxLlmIterationsPerStep": 8,
    "maxPoRoundTrips": 3,
    "maxStuckSteps": 3,
}

DEFAULT_SPRINT_SUMMARY: Dict[str, Any] = {
    "stepsRun": 0,
    "completed": [],
    "qaFailed": [],
    "blocked": [],
    "needsPo": 0,
    "needsUser": 0,
    "status": "completed",
}


def _settings_key(project_id: str) -> str:
    return f"workflow:{project_id}"


def _summary_key(project_id: str) -> str:
    return f"sprint_summary:{project_id}"


def get_workflow_settings(project_id: str | None = None) -> Dict[str, Any]:
    pid = project_id or state.CURRENT_PROJECT_ID
    raw = state.storage.get_setting(_settings_key(pid))
    if not raw:
        return dict(DEFAULT_WORKFLOW_SETTINGS)
    try:
        merged = {**DEFAULT_WORKFLOW_SETTINGS, **json.loads(raw)}
        return merged
    except json.JSONDecodeError:
        return dict(DEFAULT_WORKFLOW_SETTINGS)


def save_workflow_settings(settings: Dict[str, Any], project_id: str | None = None) -> Dict[str, Any]:
    pid = project_id or state.CURRENT_PROJECT_ID
    current = get_workflow_settings(pid)
    current.update(settings)
    state.storage.set_setting(_settings_key(pid), json.dumps(current))
    return current


def get_last_sprint_summary(project_id: str | None = None) -> Dict[str, Any]:
    pid = project_id or state.CURRENT_PROJECT_ID
    raw = state.storage.get_setting(_summary_key(pid))
    if not raw:
        return dict(DEFAULT_SPRINT_SUMMARY)
    try:
        return {**DEFAULT_SPRINT_SUMMARY, **json.loads(raw)}
    except json.JSONDecodeError:
        return dict(DEFAULT_SPRINT_SUMMARY)


def save_sprint_summary(summary: Dict[str, Any], project_id: str | None = None) -> None:
    pid = project_id or state.CURRENT_PROJECT_ID
    state.storage.set_setting(_summary_key(pid), json.dumps(summary))


def get_active_lanes(settings: Dict[str, Any] | None = None) -> List[str]:
    ws = settings or get_workflow_settings()
    lanes = ["Backlog", "In Progress", "Needs PO", "Needs User", "QA", "Done"]
    if ws.get("requireBacklogApproval"):
        lanes.insert(1, "Pending Approval")
    if ws.get("requireCodeReview"):
        qa_idx = lanes.index("QA")
        lanes.insert(qa_idx, "Code Review")
    return lanes


def build_workflow_notifications() -> Dict[str, int]:
    board = state.SHARED_BOARD
    qa_failures = sum(
        1
        for lane in board.values()
        for t in lane
        if isinstance(t, dict) and t.get("qaFailure")
    )
    return {
        "needsPo": len(board.get("Needs PO", [])),
        "needsUser": len(board.get("Needs User", [])),
        "pendingApproval": len(board.get("Pending Approval", [])),
        "qaFailures": qa_failures,
    }
