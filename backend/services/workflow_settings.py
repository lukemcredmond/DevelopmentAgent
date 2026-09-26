"""Per-project workflow settings and sprint summary persistence."""

import json
import logging
from typing import Any, Dict, List

from backend import state
from backend.config import MAX_SPRINT_STEPS

logger = logging.getLogger(__name__)

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
    "enableFixVerifyLoop": False,
    # Keep fix-verify cheap: 1 round when enabled (system auto-verify covers lint).
    "maxFixVerifyRounds": 1,
    # Run project lint after Dev writes without requiring model-initiated analyze.
    "enableSystemAutoVerify": True,
    # Abort further fix-verify rounds on hard agent stops (tool_failure, plan_exhausted, …).
    "fixVerifyAbortOnHardStop": True,
    # Auto-extend one more chunk of iterations on max_iterations when progress is evident.
    # Off by default — manual Extend remains; auto +4 was a hidden latency tax.
    "autoExtendOnMaxIter": False,
    "autoExtendExtraIterations": 4,
    "identicalSuccessWriteLimit": 2,
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
    "circuitBreakerMaxBadExits": 2,
    "circuitBreakerIdenticalPatchFails": 3,
    # Park a card after this many consecutive explore-exhaust / duplicate-tool steps with no write.
    "maxConsecutiveNoWriteStall": 2,
    # Auto-sprint: backoff when steps interrupt before any Ollama call (crash/retry storms).
    "enableAutoSprintInterruptBackoff": True,
    "autoSprintInterruptBackoffSec": 5,
    "autoSprintInterruptBackoffMaxSec": 120,
    "interruptEarlyMaxMs": 30000,
    # Pause an auto sprint before a fourth identical task/reason zero-work retry.
    "enableZeroWorkRetryWatchdog": True,
    "zeroWorkRetryWatchdogMax": 3,
    "maxZeroWorkRecoveryAttempts": 2,
    # Cap Explore→Patch→Verify cycles per card before forcing stuck / split.
    "maxDevPhaseCyclesPerCard": 8,
    "maxDevStepsPerCard": 8,
    # Optional: run independent In Progress cards concurrently (workspace write-locked).
    "enableParallelIndependentCards": False,
    "maxParallelDevCards": 2,
    "requireWorkspaceStructure": True,
    "autoScaffoldOnStructureGap": True,
    # Block lane advance when written files contain TODO/stub/placeholder content.
    "requireFileCompleteness": True,
    # When on, scaffolder may advance if another card explicitly owns the stubbed file.
    "allowStubDelegation": False,
    # Optional regex allowlist for placeholder patterns (advanced).
    "placeholderAllowlist": [],
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
    "maxLlmIterationsPerStep": 12,
    # The true safety net is total tool calls plus the wall-clock cap, not LLM turns.
    "maxToolCallsPerStep": 80,
    "maxPoRoundTrips": 3,
    "maxStuckSteps": 3,
    # Wall-clock cap for one agent tool loop (LLM+tools). Default 45 minutes.
    "maxAgentStepDurationSec": 2700,
    # Auto-move cards with unmet blockedBy into the Blocked lane (healthy wait).
    "enableBlockedLane": True,
    # When multiple cards fail on the same file, create one fix card and block dependents.
    "enableFileBlockerOrchestration": True,
    "fileBlockerMinDependents": 2,
    "fileBlockerAutoCreateFixCard": True,
    "maxToolFailuresPerStep": 4,
    # Agent efficiency (local Ollama): lean prompts, phase model routing, per-turn tool caps.
    "agentEfficiencyMode": "high",
    "enablePhaseModelRouting": True,
    "devExploreModel": "qwen2.5-coder:7b",
    "devPatchModel": "qwen2.5-coder:14b",
    "maxToolsPerLlmTurn": 3,
    "autoStartSprint": True,
    "autonomousMode": False,
    # scrum = PO/Dev/CR/QA roles; implementer = Cursor-like single coding agent on cards.
    "executionProfile": "implementer",
    # Plan & Run defaults to implementer unless set to scrum or inherit.
    "planRunExecutionProfile": "implementer",
    "planRunSkipPoWhenActionable": True,
    # Optional frontier cloud model for Developer (OpenAI-compatible API).
    "enableCloudDevProvider": False,
    "cloudDevBaseUrl": "",
    "cloudDevApiKey": "",
    "cloudDevModel": "",
    "cloudDevUseFor": "implementer_autonomous",
    "cloudDevStuckSteps": 1,
    "cloudDevRequestTimeoutSec": 900,
    # Per-card LLM thread continuity across sprint steps on the same card.
    "enableCardSessionContinuity": True,
    "enableCardSessionSummarize": True,
    # Auto-load AGENTS.md / .cursor/rules into system prompt.
    "enableWorkspaceRulesInject": True,
    "workspaceRulesMaxChars": 12000,
    # Soften completeness/CR/QA gates during implementer auto-sprint.
    "implementerRelaxGatesOnAutoSprint": True,
    "implementerRelaxGatesAlways": True,
    "implementerStopOnLatch": True,
    "implementerStopOnLatchCount": 1,
    "useComposerDevStep": True,
    "implementerMaxLlmIterationsPerStep": 5,
    "implementerMaxDevStepWallSec": 180,
    "implementerMaxPreloadTokens": 4000,
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
    "singleModelMode": "on",
    # Start each step at ollamaNumCtxAdaptiveStart; on exceed_context errors, increase and retry.
    "ollamaNumCtxAdaptive": True,
    "ollamaNumCtxAdaptiveStart": 6144,
    "ollamaNumCtxAdaptiveStep": 8192,
    "ollamaKeepAlive": "30m",
    "warmModelOnSprintStart": True,
    "devNumPredictDefault": 2048,
    "devEvalTimeoutSec": 60,
    "runawayAbortCapPerStep": 2,
    "forcedToolNumPredict": 512,
    "ollamaRequestTimeoutSec": 900,
    # Abort a stream if prefill finished and no eval tokens arrive (hung empty gen).
    "ollamaEmptyGenerationTimeoutSec": 90,
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
    "devExploreMaxTools": 2,
    # When explore budget is hit, transition to Patch in the same step (block further reads).
    "devExploreForcePatchInStep": True,
    "poNumPredictOverride": False,
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
    # GVS5H-style per-card ledger (plan.md / notes.md / tasks.json) injected before transcript.
    "enableCardLedger": True,
    # First Dev visit: no-code brainstorm into notes.md before Explore/Patch.
    "enableDevIdeation": True,
    # Failing subprocess lint/test overrides Done even if the agent claims solved.
    "enableOracleDoneOverride": True,
    # Reissued identical next-work with no writes parks the card.
    "enableSameNextTaskStop": True,
    # Summarize truncated (non-empty) generations; never retry empty-gen.
    "enableCutoffSummarizer": True,
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


# Keys saved as "" should not override shipped defaults (merge would keep "").
_PERFORMANCE_UNSET_IF_EMPTY = (
    "devExploreModel",
    "devPatchModel",
)

# Backfill when older projects never stored performance keys.
_PERFORMANCE_BACKFILL_KEYS = (
    "devExploreModel",
    "devPatchModel",
    "devExploreMaxTools",
    "devExploreForcePatchInStep",
    "ollamaNumCtxAdaptive",
    "ollamaNumCtxAdaptiveStart",
    "enablePhaseModelRouting",
)


def normalize_performance_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Drop empty performance overrides and clamp stale PO decode caps."""
    out = dict(settings or {})
    for key in _PERFORMANCE_UNSET_IF_EMPTY:
        if key in out and not str(out.get(key) or "").strip():
            out.pop(key, None)
    try:
        from backend.services.po_clarification import PO_NUM_PREDICT_DEFAULT

        cap = int(PO_NUM_PREDICT_DEFAULT)
    except Exception:
        cap = 1024
    if not bool(out.get("poNumPredictOverride")):
        by_role = out.get("samplingByRole")
        if isinstance(by_role, dict):
            po = by_role.get("po")
            if isinstance(po, dict) and "num_predict" in po:
                try:
                    current = int(po.get("num_predict") or cap)
                except (TypeError, ValueError):
                    current = cap
                if current > cap:
                    po = dict(po)
                    po["num_predict"] = cap
                    by_role = dict(by_role)
                    by_role["po"] = po
                    out["samplingByRole"] = by_role
    return out


def migrate_performance_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    """One-time backfill of performance defaults for projects saved before tuning."""
    out = normalize_performance_settings(settings)
    changed = False
    for key in _PERFORMANCE_BACKFILL_KEYS:
        if key not in settings:
            default_val = DEFAULT_WORKFLOW_SETTINGS.get(key)
            if default_val is not None and out.get(key) != default_val:
                out[key] = default_val
                changed = True
    if changed:
        out["performanceSettingsVersion"] = max(
            int(out.get("performanceSettingsVersion") or 0),
            1,
        )
    return out


def get_workflow_settings(project_id: str | None = None) -> Dict[str, Any]:
    pid = project_id or state.CURRENT_PROJECT_ID
    raw = state.storage.get_setting(_settings_key(pid))
    if not raw:
        return dict(DEFAULT_WORKFLOW_SETTINGS)
    try:
        merged = migrate_performance_settings({**DEFAULT_WORKFLOW_SETTINGS, **json.loads(raw)})
        from backend.services.llm_provider import normalize_llm_provider_settings

        return normalize_llm_provider_settings(merged)
    except json.JSONDecodeError:
        return dict(DEFAULT_WORKFLOW_SETTINGS)


def _sync_project_sidecar(project_id: str) -> None:
    try:
        from backend.services.project_file import sync_project_sidecar

        sync_project_sidecar(project_id)
    except Exception:
        logger.exception("Failed to sync allhands.project.json after workflow settings save")


def save_workflow_settings(
    settings: Dict[str, Any],
    project_id: str | None = None,
    *,
    sync_sidecar: bool = True,
) -> Dict[str, Any]:
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
    if "executionProfile" in updates:
        raw_profile = str(updates.get("executionProfile") or "scrum").strip().lower()
        updates["executionProfile"] = (
            "implementer" if raw_profile == "implementer" else "scrum"
        )
    current.update(updates)
    current = migrate_performance_settings(current)
    from backend.services.llm_provider import normalize_llm_provider_settings

    current = normalize_llm_provider_settings(current)
    state.storage.set_setting(_settings_key(pid), json.dumps(current))
    if sync_sidecar:
        _sync_project_sidecar(pid)
    return current


def reset_workflow_settings(project_id: str | None = None) -> Dict[str, Any]:
    """Replace workflow settings with defaults (used by tests and explicit UI reset)."""
    pid = project_id or state.CURRENT_PROJECT_ID
    defaults = dict(DEFAULT_WORKFLOW_SETTINGS)
    state.storage.set_setting(_settings_key(pid), json.dumps(defaults))
    _sync_project_sidecar(pid)
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
    _sync_project_sidecar(pid)
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


def get_execution_profile(settings: Dict[str, Any] | None = None) -> str:
    ws = settings if settings is not None else get_workflow_settings()
    raw = str(ws.get("executionProfile") or "scrum").strip().lower()
    return "implementer" if raw == "implementer" else "scrum"


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
