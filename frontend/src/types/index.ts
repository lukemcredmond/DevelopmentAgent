export type AgentId = 'po' | 'dev' | 'cr' | 'qa'

export type BoardLane =
  | 'Backlog'
  | 'Pending Approval'
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
}

export interface QaFailure {
  reason: string
  output?: string
  timestamp: string
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
  qaFailure?: QaFailure | null
  userQuestion?: string | null
  poRoundTrips?: number
}

export type Board = Partial<Record<BoardLane, Task[]>>

export interface WorkflowSettings {
  requireBacklogApproval: boolean
  requireCodeReview: boolean
  definitionOfDone: string[]
  maxSprintSteps: number
  maxLlmIterationsPerStep: number
  maxPoRoundTrips: number
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
  definitionOfDone?: string[]
  maxSprintSteps?: number
  maxLlmIterationsPerStep?: number
  maxPoRoundTrips?: number
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
}

export interface ChatResponse {
  reply: string
  agent: AgentId
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

export type AppEventType =
  | 'state'
  | 'board'
  | 'files'
  | 'log'
  | 'task'
  | 'sprint'
  | 'activity'
  | 'pending_tool'
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
  definitionOfDone: [],
  maxSprintSteps: 20,
  maxLlmIterationsPerStep: 8,
  maxPoRoundTrips: 3,
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
  if (settings?.requireCodeReview) lanes.splice(3, 0, 'Code Review')
  return lanes.some((lane) => (board[lane]?.length ?? 0) > 0)
}

export function getDisplayLanes(
  activeLanes?: BoardLane[],
  settings?: WorkflowSettings,
): BoardLane[] {
  if (activeLanes && activeLanes.length > 0) return activeLanes
  const lanes: BoardLane[] = ['Backlog']
  if (settings?.requireBacklogApproval) lanes.push('Pending Approval')
  lanes.push('In Progress', 'Needs PO', 'Needs User')
  if (settings?.requireCodeReview) lanes.push('Code Review')
  lanes.push('QA', 'Done')
  return lanes
}
