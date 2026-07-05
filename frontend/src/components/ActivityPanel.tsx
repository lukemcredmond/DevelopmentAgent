import { useMemo, useState } from 'react'
import type { ActivityEvent } from '../types'
import { formatTaskText } from '../utils/taskFormat'

type ActivityFilter = 'all' | 'po_bounce' | 'transcript'

interface ActivityPanelProps {
  events: ActivityEvent[]
  onTaskClick?: (taskId: string) => void
}

export default function ActivityPanel({ events, onTaskClick }: ActivityPanelProps) {
  const [filter, setFilter] = useState<ActivityFilter>('all')
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const filtered = useMemo(() => {
    if (filter === 'po_bounce') {
      return events.filter((e) =>
        ['po_round_trip', 'dev_escalation', 'po_clarified', 'po_limit', 'stuck_loop'].includes(e.kind),
      )
    }
    if (filter === 'transcript') {
      return events.filter((e) =>
        [
          'transcript',
          'decision_detail',
          'tool',
          'tool_failed',
          'tool_start',
          'tool_end',
          'pending_tool',
          'tool_alias_saved',
        ].includes(e.kind),
      )
    }
    return events
  }, [events, filter])

  const toggleExpand = (index: number) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0f0f15]">
      <div className="bg-cat-mantle border-b border-cat-surface1 px-4 py-2 flex items-center justify-between shrink-0 gap-2">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Agent Activity
        </h3>
        <div className="flex gap-1">
          {(['all', 'po_bounce', 'transcript'] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`text-[9px] px-2 py-0.5 rounded uppercase ${
                filter === f
                  ? 'bg-indigo-950/50 text-indigo-300 border border-indigo-500/40'
                  : 'text-cat-overlay hover:text-white'
              }`}
            >
              {f === 'po_bounce' ? 'PO↔Dev' : f}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 p-3 overflow-y-auto space-y-2 font-mono text-xs">
        {filtered.length === 0 && (
          <p className="text-cat-overlay italic">No agent activity yet. Run a sprint step to see transcripts.</p>
        )}
        {[...filtered].reverse().map((event, i) => {
          const idx = filtered.length - 1 - i
          const isOpen = expanded.has(idx)
          const contentStr = formatTaskText(event.content)
          const preview =
            contentStr.length > 120 && !isOpen
              ? `${contentStr.slice(0, 120)}…`
              : contentStr
          return (
            <button
              key={`${event.timestamp}-${event.taskId}-${idx}`}
              type="button"
              onClick={() => toggleExpand(idx)}
              className={`w-full text-left p-2 rounded border transition-colors ${
                event.kind === 'tool_failed'
                  ? 'border-rose-500/40 bg-rose-950/20 hover:border-rose-500/60'
                  : 'border-cat-surface1/40 bg-cat-surface0/30 hover:border-indigo-500/30'
              }`}
            >
              <div className="flex items-center justify-between opacity-75 mb-1 text-[10px] gap-2">
                <span className="font-bold text-indigo-300 truncate">
                  {formatTaskText(event.agent)} · {formatTaskText(event.taskTitle)}
                </span>
                <span className="shrink-0">{event.timestamp}</span>
              </div>
              <div className="text-[10px] text-cat-overlay mb-1">
                {event.kind}
                {event.lane ? ` → ${event.lane}` : ''}
              </div>
              <p className="whitespace-pre-wrap text-cat-subtext">{preview}</p>
              {onTaskClick && (
                <span
                  role="presentation"
                  onClick={(e) => {
                    e.stopPropagation()
                    onTaskClick(event.taskId)
                  }}
                  className="inline-block mt-1 text-[10px] text-indigo-400 hover:underline"
                >
                  Open task
                </span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
