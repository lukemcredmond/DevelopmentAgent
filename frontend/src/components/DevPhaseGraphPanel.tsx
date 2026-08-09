import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchTaskFlow } from '../api/client'
import type { AgentRunState, DevPhaseGraphSnapshot, TaskFlowNode } from '../types'
import DevPhaseDiagram from './DevPhaseDiagram'
import DevPhaseStepper from './DevPhaseStepper'

const VIEW_STORAGE_KEY = 'ah-phase-graph-view'

type PhaseGraphView = 'list' | 'diagram'

interface DevPhaseGraphPanelProps {
  taskId: string
  active: boolean
  refreshKey?: number | string
  liveRefresh?: boolean
  /** Live agent run for this card when available. */
  activeRun?: AgentRunState | null
  lastSnapshot?: DevPhaseGraphSnapshot | null
  lastLabel?: string | null
  onSelectNode?: (nodeId: string) => void
}

function phaseChipClass(tag: string | null | undefined): string {
  const t = (tag || '').toLowerCase()
  if (t === 'explore') return 'border-sky-500/40 text-sky-200 bg-sky-950/40'
  if (t === 'patch') return 'border-amber-500/40 text-amber-200 bg-amber-950/40'
  if (t === 'verify') return 'border-emerald-500/40 text-emerald-200 bg-emerald-950/40'
  return 'border-cat-surface1 text-cat-overlay'
}

function phaseLabel(tag: string | null | undefined): string {
  const t = (tag || '').toLowerCase()
  if (t === 'explore') return 'Explore'
  if (t === 'patch') return 'Patch'
  if (t === 'verify') return 'Verify'
  return ''
}

function readStoredView(): PhaseGraphView {
  try {
    const v = sessionStorage.getItem(VIEW_STORAGE_KEY)
    if (v === 'diagram' || v === 'list') return v
  } catch {
    /* ignore */
  }
  return 'list'
}

export default function DevPhaseGraphPanel({
  taskId,
  active,
  refreshKey = 0,
  liveRefresh = false,
  activeRun = null,
  lastSnapshot = null,
  lastLabel = null,
  onSelectNode,
}: DevPhaseGraphPanelProps) {
  const [view, setView] = useState<PhaseGraphView>(() => readStoredView())
  const [tools, setTools] = useState<TaskFlowNode[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const setViewPersist = (next: PhaseGraphView) => {
    setView(next)
    try {
      sessionStorage.setItem(VIEW_STORAGE_KEY, next)
    } catch {
      /* ignore */
    }
  }

  const load = useCallback(async () => {
    if (!taskId) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetchTaskFlow(taskId, {
        limit: 40,
        offset: 0,
        order: 'desc',
        includeFull: false,
      })
      if (!mounted.current) return
      const toolNodes = (res.nodes || []).filter((n) => n.kind === 'tool')
      setTools(toolNodes)
    } catch (err) {
      if (!mounted.current) return
      setError(err instanceof Error ? err.message : 'Failed to load phase tools')
    } finally {
      if (mounted.current) setLoading(false)
    }
  }, [taskId])

  // List view owns the tool fetch; diagram does not need it.
  useEffect(() => {
    if (!active || view !== 'list') return
    void load()
  }, [active, refreshKey, load, view])

  useEffect(() => {
    if (!active || !liveRefresh || view !== 'list') return
    const id = window.setInterval(() => {
      void load()
    }, 4000)
    return () => window.clearInterval(id)
  }, [active, liveRefresh, load, view])

  const snapshot = activeRun?.devPhaseGraph ?? lastSnapshot
  const label = activeRun?.devPhase ?? lastLabel
  const forThisTask = !activeRun?.taskId || activeRun.taskId === taskId
  const liveStatus = (forThisTask && snapshot?.statusText?.trim()) || ''

  return (
    <div className="space-y-2" data-testid="dev-phase-graph-panel">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex-1 min-w-[12rem] space-y-0.5">
          <p className="text-[10px] text-cat-subtext" data-testid="phase-graph-blurb">
            {liveStatus ||
              'Explore → Patch → Verify loop. Done = this step’s verify budget, not card Done.'}
          </p>
          <p className="text-[9px] text-cat-overlay">
            Setting: Workflow → Autonomy → Dev phase graph.
          </p>
        </div>
        <div
          className="inline-flex rounded border border-cat-surface1 overflow-hidden shrink-0"
          role="group"
          aria-label="Phase graph view"
        >
          <button
            type="button"
            onClick={() => setViewPersist('list')}
            className={`text-[10px] px-2 py-0.5 font-mono ${
              view === 'list'
                ? 'bg-sky-950/50 text-sky-200'
                : 'text-cat-overlay hover:text-cat-subtext'
            }`}
            data-testid="phase-graph-view-list"
          >
            List
          </button>
          <button
            type="button"
            onClick={() => setViewPersist('diagram')}
            className={`text-[10px] px-2 py-0.5 font-mono border-l border-cat-surface1 ${
              view === 'diagram'
                ? 'bg-sky-950/50 text-sky-200'
                : 'text-cat-overlay hover:text-cat-subtext'
            }`}
            data-testid="phase-graph-view-diagram"
          >
            Diagram
          </button>
        </div>
      </div>

      {view === 'diagram' ? (
        <div className="space-y-1.5">
          {active &&
            (forThisTask && (snapshot || label) ? (
              <DevPhaseDiagram snapshot={snapshot} label={label} />
            ) : (
              <DevPhaseDiagram snapshot={null} label={null} />
            ))}
          <p className="text-[10px] text-cat-overlay">
            Switch to List to jump from tools into Flow rows.
          </p>
        </div>
      ) : (
        <>
          {forThisTask && (snapshot || label) ? (
            <DevPhaseStepper snapshot={snapshot} label={label} />
          ) : (
            <p className="text-[10px] text-cat-subtext">
              No live phase yet — shown during Developer In Progress steps when Dev phase graph is
              on.
            </p>
          )}
          <p className="text-[10px] text-cat-overlay">
            Click a tool to jump to the matching Flow row.
          </p>
          {loading && tools.length === 0 && (
            <p className="text-[10px] text-cat-overlay">Loading tools…</p>
          )}
          {error && <p className="text-[10px] text-rose-300">{error}</p>}
          {tools.length > 0 && (
            <ul className="space-y-1 max-h-56 overflow-y-auto">
              {tools.map((n) => {
                const tag = n.devPhaseTag || null
                const chip = phaseLabel(tag)
                return (
                  <li key={n.id}>
                    <button
                      type="button"
                      className="w-full text-left flex items-center gap-2 text-[10px] font-mono px-2 py-1 rounded border border-cat-surface1/80 hover:border-sky-500/40 hover:bg-sky-950/20"
                      onClick={() => onSelectNode?.(n.id)}
                      title={n.devPhase || chip || n.toolName}
                    >
                      {chip ? (
                        <span className={`shrink-0 px-1 rounded border ${phaseChipClass(tag)}`}>
                          {chip}
                        </span>
                      ) : (
                        <span className="shrink-0 px-1 rounded border border-cat-surface1 text-cat-overlay">
                          —
                        </span>
                      )}
                      <span
                        className={
                          n.success === false
                            ? 'text-rose-300 truncate'
                            : 'text-cat-subtext truncate'
                        }
                      >
                        {n.toolName || '?'}
                      </span>
                      {n.timestamp && (
                        <span className="ml-auto text-cat-overlay shrink-0">{n.timestamp}</span>
                      )}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          {!loading && !error && tools.length === 0 && (
            <p className="text-[10px] text-cat-overlay">No tool events for this card yet.</p>
          )}
        </>
      )}
    </div>
  )
}

export { phaseChipClass, phaseLabel }
