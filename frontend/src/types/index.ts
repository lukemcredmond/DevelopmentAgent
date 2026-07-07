export type AgentId = 'po' | 'dev' | 'cr' | 'qa'

export type BoardLane =
  | 'Backlog'
  | 'Pending Approval'
  | 'Refinement'
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

export interface Task {
  id: string
  title: string
  description: string
  status: BoardLane | string
  files?: (TaskFile | string)[]
  decisions?: TaskDecision[]
  transcript?: TaskTranscriptEntry[]
  acceptanceCriteria?: string[]
  priority?: number
  blockedBy?: string[]
  relatedTaskIds?: string[]
  gitCommit?: TaskGitCommit | null
  qaFailure?: QaFailure | null
  qaEvidence?: QaEvidence | null
  userQuestion?: string | null
  needsUserReason?: string | null
  needsUserAction?: string | null
  poRoundTrips?: number
  workType?: 'planning' | 'implementation' | 'review' | 'qa' | 'user_action'
  requiresDev?: boolean
  requiresQa?: boolean
  createdBy?: 'po' | 'user' | 'split'
  lastDiagnosis?: TaskDiagnosis
  lastCommandDiagnostics?: CommandDiagnostic[]
  refinementStatus?: 'pending' | 'dev_reviewed' | 'po_updated' | 'ready' | 'blocked'
  refinementComplete?: boolean
  refinementRoundTrips?: number
  refinementQuestions?: string[]
  refinementNotes?: string | null
  refinementDevReady?: boolean
  parentTaskId?: string | null
  subtaskIds?: string[]
  executionOrder?: number
  subtaskSpawnCount?: number
  subtaskEscapeCount?: number
  subtaskSkipped?: boolean
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
  model: string
  iteration: number
  requestMessages: unknown[]
  toolNames: string[]
  responseContent: string
  responseToolCalls: unknown[]
  durationMs: number
  error?: string
}

export type Board = Partial<Record<BoardLane, Task[]>>

export interface WorkflowSettings {
  requireBacklogApproval: boolean
  requireCodeReview: boolean
  requireDevVerification?: boolean
  requireCleanLint?: boolean
  requireBacklogRefinement?: boolean
  maxRefinementRoundTrips?: number
  maxSubtaskDepth?: number
  maxSubtaskSpawns?: number
  enableFixVerifyLoop?: boolean
  maxFixVerifyRounds?: number
  requireToolApproval?: boolean
  toolApprovalTools?: string[]
  nonBlockingToolApproval?: boolean
  commandAutoRunMode?: 'off' | 'allowlist' | 'denylist' | 'all'
  commandAllowlist?: string[]
  commandDenylist?: string[]
  allowChainedCommands?: boolean
  maxMcpTools?: number
  mcpServers?: McpServerConfig[]
  definitionOfDone: string[]
  maxSprintSteps: number
  maxLlmIterationsPerStep: number
  maxPoRoundTrips: number
  maxStuckSteps?: number
  maxToolFailuresPerStep?: number
  autoStartSprint?: boolean
  autonomousMode?: boolean
  maxNeedsUserPerSprint?: number
  enableWebSearch?: boolean
  enableSemanticSearch?: boolean
  qdrantUrl?: string
  qdrantApiKey?: string
  qdrantApiKeyConfigured?: boolean
  embedModel?: string
  ollamaNumCtx?: number
  ollamaKeepAlive?: string
  maxToolOutputCharsForLlm?: number
  messagePruneThresholdPct?: number
  enableSemanticSprintContext?: boolean
  pauseSprintOnNeedsUser?: boolean
  autoFormatAfterEdit?: boolean
}

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
  status: 'running' | 'completed' | 'failed'
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
  status?: 'completed' | 'idle' | 'cancelled' | 'max_steps'
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

export interface AppState {
  projectId: string
  projectName: string
  brief: string
  workspaceDir: string
  skillsDir: string
  board: Board
  files: Record<string, string>
  logs: SystemLog[]
  availableSkills: Skill[]
  assignedSkills: Record<AgentId, string[]>
  models: Record<AgentId, string>
  projectsList: ProjectSummary[]
  workflowSettings?: WorkflowSettings
  activeLanes?: BoardLane[]
  briefChangelog?: BriefChangelogEntry[]
  lastSprintSummary?: SprintSummary
  notifications?: WorkflowNotifications
  chatMessages?: ChatMessageRecord[]
  activeAgentRun?: AgentRunState | null
  pendingToolApprovals?: PendingToolApproval[]
}

export interface ConfigPayload {
  projectName: string
  workspaceDir: string
  skillsDir: string
  poModel: string
  devModel: string
  crModel: string
  qaModel: string
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

export interface CreateProjectPayload {
  projectName: string
  workspaceDir: string
}

export interface ManualTaskPayload {
  title: string
  description: string
  ollama_url?: string
}

export interface UpdateTaskPayload {
  title?: string
  description?: string
  acceptanceCriteria?: string[]
  blockedBy?: string[]
  priority?: number
  status?: BoardLane
}

export interface MoveTaskPayload {
  taskId: string
  fromLane: BoardLane
  toLane: BoardLane
  index?: number
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
  requireToolApproval?: boolean
  toolApprovalTools?: string[]
  nonBlockingToolApproval?: boolean
  commandAutoRunMode?: 'off' | 'allowlist' | 'denylist' | 'all'
  commandAllowlist?: string[]
  commandDenylist?: string[]
  allowChainedCommands?: boolean
  maxMcpTools?: number
  mcpServers?: McpServerConfig[]
  definitionOfDone?: string[]
  maxSprintSteps?: number
  maxLlmIterationsPerStep?: number
  maxPoRoundTrips?: number
  maxStuckSteps?: number
  maxToolFailuresPerStep?: number
  autoStartSprint?: boolean
  autonomousMode?: boolean
  maxNeedsUserPerSprint?: number
  enableWebSearch?: boolean
  enableSemanticSearch?: boolean
  qdrantUrl?: string
  qdrantApiKey?: string
  qdrantApiKeyConfigured?: boolean
  embedModel?: string
  ollamaNumCtx?: number
  ollamaKeepAlive?: string
  maxToolOutputCharsForLlm?: number
  messagePruneThresholdPct?: number
  enableSemanticSprintContext?: boolean
  pauseSprintOnNeedsUser?: boolean
  autoFormatAfterEdit?: boolean
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
}

export interface ChatResponse {
  agent: AgentId
  response: string
  reply?: string
  messages?: unknown[]
  splitHint?: string
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
  maxRefinementRoundTrips: 3,
  maxSubtaskDepth: 4,
  maxSubtaskSpawns: 8,
  enableFixVerifyLoop: false,
  maxFixVerifyRounds: 3,
  requireToolApproval: false,
  toolApprovalTools: ['write_file', 'run_command', 'delete_file'],
  nonBlockingToolApproval: true,
  commandAutoRunMode: 'off',
  commandAllowlist: ['flutter analyze', 'dart analyze', 'npm test', 'npm run lint', 'pytest', 'ruff check'],
  commandDenylist: ['rm ', 'del ', 'rmdir ', 'format ', 'shutdown'],
  allowChainedCommands: false,
  maxMcpTools: 40,
  mcpServers: [],
  definitionOfDone: [],
  maxSprintSteps: 20,
  maxLlmIterationsPerStep: 8,
  maxPoRoundTrips: 3,
  maxStuckSteps: 3,
  maxToolFailuresPerStep: 5,
  autoStartSprint: true,
  autonomousMode: false,
  maxNeedsUserPerSprint: 2,
  enableWebSearch: false,
  enableSemanticSearch: true,
  qdrantUrl: 'http://localhost:6333',
  qdrantApiKeyConfigured: false,
  embedModel: 'nomic-embed-text',
  ollamaNumCtx: 32768,
  ollamaKeepAlive: '30m',
  maxToolOutputCharsForLlm: 6000,
  messagePruneThresholdPct: 60,
  enableSemanticSprintContext: true,
  pauseSprintOnNeedsUser: false,
  autoFormatAfterEdit: true,
}

export const EMPTY_BOARD: Board = {
  Backlog: [],
  'In Progress': [],
  'Needs PO': [],
  'Needs User': [],
  QA: [],
  Done: [],
}

export function hasSprintWork(board: Board, settings?: WorkflowSettings): boolean {
  const lanes: BoardLane[] = ['Needs PO', 'In Progress', 'Backlog', 'QA']
  if (settings?.requireBacklogRefinement) lanes.splice(2, 0, 'Refinement')
  if (settings?.requireCodeReview) lanes.splice(lanes.indexOf('Backlog') + 1, 0, 'Code Review')
  return lanes.some((lane) => (board[lane]?.length ?? 0) > 0)
}

export function getDisplayLanes(
  activeLanes?: BoardLane[],
  settings?: WorkflowSettings,
): BoardLane[] {
  if (activeLanes && activeLanes.length > 0) return activeLanes
  const lanes: BoardLane[] = ['Backlog']
  if (settings?.requireBacklogApproval) lanes.push('Pending Approval')
  if (settings?.requireBacklogRefinement) lanes.push('Refinement')
  lanes.push('In Progress', 'Needs PO', 'Needs User')
  if (settings?.requireCodeReview) lanes.push('Code Review')
  lanes.push('QA', 'Done')
  return lanes
}
