import { useCallback, useEffect, useMemo, useState } from 'react'
import { applyRefinementAudit, fetchRefinementAudit } from '../api/client'
import type { RefinementAuditCluster, RefinementAuditReport } from '../types'

interface RefinementAuditModalProps {
  open: boolean
  onClose: () => void
  onApplied: (state: import('../types').AppState) => void
}

export default function RefinementAuditModal({
  open,
  onClose,
  onApplied,
}: RefinementAuditModalProps) {
  const [report, setReport] = useState<RefinementAuditReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedRemove, setSelectedRemove] = useState<Set<string>>(new Set())
  const [selectedQuality, setSelectedQuality] = useState<Set<string>>(new Set())
  const [tab, setTab] = useState<'duplicates' | 'quality'>('duplicates')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetchRefinementAudit()
      setReport(res)
      setSelectedRemove(new Set(res.defaultRemoveTaskIds ?? []))
      setSelectedQuality(new Set())
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

  const clusters = report?.clusters ?? []
  const quality = report?.qualityIssues ?? []

  const duplicateOfMap = useMemo(() => {
    const map: Record<string, string> = {}
    for (const c of clusters) {
      const keep = c.suggestedKeepTaskId
      for (const id of c.removableTaskIds ?? []) {
        if (selectedRemove.has(id)) map[id] = keep
      }
    }
    return map
  }, [clusters, selectedRemove])

  const toggleRemove = (id: string) => {
    setSelectedRemove((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const selectAllDuplicates = () => {
    setSelectedRemove(new Set(report?.defaultRemoveTaskIds ?? []))
  }

  const toggleQuality = (id: string) => {
    setSelectedQuality((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const applyDelete = async () => {
    const deleteTaskIds = [...selectedRemove, ...selectedQuality]
    if (!deleteTaskIds.length) return
    setApplying(true)
    setError(null)
    try {
      const data = await applyRefinementAudit({ deleteTaskIds })
      onApplied(data)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setApplying(false)
    }
  }

  const applyMoveDone = async () => {
    const moveToDoneTaskIds = [...selectedRemove]
    if (!moveToDoneTaskIds.length) return
    setApplying(true)
    setError(null)
    try {
      const data = await applyRefinementAudit({
        moveToDoneTaskIds,
        duplicateOfByTaskId: duplicateOfMap,
      })
      onApplied(data)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setApplying(false)
    }
  }

  const applyMoveBacklog = async () => {
    const ids = [...selectedQuality]
    if (!ids.length) return
    setApplying(true)
    setError(null)
    try {
      const data = await applyRefinementAudit({ moveToBacklogTaskIds: ids })
      onApplied(data)
      await load()
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
      aria-labelledby="refinement-audit-title"
    >
      <div className="bg-cat-mantle border border-cat-surface1 rounded-xl max-w-3xl w-full max-h-[85vh] flex flex-col shadow-xl">
        <div className="px-4 py-3 border-b border-cat-surface1 flex items-center justify-between gap-2">
          <h2 id="refinement-audit-title" className="text-sm font-bold text-white">
            Review Refinement lane
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
        <div className="px-4 py-2 text-[11px] text-cat-subtext border-b border-cat-surface1 space-y-1">
          <p>
            Finds likely duplicate cards (same or similar titles) and quality flags (empty AC,
            epics in Refinement, already marked refinementComplete). Pick a card to keep in each
            group; remove or consolidate the rest.
          </p>
          {report && (
            <p className="text-violet-200 font-mono text-[10px]">
              {report.totalRefinement} in Refinement · {report.duplicateClusterCount} duplicate
              group(s) · ~{report.estimatedUniqueAfterMerge} unique after merge ·{' '}
              {report.qualityIssueCount} quality flag(s)
            </p>
          )}
        </div>
        {error && (
          <p className="px-4 py-2 text-[11px] text-rose-300 bg-rose-950/30">{error}</p>
        )}
        <div className="px-4 pt-2 flex gap-2 border-b border-cat-surface1">
          <button
            type="button"
            onClick={() => setTab('duplicates')}
            className={`text-[11px] px-2 py-1 rounded-t border-b-2 ${
              tab === 'duplicates'
                ? 'border-violet-400 text-white'
                : 'border-transparent text-cat-overlay'
            }`}
          >
            Duplicates ({clusters.length})
          </button>
          <button
            type="button"
            onClick={() => setTab('quality')}
            className={`text-[11px] px-2 py-1 rounded-t border-b-2 ${
              tab === 'quality'
                ? 'border-violet-400 text-white'
                : 'border-transparent text-cat-overlay'
            }`}
          >
            Quality ({quality.length})
          </button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-2">
          {loading && <p className="text-xs text-cat-subtext">Analyzing Refinement…</p>}
          {!loading && report && tab === 'duplicates' && clusters.length === 0 && (
            <p className="text-xs text-emerald-300">No duplicate groups detected at the current threshold.</p>
          )}
          {!loading && tab === 'duplicates' && clusters.length > 0 && (
            <div className="space-y-3">
              <button
                type="button"
                onClick={selectAllDuplicates}
                className="text-[10px] text-indigo-300 hover:text-indigo-200"
              >
                Select all suggested removals ({report?.defaultRemoveTaskIds?.length ?? 0})
              </button>
              {clusters.map((cluster: RefinementAuditCluster) => (
                <div
                  key={cluster.clusterId}
                  className="rounded border border-cat-surface1/80 bg-cat-base/40 p-2"
                >
                  <div className="text-[10px] text-cat-overlay mb-1.5">
                    {cluster.matchKind.replace(/_/g, ' ')} · {cluster.memberCount} cards · max score{' '}
                    {cluster.maxScore}
                  </div>
                  <ul className="space-y-1">
                    {cluster.members.map((m) => (
                      <li
                        key={m.taskId}
                        className="flex items-start gap-2 text-[11px] border-t border-cat-surface1/50 pt-1 first:border-0 first:pt-0"
                      >
                        {m.isSuggestedKeep ? (
                          <span className="text-[9px] px-1 rounded bg-emerald-950/50 text-emerald-300 shrink-0 mt-0.5">
                            keep
                          </span>
                        ) : (
                          <input
                            type="checkbox"
                            checked={selectedRemove.has(m.taskId)}
                            onChange={() => toggleRemove(m.taskId)}
                            className="mt-0.5 shrink-0"
                            aria-label={`Remove ${m.taskId}`}
                          />
                        )}
                        <div className="min-w-0">
                          <span className="font-mono text-indigo-300">{m.taskId}</span>
                          <div className="text-white truncate" title={m.title}>
                            {m.title}
                          </div>
                          {!m.isSuggestedKeep && m.reasons.length > 0 && (
                            <div className="text-cat-overlay text-[10px]">{m.reasons.join('; ')}</div>
                          )}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
          {!loading && tab === 'quality' && quality.length === 0 && (
            <p className="text-xs text-emerald-300">No quality flags on current Refinement cards.</p>
          )}
          {!loading && tab === 'quality' && quality.length > 0 && (
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-cat-overlay text-left">
                  <th className="py-1 w-8" />
                  <th className="py-1">Card</th>
                  <th className="py-1">Flags</th>
                </tr>
              </thead>
              <tbody>
                {quality.map((row) => (
                  <tr key={row.taskId} className="border-t border-cat-surface1/60 align-top">
                    <td className="py-1.5">
                      <input
                        type="checkbox"
                        checked={selectedQuality.has(row.taskId)}
                        onChange={() => toggleQuality(row.taskId)}
                        aria-label={`Select ${row.taskId}`}
                      />
                    </td>
                    <td className="py-1.5 pr-2">
                      <span className="font-mono text-indigo-300">{row.taskId}</span>
                      <div className="text-white truncate max-w-[14rem]" title={row.title}>
                        {row.title}
                      </div>
                    </td>
                    <td className="py-1.5 text-cat-subtext">
                      <ul className="list-disc pl-4 space-y-0.5">
                        {row.reasons.map((r, i) => (
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
            disabled={applying || selectedQuality.size === 0}
            onClick={() => void applyMoveBacklog()}
            className="text-xs px-3 py-1.5 rounded bg-cat-surface0 border border-cat-surface1 text-white disabled:opacity-50"
          >
            Move flagged to Backlog
          </button>
          <button
            type="button"
            disabled={applying || selectedRemove.size === 0}
            onClick={() => void applyMoveDone()}
            className="text-xs px-3 py-1.5 rounded bg-cat-surface0 border border-amber-500/40 text-amber-100 disabled:opacity-50"
          >
            Mark dupes Done
          </button>
          <button
            type="button"
            disabled={applying || (selectedRemove.size === 0 && selectedQuality.size === 0)}
            onClick={() => void applyDelete()}
            className="text-xs px-3 py-1.5 rounded bg-rose-900/60 hover:bg-rose-800/70 text-white disabled:opacity-50"
          >
            {applying ? 'Applying…' : 'Delete selected'}
          </button>
        </div>
      </div>
    </div>
  )
}
