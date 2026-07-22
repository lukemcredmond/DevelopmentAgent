import type { SprintProgress } from '../types'

interface SprintProgressBarProps {
  progress: SprintProgress | null
  planRunActive: boolean
  sprintRunning?: boolean
  currentTool?: string | null
  onOpenTask?: (taskId: string) => void
}

function phaseLabel(phase: SprintProgress['phase']): string {
  switch (phase) {
    case 'po_plan':
      return 'PO planning'
    case 'sprint_step':
      return 'Sprint step'
    case 'done':
      return 'Complete'
    case 'cancelled':
      return 'Cancelled'
    default:
      return phase
  }
}

export default function SprintProgressBar({
  progress,
  planRunActive,
  sprintRunning = false,
  currentTool,
  onOpenTask,
}: SprintProgressBarProps) {
  const active =
    planRunActive ||
    sprintRunning ||
    (progress != null && progress.phase !== 'done' && progress.phase !== 'cancelled')

  if (!active) {
    return null
  }

  const phase = progress?.phase ?? (planRunActive ? 'po_plan' : 'sprint_step')
  const step = progress?.step ?? 0
  const maxSteps = progress?.maxSteps ?? 20
  const showStepCounter = (phase === 'sprint_step' || sprintRunning) && maxSteps > 0
  const progressValue = phase === 'po_plan' && step === 0 ? undefined : Math.min(step, maxSteps)
  const title = planRunActive ? 'Plan & Run' : sprintRunning ? 'Auto sprint' : 'Sprint'

  return (
    <div className="shrink-0 border-b border-violet-500/30 bg-violet-950/25 text-[11px]">
      <div className="px-4 py-2 space-y-1.5">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="inline-block w-2 h-2 rounded-full bg-violet-400 animate-pulse shrink-0" />
          <span className="font-bold text-violet-200 uppercase tracking-wide text-[10px]">
            {title}
          </span>
          <span className="text-cat-subtext">{phaseLabel(phase)}</span>
          {showStepCounter && step > 0 && (
            <span className="text-indigo-300 font-mono">
              step {step}/{maxSteps}
            </span>
          )}
          {progress?.status && !progress?.intent && (
            <span className="text-cat-overlay italic">{progress.status}</span>
          )}
          {progress?.intent && (
            <span className="text-violet-200 truncate max-w-[min(100%,36rem)]" title={progress.intent}>
              {progress.intent}
            </span>
          )}
          {progress?.cardProgress &&
            ((progress.cardProgress.subtasksTotal ?? 0) > 0 ||
              (progress.cardProgress.stuckLoops ?? 0) > 0 ||
              (progress.cardProgress.gatesRemaining?.length ?? 0) > 0) && (
              <span className="text-sky-300/90 text-[10px]">
                {(progress.cardProgress.subtasksTotal ?? 0) > 0
                  ? `todos ${progress.cardProgress.subtasksDone ?? 0}/${progress.cardProgress.subtasksTotal}`
                  : null}
                {(progress.cardProgress.stuckLoops ?? 0) > 0
                  ? `${(progress.cardProgress.subtasksTotal ?? 0) > 0 ? ' · ' : ''}stuck ×${progress.cardProgress.stuckLoops}`
                  : null}
                {(progress.cardProgress.gatesRemaining?.length ?? 0) > 0
                  ? `${(progress.cardProgress.subtasksTotal ?? 0) > 0 || (progress.cardProgress.stuckLoops ?? 0) > 0 ? ' · ' : ''}→ ${progress.cardProgress.gatesRemaining!.join(' → ')}`
                  : null}
              </span>
            )}
          {progress?.agent && (
            <span className="text-cat-subtext">{progress.agent}</span>
          )}
          {currentTool && (
            <span className="text-amber-300/90 font-mono text-[10px]">{currentTool}</span>
          )}
          {progress?.taskTitle &&
            (progress.taskId && progress.taskId !== 'PLANNING' && onOpenTask ? (
              <button
                type="button"
                onClick={() => onOpenTask(progress.taskId!)}
                className="text-white truncate max-w-[min(100%,28rem)] text-left underline decoration-violet-400/50 hover:decoration-violet-300 hover:text-violet-100"
                title="Open card"
              >
                {progress.taskTitle}
              </button>
            ) : (
              <span className="text-white truncate max-w-[min(100%,28rem)]">
                {progress.taskTitle}
              </span>
            ))}
          {progress?.taskId && progress.taskId !== 'PLANNING' && (
            onOpenTask ? (
              <button
                type="button"
                onClick={() => onOpenTask(progress.taskId!)}
                className="text-cat-overlay font-mono text-[10px] underline decoration-violet-400/40 hover:text-violet-200 hover:decoration-violet-300"
                title="Open card"
              >
                {progress.taskId}
              </button>
            ) : (
              <span className="text-cat-overlay font-mono text-[10px]">{progress.taskId}</span>
            )
          )}
          {!progress && (planRunActive || sprintRunning) && (
            <span className="text-cat-overlay italic">
              Working… first Ollama call may take a few minutes
            </span>
          )}
        </div>
        {phase === 'po_plan' && progressValue === undefined ? (
          <progress className="w-full h-1.5 accent-violet-500" />
        ) : showStepCounter ? (
          <progress
            className="w-full h-1.5 accent-violet-500"
            value={progressValue ?? 0}
            max={maxSteps}
          />
        ) : null}
      </div>
    </div>
  )
}
