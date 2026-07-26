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
    # Auto-extend one more chunk of iterations on max_iterations when progress is evident.
    "autoExtendOnMaxIter": True,
    "autoExtendExtraIterations": 4,
    # Hybrid lint fan-out: keep a small in-card budget; spawn related Backlog cards for the rest.
    "maxInCardLintFixes": 5,
    "maxLintFanoutCards": 8,
    "lintFanoutThreshold": 6,
    "enableBackupModelOnStuck": True,
    "backupModelStuckSteps": 2,
    # After backup attempts fail at maxStuckSteps: one PO auto-split before Needs PO.
    "enableSplitOnStuck": True,
    "requireWorkspaceStructure": True,
    "autoScaffoldOnStructureGap": True,
    "toolApprovalTools": ["write_file", "run_command", "delete_file"],
    "nonBlockingToolApproval": True,
    "commandAutoRunMode": "off",
    "commandAllowlist": [
        "flutter analyze",
        "dart analyze",
        "npm test",
        "npm run lint",
        "npm create vite",
        "npx create-vite",
        "dotnet new",
        "dotnet build",
        "dotnet test",
        "pytest",
        "ruff check",
    ],
    "commandDenylist": ["rm ", "del ", "rmdir ", "format ", "shutdown"],
    "allowChainedCommands": True,
    "maxMcpTools": 40,
    "mcpServers": [],
    # Opt-in per-agent tool allowlists (empty/missing → built-in defaults).
    "agentTools": {},
    "agentToolsAllowWritesInRefinement": False,
    # User-defined tools: name/schema + shell|http|sql executor.
    "customTools": [],
    "definitionOfDone": [],
    "maxSprintSteps": MAX_SPRINT_STEPS,
    "maxLlmIterationsPerStep": 8,
    "maxPoRoundTrips": 3,
    "maxStuckSteps": 3,
    # Wall-clock cap for one agent tool loop (LLM+tools). Default 45 minutes.
    "maxAgentStepDurationSec": 2700,
    # Auto-move cards with unmet blockedBy into the Blocked lane (healthy wait).
    "enableBlockedLane": True,
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
    # Optional per-role override map {po,dev,cr,qa}; unset roles use sensible defaults.
    "ollamaNumCtxByRole": {},
    # When true, halve Dev ctx on low/minimal VRAM tiers.
    "ollamaNumCtxAuto": False,
    "ollamaKeepAlive": "30m",
    "ollamaRequestTimeoutSec": 300,
    "terminalTimeoutSec": 600,
    # Unload primary before loading backup when VRAM is nearly full.
    "enableVramAwareModelSwap": True,
    "ollamaMaxRetries": 4,
    "ollamaRetryDelaySec": [0, 2, 5, 10],
    "ollamaCooldownRetryEnabled": True,
    "ollamaCooldownRetrySec": 15,
    "ollamaCooldownRetryAttempts": 2,
    "maxToolOutputCharsForLlm": 6000,
    "messagePruneThresholdPct": 60,
    "enableSemanticSprintContext": True,
    "enableHybridSearch": True,
    "semanticMinScore": 0.35,
    "semanticSprintTopK": 5,
    "enableObservationSummaries": True,
    "enableEpisodeSummary": True,
    "enableStepLessonMemory": True,
    "pauseSprintOnNeedsUser": False,
    "autoFormatAfterEdit": True,
    # Outbound-only phone alerts (Discord webhook) — never opens inbound ports.
    "phoneNotifyEnabled": False,
    "phoneNotifyProvider": "discord",
    "phoneNotifyDiscordWebhookUrl": "",
    "phoneNotifyOnNeedsUser": True,
    "phoneNotifyOnNeedsPo": False,
    "phoneNotifyOnToolApproval": True,
    "phoneNotifyOnSprintEnd": True,
    "phoneNotifyOnBoardStatus": True,
    "phoneNotifyOnStuckEscalation": True,
    "phoneNotifyOnStepTimeout": True,
    "phoneNotifyOnBackupArmed": True,
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
    if not str(updates.get("phoneNotifyDiscordWebhookUrl") or "").strip():
        updates.pop("phoneNotifyDiscordWebhookUrl", None)
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
    lanes = ["Features", "Backlog"]
    if ws.get("requireBacklogApproval"):
        lanes.append("Pending Approval")
    if ws.get("requireBacklogRefinement"):
        lanes.append("Refinement")
    if ws.get("enableBlockedLane", True):
        lanes.append("Blocked")
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
