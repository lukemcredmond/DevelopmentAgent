import { useEffect, useRef, useState, type ReactNode } from 'react'
import {
  fetchTaskFieldHistory,
  fetchTaskFieldHistoryEntry,
  type TaskFieldHistoryEntry,
  type TaskFieldHistoryField,
} from '../api/client'

function formatStamp(raw: string): string {
  const s = String(raw || '').trim()
  if (!s) return '—'
  try {
    const d = new Date(s.includes('T') ? s : s.replace(' ', 'T') + 'Z')
    if (!Number.isNaN(d.getTime())) return d.toLocaleString()
  } catch {
    /* ignore */
  }
  return s
}

function renderSnapshot(field: TaskFieldHistoryField, value: string): React.ReactNode {
  if (field === 'acceptanceCriteria') {
    try {
      const arr = JSON.parse(value) as unknown
      if (Array.isArray(arr)) {
        if (arr.length === 0) return <p className="text-cat-overlay text-[11px]">(empty)</p>
        return (
          <ul className="list-disc pl-4 space-y-1 text-[11px] text-cat-subtext">
            {arr.map((c, i) => (
              <li key={i} className="whitespace-pre-wrap">
                {String(c)}
              </li>
            ))}
          </ul>
        )
      }
    } catch {
      /* fall through */
    }
  }
  if (field === 'sdd') {
    try {
      const obj = JSON.parse(value) as Record<string, unknown>
      const keys = ['userStory', 'scope', 'outOfScope', 'testPlan'] as const
      return (
        <div className="space-y-2 text-[11px]">
          {keys.map((k) => (
            <div key={k}>
              <p className="text-[10px] uppercase tracking-wide text-cat-overlay">{k}</p>
              <p className="text-cat-subtext whitespace-pre-wrap">
                {String(obj[k] || '').trim() || '(empty)'}
              </p>
            </div>
          ))}
        </div>
      )
    } catch {
      /* fall through */
    }
  }
  return (
    <pre className="text-[11px] text-cat-subtext whitespace-pre-wrap font-mono max-h-64 overflow-y-auto">
      {value || '(empty)'}
    </pre>
  )
}

interface FieldHistoryButtonProps {
  taskId: string
  field: TaskFieldHistoryField
  className?: string
}

/** Clock control: list prior field values by datetime, open snapshot. */
export default function FieldHistoryButton({ taskId, field, className = '' }: FieldHistoryButtonProps) {
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [entries, setEntries] = useState<TaskFieldHistoryEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<{
    timestamp: string
    source: string
    value: string
  } | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) {
        setOpen(false)
        setSelected(null)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const loadList = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchTaskFieldHistory(taskId, field)
      setEntries(data.entries || [])
      if (!(data.entries || []).length) {
        setError('No prior versions yet')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load history')
      setEntries([])
    } finally {
      setLoading(false)
    }
  }

  const openEntry = async (entry: TaskFieldHistoryEntry) => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchTaskFieldHistoryEntry(taskId, entry.id)
      setSelected({
        timestamp: data.entry.timestamp,
        source: data.entry.source,
        value: data.entry.value,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load snapshot')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div ref={rootRef} className={`relative inline-flex ${className}`}>
      <button
        type="button"
        title="Field history"
        data-testid={`field-history-${field}`}
        className="text-cat-overlay hover:text-indigo-300 px-1 py-0.5 rounded"
        onClick={(e) => {
          e.stopPropagation()
          e.preventDefault()
          const next = !open
          setOpen(next)
          setSelected(null)
          if (next) void loadList()
        }}
      >
        <i className="fa-regular fa-clock text-[11px]" aria-hidden />
        <span className="sr-only">History</span>
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-40 w-72 max-h-80 overflow-hidden rounded-lg border border-cat-surface1 bg-cat-surface0 shadow-lg"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="px-2 py-1.5 border-b border-cat-surface1 flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-wide text-cat-overlay">
              {selected ? 'Snapshot' : `${field} history`}
            </span>
            {selected && (
              <button
                type="button"
                className="text-[10px] text-indigo-300 hover:text-indigo-200"
                onClick={() => setSelected(null)}
              >
                Back to list
              </button>
            )}
          </div>
          <div className="overflow-y-auto max-h-64 p-2">
            {loading && <p className="text-[10px] text-cat-overlay">Loading…</p>}
            {!loading && error && !selected && (
              <p className="text-[10px] text-cat-overlay">{error}</p>
            )}
            {!loading && selected && (
              <div className="space-y-1">
                <p className="text-[10px] text-cat-overlay font-mono">
                  {formatStamp(selected.timestamp)} · {selected.source}
                </p>
                {renderSnapshot(field, selected.value)}
              </div>
            )}
            {!loading && !selected &&
              entries.map((e) => (
                <button
                  key={e.id}
                  type="button"
                  className="w-full text-left px-2 py-1.5 rounded hover:bg-cat-base border border-transparent hover:border-cat-surface1 mb-1"
                  onClick={() => void openEntry(e)}
                >
                  <span className="block text-[10px] font-mono text-indigo-300">
                    {formatStamp(e.timestamp)}
                  </span>
                  <span className="block text-[10px] text-cat-overlay truncate">
                    {e.source} — {e.preview || '(empty)'}
                  </span>
                </button>
              ))}
          </div>
        </div>
      )}
    </div>
  )
}
