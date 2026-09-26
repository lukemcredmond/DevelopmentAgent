import { useCallback, useEffect, useState } from 'react'
import {
  applyBoardDuplicateAudit,
  fetchBoardDuplicateAudit,
  fetchSprintDiagnosticsRollup,
} from '../api/client'
import type {
  AppState,
  BoardDuplicateAuditCluster,
  BoardDuplicateAuditReport,
  SprintDiagnosticsRollup,
} from '../types'

interface SprintDiagnosticsPanelProps {
  active?: boolean
  sprintRunning?: boolean
  onStateUpdate?: (state: AppState) => void
  onOpenTreeDependencies?: () => void
}

function CountTable({
  title,
  rows,
}: {
  title: string
  rows: [string, number][]
}) {
  if (rows.length === 0) {
    return (
      <p className="text-[10px] text-cat-overlay italic">
        {title}: no data in window
      </p>
    )
  }
  const max = Math.max(...rows.map(([, c]) => c), 1)
  return (
    <div className="space-y-1">
      <div className="text-[10px] font-semibold uppercase text-cat-subtext">{title}</div>
      {rows.map(([label, count]) => (
        <div key={label} className="flex items-center gap-2 text-[10px] font-mono">
          <span className="w-28 truncate text-cat-subtext" title={label}>
            {label}
          </span>
          <div className="flex-1 h-2 bg-cat-base rounded overflow-hidden">
            <div
              className="h-full bg-indigo-500/60"
              style={{ width: `${Math.round((count / max) * 100)}%` }}
            />
          </div>
          <span className="w-6 text-right text-cat-overlay">{count}</span>
        </div>
      ))}
    </div>
  )
}

export default function SprintDiagnosticsPanel({
  active = true,
  sprintRunning = false,
  onStateUpdate,
  onOpenTreeDependencies,
}: SprintDiagnosticsPanelProps) {
  const [rollup, setRollup] = useState<SprintDiagnosticsRollup | null>(null)
  const [dupReport, setDupReport] = useState<BoardDuplicateAuditReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!active) return
    setLoading(true)
    setError(null)
    try {
      const [r, d] = await Promise.all([
        fetchSprintDiagnosticsRollup({ limit: 50, sinceHours: 72 }),
        fetchBoardDuplicateAudit(),
      ])
      setRollup(r)
      setDupReport(d)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load diagnostics')
    } finally {
      setLoading(false)
    }
  }, [active])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    if (!active) return
    const ms = sprintRunning ? 30_000 : 120_000
    const id = window.setInterval(() => void refresh(), ms)
    return () => window.clearInterval(id)
  }, [active, sprintRunning, refresh])

  const handleApplyRecommended = () => {
    if (
      !window.confirm(
        'Move duplicate cards to Blocked (or Done for stubs), keep the richest card in each cluster?',
      )
    ) {
      return
    }
    setApplying(true)
    void applyBoardDuplicateAudit({ applyRecommended: true })
      .then((data) => {
        onStateUpdate?.(data)
        return refresh()
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Apply failed'))
      .finally(() => setApplying(false))
  }

  const exitRows = Object.entries(rollup?.exitReason ?? {}).sort((a, b) => b[1] - a[1])
  const refusalRows = Object.entries(rollup?.textRefusalClass ?? {}).sort((a, b) => b[1] - a[1])

  return (
    <div
      className="space-y-4 border border-cat-surface1 rounded-lg p-3 bg-cat-mantle/30"
      data-testid="sprint-diagnostics-panel"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-[11px] font-bold uppercase tracking-wider text-indigo-300">
          Sprint diagnostics rollup
        </h4>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="text-[10px] text-cat-overlay hover:text-white ml-auto"
        >
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {error && <p className="text-[10px] text-rose-400">{error}</p>}

      {rollup && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="text-[10px] text-cat-subtext space-y-0.5">
            <p>
              Steps: <span className="text-white">{rollup.stepsTotal}</span>
              {rollup.window?.sinceHours != null && (
                <> · last {rollup.window.sinceHours}h</>
              )}
            </p>
            <p>
              OK rate: {((rollup.okRate ?? 0) * 100).toFixed(0)}%
              {rollup.medianDurationMs != null && (
                <> · median {Math.round(rollup.medianDurationMs / 1000)}s</>
              )}
            </p>
            <p>
              Duplicates (board scan):{' '}
              <span className="text-amber-200">{rollup.duplicateClusterCount ?? 0}</span> clusters,{' '}
              {rollup.duplicateExtraCount ?? 0} extras
            </p>
          </div>
          <CountTable title="exitReason" rows={exitRows} />
          <CountTable title="textRefusalClass" rows={refusalRows} />
          <div className="text-[10px] font-mono text-cat-subtext">
            <span className="text-cat-overlay">toolPolicyDeadlock:</span>{' '}
            yes {rollup.toolPolicyDeadlock?.true ?? 0} / no{' '}
            {rollup.toolPolicyDeadlock?.false ?? 0}
            {(rollup.toolPolicyDeadlock?.topReasons?.length ?? 0) > 0 && (
              <ul className="mt-1 list-disc pl-4 text-cat-overlay">
                {rollup.toolPolicyDeadlock!.topReasons!.slice(0, 4).map((r) => (
                  <li key={r.reason}>
                    {r.count}× {r.reason}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}

      <div className="border-t border-cat-surface1 pt-3 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-[11px] font-bold uppercase tracking-wider text-amber-300/90">
            Duplicate cards
          </h4>
          {onOpenTreeDependencies && (
            <button
              type="button"
              onClick={onOpenTreeDependencies}
              className="text-[10px] text-indigo-300 hover:text-indigo-200"
            >
              Open board → Tree → Dependencies
            </button>
          )}
          {(dupReport?.duplicateClusterCount ?? 0) > 0 && (
            <button
              type="button"
              onClick={handleApplyRecommended}
              disabled={applying || sprintRunning}
              className="ml-auto text-[10px] px-2 py-1 rounded bg-amber-700/40 hover:bg-amber-700/60 text-amber-100 disabled:opacity-40"
              title={sprintRunning ? 'Wait for sprint step to finish' : undefined}
            >
              {applying ? 'Applying…' : 'Apply recommended'}
            </button>
          )}
        </div>
        <p className="text-[10px] text-cat-overlay">
          Scans Needs PO, In Progress, Backlog, Features for overlapping work (title, files, edit
          target, blockedBy).
        </p>
        {(dupReport?.clusters ?? []).length === 0 ? (
          <p className="text-[10px] text-cat-overlay italic">No duplicate clusters found.</p>
        ) : (
          <ul className="space-y-2 max-h-48 overflow-y-auto">
            {(dupReport?.clusters ?? []).map((cluster: BoardDuplicateAuditCluster) => (
              <li
                key={cluster.clusterId}
                className="text-[10px] border border-cat-surface1 rounded p-2 bg-cat-base/40"
              >
                <div className="font-semibold text-cat-text">
                  {cluster.matchKind} · {cluster.memberCount} cards · keep{' '}
                  {cluster.suggestedKeepTaskId}
                </div>
                <div className="text-cat-subtext mt-0.5">{cluster.achievementSummary}</div>
                <ul className="mt-1 text-cat-overlay font-mono">
                  {cluster.members.map((m) => (
                    <li key={m.taskId}>
                      {m.isSuggestedKeep ? '★ ' : '· '}
                      {m.taskId} ({m.lane}) — {m.title.slice(0, 60)}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
