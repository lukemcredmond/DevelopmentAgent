import { useEffect, useRef } from 'react'
import type { SystemLog } from '../types'

interface AgentConsoleProps {
  logs: SystemLog[]
}

const SCROLL_THRESHOLD_PX = 48

function isActionableWarning(text: string): boolean {
  const lower = text.toLowerCase()
  return (
    lower.includes('check transcript') ||
    lower.includes('failed tool') ||
    lower.includes('tool fail') ||
    lower.includes('no files recorded')
  )
}

export default function AgentConsole({ logs }: AgentConsoleProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    stickToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_THRESHOLD_PX
  }

  useEffect(() => {
    const el = scrollRef.current
    if (!el || !stickToBottomRef.current) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [logs.length])

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0f0f15]">
      <div className="bg-cat-mantle border-b border-cat-surface1 px-4 py-2 flex items-center justify-between shrink-0">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Agent Console Event Stream
        </h3>
        <span className="text-[10px] text-cat-overlay">{logs.length} events</span>
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 p-3 overflow-y-auto space-y-2 font-mono text-xs"
      >
        {logs.length === 0 && (
          <p className="text-cat-overlay italic">No log events yet.</p>
        )}
        {logs.map((log, i) => {
          const actionable = log.type === 'warning' && isActionableWarning(log.text)
          return (
            <div
              key={i}
              className={`p-2 rounded border ${
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
        })}
      </div>
    </div>
  )
}
