import { useEffect, useState } from 'react'
import type { AgentRunState, RecentToolEntry } from '../types'

interface AgentRunBarProps {
  activeRun: AgentRunState | null
  currentTool?: string | null
}

function mapRecentTool(raw: Record<string, unknown>): RecentToolEntry {
  return {
    toolName: String(raw.toolName ?? raw.tool_name ?? '?'),
    toolSuccess: Boolean(raw.toolSuccess ?? raw.tool_success),
    toolOutput: String(raw.toolOutput ?? raw.tool_output ?? ''),
    durationMs: Number(raw.durationMs ?? raw.duration_ms ?? 0),
    timestamp: String(raw.timestamp ?? ''),
  }
}

export default function AgentRunBar({ activeRun, currentTool }: AgentRunBarProps) {
  const [expanded, setExpanded] = useState(true)

  useEffect(() => {
    if (activeRun && activeRun.status !== 'completed' && activeRun.status !== 'failed') {
      setExpanded(true)
    }
  }, [activeRun?.runId, activeRun?.status])

  if (!activeRun) return null

  const toolLabel = currentTool || activeRun.currentTool
  const isWaitingApproval = activeRun.status === 'awaiting_approval'
  const isRunning =
    activeRun.status === 'thinking' ||
    activeRun.status === 'tool_executing' ||
    activeRun.status === 'awaiting_approval'
  const isDone = activeRun.status === 'completed' || activeRun.status === 'failed'

  if (isDone && !activeRun.recentTools?.length && !activeRun.error) {
    return null
  }

  const recentTools: RecentToolEntry[] = (activeRun.recentTools ?? []).map((t) => {
    if (typeof t === 'object' && t !== null && 'toolName' in t) {
      return t as RecentToolEntry
    }
    const entry = t as Record<string, unknown>
    return mapRecentTool(entry)
  })
  const lastTool = recentTools[recentTools.length - 1]

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
        {(recentTools.length > 0 || lastTool) && (
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="text-[10px] text-indigo-400 hover:text-indigo-300"
          >
            {expanded ? 'Hide tools' : `Show tools (${recentTools.length})`}
          </button>
        )}
      </div>

      {expanded && lastTool && (
        <div
          className={`mx-4 mb-2 p-2 rounded border ${
            lastTool.toolSuccess
              ? 'border-emerald-500/40 bg-emerald-950/20'
              : 'border-rose-500/40 bg-rose-950/20'
          }`}
        >
          <div className="flex items-center gap-2 mb-1 flex-wrap">
            <span
              className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded ${
                lastTool.toolSuccess
                  ? 'bg-emerald-900/60 text-emerald-200'
                  : 'bg-rose-900/60 text-rose-200'
              }`}
            >
              {lastTool.toolSuccess ? 'OK' : 'FAILED'}
            </span>
            <span className="text-indigo-300">{lastTool.toolName}</span>
            {lastTool.durationMs > 0 && (
              <span className="text-cat-overlay text-[10px]">{lastTool.durationMs}ms</span>
            )}
          </div>
          <p
            className={`whitespace-pre-wrap text-[10px] max-h-20 overflow-y-auto ${
              lastTool.toolSuccess ? 'text-cat-subtext' : 'text-rose-100'
            }`}
          >
            {lastTool.toolOutput || '(no output)'}
          </p>
        </div>
      )}

      {expanded && recentTools.length > 1 && (
        <div className="mx-4 mb-2 space-y-1 max-h-24 overflow-y-auto">
          {recentTools.slice(0, -1).reverse().map((t, i) => (
            <div
              key={`${t.toolName}-${t.timestamp}-${i}`}
              className="flex items-center gap-2 text-[10px] text-cat-overlay"
            >
              <span className={t.toolSuccess ? 'text-emerald-400' : 'text-rose-400'}>
                {t.toolSuccess ? '✓' : '✗'}
              </span>
              <span className="text-indigo-300">{t.toolName}</span>
              <span className="truncate flex-1">{t.toolOutput.slice(0, 80)}</span>
            </div>
          ))}
        </div>
      )}

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
