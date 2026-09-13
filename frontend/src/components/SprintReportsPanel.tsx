import { memo, useMemo, useState } from 'react'
import type { SprintReport } from '../types'
import { formatTaskText } from '../utils/taskFormat'

interface SprintReportsPanelProps {
  current: SprintReport | null
  history: SprintReport[]
  onTaskClick?: (taskId: string) => void
}

function ReportBody({
  report,
  onTaskClick,
}: {
  report: SprintReport
  onTaskClick?: (taskId: string) => void
}) {
  const transitions = Object.entries(report.byTransition || {}).sort((a, b) => b[1] - a[1])
  const moves = report.moves || []
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label="Moved" value={String(report.movedCount ?? moves.length)} />
        <Stat label="Unblocked" value={String(report.unblocked ?? 0)} />
        <Stat label="Split parents" value={String(report.splitParents ?? 0)} />
        <Stat label="Needs User in / out" value={`${report.needsUserIn ?? 0} / ${report.needsUserOut ?? 0}`} />
      </div>
      {transitions.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-cat-overlay mb-1">Where they moved</p>
          <ul className="space-y-0.5 font-mono text-[11px] text-cat-subtext">
            {transitions.map(([edge, count]) => (
              <li key={edge}>
                <span className="text-cat-text">{edge}</span>
                <span className="text-cat-overlay"> × {count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {moves.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-cat-overlay mb-1">Cards</p>
          <ul className="space-y-1">
            {moves.map((move, i) => (
              <li key={`${move.taskId}-${move.at}-${i}`} className="text-[11px] font-mono">
                <button
                  type="button"
                  className="text-sky-300 hover:underline"
                  onClick={() => onTaskClick?.(move.taskId)}
                >
                  {move.taskId.slice(0, 12)}
                </button>
                <span className="text-cat-overlay"> {formatTaskText(move.title).slice(0, 60)}</span>
                <span className="text-cat-subtext">
                  {' '}
                  {move.fromLane} → {move.toLane}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-cat-surface1 bg-cat-mantle/60 px-2 py-1.5">
      <p className="text-[9px] uppercase tracking-wider text-cat-overlay">{label}</p>
      <p className="text-sm text-cat-text font-medium">{value}</p>
    </div>
  )
}

export default memo(function SprintReportsPanel({
  current,
  history,
  onTaskClick,
}: SprintReportsPanelProps) {
  const [selectedAt, setSelectedAt] = useState<string | null>(null)
  const selected = useMemo(() => {
    if (!selectedAt) return null
    return history.find((r) => r.startedAt === selectedAt) || null
  }, [history, selectedAt])

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#0f0f15]">
      <div className="bg-cat-mantle border-b border-cat-surface1 px-4 py-2 flex items-center justify-between shrink-0 gap-2">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">Sprint Reports</h3>
        <span className="text-[9px] text-cat-overlay">{history.length} saved</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4 text-xs">
        <section>
          <h4 className="text-[10px] uppercase tracking-wider text-indigo-300 mb-2">Current sprint</h4>
          {current ? (
            <ReportBody report={current} onTaskClick={onTaskClick} />
          ) : (
            <p className="text-cat-overlay italic">No sprint in progress. Start Auto Sprint to record lane moves.</p>
          )}
        </section>
        <section>
          <h4 className="text-[10px] uppercase tracking-wider text-cat-overlay mb-2">Recent sprints</h4>
          {history.length === 0 ? (
            <p className="text-cat-overlay italic">Finished sprints will list here (last 20).</p>
          ) : (
            <ul className="space-y-2">
              {history.map((report, i) => {
                const key = report.startedAt || String(i)
                const open = selectedAt === report.startedAt
                return (
                  <li key={key} className="border border-cat-surface1 rounded-lg overflow-hidden">
                    <button
                      type="button"
                      className="w-full text-left px-3 py-2 hover:bg-cat-surface0/50"
                      onClick={() => setSelectedAt(open ? null : report.startedAt || null)}
                    >
                      <span className="text-cat-text">
                        {report.startedAt || 'sprint'} · {report.status || 'completed'}
                      </span>
                      <span className="text-cat-overlay">
                        {' '}
                        · {report.stepsRun ?? 0} steps · {report.movedCount ?? 0} moves · {report.unblocked ?? 0}{' '}
                        unblocked
                      </span>
                    </button>
                    {open && selected && (
                      <div className="px-3 pb-3 border-t border-cat-surface1 pt-2">
                        <ReportBody report={selected} onTaskClick={onTaskClick} />
                      </div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  )
})
