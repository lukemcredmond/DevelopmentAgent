import { memo, useEffect, useRef } from 'react'
import type { SystemLog } from '../types'
import VirtualScrollList from './VirtualScrollList'

interface AgentConsoleProps {
  logs: SystemLog[]
  onClear?: () => void
  sseLive?: boolean
}

function isActionableWarning(text: string): boolean {
  const lower = text.toLowerCase()
  return (
    lower.includes('check transcript') ||
    lower.includes('failed tool') ||
    lower.includes('tool fail') ||
    lower.includes('no files recorded')
  )
}

export default memo(function AgentConsole({ logs, onClear, sseLive = true }: AgentConsoleProps) {
  const stickToBottomRef = useRef(true)

  const handleScroll = () => {
    stickToBottomRef.current = false
  }

  useEffect(() => {
    if (!stickToBottomRef.current) return
    stickToBottomRef.current = true
  }, [logs.length])

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0f0f15]">
      <div className="bg-cat-mantle border-b border-cat-surface1 px-4 py-2 flex items-center justify-between shrink-0 gap-2">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Agent Console Event Stream
        </h3>
        <div className="flex items-center gap-2">
          <span className="text-[9px] text-cat-overlay normal-case">Newest first</span>
          <span
            className={`text-[9px] uppercase px-1.5 py-0.5 rounded ${
              sseLive
                ? 'text-emerald-400 bg-emerald-950/30'
                : 'text-amber-400 bg-amber-950/30'
            }`}
          >
            {sseLive ? 'Live' : 'Reconnecting…'}
          </span>
          <span className="text-[10px] text-cat-overlay">{logs.length} events</span>
          {onClear && logs.length > 0 && (
            <button
              type="button"
              onClick={onClear}
              className="text-[10px] px-2 py-0.5 rounded border border-cat-surface1 text-cat-subtext hover:text-white hover:bg-cat-surface0"
            >
              Clear
            </button>
          )}
        </div>
      </div>
      <VirtualScrollList
        className="flex-1 p-3 font-mono text-xs"
        items={logs}
        estimateRowHeight={80}
        getKey={(_, i) => i}
        onScroll={handleScroll}
        newestFirst
        empty={<p className="text-cat-overlay italic">No log events yet.</p>}
        renderRow={(log) => {
          const actionable = log.type === 'warning' && isActionableWarning(log.text)
          return (
            <div
              className={`p-2 rounded border mb-2 ${
                actionable
                  ? 'text-amber-200 bg-amber-950/25 border-amber-500/50 ring-1 ring-amber-500/20'
                  : log.type === 'success'
                    ? 'text-emerald-400 bg-emerald-950/10 border-cat-surface1/40'
                    : log.type === 'error'
                      ? 'text-rose-400 bg-rose-950/10 border-rose-500/40'
                      : log.type === 'warning'
                        ? 'text-amber-400 bg-amber-950/10 border-amber-500/30'
                        : 'text-indigo-400 border-cat-surface1/40'
              }`}
            >
              <div className="flex items-center justify-between opacity-75 mb-0.5 text-[10px] gap-2">
                <span className="font-bold uppercase flex items-center gap-2">
                  {actionable && (
                    <span className="text-[9px] px-1 py-0.5 rounded bg-amber-900/60 text-amber-100 normal-case">
                      Action needed
                    </span>
                  )}
                  {log.source}
                </span>
                <span>{log.timestamp}</span>
              </div>
              <p className="whitespace-pre-wrap">{log.text}</p>
            </div>
          )
        }}
      />
    </div>
  )
})
