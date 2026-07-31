import { useEffect, useRef, useState } from 'react'
import { fetchTaskFlow } from '../api/client'
import type { TaskFlowNode, TaskFlowResponse, TaskFlowWorkItemIndexEntry } from '../types'

interface TaskFlowPanelProps {
  taskId: string
  active: boolean
  highlightWorkItemId?: string | null
  onHighlightWorkItem?: (workItemId: string | null) => void
}

function FlowNode({
  node,
  highlighted,
  workItemIndex,
  onSelectWorkItem,
}: {
  node: TaskFlowNode
  highlighted: boolean
  workItemIndex?: Record<string, TaskFlowWorkItemIndexEntry>
  onSelectWorkItem?: (workItemId: string) => void
}) {
  const [open, setOpen] = useState(false)
  const isLlm = node.kind === 'llm'
  const border = highlighted
    ? 'border-sky-400 ring-2 ring-sky-400/50 bg-sky-950/30'
    : isLlm
      ? 'border-indigo-500/30 bg-indigo-950/20'
      : node.success === false
        ? 'border-rose-500/30 bg-rose-950/20'
        : 'border-amber-500/30 bg-amber-950/15'

  const linkedIds = node.workItemIds ?? []

  return (
    <div
      id={`flow-node-${node.id}`}
      className={`rounded-lg border ${border} px-2.5 py-2`}
      data-testid={`flow-node-${node.kind}`}
      data-highlighted={highlighted ? '1' : '0'}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left space-y-0.5"
      >
        <div className="flex flex-wrap gap-2 text-[10px]">
          <span className={isLlm ? 'text-indigo-300 font-semibold' : 'text-amber-300 font-semibold'}>
            {isLlm ? 'LLM' : `Tool: ${node.toolName || '?'}`}
          </span>
          {node.agent && <span className="text-cat-subtext">{node.agent}</span>}
          {node.timestamp && <span className="text-cat-overlay">{node.timestamp}</span>}
          {node.iteration != null && <span className="text-cat-overlay">iter {node.iteration}</span>}
          {node.durationMs != null && <span className="text-cat-overlay">{node.durationMs}ms</span>}
          {node.error && <span className="text-rose-400">ERR</span>}
          {!isLlm && node.success === false && <span className="text-rose-400">failed</span>}
          {node.source && <span className="text-cat-overlay font-mono">{node.source}</span>}
          <span className="text-cat-overlay ml-auto">{open ? '▾' : '▸'}</span>
        </div>
        {!open && isLlm && node.responseContent && (
          <p className="text-[10px] text-cat-subtext truncate">{node.responseContent}</p>
        )}
        {!open && !isLlm && (
          <p className="text-[10px] text-cat-subtext font-mono truncate">
            {JSON.stringify(node.toolArgs || {}).slice(0, 120)}
          </p>
        )}
      </button>
      {linkedIds.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {linkedIds.map((wid) => (
            <button
              key={wid}
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                onSelectWorkItem?.(wid)
              }}
              className={`text-[9px] px-1.5 py-0.5 rounded border ${
                highlighted && linkedIds.includes(wid)
                  ? 'border-sky-400/60 text-sky-200 bg-sky-950/40'
                  : 'border-cat-surface1 text-cat-subtext hover:border-sky-500/40 hover:text-sky-200'
              }`}
              title={`Highlight agent progress: ${workItemIndex?.[wid]?.label || wid}`}
            >
              {workItemIndex?.[wid]?.label || wid}
            </button>
          ))}
        </div>
      )}
      {open && (
        <div className="mt-2 space-y-2 border-t border-cat-surface1/60 pt-2">
          {isLlm && (node.requestMessages?.length ?? 0) > 0 && (
            <div>
              <div className="text-[9px] uppercase text-cat-overlay mb-1">Prompt to model</div>
              <pre className="text-[10px] text-cat-subtext whitespace-pre-wrap max-h-64 overflow-y-auto bg-black/30 rounded p-2">
                {(node.requestMessages || [])
                  .map((m) =>
                    typeof m === 'string'
                      ? m
                      : `[${m.role || '?'}]\n${m.content || ''}`,
                  )
                  .join('\n\n---\n\n')}
              </pre>
            </div>
          )}
          {isLlm && node.responseContent && (
            <div>
              <div className="text-[9px] uppercase text-cat-overlay mb-1">Model response</div>
              <pre className="text-[10px] text-cat-subtext whitespace-pre-wrap max-h-48 overflow-y-auto bg-black/30 rounded p-2">
                {node.responseContent}
              </pre>
            </div>
          )}
          {isLlm && (node.toolCalls?.length ?? 0) > 0 && (
            <div>
              <div className="text-[9px] uppercase text-cat-overlay mb-1">Tool calls</div>
              <pre className="text-[10px] font-mono text-amber-200/90 whitespace-pre-wrap bg-black/30 rounded p-2">
                {JSON.stringify(node.toolCalls, null, 2)}
              </pre>
            </div>
          )}
          {!isLlm && (
            <>
              <div>
                <div className="text-[9px] uppercase text-cat-overlay mb-1">Args</div>
                <pre className="text-[10px] font-mono text-cat-subtext whitespace-pre-wrap max-h-40 overflow-y-auto bg-black/30 rounded p-2">
                  {JSON.stringify(node.toolArgs || {}, null, 2)}
                </pre>
              </div>
              {node.toolOutput && (
                <div>
                  <div className="text-[9px] uppercase text-cat-overlay mb-1">Output</div>
                  <pre className="text-[10px] text-cat-subtext whitespace-pre-wrap max-h-64 overflow-y-auto bg-black/30 rounded p-2">
                    {node.toolOutput}
                  </pre>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default function TaskFlowPanel({
  taskId,
  active,
  highlightWorkItemId = null,
  onHighlightWorkItem,
}: TaskFlowPanelProps) {
  const [data, setData] = useState<TaskFlowResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const scrolledFor = useRef<string | null>(null)

  useEffect(() => {
    if (!active || !taskId) return
    let cancelled = false
    setLoading(true)
    setError(null)
    void fetchTaskFlow(taskId, { limit: 80, includeFull: true })
      .then((res) => {
        if (!cancelled) setData(res)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [active, taskId])

  useEffect(() => {
    if (!highlightWorkItemId || !data) return
    const entry = data.workItemIndex?.[highlightWorkItemId]
    const firstId = entry?.nodeIds?.[0]
    if (!firstId) return
    if (scrolledFor.current === `${highlightWorkItemId}:${firstId}`) return
    scrolledFor.current = `${highlightWorkItemId}:${firstId}`
    window.requestAnimationFrame(() => {
      document.getElementById(`flow-node-${firstId}`)?.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      })
    })
  }, [highlightWorkItemId, data])

  if (!active) return null

  const matchedCount =
    highlightWorkItemId && data?.workItemIndex?.[highlightWorkItemId]?.nodeIds
      ? data.workItemIndex[highlightWorkItemId].nodeIds!.length
      : 0

  return (
    <div className="space-y-2" data-testid="task-flow-panel">
      <p className="text-[10px] text-cat-overlay leading-relaxed">
        Loaded on demand from persisted LLM/tool logs and step diagnostics — not kept in board memory.
        Expand a node for full prompt / response / tool output. Click an agent-progress chip to filter.
      </p>
      {highlightWorkItemId && (
        <div className="flex flex-wrap items-center gap-2 text-[10px]">
          <span className="text-sky-200">
            Filtered: {data?.workItemIndex?.[highlightWorkItemId]?.label || highlightWorkItemId}
            {matchedCount > 0 ? ` (${matchedCount} node${matchedCount === 1 ? '' : 's'})` : ' (no Flow tools yet)'}
          </span>
          <button
            type="button"
            onClick={() => onHighlightWorkItem?.(null)}
            className="px-1.5 py-0.5 rounded border border-cat-surface1 text-cat-subtext hover:text-white"
          >
            Clear filter
          </button>
        </div>
      )}
      {loading && <p className="text-[11px] text-cat-subtext">Loading flow…</p>}
      {error && <p className="text-[11px] text-rose-300">{error}</p>}
      {!loading && !error && data && (
        <>
          <p className="text-[10px] text-cat-overlay font-mono">
            {data.count ?? data.nodes.length} nodes
            {(data.traces?.length ?? 0) > 0 ? ` · ${data.traces!.length} step trace(s)` : ''}
          </p>
          <div className="relative pl-3 space-y-2 before:absolute before:left-1 before:top-2 before:bottom-2 before:w-px before:bg-cat-surface1">
            {data.nodes.length === 0 ? (
              <p className="text-[11px] text-cat-overlay italic">No LLM/tool events for this card yet.</p>
            ) : (
              data.nodes.map((node) => {
                const highlighted = Boolean(
                  highlightWorkItemId && (node.workItemIds || []).includes(highlightWorkItemId),
                )
                if (highlightWorkItemId && !highlighted) {
                  // Dim non-matching but keep visible for context
                }
                return (
                  <div
                    key={node.id}
                    className={`relative ${highlightWorkItemId && !highlighted ? 'opacity-35' : ''}`}
                  >
                    <span
                      className={`absolute -left-3 top-3 h-2 w-2 rounded-full ${
                        highlighted ? 'bg-sky-400' : 'bg-cat-overlay/80'
                      }`}
                    />
                    <FlowNode
                      node={node}
                      highlighted={highlighted}
                      workItemIndex={data.workItemIndex}
                      onSelectWorkItem={(wid) => onHighlightWorkItem?.(wid)}
                    />
                  </div>
                )
              })
            )}
          </div>
        </>
      )}
    </div>
  )
}
