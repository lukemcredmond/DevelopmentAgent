import type {
  ActiveStepDiagnostics,
  AgentRunState,
  CardWorkProgress,
  SprintProgress,
  StepProgress,
  Task,
} from '../types'

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
  intent?: string | null
  cardProgress?: CardWorkProgress | null
  whyCardStayed?: string | null
  suggestedAction?: string | null
}

function formatCardProgressLine(cp: CardWorkProgress | null | undefined): string | null {
  if (!cp) return null
  const parts: string[] = []
  if ((cp.subtasksTotal ?? 0) > 0) {
    parts.push(`todos ${cp.subtasksDone ?? 0}/${cp.subtasksTotal}`)
  }
  if ((cp.gatesRemaining?.length ?? 0) > 0) {
    parts.push(`gates: ${cp.gatesRemaining!.join(' → ')}`)
  }
  if ((cp.stuckLoops ?? 0) > 0 || (cp.stepsOnCard ?? 0) > 0) {
    parts.push(`stuck ×${cp.stuckLoops ?? cp.stepsOnCard ?? 0}`)
  }
  if ((cp.acCount ?? 0) > 0) {
    parts.push(`${cp.acCount} ACs`)
  }
  return parts.length ? parts.join(' · ') : null
}

export function buildTaskRunInfo(args: {
  activeRun: AgentRunState | null
  sprintProgress: SprintProgress | null
  activeStepDiagnostics: ActiveStepDiagnostics | null | undefined
  currentTool?: string | null
  task?: Task | null
}): TaskRunInfo | null {
  const { activeRun, sprintProgress, activeStepDiagnostics, currentTool, task } = args
  const taskId =
    activeRun?.taskId ||
    (sprintProgress?.taskId && sprintProgress.taskId !== 'PLANNING' ? sprintProgress.taskId : '') ||
    activeStepDiagnostics?.taskId ||
    ''
  if (!taskId) return null

  const lastProgress =
    (task?.lastStepProgress as StepProgress | null | undefined) ?? null
  const cardProgress =
    activeRun?.cardProgress ??
    sprintProgress?.cardProgress ??
    lastProgress?.cardProgress ??
    null

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
    intent: activeRun?.intent || sprintProgress?.intent || lastProgress?.intent || null,
    cardProgress,
    whyCardStayed: lastProgress?.whyCardStayed ?? null,
    suggestedAction: lastProgress?.suggestedAction ?? null,
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

export function formatCardProgressBrief(info: TaskRunInfo): string | null {
  return formatCardProgressLine(info.cardProgress)
}
