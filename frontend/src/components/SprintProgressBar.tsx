import type { SprintProgress } from '../types'

interface SprintProgressBarProps {
  progress: SprintProgress | null
  planRunActive: boolean
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
}: SprintProgressBarProps) {
  const active =
    planRunActive ||
    (progress != null && progress.phase !== 'done' && progress.phase !== 'cancelled')

  if (!active) {
    return null
  }

  const phase = progress?.phase ?? 'po_plan'
  const step = progress?.step ?? 0
  const maxSteps = progress?.maxSteps ?? 20
  const showStepCounter = phase === 'sprint_step' && step > 0

  return (
    <div className="shrink-0 border-b border-violet-500/30 bg-violet-950/25 text-[11px]">
      <div className="px-4 py-2 flex items-center gap-3 flex-wrap">
        <span className="inline-block w-2 h-2 rounded-full bg-violet-400 animate-pulse shrink-0" />
        <span className="font-bold text-violet-200 uppercase tracking-wide text-[10px]">
          Plan &amp; Run
        </span>
        <span className="text-cat-subtext">{phaseLabel(phase)}</span>
        {showStepCounter && (
          <span className="text-indigo-300 font-mono">
            step {step}/{maxSteps}
          </span>
        )}
        {progress?.agent && (
          <span className="text-cat-subtext">{progress.agent}</span>
        )}
        {progress?.taskTitle && (
          <span className="text-white truncate max-w-[min(100%,28rem)]">
            {progress.taskTitle}
          </span>
        )}
        {progress?.taskId && progress.taskId !== 'PLANNING' && (
          <span className="text-cat-overlay font-mono text-[10px]">{progress.taskId}</span>
        )}
        {!progress && planRunActive && (
          <span className="text-cat-overlay italic">
            Working… first Ollama call may take a few minutes
          </span>
        )}
      </div>
    </div>
  )
}
