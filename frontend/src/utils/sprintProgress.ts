import type { CardWorkProgress, SprintProgress } from '../types'

export function isSprintProgressActive(params: {
  planRunActive: boolean
  planBacklogActive?: boolean
  sprintRunning?: boolean
  progress: SprintProgress | null
  needsUserCount?: number
}): boolean {
  const {
    planRunActive,
    planBacklogActive = false,
    sprintRunning = false,
    progress,
    needsUserCount = 0,
  } = params
  return (
    planRunActive ||
    planBacklogActive ||
    sprintRunning ||
    (progress != null && progress.phase !== 'done' && progress.phase !== 'cancelled') ||
    needsUserCount > 0
  )
}

export function mapCardProgress(raw: unknown): CardWorkProgress | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const d = raw as Record<string, unknown>
  const gates = Array.isArray(d.gatesRemaining)
    ? d.gatesRemaining.map((g) => String(g))
    : Array.isArray(d.gates_remaining)
      ? (d.gates_remaining as unknown[]).map((g) => String(g))
      : undefined
  const files = Array.isArray(d.filesThisStep)
    ? d.filesThisStep.map((f) => String(f))
    : Array.isArray(d.files_this_step)
      ? (d.files_this_step as unknown[]).map((f) => String(f))
      : undefined
  return {
    subtasksDone: Number(d.subtasksDone ?? d.subtasks_done ?? 0) || 0,
    subtasksTotal: Number(d.subtasksTotal ?? d.subtasks_total ?? 0) || 0,
    stepsOnCard: Number(d.stepsOnCard ?? d.steps_on_card ?? 0) || 0,
    stuckLoops: Number(d.stuckLoops ?? d.stuck_loops ?? 0) || 0,
    poRoundTrips: Number(d.poRoundTrips ?? d.po_round_trips ?? 0) || 0,
    gatesRemaining: gates,
    filesThisStep: files,
    acCount: Number(d.acCount ?? d.ac_count ?? 0) || 0,
    lane: d.lane != null ? String(d.lane) : undefined,
  }
}

export function mapSprintProgress(data: Record<string, unknown>): SprintProgress {
  const phase = String(data.phase ?? 'po_plan')
  const validPhases = ['po_plan', 'sprint_step', 'done', 'cancelled'] as const
  const phaseVal = validPhases.includes(phase as SprintProgress['phase'])
    ? (phase as SprintProgress['phase'])
    : 'po_plan'
  return {
    phase: phaseVal,
    step: Number(data.step ?? 0),
    maxSteps: Number(data.maxSteps ?? data.max_steps ?? 20),
    agent: String(data.agent ?? ''),
    taskId: String(data.taskId ?? data.task_id ?? ''),
    taskTitle: String(data.taskTitle ?? data.task_title ?? ''),
    lane: String(data.lane ?? ''),
    status: data.status != null ? String(data.status) : undefined,
    intent: data.intent != null ? String(data.intent) : undefined,
    cardProgress: mapCardProgress(data.cardProgress ?? data.card_progress),
    ollamaWaitSec:
      data.ollamaWaitSec != null
        ? Number(data.ollamaWaitSec)
        : data.ollama_wait_sec != null
          ? Number(data.ollama_wait_sec)
          : undefined,
    ollamaWaitMaxSec:
      data.ollamaWaitMaxSec != null
        ? Number(data.ollamaWaitMaxSec)
        : data.ollama_wait_max_sec != null
          ? Number(data.ollama_wait_max_sec)
          : undefined,
    phaseCycleCapReached:
      data.phaseCycleCapReached != null
        ? Boolean(data.phaseCycleCapReached)
        : data.phase_cycle_cap_reached != null
          ? Boolean(data.phase_cycle_cap_reached)
          : undefined,
  }
}
