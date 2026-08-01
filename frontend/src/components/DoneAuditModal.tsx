import { useCallback, useEffect, useMemo, useState } from 'react'
import { applyDoneAudit, fetchDoneAudit } from '../api/client'
import type { DoneAuditItem, DoneAuditReport } from '../types'

interface DoneAuditModalProps {
  open: boolean
  onClose: () => void
  onApplied: (state: import('../types').AppState) => void
}

export default function DoneAuditModal({ open, onClose, onApplied }: DoneAuditModalProps) {
  const [report, setReport] = useState<DoneAuditReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetchDoneAudit()
      setReport(res)
      setSelected(new Set((res.items ?? []).map((i) => i.taskId)))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setReport(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const items = report?.items ?? []

  const allSelected = useMemo(
    () => items.length > 0 && items.every((i) => selected.has(i.taskId)),
    [items, selected],
  )

  const toggleAll = () => {
    if (allSelected) setSelected(new Set())
    else setSelected(new Set(items.map((i) => i.taskId)))
  }

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const applyMove = async (moveTo: 'In Progress' | 'Backlog') => {
    const taskIds = [...selected]
    if (!taskIds.length) return
    setApplying(true)
    setError(null)
    try {
      const data = await applyDoneAudit({ taskIds, moveTo, onlyIncomplete: true })
      onApplied(data)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setApplying(false)
    }
  }

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal
      aria-labelledby="done-audit-title"
    >
      <div className="bg-cat-mantle border border-cat-surface1 rounded-xl max-w-2xl w-full max-h-[85vh] flex flex-col shadow-xl">
        <div className="px-4 py-3 border-b border-cat-surface1 flex items-center justify-between gap-2">
          <h2 id="done-audit-title" className="text-sm font-bold text-white">
            Audit Done column
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-cat-overlay hover:text-white text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="px-4 py-2 text-[11px] text-cat-subtext border-b border-cat-surface1">
          Flags cards in Done with pending Agent progress (read / implement / verify) or unchecked
          acceptance criteria. Move selected cards back to In Progress or Backlog to finish properly.
        </div>
        {error && (
          <p className="px-4 py-2 text-[11px] text-rose-300 bg-rose-950/30">{error}</p>
        )}
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-2">
          {loading && <p className="text-xs text-cat-subtext">Loading audit…</p>}
          {!loading && report && items.length === 0 && (
            <p className="text-xs text-emerald-300">
              All {report.totalDone} Done card(s) pass the current checks.
            </p>
          )}
          {!loading && items.length > 0 && (
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-cat-overlay text-left">
                  <th className="py-1 w-8">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label="Select all"
                    />
                  </th>
                  <th className="py-1">Card</th>
                  <th className="py-1">Issues</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row: DoneAuditItem) => (
                  <tr key={row.taskId} className="border-t border-cat-surface1/60 align-top">
                    <td className="py-1.5">
                      <input
                        type="checkbox"
                        checked={selected.has(row.taskId)}
                        onChange={() => toggleOne(row.taskId)}
                        aria-label={`Select ${row.taskId}`}
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <span className="font-mono text-indigo-300">{row.taskId}</span>
                      <div className="text-white truncate max-w-[12rem]" title={row.title}>
                        {row.title}
                      </div>
                    </td>
                    <td className="py-1.5 text-cat-subtext">
                      <ul className="list-disc pl-4 space-y-0.5">
                        {(row.reasons ?? []).map((r, i) => (
                          <li key={i}>{r}</li>
                        ))}
                      </ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <div className="px-4 py-3 border-t border-cat-surface1 flex flex-wrap gap-2 justify-end">
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading || applying}
            className="text-xs px-3 py-1.5 rounded border border-cat-surface1 text-cat-subtext hover:text-white"
          >
            Refresh
          </button>
          <button
            type="button"
            disabled={applying || selected.size === 0}
            onClick={() => void applyMove('Backlog')}
            className="text-xs px-3 py-1.5 rounded bg-cat-surface0 border border-cat-surface1 text-white disabled:opacity-50"
          >
            Move to Backlog
          </button>
          <button
            type="button"
            disabled={applying || selected.size === 0}
            onClick={() => void applyMove('In Progress')}
            className="text-xs px-3 py-1.5 rounded bg-indigo-600 hover:bg-indigo-500 text-white disabled:opacity-50"
          >
            {applying ? 'Moving…' : 'Move to In Progress'}
          </button>
        </div>
      </div>
    </div>
  )
}
