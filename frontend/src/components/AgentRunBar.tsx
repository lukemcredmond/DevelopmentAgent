import type {
  AgentRunState,
  CardWorkProgress,
  LastStepDiagnostics,
  LastStepOutcome,
  StepProgress,
} from '../types'

interface AgentRunBarProps {
  activeRun: AgentRunState | null
  currentTool?: string | null
  planRunActive?: boolean
  onOpenTools?: () => void
  onOpenTask?: (taskId: string) => void
  onRetry?: (mode: 'same' | 'optimized') => void | Promise<void>
  retrying?: boolean
  lastStepOutcome?: LastStepOutcome | null
  lastStepDiagnostics?: LastStepDiagnostics | null
  onExtend?: (extraIterations: number) => void | Promise<void>
  onResetStep?: () => void | Promise<void>
  extending?: boolean
  sprintProgress?: { intent?: string; cardProgress?: CardWorkProgress } | null
}

function formatMs(ms: number | undefined): string {
  if (ms == null || Number.isNaN(ms)) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function progressLine(progress: StepProgress): string {
  const tools = progress.toolsUsed?.length
    ? progress.toolsUsed.join(', ')
    : 'none'
  const plan = progress.planRejections ?? 0
  const text = progress.textRejections ?? 0
  const loop = progress.stuckLoop
    ? 'repeated same tool fails'
    : 'not stuck in a loop'
  return `Used: ${tools} · ${plan} plan / ${text} text rejects · ${loop}`
}

function cardProgressLine(cp: CardWorkProgress | null | undefined): string | null {
  if (!cp) return null
  const parts: string[] = []
  if ((cp.subtasksTotal ?? 0) > 0) {
    parts.push(`todos ${cp.subtasksDone ?? 0}/${cp.subtasksTotal}`)
  }
  if ((cp.gatesRemaining?.length ?? 0) > 0) {
    parts.push(`next: ${cp.gatesRemaining!.join(' → ')}`)
  }
  if ((cp.stuckLoops ?? 0) > 0) {
    parts.push(`no lane move ×${cp.stuckLoops}`)
  }
  if ((cp.acCount ?? 0) > 0) {
    parts.push(`${cp.acCount} ACs`)
  }
  if ((cp.filesThisStep?.length ?? 0) > 0) {
    parts.push(`wrote ${cp.filesThisStep!.length} file(s)`)
  }
  return parts.length ? parts.join(' · ') : null
}

export default function AgentRunBar({
  activeRun,
  currentTool,
  planRunActive = false,
  onOpenTools,
  onOpenTask,
  onRetry,
  retrying = false,
  lastStepOutcome = null,
  lastStepDiagnostics = null,
  onExtend,
  onResetStep,
  extending = false,
  sprintProgress = null,
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

  const progress =
    lastStepOutcome?.stepProgress ??
    lastStepDiagnostics?.stepProgress ??
    null
  const intent =
    activeRun?.intent ||
    sprintProgress?.intent ||
    progress?.intent ||
    null
  const cardLine = cardProgressLine(
    activeRun?.cardProgress ?? sprintProgress?.cardProgress ?? progress?.cardProgress,
  )
  const whyStayed = lastStepOutcome?.whyCardStayed ?? progress?.whyCardStayed
  const suggested = lastStepOutcome?.suggestedAction ?? progress?.suggestedAction
  const isMaxIter =
    lastStepOutcome?.stopReason === 'max_iterations' ||
    (activeRun?.error ?? '').startsWith('Max tool iterations') ||
    lastStepDiagnostics?.exitReason === 'max_iterations'

  const showMaxIterPanel =
    isMaxIter &&
    (isDone || !hasActiveRun || activeRun?.status === 'failed') &&
    (onExtend || onResetStep)

  const timing = lastStepDiagnostics
  const showTiming =
    timing &&
    typeof timing.durationMs === 'number' &&
    (timing.durationMs > 0 || (timing.ollamaMsTotal ?? 0) > 0)

  const showIdleWhy = !hasActiveRun && !showMaxIterPanel && (whyStayed || suggested)

  if (!hasActiveRun && !showMaxIterPanel && !showTiming && !showIdleWhy) {
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

  if (hasActiveRun && isDone && !activeRun.error && !showMaxIterPanel && !showTiming && !whyStayed) {
    return null
  }

  const statusLabel = isWaitingApproval
    ? 'awaiting approval — agent paused'
    : activeRun?.status === 'tool_executing'
      ? 'running tool'
      : activeRun?.status === 'completed'
        ? 'step completed'
        : activeRun?.status === 'failed'
          ? 'step failed'
          : activeRun?.status ?? ''

  return (
    <div className="shrink-0 border-b border-indigo-500/30 bg-indigo-950/30 text-[11px] font-mono">
      {hasActiveRun && (isRunning || activeRun.error) && (
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
            <span className="text-indigo-300 truncate max-w-[200px]" title={toolLabel}>
              {toolLabel}
            </span>
          )}
          {activeRun?.currentToolDetail && isRunning && (
            <span
              className="text-amber-200/90 font-mono truncate max-w-[min(100%,28rem)]"
              title={activeRun.currentToolDetail}
            >
              {activeRun.currentToolDetail}
            </span>
          )}
          {activeRun.taskId && onOpenTask ? (
            <button
              type="button"
              onClick={() => onOpenTask(activeRun.taskId)}
              className="text-cat-overlay ml-auto text-[10px] underline decoration-indigo-400/40 hover:text-indigo-200 hover:decoration-indigo-300"
              title="Open card"
            >
              {activeRun.taskId}
            </button>
          ) : (
            <span className="text-cat-overlay ml-auto text-[10px]">{activeRun.taskId}</span>
          )}
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
      )}

      {intent && (isRunning || showIdleWhy) && (
        <p className="mx-4 mb-1 text-[10px] text-violet-200 truncate" title={intent}>
          {intent}
        </p>
      )}
      {cardLine && (isRunning || showIdleWhy || showMaxIterPanel) && (
        <p className="mx-4 mb-1 text-[10px] text-sky-200/90 truncate" title={cardLine}>
          {cardLine}
        </p>
      )}
      {showIdleWhy && (whyStayed || suggested) && (
        <div className="mx-4 mb-2 space-y-0.5 border border-amber-500/30 bg-amber-950/20 rounded px-2.5 py-1.5">
          {whyStayed && (
            <p className="text-[10px] text-amber-200">Stayed In Progress: {whyStayed}</p>
          )}
          {suggested && (
            <p className="text-[10px] text-cat-subtext">Suggested: {suggested}</p>
          )}
        </div>
      )}

      {isWaitingApproval && (
        <p className="mx-4 mb-2 text-[10px] text-amber-200">
          Agent paused — approve or deny the tool in the modal to continue.
        </p>
      )}

      {activeRun?.error && !showMaxIterPanel && (
        <div className="mx-4 mb-2 flex flex-wrap items-center gap-2">
          <p className="text-[10px] text-rose-300 flex-1 min-w-[200px]">{activeRun.error}</p>
          {onRetry && activeRun.status === 'failed' && (
            <>
              <button
                type="button"
                disabled={retrying}
                onClick={() => void onRetry('same')}
                className="text-[10px] px-2 py-1 rounded border border-rose-500/40 text-rose-200 hover:bg-rose-950/40 disabled:opacity-50"
              >
                {retrying ? '…' : 'Retry'}
              </button>
              <button
                type="button"
                disabled={retrying}
                onClick={() => void onRetry('optimized')}
                className="text-[10px] px-2 py-1 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/40 disabled:opacity-50"
              >
                {retrying ? '…' : 'Retry (optimized)'}
              </button>
            </>
          )}
        </div>
      )}

      {showMaxIterPanel && (
        <div className="mx-4 mb-2 space-y-1.5 border border-amber-500/40 bg-amber-950/30 rounded-lg px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-amber-200 font-bold text-[11px]">
              Hit LLM iteration limit
              {progress
                ? ` (${progress.iterationsUsed}/${progress.iterationsMax})`
                : activeRun?.maxIterations != null
                  ? ` (${activeRun.iteration ?? '—'}/${activeRun.maxIterations})`
                  : ''}
            </span>
          </div>
          {progress && (
            <p className="text-[10px] text-cat-subtext leading-relaxed">{progressLine(progress)}</p>
          )}
          {progress?.stuckLoop ? (
            <p className="text-[10px] text-amber-300">
              Repeated same tool args — extend may not help; fix approach or reset.
            </p>
          ) : (
            <p className="text-[10px] text-cat-overlay">
              Agent was still working (not idle). Extend continues with context from what already ran
              (new step — chat history is not resumed in-memory).
            </p>
          )}
          <div className="flex flex-wrap gap-2 pt-0.5">
            {onExtend && (
              <>
                <button
                  type="button"
                  disabled={extending}
                  onClick={() => void onExtend(4)}
                  className="text-[10px] px-2.5 py-1 rounded border border-emerald-500/40 text-emerald-200 hover:bg-emerald-950/40 disabled:opacity-50"
                >
                  {extending ? '…' : 'Extend +4'}
                </button>
                <button
                  type="button"
                  disabled={extending}
                  onClick={() => void onExtend(8)}
                  className="text-[10px] px-2.5 py-1 rounded border border-emerald-500/40 text-emerald-200 hover:bg-emerald-950/40 disabled:opacity-50"
                >
                  {extending ? '…' : 'Extend +8'}
                </button>
              </>
            )}
            {onResetStep && (
              <button
                type="button"
                disabled={extending}
                onClick={() => void onResetStep()}
                className="text-[10px] px-2.5 py-1 rounded border border-rose-500/40 text-rose-200 hover:bg-rose-950/40 disabled:opacity-50"
              >
                {extending ? '…' : 'Reset & retry'}
              </button>
            )}
            {onOpenTools && (
              <button
                type="button"
                onClick={onOpenTools}
                className="text-[10px] px-2.5 py-1 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/40"
              >
                Tools →
              </button>
            )}
          </div>
        </div>
      )}

      {showTiming && timing && (
        <p className="mx-4 mb-2 text-[10px] text-cat-overlay tabular-nums">
          Step {formatMs(timing.durationMs)}
          {' · '}
          Ollama {formatMs(timing.ollamaMsTotal)} ({timing.ollamaCallCount ?? timing.llmIterations?.used ?? '?'}{' '}
          calls)
          {' · '}
          Tools {formatMs(timing.toolMsTotal)}
          {' · '}
          {timing.llmIterations?.used ?? '?'}/{timing.llmIterations?.max ?? '?'} iters
          <span className="text-cat-overlay/70">
            {' '}
            — sprint prompts/tool loops are larger than chat
          </span>
        </p>
      )}
    </div>
  )
}
