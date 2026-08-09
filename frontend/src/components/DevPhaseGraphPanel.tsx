import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchTaskFlow } from '../api/client'
import type { AgentRunState, DevPhaseGraphSnapshot, TaskFlowNode } from '../types'
import DevPhaseStepper from './DevPhaseStepper'

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

  useEffect(() => {
    if (!active) return
    void load()
  }, [active, refreshKey, load])

  useEffect(() => {
    if (!active || !liveRefresh) return
    const id = window.setInterval(() => {
      void load()
    }, 4000)
    return () => window.clearInterval(id)
  }, [active, liveRefresh, load])

  const snapshot = activeRun?.devPhaseGraph ?? lastSnapshot
  const label = activeRun?.devPhase ?? lastLabel
  const forThisTask = !activeRun?.taskId || activeRun.taskId === taskId

  return (
    <div className="space-y-2" data-testid="dev-phase-graph-panel">
      <p className="text-[10px] text-cat-overlay">
        Explore → Patch → Verify loop. Click a tool to jump to the matching Flow row. Setting:
        Workflow → Autonomy → Dev phase graph.
      </p>
      {forThisTask && (snapshot || label) ? (
        <DevPhaseStepper snapshot={snapshot} label={label} />
      ) : (
        <p className="text-[10px] text-cat-subtext">
          No live phase yet — shown during Developer In Progress steps when Dev phase graph is on.
        </p>
      )}
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
                      n.success === false ? 'text-rose-300 truncate' : 'text-cat-subtext truncate'
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
    </div>
  )
}

export { phaseChipClass, phaseLabel }
