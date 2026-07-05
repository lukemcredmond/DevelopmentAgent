from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class BriefPayload(BaseModel):
    brief: str
    ollama_url: str = "http://localhost:11434"
    context_files: List[str] = Field(default_factory=list)


class ConfigPayload(BaseModel):
    projectName: str
    workspaceDir: str
    skillsDir: str
    poModel: str
    devModel: str
    crModel: str
    qaModel: str


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


class MoveTaskPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(alias="taskId")
    target_lane: str = Field(alias="toLane")
    from_lane: Optional[str] = Field(default=None, alias="fromLane")


class UpdateTaskPayload(BaseModel):
    task_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    acceptanceCriteria: Optional[List[str]] = None
    blockedBy: Optional[List[str]] = None
    priority: Optional[int] = None


class ReorderTasksPayload(BaseModel):
    lane: str = "Backlog"
    taskIds: List[str] = Field(default_factory=list)


class EscapeSubtaskPayload(BaseModel):
    mode: str = "needs_po"


class ResolveUserPayload(BaseModel):
    answer: str


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
    maxRefinementRoundTrips: Optional[int] = None
    maxSubtaskDepth: Optional[int] = None
    maxSubtaskSpawns: Optional[int] = None
    enableFixVerifyLoop: Optional[bool] = None
    maxFixVerifyRounds: Optional[int] = None
    requireToolApproval: Optional[bool] = None
    toolApprovalTools: Optional[List[str]] = None
    definitionOfDone: Optional[List[str]] = None
    maxSprintSteps: Optional[int] = None
    maxLlmIterationsPerStep: Optional[int] = None
    maxPoRoundTrips: Optional[int] = None
    maxToolFailuresPerStep: Optional[int] = None
    maxStuckSteps: Optional[int] = None
    autoStartSprint: Optional[bool] = None
    autonomousMode: Optional[bool] = None
    maxNeedsUserPerSprint: Optional[int] = None
    enableWebSearch: Optional[bool] = None
    enableSemanticSearch: Optional[bool] = None
    qdrantUrl: Optional[str] = None
    embedModel: Optional[str] = None
    ollamaNumCtx: Optional[int] = None


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
