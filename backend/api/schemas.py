from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BriefPayload(BaseModel):
    brief: str
    ollama_url: str = "http://localhost:11434"
    context_files: List[str] = Field(default_factory=list)


class PlanBacklogPayload(BaseModel):
    brief: str
    ollama_url: str = "http://localhost:11434"
    outline: Optional[str] = None


class ConfigPayload(BaseModel):
    projectName: str
    workspaceDir: str
    skillsDir: str
    poModel: str
    devModel: str
    crModel: str
    qaModel: str
    poBackupModel: Optional[str] = None
    devBackupModel: Optional[str] = None
    crBackupModel: Optional[str] = None
    qaBackupModel: Optional[str] = None


class SkillPayload(BaseModel):
    agent: str
    skillFile: str


class BulkSkillPayload(BaseModel):
    agent: str
    skillFiles: List[str] = Field(default_factory=list)


class CreateProjectPayload(BaseModel):
    projectName: str
    workspaceDir: str


class ManualTaskPayload(BaseModel):
    title: str
    description: str
    ollama_url: str = "http://localhost:11434"
    preferredFeatureId: Optional[str] = None


class MoveTaskPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    target_lane: str = Field(alias="toLane")
    from_lane: Optional[str] = Field(default=None, alias="fromLane")
    skip_refinement: bool = Field(default=False, alias="skipRefinement")


class ClaimReadyPayload(BaseModel):
    limit: int = 5


class DoneAuditApplyPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_ids: Optional[List[str]] = Field(default=None, alias="taskIds")
    move_to: str = Field(alias="moveTo")
    only_incomplete: bool = Field(default=True, alias="onlyIncomplete")


class UpdateTaskPayload(BaseModel):
    task_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    acceptanceCriteria: Optional[List[str]] = None
    acChecklist: Optional[List[bool]] = None
    blockedBy: Optional[List[str]] = None
    priority: Optional[int] = None


class ReorderTasksPayload(BaseModel):
    lane: str = "Backlog"
    taskIds: List[str] = Field(default_factory=list)


class EscapeSubtaskPayload(BaseModel):
    mode: str = "needs_po"


class ResolveUserPayload(BaseModel):
    answer: str
    target: str = "dev"  # dev | refinement | po


class ReindexPayload(BaseModel):
    ollama_url: str = "http://localhost:11434"
    force: bool = False


class MemoryCreatePayload(BaseModel):
    content: str
    category: str = "user_note"
    agent: str = "System"


class MemoryUpdatePayload(BaseModel):
    content: str
    category: Optional[str] = None


class RunInProgressPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    brief: str = ""
    ollama_url: str = "http://localhost:11434"
    task_id: Optional[str] = Field(default=None, alias="taskId")


class InjectToolEvidencePayload(BaseModel):
    toolName: str = "run_command"
    toolArgs: Dict[str, Any] = Field(default_factory=dict)
    toolOutput: str
    note: str = ""


class SplitTaskPayload(BaseModel):
    ollama_url: str = "http://localhost:11434"
    guidance: str = ""


class WorkflowSettingsPayload(BaseModel):
    requireBacklogApproval: Optional[bool] = None
    requireCodeReview: Optional[bool] = None
    requireDevVerification: Optional[bool] = None
    requireCleanLint: Optional[bool] = None
    requireBacklogRefinement: Optional[bool] = None
    prioritizeImplementationOverRefinement: Optional[bool] = None
    maxRefinementRoundTrips: Optional[int] = None
    maxSubtaskDepth: Optional[int] = None
    maxSubtaskSpawns: Optional[int] = None
    enableFixVerifyLoop: Optional[bool] = None
    maxFixVerifyRounds: Optional[int] = None
    autoExtendOnMaxIter: Optional[bool] = None
    autoExtendExtraIterations: Optional[int] = None
    maxInCardLintFixes: Optional[int] = None
    maxLintFanoutCards: Optional[int] = None
    lintFanoutThreshold: Optional[int] = None
    enableBackupModelOnStuck: Optional[bool] = None
    backupModelStuckSteps: Optional[int] = None
    enableSplitOnStuck: Optional[bool] = None
    requireWorkspaceStructure: Optional[bool] = None
    autoScaffoldOnStructureGap: Optional[bool] = None
    requireToolApproval: Optional[bool] = None
    toolApprovalTools: Optional[List[str]] = None
    nonBlockingToolApproval: Optional[bool] = None
    commandAutoRunMode: Optional[str] = None
    commandAllowlist: Optional[List[str]] = None
    commandDenylist: Optional[List[str]] = None
    allowChainedCommands: Optional[bool] = None
    maxMcpTools: Optional[int] = None
    mcpServers: Optional[List[Dict[str, Any]]] = None
    agentTools: Optional[Dict[str, List[str]]] = None
    agentToolsAllowWritesInRefinement: Optional[bool] = None
    customTools: Optional[List[Dict[str, Any]]] = None
    definitionOfDone: Optional[List[str]] = None
    maxSprintSteps: Optional[int] = None
    maxLlmIterationsPerStep: Optional[int] = None
    maxPoRoundTrips: Optional[int] = None
    maxToolFailuresPerStep: Optional[int] = None
    maxStuckSteps: Optional[int] = None
    maxAgentStepDurationSec: Optional[int] = None
    enableBlockedLane: Optional[bool] = None
    autoStartSprint: Optional[bool] = None
    autonomousMode: Optional[bool] = None
    maxNeedsUserPerSprint: Optional[int] = None
    needsUserCooldownSteps: Optional[int] = None
    enableWebSearch: Optional[bool] = None
    enableSemanticSearch: Optional[bool] = None
    qdrantUrl: Optional[str] = None
    qdrantApiKey: Optional[str] = None
    embedModel: Optional[str] = None
    ollamaNumCtx: Optional[int] = None
    ollamaNumCtxByRole: Optional[Dict[str, int]] = None
    ollamaNumCtxAuto: Optional[bool] = None
    ollamaKeepAlive: Optional[str] = None
    ollamaRequestTimeoutSec: Optional[int] = None
    terminalTimeoutSec: Optional[int] = None
    enableVramAwareModelSwap: Optional[bool] = None
    ollamaMaxRetries: Optional[int] = None
    ollamaRetryDelaySec: Optional[List[int]] = None
    ollamaCooldownRetryEnabled: Optional[bool] = None
    ollamaCooldownRetrySec: Optional[int] = None
    ollamaCooldownRetryAttempts: Optional[int] = None
    maxToolOutputCharsForLlm: Optional[int] = None
    messagePruneThresholdPct: Optional[int] = None
    enableSemanticSprintContext: Optional[bool] = None
    enableHybridSearch: Optional[bool] = None
    semanticMinScore: Optional[float] = None
    semanticSprintTopK: Optional[int] = None
    sprintFileContextMode: Optional[str] = None
    enableObservationSummaries: Optional[bool] = None
    enableEpisodeSummary: Optional[bool] = None
    enableStepLessonMemory: Optional[bool] = None
    enableLlmContextCompress: Optional[bool] = None
    contextCompressMinChars: Optional[int] = None
    contextCompressMaxChars: Optional[int] = None
    contextCompressModel: Optional[str] = None
    pauseSprintOnNeedsUser: Optional[bool] = None
    autoFormatAfterEdit: Optional[bool] = None
    phoneNotifyEnabled: Optional[bool] = None
    phoneNotifyProvider: Optional[str] = None
    phoneNotifyDiscordWebhookUrl: Optional[str] = None
    phoneNotifyOnNeedsUser: Optional[bool] = None
    phoneNotifyOnNeedsPo: Optional[bool] = None
    phoneNotifyOnToolApproval: Optional[bool] = None
    phoneNotifyOnSprintEnd: Optional[bool] = None
    phoneNotifyOnBoardStatus: Optional[bool] = None
    phoneNotifyOnStuckEscalation: Optional[bool] = None
    phoneNotifyOnStepTimeout: Optional[bool] = None
    phoneNotifyOnBackupArmed: Optional[bool] = None
    discordBotEnabled: Optional[bool] = None
    discordBotToken: Optional[str] = None
    discordBotGuildId: Optional[str] = None
    discordBotAllowedUserIds: Optional[List[str]] = None
    discordModelPresetFast: Optional[str] = None
    discordModelPresetQuality: Optional[str] = None
    requireAcChecklistForDone: Optional[bool] = None
    confirmSimulationFallback: Optional[bool] = None
    simulationConfirmSeconds: Optional[int] = None
    simulationAutoAccept: Optional[bool] = None
    simulationAutoUseExistingFile: Optional[bool] = None


class DiagnoseTaskPayload(BaseModel):
    ollamaUrl: str = "http://localhost:11434"


class DeleteTaskPayload(BaseModel):
    task_id: str


class ChatPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str
    agent: str = "dev"
    ollama_url: str = "http://localhost:11434"
    context_files: List[str] = Field(default_factory=list, alias="contextFiles")
    task_id: Optional[str] = Field(default=None, alias="taskId")
    allow_done_retry: bool = Field(default=False, alias="allowDoneRetry")


class SaveFilePayload(BaseModel):
    path: str
    content: str
    author: Optional[str] = None


class SearchFilesPayload(BaseModel):
    query: str
    limit: int = 50


class TerminalPayload(BaseModel):
    command: str


class SprintRunPayload(BaseModel):
    brief: str = ""
    ollama_url: str = "http://localhost:11434"
    max_steps: int = 20
