import type { AgentRunState } from '../types'

interface AgentRunBarProps {
  activeRun: AgentRunState | null
  currentTool?: string | null
  planRunActive?: boolean
  onOpenTools?: () => void
}

export default function AgentRunBar({
  activeRun,
  currentTool,
  planRunActive = false,
  onOpenTools,
}: AgentRunBarProps) {
  const hasActiveRun = activeRun != null
  const toolLabel = currentTool || activeRun?.currentTool
  const isWaitingApproval = activeRun?.status === 'awaiting_approval'
  const isRunning =
    activeRun?.status === 'thinking' ||
    activeRun?.status === 'tool_executing' ||
    activeRun?.status === 'awaiting_approval'
  const isDone =
    activeRun?.status === 'completed' || activeRun?.status === 'failed'

  if (hasActiveRun && isDone && !activeRun.error) {
    return null
  }

  if (!hasActiveRun) {
    return (
      <div className="shrink-0 border-b border-cat-surface1 bg-cat-mantle/60 text-[11px]">
        <div className="px-4 py-1.5 flex items-center gap-3 flex-wrap">
          {planRunActive ? (
            <>
              <span className="inline-block w-2 h-2 rounded-full bg-violet-400 animate-pulse shrink-0" />
              <span className="text-violet-200">
                Plan &amp; Run in progress — see <strong className="font-normal">Console</strong>{' '}
                for live logs
              </span>
            </>
          ) : (
            <span className="text-cat-overlay">
              Open the <strong className="text-cat-subtext font-normal">Tools</strong> tab to
              manually run or replay tool calls.
            </span>
          )}
          {onOpenTools && (
            <button
              type="button"
              onClick={onOpenTools}
              className="ml-auto shrink-0 text-[10px] font-semibold px-2.5 py-1 rounded border border-indigo-500/50 text-indigo-300 hover:bg-indigo-950/40 hover:text-indigo-200 transition-colors"
            >
              Tools →
            </button>
          )}
        </div>
      </div>
    )
  }

  const statusLabel = isWaitingApproval
    ? 'awaiting approval — agent paused'
    : activeRun.status === 'tool_executing'
      ? 'running tool'
      : activeRun.status === 'completed'
        ? 'step completed'
        : activeRun.status === 'failed'
          ? 'step failed'
          : activeRun.status

  return (
    <div className="shrink-0 border-b border-indigo-500/30 bg-indigo-950/30 text-[11px] font-mono">
      <div className="px-4 py-1.5 flex items-center gap-3 flex-wrap">
        {isRunning && (
          <span className="inline-block w-2 h-2 rounded-full bg-indigo-400 animate-pulse shrink-0" />
        )}
        {isDone && activeRun.status === 'completed' && (
          <span className="inline-block w-2 h-2 rounded-full bg-emerald-400 shrink-0" />
        )}
        {isDone && activeRun.status === 'failed' && (
          <span className="inline-block w-2 h-2 rounded-full bg-rose-400 shrink-0" />
        )}
        <span className="text-indigo-200 font-bold">{activeRun.agent}</span>
        {activeRun.iteration != null && activeRun.maxIterations != null && (
          <span className="text-cat-subtext">
            iteration {activeRun.iteration}/{activeRun.maxIterations}
          </span>
        )}
        <span className={isWaitingApproval ? 'text-amber-300' : 'text-cat-subtext'}>
          {statusLabel}
        </span>
        {toolLabel && isRunning && (
          <span className="text-indigo-300 truncate max-w-[200px]">{toolLabel}</span>
        )}
        <span className="text-cat-overlay ml-auto text-[10px]">{activeRun.taskId}</span>
        {onOpenTools && (
          <button
            type="button"
            onClick={onOpenTools}
            className="shrink-0 text-[10px] font-semibold px-2.5 py-1 rounded border border-indigo-500/50 text-indigo-300 hover:bg-indigo-950/40 hover:text-indigo-200 transition-colors"
          >
            Tools →
          </button>
        )}
      </div>

      {isWaitingApproval && (
        <p className="mx-4 mb-2 text-[10px] text-amber-200">
          Agent paused — approve or deny the tool in the modal to continue.
        </p>
      )}

      {activeRun.error && (
        <p className="mx-4 mb-2 text-[10px] text-rose-300">{activeRun.error}</p>
      )}
    </div>
  )
}
