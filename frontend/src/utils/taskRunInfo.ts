import type { ActiveStepDiagnostics, AgentRunState, SprintProgress } from '../types'

export interface TaskRunInfo {
  taskId: string
  agent: string
  status?: AgentRunState['status']
  currentTool?: string | null
  iteration?: number
  maxIterations?: number
  lastEvent?: string
  phase?: SprintProgress['phase']
  lane?: string
}

export function buildTaskRunInfo(args: {
  activeRun: AgentRunState | null
  sprintProgress: SprintProgress | null
  activeStepDiagnostics: ActiveStepDiagnostics | null | undefined
  currentTool?: string | null
}): TaskRunInfo | null {
  const { activeRun, sprintProgress, activeStepDiagnostics, currentTool } = args
  const taskId =
    activeRun?.taskId ||
    (sprintProgress?.taskId && sprintProgress.taskId !== 'PLANNING' ? sprintProgress.taskId : '') ||
    activeStepDiagnostics?.taskId ||
    ''
  if (!taskId) return null

  return {
    taskId,
    agent: activeRun?.agent || sprintProgress?.agent || 'Developer',
    status: activeRun?.status,
    currentTool: currentTool || activeRun?.currentTool,
    iteration: activeRun?.iteration,
    maxIterations: activeRun?.maxIterations,
    lastEvent: activeStepDiagnostics?.lastEvent,
    phase: sprintProgress?.phase,
    lane: sprintProgress?.lane,
  }
}

export function formatRunStatus(info: TaskRunInfo): string {
  if (info.status === 'awaiting_approval') return 'awaiting tool approval'
  if (info.status === 'tool_executing') return 'running tool'
  if (info.status === 'thinking') return 'thinking'
  if (info.phase === 'po_plan') return 'PO planning'
  if (info.phase === 'sprint_step') return 'sprint step'
  if (info.lastEvent?.startsWith('ollama:')) return 'LLM call'
  if (info.lastEvent?.startsWith('tool:')) return 'tool call'
  return 'working'
}
