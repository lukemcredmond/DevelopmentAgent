export type AgentId = 'po' | 'dev' | 'cr' | 'qa'

export type BoardLane =
  | 'Features'
  | 'Backlog'
  | 'Pending Approval'
  | 'Refinement'
  | 'Blocked'
  | 'In Progress'
  | 'Needs PO'
  | 'Needs User'
  | 'Code Review'
  | 'QA'
  | 'Done'

export type LogType = 'info' | 'success' | 'warning' | 'error'

export interface TaskFile {
  path: string
  action?: string
  lastTouchedAt?: string
}

export interface TaskGitCommit {
  hash: string
  message?: string
  timestamp?: string
  remoteUrl?: string
}

export interface TaskDecision {
  timestamp: string
  agent: string
  type: string
  summary: string
  detail?: string
}

export interface TaskTranscriptEntry {
  timestamp: string
  role: string
  content: string
  agent?: string
  toolName?: string
  toolSuccess?: boolean
  toolArgs?: Record<string, unknown>
  toolOutput?: string
}

export interface AcVerificationRow {
  criterion: string
  expected?: string
  actual?: string
  met?: boolean | null
  updatedAt?: string
}

export interface QaFailure {
  reason: string
  output?: string
  timestamp: string
}

export interface QaEvidence {
  playbookRun: boolean
  commands: string[]
  passed: boolean
  userOverride?: boolean
}

export interface UserResolution {
  question: string
  answer: string
  timestamp: string
  targetLane: string
}

export interface DependencyOutcome {
  taskId: string
  title: string
  completedAt: string
  summary: string
  decisions?: TaskDecision[]
  files?: string[]
  refinementNotes?: string
  spikeReport?: string
}

export interface AgentWorkItemFlowMatch {
  toolNames?: string[]
  stopReasons?: string[]
  tags?: string[]
}

export interface AgentWorkItem {
  id: string
  label: string
  status: 'pending' | 'done' | 'blocked'
  source?: 'derived'
  agentRole?: string
  updatedAt?: string
  flowMatch?: AgentWorkItemFlowMatch
}

export interface TaskFlowNode {
  kind: 'llm' | 'tool'
  id: string
  timestamp?: string
  agent?: string
  agentId?: string
  taskId?: string
  runId?: string
  model?: string
  iteration?: number
  durationMs?: number
  error?: string
  requestMessages?: Array<{ role?: string; content?: string } | string>
  responseContent?: string
  toolCalls?: unknown[]
  toolNames?: string[]
  memoriesUsed?: Array<{ category?: string; content?: string }>
  decisionsIncluded?: number
  toolName?: string
  toolArgs?: Record<string, unknown>
  toolOutput?: string
  success?: boolean
  status?: string
  source?: string
  traceId?: string
  textChars?: number
  exitReason?: string
  duplicateSkip?: boolean
  workItemIds?: string[]
  primaryWorkItemId?: string
}

export interface TaskFlowWorkItemIndexEntry {
  label?: string
  status?: string
  flowMatch?: AgentWorkItemFlowMatch
  nodeIds?: string[]
  llmCalls?: number
  toolCalls?: number
  toolCounts?: Record<string, number>
  failedToolCalls?: number
  duplicateSkips?: number
  llmMs?: number
  toolMs?: number
  durationMs?: number
  firstAt?: string | null
  lastAt?: string | null
  toolLinked?: boolean
}

export interface TaskFlowTotals {
  llmCalls?: number
  toolCalls?: number
  llmMs?: number
  toolMs?: number
  failedToolCalls?: number
  duplicateSkips?: number
}

export interface TaskFlowResponse {
  taskId: string
  nodes: TaskFlowNode[]
  traces?: Array<{
    path?: string
    traceId?: string
    startedAt?: string
    endedAt?: string
    status?: string
    exitReason?: string
    agent?: string
  }>
  count?: number
  totalCount?: number
  includeFull?: boolean
  workItemIndex?: Record<string, TaskFlowWorkItemIndexEntry>
  totals?: TaskFlowTotals
  agentWorkItems?: AgentWorkItem[]
  suggestedFocusWorkItemId?: string | null
}

export interface TaskFlowSummaryResponse {
  taskId: string
  workItemIndex?: Record<string, TaskFlowWorkItemIndexEntry>
  agentWorkItems?: AgentWorkItem[]
  totals?: TaskFlowTotals
  count?: number
  totalCount?: number
  suggestedFocusWorkItemId?: string | null
}

export interface Task {
  id: string
  title: string
  description: string
  status: BoardLane | string
  files?: (TaskFile | string)[]
  decisions?: TaskDecision[]
  decisionsTruncated?: boolean
  transcript?: TaskTranscriptEntry[]
  transcriptTruncated?: boolean
  acceptanceCriteria?: string[]
  acChecklist?: boolean[]
  priority?: number
  blockedBy?: string[]
  dependencyOutcomes?: DependencyOutcome[]
  relatedTaskIds?: string[]
  gitCommit?: TaskGitCommit | null
  qaFailure?: QaFailure | null
  qaEvidence?: QaEvidence | null
  userQuestion?: string | null
  needsUserReason?: string | null
  needsUserAction?: string | null
  userResolutions?: UserResolution[]
  needsUserCooldownUntilStep?: number | null
  needsUserDuplicate?: boolean
  poRoundTrips?: number
  workType?: 'planning' | 'implementation' | 'review' | 'qa' | 'user_action' | 'spike' | 'feature'
  requiresDev?: boolean
  requiresQa?: boolean
  createdBy?: 'po' | 'user' | 'split'
  lastDiagnosis?: TaskDiagnosis
  lastCommandDiagnostics?: CommandDiagnostic[]
  lastStepOutcome?: LastStepOutcome | null
  lastStepDiagnostics?: Partial<LastStepDiagnostics> | null
  autoExtendUsed?: boolean
  retrievalFeedback?: {
    weakHits?: number
    totalHits?: number
    minScore?: number
    note?: string
  }
  refinementStatus?:
    | 'pending'
    | 'dev_reviewed'
    | 'po_updated'
    | 'ready'
    | 'blocked'
    | 'spike_pending'
  refinementComplete?: boolean
  refinementRoundTrips?: number
  refinementQuestions?: string[]
  refinementNotes?: string | null
  refinementDevReady?: boolean
  needsSpike?: boolean
  spikeForTaskId?: string | null
  spikeStatus?: 'pending' | 'running' | 'complete'
  spikeObjective?: string | null
  spikeReport?: string | null
  parentTaskId?: string | null
  subtaskIds?: string[]
  executionOrder?: number
  subtaskSpawnCount?: number
  subtaskEscapeCount?: number
  subtaskSkipped?: boolean
  featureId?: string | null
  featureHistory?: FeatureHistoryEntry[]
  childTaskIds?: string[]
  featureRollup?: FeatureRollup | null
  stuckLoops?: number
  lastStepProgress?: StepProgress | null
  agentWorkItems?: AgentWorkItem[]
  qaMarkdownPath?: string | null
  userStory?: string
  scope?: string
  outOfScope?: string
  testPlan?: string
  specMarkdownPath?: string | null
  specVersion?: number
  expectedSummary?: string
  actualSummary?: string
  acVerification?: AcVerificationRow[]
  sddInheritedFromFeature?: string[]
  agentUsage?: Record<string, AgentUsageEntry> | null
}

export interface AgentUsageEntry {
  stepCount?: number
  callCount?: number
  durationMs?: number
  ollamaMs?: number
  toolMs?: number
  promptTokens?: number
  evalTokens?: number
  totalTokens?: number
  tokensReported?: boolean
}

export interface FeatureRollupChild {
  id: string
  title: string
  status: string
  lane: string
}

export interface FeatureRollupDecision {
  agent: string
  type: string
  summary: string
  timestamp?: string
  childTaskId?: string
  childTitle?: string
}

export interface FeatureRollup {
  children: FeatureRollupChild[]
  files: string[]
  recentDecisions: FeatureRollupDecision[]
}

export interface FeatureHistoryEntry {
  timestamp: string
  source: string
  requestTitle: string
  requestBody: string
  poSummary: string
  childTaskId?: string
}

export interface TaskDiagnosis {
  summary: string
  problem: string
  rootCause: string
  evidence: string[]
  recommendedAction: string
  suggestedAgent: string
  taskId?: string
}

export interface LlmDebugEntry {
  id: string
  timestamp: string
  agent: string
  agentId: string
  taskId?: string
  runId?: string
  model: string
  iteration: number
  requestMessages: unknown[]
  toolNames: string[]
  responseContent: string
  responseToolCalls: unknown[]
  durationMs: number
  error?: string
  memoriesUsed?: Array<{ category: string; content: string }>
  decisionsIncluded?: number
  promptTokens?: number
  evalTokens?: number
  totalTokens?: number
  tokensReported?: boolean
}

export interface ModelTimelineItem {
  kind: 'llm' | 'tool'
  id?: string
  timestamp?: string
  agent?: string
  agentId?: string
  taskId?: string
  runId?: string
  model?: string
  iteration?: number
  durationMs?: number
  error?: string
  content?: string
  toolCalls?: unknown[]
  toolNames?: string[]
  memoriesUsed?: Array<{ category: string; content: string }>
  decisionsIncluded?: number
  toolName?: string
  toolArgs?: Record<string, unknown>
  toolOutput?: string
  success?: boolean
  status?: string
  source?: string
}

export interface ModelTimelineThread {
  taskId: string
  items: ModelTimelineItem[]
}

export interface ModelTimelineResponse {
  items: ModelTimelineItem[]
  threads: ModelTimelineThread[]
  count: number
}

export interface StackCatalogEntry {
  id: string
  label: string
  description: string
  recommendedSkills: string[]
  exampleCommands: string[]
  agentsWithTools: string[]
  notes: string
  tools: Record<string, string[]>
  matched?: boolean
}

export interface StackCatalogResponse {
  stacks: StackCatalogEntry[]
  briefCategories: BriefCategory[]
  agents: string[]
}

export type Board = Partial<Record<BoardLane, Task[]>>

export interface McpServerConfig {
  name: string
  transport?: string
  command?: string
  args?: string[]
  url?: string
  headers?: Record<string, string>
  enabled?: boolean
  enabledTools?: string[]
  disabledTools?: string[]
}

export interface CustomToolDef {
  id?: string
  name: string
  description: string
  parameters: Record<string, unknown>
  agents: string[]
  executor: 'shell' | 'http' | 'sql'
  scope?: 'project' | 'global'
  shell?: { command?: string }
  http?: { url?: string; method?: string; headers?: Record<string, string>; timeoutSec?: number }
  sql?: {
    connections?: Record<string, string>
    readOnly?: boolean
    maxRows?: number
  }
}

export interface ToolsCatalogResponse {
  builtins: Array<{ name: string; description: string; parameters: Record<string, unknown>; kind: string }>
  customTools: CustomToolDef[]
  agents: Record<string, { agentId: string; tools: string[] }>
  presets: { query_sql?: CustomToolDef }
  agentTools: Record<string, string[]>
  agentToolsAllowWritesInRefinement: boolean
}

export interface WorkflowSettings {
  requireBacklogApproval: boolean
  requireCodeReview: boolean
  requireDevVerification?: boolean
  requireCleanLint?: boolean
  requireBacklogRefinement?: boolean
  prioritizeImplementationOverRefinement?: boolean
  maxRefinementRoundTrips?: number
  maxSubtaskDepth?: number
  maxSubtaskSpawns?: number
  enableFixVerifyLoop?: boolean
  maxFixVerifyRounds?: number
  autoExtendOnMaxIter?: boolean
  autoExtendExtraIterations?: number
  maxInCardLintFixes?: number
  maxLintFanoutCards?: number
  lintFanoutThreshold?: number
  enableBackupModelOnStuck?: boolean
  backupModelStuckSteps?: number
  /** After backup attempts fail at maxStuckSteps, auto-split once before Needs PO. */
  enableSplitOnStuck?: boolean
  requireWorkspaceStructure?: boolean
  autoScaffoldOnStructureGap?: boolean
  requireToolApproval?: boolean
  toolApprovalTools?: string[]
  nonBlockingToolApproval?: boolean
  commandAutoRunMode?: 'off' | 'allowlist' | 'denylist' | 'all'
  commandAllowlist?: string[]
  commandDenylist?: string[]
  allowChainedCommands?: boolean
  maxMcpTools?: number
  mcpServers?: McpServerConfig[]
  /** Per-agent tool allowlists; empty/missing role → built-in defaults */
  agentTools?: Record<string, string[]>
  agentToolsAllowWritesInRefinement?: boolean
  customTools?: CustomToolDef[]
  definitionOfDone: string[]
  maxSprintSteps: number
  maxLlmIterationsPerStep: number
  maxPoRoundTrips: number
  maxStuckSteps?: number
  /** Wall-clock seconds for one agent tool loop (default 2700 = 45 min). */
  maxAgentStepDurationSec?: number
  /** Auto-move unmet blockedBy cards into Blocked lane (default true). */
  enableBlockedLane?: boolean
  maxToolFailuresPerStep?: number
  autoStartSprint?: boolean
  autonomousMode?: boolean
  maxNeedsUserPerSprint?: number
  needsUserCooldownSteps?: number
  enableWebSearch?: boolean
  enableSemanticSearch?: boolean
  qdrantUrl?: string
  qdrantApiKey?: string
  qdrantApiKeyConfigured?: boolean
  embedModel?: string
  ollamaNumCtx?: number
  ollamaNumCtxByRole?: Partial<Record<'po' | 'dev' | 'cr' | 'qa', number>>
  ollamaNumCtxAuto?: boolean
  ollamaKeepAlive?: string
  ollamaRequestTimeoutSec?: number
  terminalTimeoutSec?: number
  enableVramAwareModelSwap?: boolean
  ollamaMaxRetries?: number
  ollamaRetryDelaySec?: number[]
  ollamaCooldownRetryEnabled?: boolean
  ollamaCooldownRetrySec?: number
  ollamaCooldownRetryAttempts?: number
  maxToolOutputCharsForLlm?: number
  messagePruneThresholdPct?: number
  enableSemanticSprintContext?: boolean
  enableHybridSearch?: boolean
  semanticMinScore?: number
  semanticSprintTopK?: number
  sprintFileContextMode?: 'excerpt' | 'full' | string
  enableObservationSummaries?: boolean
  enableEpisodeSummary?: boolean
  enableStepLessonMemory?: boolean
  enableLlmContextCompress?: boolean
  contextCompressMinChars?: number
  contextCompressMaxChars?: number
  contextCompressModel?: string
  pauseSprintOnNeedsUser?: boolean
  autoFormatAfterEdit?: boolean
  phoneNotifyEnabled?: boolean
  phoneNotifyProvider?: 'discord' | string
  phoneNotifyDiscordWebhookUrl?: string
  phoneNotifyDiscordWebhookConfigured?: boolean
  phoneNotifyOnNeedsUser?: boolean
  phoneNotifyOnNeedsPo?: boolean
  phoneNotifyOnToolApproval?: boolean
  phoneNotifyOnSprintEnd?: boolean
  phoneNotifyOnBoardStatus?: boolean
  phoneNotifyOnStuckEscalation?: boolean
  phoneNotifyOnStepTimeout?: boolean
  phoneNotifyOnBackupArmed?: boolean
  discordBotEnabled?: boolean
  discordBotToken?: string
  discordBotTokenConfigured?: boolean
  discordBotGuildId?: string
  discordBotAllowedUserIds?: string[]
  discordModelPresetFast?: string
  discordModelPresetQuality?: string
  requireAcChecklistForDone?: boolean
  /** When Ollama returns SIMULATION_FALLBACK, wait for UI confirm before applying. */
  confirmSimulationFallback?: boolean
  /** Countdown seconds before auto-accepting offline simulation (1–60). */
  simulationConfirmSeconds?: number
  /** When true, auto-accept after countdown (with 3s grace). Default false = wait for explicit confirm. */
  simulationAutoAccept?: boolean
  /** When true (default), dev offline steps use an existing workspace file without showing the popup. */
  simulationAutoUseExistingFile?: boolean
  duplicateToolPolicy?: string
  duplicateToolHardStopExclude?: string[]
  autoSprintSessionRefreshEnabled?: boolean
  autoSprintSessionRefreshMinutes?: number
  autoSprintHardReload?: boolean
}

export interface RecentToolEntry {
  toolName: string
  toolSuccess: boolean
  toolOutput: string
  durationMs: number
  timestamp: string
}

export interface CommandDiagnostic {
  file: string
  line: number
  column: number
  severity: string
  message: string
}

export interface ToolExecutionEvent {
  id: string
  runId?: string
  taskId?: string
  agent: string
  toolName: string
  toolArgs?: Record<string, unknown>
  toolSuccess?: boolean
  toolOutput?: string
  durationMs?: number
  timestamp: string
  status: 'running' | 'completed' | 'failed' | 'awaiting_approval'
  source: 'agent' | 'manual' | 'replay' | 'orchestrator' | 'context_inject' | 'user'
  exitCode?: number
  runCommandStatus?: string
  command?: string
  diagnostics?: CommandDiagnostic[]
  diagnosticsCount?: number
}

export interface ToolDefinition {
  name: string
  description: string
  parameters: Record<string, unknown>
  kind?: string
  scope?: 'project' | 'global'
}

export type ToolProbeStatus = 'pass' | 'fail' | 'skip'

export interface ToolProbeResult {
  toolName: string
  status: ToolProbeStatus
  success: boolean
  output: string
  durationMs: number
  hints: string[]
  probeArgs: Record<string, unknown>
  skipReason?: string | null
  mode?: 'smoke' | 'llm'
  model?: string
  modelCalledTool?: boolean
  llmContent?: string
}

export interface ToolRegistryResponse {
  agent: string
  tools: ToolDefinition[]
}

export interface ToolExecutePayload {
  agent: string
  toolName: string
  arguments: Record<string, unknown>
  taskId?: string
}

export interface ToolExecuteResult {
  toolName: string
  toolArgs: Record<string, unknown>
  toolSuccess: boolean
  toolOutput: string
  durationMs: number
  timestamp: string
  agent: string
  agentId: string
  taskId?: string
  source: string
  runId: string
}

export interface TranscriptToolEntry {
  index: number
  toolName: string
  toolArgs: Record<string, unknown>
  toolSuccess?: boolean
  timestamp?: string
  source?: string
  content?: string
}

export interface ToolReplayPayload {
  taskId: string
  entryIndices?: number[]
  failedOnly?: boolean
}

export interface AgentRunState {
  runId: string
  taskId: string
  agent: string
  status: 'idle' | 'thinking' | 'tool_executing' | 'awaiting_approval' | 'completed' | 'failed'
  currentTool?: string | null
  startedAt: string
  error?: string | null
  iteration?: number
  maxIterations?: number
  recentTools?: RecentToolEntry[]
  intent?: string | null
  cardProgress?: CardWorkProgress | null
  currentToolDetail?: string | null
  fixVerifyRound?: number | null
  fixVerifyMaxRounds?: number | null
}

export interface PendingToolApproval {
  id: string
  runId: string
  taskId?: string
  agent: string
  toolName: string
  toolArgs?: Record<string, unknown>
  timestamp: string
  nonBlocking?: boolean
}

export interface BriefChangelogEntry {
  source: string
  summary: string
  snippet?: string
  timestamp: string
}

export interface SprintSummary {
  stepsRun: number
  completed: string[]
  qaFailed: string[]
  blocked: string[]
  needsPo: number
  needsUser: number
  status?: 'completed' | 'idle' | 'cancelled' | 'max_steps' | 'simulation_pending' | 'session_refresh'
}

export interface CardWorkProgress {
  subtasksDone?: number
  subtasksTotal?: number
  stepsOnCard?: number
  stuckLoops?: number
  poRoundTrips?: number
  gatesRemaining?: string[]
  filesThisStep?: string[]
  acCount?: number
  lane?: string
  agentWorkItems?: AgentWorkItem[]
}

export interface SprintProgress {
  phase: 'po_plan' | 'sprint_step' | 'done' | 'cancelled'
  step: number
  maxSteps: number
  agent: string
  taskId: string
  taskTitle: string
  lane: string
  status?: string
  intent?: string
  cardProgress?: CardWorkProgress
}

export interface StepProgress {
  taskId?: string | null
  iterationsUsed: number
  iterationsMax: number
  toolsUsed: string[]
  lastTools?: { toolName?: string; success?: boolean; summary?: string }[]
  planRejections?: number
  textRejections?: number
  lastToolSummary?: string
  stuckLoop?: boolean
  durationMs?: number
  intent?: string
  cardProgress?: CardWorkProgress
  filesThisStep?: string[]
  whyCardStayed?: string
  suggestedAction?: string
}

export interface LastStepOutcome {
  taskId: string
  agent: string
  laneBefore: string
  laneAfter: string
  toolFailures: number
  ok: boolean
  message: string
  stopReason?: string
  whyCardStayed?: string
  suggestedAction?: string
  modelResponseType?: string
  planRejections?: number
  textRejections?: number
  toolsUsed?: string[]
  agentResultSnippet?: string
  stepProgress?: StepProgress
}

export interface LastStepDiagnostics {
  traceId: string
  projectId: string
  taskId: string
  taskTitle: string
  agent: string
  status?: 'running' | 'complete'
  startedAt: string
  endedAt?: string
  durationMs: number
  ollamaMsTotal?: number
  ollamaCallCount?: number
  toolMsTotal?: number
  promptTokensTotal?: number
  evalTokensTotal?: number
  totalTokens?: number
  tokensReported?: boolean
  exitReason?: string
  laneBefore: string
  laneAfter?: string
  toolsUsed: string[]
  toolFailures: number
  planRejections: number
  textRejections: number
  llmIterations: { used: number; max: number }
  agentResultSnippet?: string
  hint?: string
  filePath: string
  ok?: boolean
  lastEvent?: string
  stepProgress?: StepProgress
}

export interface ActiveStepDiagnostics {
  traceId: string
  filePath: string
  status: 'running'
  taskId: string
  taskTitle: string
  lastEvent?: string
  updatedAt?: string
}

export interface RecoveryContext {
  interrupted: boolean
  taskId: string
  taskTitle: string
  lane: string
  agent: string
  diagnosticsFile?: string
  lastEvent?: string
  sprintMode?: 'auto' | 'single_step' | 'in_progress' | string
  suggestedAction?: string
}

export interface IndexProgress {
  phase: string
  filesDone: number
  filesTotal: number
  chunks: number
  currentFile?: string
  embedFailures?: number
}

export interface ProjectMemoryEntry {
  id: string
  agent: string
  category: string
  content: string
  timestamp: string
  duplicateCount?: number
  duplicateIds?: string[]
}

export interface ActivityEvent {
  taskId: string
  taskTitle: string
  kind: string
  role: string
  agent: string
  content: string
  lane?: string
  timestamp: string
}

export interface ChatMessageRecord {
  role: 'user' | 'assistant'
  content: string
  agent?: string
  timestamp?: string
}

export interface WorkflowNotifications {
  needsPo: number
  needsUser: number
  pendingApproval: number
  qaFailures: number
}

export interface SystemLog {
  source: string
  type: LogType | string
  text: string
  timestamp: string
}

export interface Skill {
  filename: string
  title: string
  folder: string
  agents?: string[]
  categories?: string[]
}

export interface BriefCategory {
  id: string
  label: string
}

export interface SkillSuggestion {
  filename: string
  title: string
  score: number
  reason: string
}

export interface SkillSuggestionsResponse {
  briefCategories: BriefCategory[]
  suggestions: SkillSuggestion[]
}

export interface ProjectSummary {
  id: string
  name: string
}

export interface ProjectToolEvidence {
  id: string
  toolName: string
  command?: string
  toolArgs?: Record<string, unknown>
  toolOutput: string
  note?: string
  outcome?: string
  success?: boolean
  timestamp: string
}

export interface PendingSimulation {
  id: string
  taskId?: string
  agent?: string
  kind?: string
  title?: string
  summary?: string
  defaultPreview?: Record<string, unknown>
  createdAt?: string
  source?: string
}

export interface AppState {
  projectId: string
  projectName: string
  brief: string
  projectPlanOutline?: string
  workspaceDir: string
  skillsDir: string
  board: Board
  filePaths?: string[]
  files: Record<string, string>
  recommendedLintCommand?: string | null
  projectToolEvidence?: ProjectToolEvidence[]
  logs: SystemLog[]
  availableSkills: Skill[]
  assignedSkills: Record<AgentId, string[]>
  models: Record<AgentId, string>
  backupModels?: Partial<Record<AgentId, string>>
  projectsList: ProjectSummary[]
  sprintCancel?: boolean
  sprintCancelIntent?: string | null
  discordBotStatus?: {
    status?: string
    lastError?: string
    readyAt?: string
    running?: boolean
  }
  workflowSettings?: WorkflowSettings
  activeLanes?: BoardLane[]
  briefChangelog?: BriefChangelogEntry[]
  lastSprintSummary?: SprintSummary
  notifications?: WorkflowNotifications
  chatMessages?: ChatMessageRecord[]
  activeAgentRun?: AgentRunState | null
  pendingToolApprovals?: PendingToolApproval[]
  lastStepOutcome?: LastStepOutcome | null
  lastStepDiagnostics?: LastStepDiagnostics | null
  activeStepDiagnostics?: ActiveStepDiagnostics | null
  recovery?: RecoveryContext | null
  pendingSimulation?: PendingSimulation | null
  sprintPausedForSimulation?: boolean
}

export interface ConfigPayload {
  projectName: string
  workspaceDir: string
  skillsDir: string
  poModel: string
  devModel: string
  crModel: string
  qaModel: string
  poBackupModel?: string
  devBackupModel?: string
  crBackupModel?: string
  qaBackupModel?: string
}

export interface BriefPayload {
  brief: string
  ollama_url: string
}

export interface SkillPayload {
  agent: AgentId
  skillFile: string
}

export interface BulkSkillPayload {
  agent: AgentId
  skillFiles: string[]
}

export interface CombineSkillsPayload {
  agent: AgentId
  skillFiles: string[]
  outputName?: string
  ollamaUrl?: string
}

export interface CombineSkillsResponse {
  skillRel: string
  markdown: string
  charCount: number
  skillsContextMaxChars: number
  sources: string[]
  warning?: string | null
  mergeRounds?: number
}

export interface SaveBuiltSkillPayload {
  skillRel: string
  markdown: string
}

export interface CreateProjectPayload {
  projectName: string
  workspaceDir: string
}

export interface ManualTaskPayload {
  title: string
  description: string
  ollama_url?: string
  preferredFeatureId?: string
}

export interface UpdateTaskPayload {
  title?: string
  description?: string
  acceptanceCriteria?: string[]
  acChecklist?: boolean[]
  blockedBy?: string[]
  priority?: number
  userStory?: string
  scope?: string
  outOfScope?: string
  testPlan?: string
  actualSummary?: string
  status?: BoardLane
}

export interface MoveTaskPayload {
  taskId: string
  fromLane: BoardLane
  toLane: BoardLane
  index?: number
  skipRefinement?: boolean
}

export interface WorkflowSettingsPayload {
  requireBacklogApproval?: boolean
  requireCodeReview?: boolean
  requireDevVerification?: boolean
  requireCleanLint?: boolean
  requireBacklogRefinement?: boolean
  maxRefinementRoundTrips?: number
  maxSubtaskDepth?: number
  maxSubtaskSpawns?: number
  enableFixVerifyLoop?: boolean
  maxFixVerifyRounds?: number
  autoExtendOnMaxIter?: boolean
  autoExtendExtraIterations?: number
  maxInCardLintFixes?: number
  maxLintFanoutCards?: number
  lintFanoutThreshold?: number
  enableBackupModelOnStuck?: boolean
  backupModelStuckSteps?: number
  enableSplitOnStuck?: boolean
  requireWorkspaceStructure?: boolean
  autoScaffoldOnStructureGap?: boolean
  requireToolApproval?: boolean
  toolApprovalTools?: string[]
  nonBlockingToolApproval?: boolean
  commandAutoRunMode?: 'off' | 'allowlist' | 'denylist' | 'all'
  commandAllowlist?: string[]
  commandDenylist?: string[]
  allowChainedCommands?: boolean
  maxMcpTools?: number
  mcpServers?: McpServerConfig[]
  agentTools?: Record<string, string[]>
  agentToolsAllowWritesInRefinement?: boolean
  customTools?: CustomToolDef[]
  definitionOfDone?: string[]
  maxSprintSteps?: number
  maxLlmIterationsPerStep?: number
  maxPoRoundTrips?: number
  maxStuckSteps?: number
  maxAgentStepDurationSec?: number
  enableBlockedLane?: boolean
  maxToolFailuresPerStep?: number
  autoStartSprint?: boolean
  autonomousMode?: boolean
  maxNeedsUserPerSprint?: number
  needsUserCooldownSteps?: number
  enableWebSearch?: boolean
  enableSemanticSearch?: boolean
  qdrantUrl?: string
  qdrantApiKey?: string
  qdrantApiKeyConfigured?: boolean
  embedModel?: string
  ollamaNumCtx?: number
  ollamaNumCtxByRole?: Partial<Record<'po' | 'dev' | 'cr' | 'qa', number>>
  ollamaNumCtxAuto?: boolean
  ollamaKeepAlive?: string
  ollamaRequestTimeoutSec?: number
  terminalTimeoutSec?: number
  enableVramAwareModelSwap?: boolean
  ollamaMaxRetries?: number
  ollamaRetryDelaySec?: number[]
  ollamaCooldownRetryEnabled?: boolean
  ollamaCooldownRetrySec?: number
  ollamaCooldownRetryAttempts?: number
  maxToolOutputCharsForLlm?: number
  messagePruneThresholdPct?: number
  enableSemanticSprintContext?: boolean
  enableHybridSearch?: boolean
  semanticMinScore?: number
  semanticSprintTopK?: number
  sprintFileContextMode?: 'excerpt' | 'full' | string
  enableObservationSummaries?: boolean
  enableEpisodeSummary?: boolean
  enableStepLessonMemory?: boolean
  enableLlmContextCompress?: boolean
  contextCompressMinChars?: number
  contextCompressMaxChars?: number
  contextCompressModel?: string
  pauseSprintOnNeedsUser?: boolean
  autoFormatAfterEdit?: boolean
  phoneNotifyEnabled?: boolean
  phoneNotifyProvider?: 'discord' | string
  phoneNotifyDiscordWebhookUrl?: string
  phoneNotifyOnNeedsUser?: boolean
  phoneNotifyOnNeedsPo?: boolean
  phoneNotifyOnToolApproval?: boolean
  phoneNotifyOnSprintEnd?: boolean
  phoneNotifyOnBoardStatus?: boolean
  phoneNotifyOnStuckEscalation?: boolean
  phoneNotifyOnStepTimeout?: boolean
  phoneNotifyOnBackupArmed?: boolean
  discordBotEnabled?: boolean
  discordBotToken?: string
  discordBotGuildId?: string
  discordBotAllowedUserIds?: string[]
  discordModelPresetFast?: string
  discordModelPresetQuality?: string
  requireAcChecklistForDone?: boolean
  /** When Ollama returns SIMULATION_FALLBACK, wait for UI confirm before applying. */
  confirmSimulationFallback?: boolean
  /** Countdown seconds before auto-accepting offline simulation (1–60). */
  simulationConfirmSeconds?: number
  /** When true, auto-accept after countdown (with 3s grace). Default false = wait for explicit confirm. */
  simulationAutoAccept?: boolean
  /** When true (default), dev offline steps use an existing workspace file without showing the popup. */
  simulationAutoUseExistingFile?: boolean
  autoSprintSessionRefreshEnabled?: boolean
  autoSprintSessionRefreshMinutes?: number
  autoSprintHardReload?: boolean
  duplicateToolPolicy?: string
  duplicateToolHardStopExclude?: string[]
}

export interface SkillsResponse {
  skillsDir: string
  workspaceDir: string
  skills: Skill[]
  count: number
}

export interface FileTreeNode {
  name: string
  path: string
  type: 'file' | 'directory'
  children?: FileTreeNode[]
}

export interface FileSearchResult {
  path: string
  line: number
  preview: string
}

export interface FileDiffResponse {
  path: string
  oldValue: string
  newValue: string
}

export interface OllamaHealthResponse {
  ok: boolean
  url?: string
  models?: string[]
  error?: string
}

export interface ChatPayload {
  agent: AgentId
  message: string
  contextFiles?: string[]
  ollama_url?: string
  taskId?: string
  allowDoneRetry?: boolean
}

export interface DoneAuditItem {
  taskId: string
  title?: string
  reasons?: string[]
  pendingDevLabels?: string[]
  blockedDevLabels?: string[]
  uncheckedAcCount?: number
}

export interface DoneAuditReport {
  totalDone: number
  incompleteCount: number
  completeCount: number
  items: DoneAuditItem[]
}

export interface ChatResponse {
  agent: AgentId
  response: string
  reply?: string
  messages?: unknown[]
  splitHint?: string
  pendingSimulation?: PendingSimulation | null
  toolCalls?: Array<{
    toolName?: string
    toolArgs?: Record<string, unknown>
    toolOutput?: string
    toolSuccess?: boolean
    status?: string
  }>
}

export interface TerminalRunPayload {
  command: string
  cwd?: string
}

export interface TerminalRunResponse {
  output?: string
  exitCode?: number
  success?: boolean
  stdout?: string
  stderr?: string
  returncode?: number
}

export interface PendingToolRequest {
  id: string
  projectId: string
  taskId?: string
  agentRole?: string
  alias: string
  arguments: Record<string, unknown>
  status: string
  timestamp: string
}

export interface ResolvePendingToolPayload {
  targetTool: string
  defaultArgs?: Record<string, string>
  saveMapping?: boolean
}

export interface SprintRunPayload {
  brief: string
  ollama_url: string
  auto?: boolean
  max_steps?: number
}

export interface GitStatusEntry {
  path: string
  status: string
}

export interface GitStatusResponse {
  branch?: string
  entries?: GitStatusEntry[]
  clean?: boolean
  success?: boolean
  stderr?: string
}

export interface BackgroundTerminalSession {
  id: string
  command: string
  output: string
  done: boolean
  exitCode?: number | null
  startedAt?: string
}

export type AppEventType =
  | 'state'
  | 'board'
  | 'files'
  | 'log'
  | 'task'
  | 'sprint'
  | 'activity'
  | 'pending_tool'
  | 'tool_start'
  | 'tool_end'
  | 'agent_run'
  | 'tool_approval_required'
  | 'terminal_stream'
  | 'sprint_progress'
  | 'index_progress'
  | 'plan_chunk'
  | 'connected'

export interface AppEvent {
  type: AppEventType
  data?: unknown
}

export const CORE_BOARD_LANES: BoardLane[] = [
  'Backlog',
  'In Progress',
  'Needs PO',
  'Needs User',
  'QA',
  'Done',
]

export const BOARD_LANES: BoardLane[] = CORE_BOARD_LANES

export const AGENT_LABELS: Record<AgentId, string> = {
  po: 'Product Owner',
  dev: 'Developer',
  cr: 'Code Reviewer',
  qa: 'QA Tester',
}

export const DEFAULT_WORKFLOW_SETTINGS: WorkflowSettings = {
  requireBacklogApproval: false,
  requireCodeReview: false,
  requireDevVerification: false,
  requireCleanLint: false,
  requireBacklogRefinement: false,
  prioritizeImplementationOverRefinement: true,
  maxRefinementRoundTrips: 3,
  maxSubtaskDepth: 4,
  maxSubtaskSpawns: 8,
  enableFixVerifyLoop: false,
  maxFixVerifyRounds: 3,
  autoExtendOnMaxIter: false,
  autoExtendExtraIterations: 4,
  maxInCardLintFixes: 5,
  maxLintFanoutCards: 8,
  lintFanoutThreshold: 6,
  enableBackupModelOnStuck: true,
  backupModelStuckSteps: 2,
  enableSplitOnStuck: true,
  requireWorkspaceStructure: true,
  autoScaffoldOnStructureGap: true,
  requireToolApproval: false,
  toolApprovalTools: ['write_file', 'run_command', 'delete_file'],
  nonBlockingToolApproval: true,
  commandAutoRunMode: 'off',
  commandAllowlist: ['flutter analyze', 'dart analyze', 'npm test', 'npm run lint', 'pytest', 'ruff check'],
  commandDenylist: ['rm ', 'del ', 'rmdir ', 'format ', 'shutdown'],
  allowChainedCommands: true,
  maxMcpTools: 40,
  mcpServers: [],
  agentTools: {},
  agentToolsAllowWritesInRefinement: false,
  customTools: [],
  definitionOfDone: [],
  maxSprintSteps: 20,
  maxLlmIterationsPerStep: 8,
  maxPoRoundTrips: 3,
  maxStuckSteps: 3,
  maxAgentStepDurationSec: 2700,
  enableBlockedLane: true,
  maxToolFailuresPerStep: 5,
  autoStartSprint: true,
  autonomousMode: false,
  maxNeedsUserPerSprint: 2,
  needsUserCooldownSteps: 3,
  enableWebSearch: false,
  enableSemanticSearch: true,
  qdrantUrl: 'http://localhost:6333',
  qdrantApiKeyConfigured: false,
  embedModel: 'nomic-embed-text',
  ollamaNumCtx: 32768,
  ollamaNumCtxByRole: {},
  ollamaNumCtxAuto: true,
  ollamaKeepAlive: '30m',
  ollamaRequestTimeoutSec: 300,
  terminalTimeoutSec: 600,
  enableVramAwareModelSwap: true,
  ollamaMaxRetries: 4,
  ollamaRetryDelaySec: [0, 2, 5, 10],
  ollamaCooldownRetryEnabled: true,
  ollamaCooldownRetrySec: 15,
  ollamaCooldownRetryAttempts: 2,
  maxToolOutputCharsForLlm: 6000,
  messagePruneThresholdPct: 60,
  enableSemanticSprintContext: true,
  enableHybridSearch: true,
  semanticMinScore: 0.35,
  semanticSprintTopK: 3,
  sprintFileContextMode: 'excerpt',
  enableObservationSummaries: true,
  enableEpisodeSummary: true,
  enableStepLessonMemory: true,
  enableLlmContextCompress: false,
  contextCompressMinChars: 8000,
  contextCompressMaxChars: 3500,
  contextCompressModel: '',
  pauseSprintOnNeedsUser: false,
  autoFormatAfterEdit: true,
  phoneNotifyEnabled: false,
  phoneNotifyProvider: 'discord',
  phoneNotifyOnNeedsUser: true,
  phoneNotifyOnNeedsPo: false,
  phoneNotifyOnToolApproval: true,
  phoneNotifyOnSprintEnd: true,
  phoneNotifyOnBoardStatus: true,
  phoneNotifyOnStuckEscalation: true,
  phoneNotifyOnStepTimeout: true,
  phoneNotifyOnBackupArmed: true,
  discordBotEnabled: false,
  discordBotGuildId: '',
  discordBotAllowedUserIds: [],
  discordModelPresetFast: 'qwen2.5-coder:7b',
  discordModelPresetQuality: 'qwen2.5-coder:14b',
  requireAcChecklistForDone: true,
  confirmSimulationFallback: true,
  simulationConfirmSeconds: 10,
  simulationAutoAccept: false,
  simulationAutoUseExistingFile: true,
  duplicateToolPolicy: 'strict',
  duplicateToolHardStopExclude: ['run_command'],
  autoSprintSessionRefreshEnabled: true,
  autoSprintSessionRefreshMinutes: 60,
  autoSprintHardReload: true,
}

export const EMPTY_BOARD: Board = {
  Features: [],
  Backlog: [],
  Blocked: [],
  'In Progress': [],
  'Needs PO': [],
  'Needs User': [],
  QA: [],
  Done: [],
}

export function hasSprintWork(board: Board, settings?: WorkflowSettings): boolean {
  // Lane order mirrors backend _sprint_lanes_active (implementation before refinement when enabled).
  const prioritizeImpl = settings?.prioritizeImplementationOverRefinement !== false
  const lanes: BoardLane[] = ['Needs PO', 'In Progress']
  if (prioritizeImpl && settings?.requireBacklogRefinement) {
    lanes.push('Backlog', 'Refinement')
  } else if (settings?.requireBacklogRefinement) {
    lanes.push('Refinement', 'Backlog')
  } else {
    lanes.push('Backlog')
  }
  if (settings?.requireCodeReview) lanes.push('Code Review')
  lanes.push('QA')
  return lanes.some((lane) => (board[lane]?.length ?? 0) > 0)
}

/** Backlog cards eligible for claim (approximates backend next_claimable_backlog_task). */
export function countClaimableBacklogTasks(
  board: Board,
  settings?: WorkflowSettings,
): number {
  const requireRefinement = settings?.requireBacklogRefinement === true
  return (board.Backlog ?? []).filter((task) => {
    if (task.requiresDev === false) return false
    if (task.workType === 'planning' || task.workType === 'feature') return false
    if (requireRefinement && task.refinementComplete === false) return false
    const blocked = task.blockedBy ?? []
    if (blocked.length > 0) {
      const doneIds = new Set((board.Done ?? []).map((t) => t.id))
      if (!blocked.every((id) => doneIds.has(id))) return false
    }
    return true
  }).length
}

export function getDisplayLanes(
  activeLanes?: BoardLane[],
  settings?: WorkflowSettings,
): BoardLane[] {
  if (activeLanes && activeLanes.length > 0) return activeLanes
  const lanes: BoardLane[] = ['Features', 'Backlog']
  if (settings?.requireBacklogApproval) lanes.push('Pending Approval')
  if (settings?.requireBacklogRefinement) lanes.push('Refinement')
  if (settings?.enableBlockedLane !== false) lanes.push('Blocked')
  lanes.push('In Progress', 'Needs PO', 'Needs User')
  if (settings?.requireCodeReview) lanes.push('Code Review')
  lanes.push('QA', 'Done')
  return lanes
}
