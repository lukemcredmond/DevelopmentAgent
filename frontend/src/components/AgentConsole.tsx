import { useEffect, useRef } from 'react'
import type { SystemLog } from '../types'

interface AgentConsoleProps {
  logs: SystemLog[]
}

export default function AgentConsole({ logs }: AgentConsoleProps) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs.length])

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0f0f15]">
      <div className="bg-cat-mantle border-b border-cat-surface1 px-4 py-2 flex items-center justify-between shrink-0">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Agent Console Event Stream
        </h3>
        <span className="text-[10px] text-cat-overlay">{logs.length} events</span>
      </div>
      <div className="flex-1 p-3 overflow-y-auto space-y-2 font-mono text-xs">
        {logs.length === 0 && (
          <p className="text-cat-overlay italic">No log events yet.</p>
        )}
        {logs.map((log, i) => (
          <div
            key={i}
            className={`p-2 rounded border border-cat-surface1/40 ${
              log.type === 'success'
                ? 'text-emerald-400 bg-emerald-950/10'
                : log.type === 'error'
                  ? 'text-rose-400 bg-rose-950/10'
                  : log.type === 'warning'
                    ? 'text-amber-400 bg-amber-950/10'
                    : 'text-indigo-400'
            }`}
          >
            <div className="flex items-center justify-between opacity-75 mb-0.5 text-[10px]">
              <span className="font-bold uppercase">{log.source}</span>
              <span>{log.timestamp}</span>
            </div>
            <p className="whitespace-pre-wrap">{log.text}</p>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
