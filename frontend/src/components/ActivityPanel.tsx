import { useEffect, useMemo, useRef, useState } from 'react'
import type { ActivityEvent } from '../types'
import { formatTaskText } from '../utils/taskFormat'

type ActivityFilter = 'all' | 'po_bounce' | 'transcript' | 'failures'

interface ActivityPanelProps {
  events: ActivityEvent[]
  onTaskClick?: (taskId: string) => void
  onClear?: () => void
  wasCleared?: boolean
}

const SCROLL_THRESHOLD_PX = 48

export default function ActivityPanel({ events, onTaskClick, onClear, wasCleared = false }: ActivityPanelProps) {
  const [filter, setFilter] = useState<ActivityFilter>('all')
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)

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
          'tool_end',
          'tool_failed',
          'tool_start',
          'pending_tool',
          'tool_alias_saved',
        ].includes(e.kind),
      )
    }
    if (filter === 'failures') {
      return events.filter((e) => e.kind === 'tool_failed')
    }
    return events
  }, [events, filter])

  const failureCount = useMemo(
    () => events.filter((e) => e.kind === 'tool_failed').length,
    [events],
  )

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    stickToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_THRESHOLD_PX
  }

  useEffect(() => {
    const el = scrollRef.current
    if (!el || !stickToBottomRef.current) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'auto' })
  }, [filtered.length, events.length])

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
        <div className="flex gap-1 flex-wrap justify-end items-center">
          <span className="text-[10px] text-cat-overlay mr-1">{events.length} events</span>
          {onClear && (
            <button
              type="button"
              onClick={onClear}
              disabled={events.length === 0 && !wasCleared}
              className="text-[10px] px-2 py-0.5 rounded border border-cat-surface1 text-cat-subtext hover:text-white hover:bg-cat-surface0 disabled:opacity-50 mr-1"
            >
              Clear
            </button>
          )}
          {(['all', 'po_bounce', 'transcript', 'failures'] as const).map((f) => (
            <button
              key={f}
              type="button"
              onClick={() => setFilter(f)}
              className={`text-[9px] px-2 py-0.5 rounded uppercase ${
                filter === f
                  ? f === 'failures'
                    ? 'bg-rose-950/50 text-rose-300 border border-rose-500/40'
                    : 'bg-indigo-950/50 text-indigo-300 border border-indigo-500/40'
                  : 'text-cat-overlay hover:text-white'
              }`}
            >
              {f === 'po_bounce' ? 'PO↔Dev' : f}
              {f === 'failures' && failureCount > 0 ? ` (${failureCount})` : ''}
            </button>
          ))}
        </div>
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 p-3 overflow-y-auto space-y-2 font-mono text-xs"
      >
        {filtered.length === 0 && (
          <p className="text-cat-overlay italic">
            {wasCleared
              ? 'Cleared — new sprint events will appear here.'
              : 'No agent activity yet. Run a sprint step to see transcripts.'}
          </p>
        )}
        {[...filtered].reverse().map((event, i) => {
          const idx = filtered.length - 1 - i
          const isOpen = expanded.has(idx)
          const isFailure = event.kind === 'tool_failed'
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
                isFailure
                  ? 'border-rose-500/60 bg-rose-950/30 hover:border-rose-400 ring-1 ring-rose-500/20'
                  : 'border-cat-surface1/40 bg-cat-surface0/30 hover:border-indigo-500/30'
              }`}
            >
              <div className="flex items-center justify-between opacity-75 mb-1 text-[10px] gap-2">
                <span className={`font-bold truncate ${isFailure ? 'text-rose-300' : 'text-indigo-300'}`}>
                  {formatTaskText(event.agent)} · {formatTaskText(event.taskTitle)}
                </span>
                <span className="shrink-0">{event.timestamp}</span>
              </div>
              <div className="text-[10px] mb-1 flex items-center gap-2">
                {isFailure && (
                  <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-rose-900/60 text-rose-200">
                    FAILED
                  </span>
                )}
                <span className={isFailure ? 'text-rose-300' : 'text-cat-overlay'}>
                  {event.kind}
                  {event.lane ? ` → ${event.lane}` : ''}
                </span>
              </div>
              <p className={`whitespace-pre-wrap ${isFailure ? 'text-rose-100' : 'text-cat-subtext'}`}>
                {preview}
              </p>
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
