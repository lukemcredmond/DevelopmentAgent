"""Per-project workflow settings and sprint summary persistence."""

import json
from typing import Any, Dict, List

from backend import state
from backend.config import MAX_SPRINT_STEPS

DEFAULT_WORKFLOW_SETTINGS: Dict[str, Any] = {
    "requireBacklogApproval": False,
    "requireCodeReview": False,
    "requireToolApproval": False,
    "requireDevVerification": False,
    "requireCleanLint": False,
    "requireBacklogRefinement": False,
    "prioritizeImplementationOverRefinement": True,
    "maxRefinementRoundTrips": 3,
    "maxSubtaskDepth": 4,
    "maxSubtaskSpawns": 8,
    "enableFixVerifyLoop": False,
    "maxFixVerifyRounds": 3,
    "toolApprovalTools": ["write_file", "run_command", "delete_file"],
    "nonBlockingToolApproval": True,
    "commandAutoRunMode": "off",
    "commandAllowlist": ["flutter analyze", "dart analyze", "npm test", "npm run lint", "pytest", "ruff check"],
    "commandDenylist": ["rm ", "del ", "rmdir ", "format ", "shutdown"],
    "allowChainedCommands": False,
    "maxMcpTools": 40,
    "mcpServers": [],
    "definitionOfDone": [],
    "maxSprintSteps": MAX_SPRINT_STEPS,
    "maxLlmIterationsPerStep": 8,
    "maxPoRoundTrips": 3,
    "maxStuckSteps": 3,
    "maxToolFailuresPerStep": 5,
    "autoStartSprint": True,
    "autonomousMode": False,
    "maxNeedsUserPerSprint": 2,
    "needsUserCooldownSteps": 3,
    "enableWebSearch": False,
    "enableSemanticSearch": True,
    "qdrantUrl": "http://localhost:6333",
    "qdrantApiKey": "",
    "embedModel": "nomic-embed-text",
    "ollamaNumCtx": 32768,
    "ollamaKeepAlive": "30m",
    "maxToolOutputCharsForLlm": 6000,
    "messagePruneThresholdPct": 60,
    "enableSemanticSprintContext": True,
    "pauseSprintOnNeedsUser": False,
    "autoFormatAfterEdit": True,
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
    updates = dict(settings)
    if not str(updates.get("qdrantApiKey") or "").strip():
        updates.pop("qdrantApiKey", None)
    current.update(updates)
    state.storage.set_setting(_settings_key(pid), json.dumps(current))
    return current


def reset_workflow_settings(project_id: str | None = None) -> Dict[str, Any]:
    """Replace workflow settings with defaults (used by tests and explicit UI reset)."""
    pid = project_id or state.CURRENT_PROJECT_ID
    defaults = dict(DEFAULT_WORKFLOW_SETTINGS)
    state.storage.set_setting(_settings_key(pid), json.dumps(defaults))
    return defaults


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
    lanes = ["Backlog"]
    if ws.get("requireBacklogApproval"):
        lanes.append("Pending Approval")
    if ws.get("requireBacklogRefinement"):
        lanes.append("Refinement")
    lanes.extend(["In Progress", "Needs PO", "Needs User"])
    if ws.get("requireCodeReview"):
        lanes.append("Code Review")
    lanes.extend(["QA", "Done"])
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
