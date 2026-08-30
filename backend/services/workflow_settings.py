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
    # Self-correcting against real lint/test output is most of what makes an agent feel
    # like it delivers; affordable now that the iteration budget is not the bottleneck.
    "enableFixVerifyLoop": True,
    # Keep fix-verify cheap: 2 rounds max by default (was 3).
    "maxFixVerifyRounds": 2,
    # Abort further fix-verify rounds on hard agent stops (tool_failure, plan_exhausted, …).
    "fixVerifyAbortOnHardStop": True,
    # Auto-extend one more chunk of iterations on max_iterations when progress is evident.
    # Off by default — manual Extend remains; auto +4 was a hidden latency tax.
    "autoExtendOnMaxIter": False,
    "autoExtendExtraIterations": 4,
    # Hybrid lint fan-out: keep a small in-card budget; spawn related Backlog cards for the rest.
    "maxInCardLintFixes": 5,
    "maxLintFanoutCards": 8,
    "lintFanoutThreshold": 6,
    "enableBackupModelOnStuck": True,
    "backupModelStuckSteps": 2,
    # After backup attempts fail at maxStuckSteps: one PO auto-split before Needs PO.
    "enableSplitOnStuck": True,
    # Block In Progress → QA/CR when step exit is unhealthy (ollama_fallback, max_iterations, …).
    "forceCompleteOnUnhealthyExit": False,
    # Circuit breaker: stop endless same-card In Progress retries after N bad exits / identical patches.
    "enableStuckCircuitBreaker": True,
    "circuitBreakerMaxBadExits": 3,
    "circuitBreakerIdenticalPatchFails": 3,
    # Auto-sprint: backoff when steps interrupt before any Ollama call (crash/retry storms).
    "enableAutoSprintInterruptBackoff": True,
    "autoSprintInterruptBackoffSec": 5,
    "autoSprintInterruptBackoffMaxSec": 120,
    "interruptEarlyMaxMs": 30000,
    # Pause an auto sprint before a fourth identical task/reason zero-work retry.
    "enableZeroWorkRetryWatchdog": True,
    "zeroWorkRetryWatchdogMax": 3,
    # Cap Explore→Patch→Verify cycles per card before forcing stuck / split.
    "maxDevPhaseCyclesPerCard": 12,
    "maxDevStepsPerCard": 12,
    # Optional: run independent In Progress cards concurrently (workspace write-locked).
    "enableParallelIndependentCards": False,
    "maxParallelDevCards": 2,
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
    "autoSprintSessionRefreshEnabled": True,
    "autoSprintSessionRefreshMinutes": 60,
    "autoSprintHardReload": True,
    # A real change needs inventory + several reads + edits + a lint round. At 6 the
    # loop ran out of turns before it could finish and reported that as a failure.
    "maxLlmIterationsPerStep": 30,
    # The true safety net is total tool calls plus the wall-clock cap, not LLM turns.
    "maxToolCallsPerStep": 80,
    "maxPoRoundTrips": 3,
    "maxStuckSteps": 3,
    # Wall-clock cap for one agent tool loop (LLM+tools). Default 45 minutes.
    "maxAgentStepDurationSec": 2700,
    # Auto-move cards with unmet blockedBy into the Blocked lane (healthy wait).
    "enableBlockedLane": True,
    "maxToolFailuresPerStep": 4,
    # Agent efficiency (local Ollama): lean prompts, phase model routing, per-turn tool caps.
    "agentEfficiencyMode": "high",
    "enablePhaseModelRouting": True,
    "devExploreModel": "",
    "devPatchModel": "",
    "maxToolsPerLlmTurn": 3,
    "autoStartSprint": True,
    "autonomousMode": False,
    "maxNeedsUserPerSprint": 2,
    "needsUserCooldownSteps": 3,
    "enableWebSearch": False,
    "enableSemanticSearch": True,
    "qdrantUrl": "http://localhost:6333",
    "qdrantApiKey": "",
    "embedModel": "nomic-embed-text",
    "llmProvider": "ollama",
    "llmProviderPreset": "ollama",
    "llmBaseUrl": "http://localhost:11434",
    "llmApiKey": "",
    "embedProvider": "ollama",
    "embedBaseUrl": "http://localhost:11434",
    "ollamaNumCtx": 32768,
    # Optional per-role override map {po,dev,cr,qa}; unset roles use sensible defaults.
    "ollamaNumCtxByRole": {},
    # When true, clamp num_ctx to what the inference host's VRAM can actually hold.
    "ollamaNumCtxAuto": True,
    # KV cache precision configured on the Ollama SERVER (OLLAMA_KV_CACHE_TYPE).
    # We cannot set it from here — it belongs to the server process — but the context
    # fit calculation must match it, and preflight warns when it looks unset.
    # q8_0 roughly halves KV memory vs f16 for negligible quality loss.
    "ollamaKvCacheType": "q8_0",
    # VRAM of the machine serving llmBaseUrl. Required when inference is remote: we
    # cannot probe another host, and probing this one would measure the wrong GPU.
    "llmHostVramMb": 0,
    # auto = collapse to one model when the host can only hold one; on/off to force.
    "singleModelMode": "auto",
    # Start each step at ollamaNumCtxAdaptiveStart; on exceed_context errors, increase and retry.
    "ollamaNumCtxAdaptive": False,
    "ollamaNumCtxAdaptiveStart": 8192,
    "ollamaNumCtxAdaptiveStep": 8192,
    "ollamaKeepAlive": "30m",
    "ollamaRequestTimeoutSec": 300,
    # Model connectivity tests must also cover a cold load of a large model.
    "modelTestTimeoutSec": 600,
    "terminalTimeoutSec": 600,
    # Unload primary before loading backup when VRAM is nearly full.
    "enableVramAwareModelSwap": True,
    # Sampling. Greedy decoding on small local models drives repetition loops, which
    # the duplicate-tool and echo guards were papering over. Overrides are merged over
    # the per-role defaults in services/sampling.py.
    "samplingDefaults": {},
    "samplingByRole": {},
    # MCP servers are optional; when one is unreachable it must fail fast rather than
    # consume an agent step's wall-clock budget.
    "mcpTimeoutSec": 20,
    "mcpConnectTimeoutSec": 5,
    "ollamaMaxRetries": 4,
    "ollamaRetryDelaySec": [0, 2, 5, 10],
    "ollamaCooldownRetryEnabled": True,
    "ollamaCooldownRetrySec": 15,
    "ollamaCooldownRetryAttempts": 2,
    "maxToolOutputCharsForLlm": 32000,
    "messagePruneThresholdPct": 60,
    "promptProfile": "full",
    "localSlmSprintPreload": True,
    "enableSemanticSprintContext": True,
    "enableHybridSearch": True,
    "semanticMinScore": 0.35,
    "semanticSprintTopK": 3,
    # excerpt = paths + short signatures (default); full = whole file bodies.
    "sprintFileContextMode": "excerpt",
    "enableObservationSummaries": True,
    "enableAgentStepRecap": True,
    "enableEpisodeSummary": True,
    "enableMessageHistoryPrune": True,
    # Cursor-style rewind: drop last assistant/tool turn(s) after failed writes.
    "enableContextRewind": True,
    "contextRewindTurns": 1,
    "enableLlmDecisionTrace": False,
    "enableLlmModelRationale": False,
    "toolOutputEchoStopAfter": 2,
    # Dev Explore → Patch → Verify phase graph (cuts open-ended read_file thrash).
    # Budgets sized so a multi-file change is reachable; the total tool-call cap and
    # the wall-clock timeout remain the real stops.
    "enableDevPhaseGraph": True,
    "devExploreMaxTools": 12,
    "devPatchMaxTools": 12,
    "devVerifyMaxTools": 8,
    "enableStepLessonMemory": True,
    # Pinned Dev core memory block (always injected; lessons merge into it).
    "enableDevCoreMemoryBlock": True,
    # Off by default: it adds one extra Ollama call per step.
    "enableLlmContextCompress": False,
    "contextCompressMinChars": 8000,
    "contextCompressMaxChars": 3500,
    "contextCompressModel": "",
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
    # Optional Discord Gateway control bot (outbound; same PC as AllHands).
    "discordBotEnabled": False,
    "discordBotToken": "",
    "discordBotGuildId": "",
    "discordBotAllowedUserIds": [],
    "discordModelPresetFast": "qwen2.5-coder:7b",
    "discordModelPresetQuality": "qwen2.5-coder:14b",
    "requireAcChecklistForDone": True,
    "confirmSimulationFallback": True,
    "simulationConfirmSeconds": 10,
    "simulationAutoAccept": False,
    "simulationAutoUseExistingFile": True,
    "duplicateToolPolicy": "strict",
    "duplicateToolHardStopExclude": [],
    "duplicateRunCommandPolicy": "strict",
    # Focus micro-steps: one AC/subtask per Dev sprint tick; rotate prompt sections per LLM iter.
    "enableFocusMicroSteps": True,
    "maxFocusStepsPerCard": 8,
    "enablePromptSectionRotation": False,
    "splitCardWhenAcOver": 3,
    "contextPacker": "off",
    "contextPackerMaxChars": 12000,
    "repomixCommand": "repomix",
    "code2promptCommand": "code2prompt",
    # Per-role prompt overrides (null/empty → shipped defaults in prompt_defaults.py).
    "agentPrompts": {
        "Product Owner": {"system": None, "stepInstructions": None},
        "Developer": {"system": None, "stepInstructions": None},
        "Code Reviewer": {"system": None, "stepInstructions": None},
        "QA Tester": {"system": None, "stepInstructions": None},
    },
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
    from backend.services.prompt_defaults import validate_agent_prompts_patch

    pid = project_id or state.CURRENT_PROJECT_ID
    current = get_workflow_settings(pid)
    updates = dict(settings)
    validate_agent_prompts_patch(updates)
    if "agentPrompts" in updates and isinstance(updates["agentPrompts"], dict):
        base_ap = dict(current.get("agentPrompts") or DEFAULT_WORKFLOW_SETTINGS.get("agentPrompts") or {})
        for role, cfg in updates["agentPrompts"].items():
            if not isinstance(cfg, dict):
                continue
            prev = dict(base_ap.get(role) or {"system": None, "stepInstructions": None})
            for key in ("system", "stepInstructions"):
                if key in cfg:
                    prev[key] = cfg[key]
            base_ap[role] = prev
        updates["agentPrompts"] = base_ap
    if not str(updates.get("qdrantApiKey") or "").strip():
        updates.pop("qdrantApiKey", None)
    if not str(updates.get("llmApiKey") or "").strip():
        updates.pop("llmApiKey", None)
    if not str(updates.get("phoneNotifyDiscordWebhookUrl") or "").strip():
        updates.pop("phoneNotifyDiscordWebhookUrl", None)
    if not str(updates.get("discordBotToken") or "").strip():
        updates.pop("discordBotToken", None)
    if "promptProfile" in updates:
        raw_profile = str(updates.get("promptProfile") or "full").strip().lower()
        if raw_profile in ("local_slm", "local", "slm", "lean"):
            updates["promptProfile"] = "local_slm"
        else:
            updates["promptProfile"] = "full"
    if "discordBotAllowedUserIds" in updates:
        raw_ids = updates.get("discordBotAllowedUserIds") or []
        if isinstance(raw_ids, str):
            raw_ids = [p.strip() for p in raw_ids.replace(",", "\n").splitlines()]
        updates["discordBotAllowedUserIds"] = [
            str(x).strip() for x in raw_ids if str(x).strip()
        ]
    current.update(updates)
    state.storage.set_setting(_settings_key(pid), json.dumps(current))
    return current


def reset_workflow_settings(project_id: str | None = None) -> Dict[str, Any]:
    """Replace workflow settings with defaults (used by tests and explicit UI reset)."""
    pid = project_id or state.CURRENT_PROJECT_ID
    defaults = dict(DEFAULT_WORKFLOW_SETTINGS)
    state.storage.set_setting(_settings_key(pid), json.dumps(defaults))
    return defaults


def restore_agent_prompt_overrides(
    project_id: str | None = None,
    role: str | None = None,
) -> Dict[str, Any]:
    """Clear per-project agent prompt overrides and persist."""
    from backend.services.prompt_defaults import AGENT_ROLES, clear_agent_prompt_overrides

    if role is not None and role not in AGENT_ROLES:
        raise ValueError(f"Unknown agent role: {role}")

    pid = project_id or state.CURRENT_PROJECT_ID
    current = get_workflow_settings(pid)
    merged = clear_agent_prompt_overrides(current, role=role)
    state.storage.set_setting(_settings_key(pid), json.dumps(merged))
    return merged


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
