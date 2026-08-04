import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchTaskFlow } from '../api/client'
import type { TaskFlowNode, TaskFlowResponse, TaskFlowWorkItemIndexEntry } from '../types'
import { formatTimeSplit, formatWorkItemCounts } from '../utils/flowCounts'
import { llmCollapsedPreview, llmToolCallCount } from '../utils/flowLlmPreview'

const PAGE_SIZE = 40

interface TaskFlowPanelProps {
  taskId: string
  active: boolean
  refreshKey?: number | string
  liveRefresh?: boolean
}

function FlowNode({
  node,
  workItemIndex,
}: {
  node: TaskFlowNode
  workItemIndex?: Record<string, TaskFlowWorkItemIndexEntry>
}) {
  const [open, setOpen] = useState(false)
  const isLlm = node.kind === 'llm'
  const border = isLlm
    ? 'border-indigo-500/30 bg-indigo-950/20'
    : node.success === false
      ? 'border-rose-500/30 bg-rose-950/20'
      : 'border-amber-500/30 bg-amber-950/15'

  const linkedIds = node.workItemIds ?? []
  const llmPreview = isLlm ? llmCollapsedPreview(node) : ''
  const llmToolCount = isLlm ? llmToolCallCount(node) : 0
  const llmTextOnly = isLlm && Boolean(node.responseContent?.trim())

  return (
    <div
      id={`flow-node-${node.id}`}
      className={`rounded-lg border ${border} px-2.5 py-2`}
      data-testid={`flow-node-${node.kind}`}
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
          {node.error && (
            <span className="text-rose-400" title={node.error}>
              ERR
            </span>
          )}
          {isLlm && llmToolCount > 0 && !llmTextOnly && (
            <span className="text-amber-300/90 border border-amber-500/30 rounded px-1">
              {llmToolCount} tool{llmToolCount === 1 ? '' : 's'}
            </span>
          )}
          {!isLlm && node.success === false && <span className="text-rose-400">failed</span>}
          {node.echoDetected && (
            <span className="text-rose-300/90 border border-rose-500/40 rounded px-1">
              tool echo
            </span>
          )}
          {node.duplicateSkip && (
            <span className="text-amber-300/90 border border-amber-500/40 rounded px-1">
              skipped duplicate
            </span>
          )}
          {isLlm && node.promptUnchangedInject && (
            <span className="text-violet-300/90 border border-violet-500/40 rounded px-1">
              unchanged prompt + progress inject
            </span>
          )}
          {isLlm && node.promptSection && (
            <span className="text-cat-overlay font-mono">{node.promptSection}</span>
          )}
          {node.exitReason && (
            <span className="text-sky-300/90 border border-sky-500/40 rounded px-1 font-mono">
              {node.exitReason}
            </span>
          )}
          {node.source && <span className="text-cat-overlay font-mono">{node.source}</span>}
          <span className="text-cat-overlay ml-auto">{open ? '▾' : '▸'}</span>
        </div>
        {!open && isLlm && (
          <div className="space-y-0.5">
            {node.decisionTrace?.detail && (
              <p className="text-[10px] text-sky-200/90 truncate" title={node.decisionTrace.detail}>
                {node.decisionTrace.detail}
              </p>
            )}
            <p
              className={`text-[10px] truncate ${
                node.error ? 'text-rose-300/90' : llmTextOnly ? 'text-cat-subtext' : 'text-amber-200/80 font-mono'
              }`}
            >
              {llmPreview}
            </p>
          </div>
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
            <span
              key={wid}
              className="text-[9px] px-1.5 py-0.5 rounded border border-cat-surface1 text-cat-subtext"
              title={workItemIndex?.[wid]?.label || wid}
            >
              {workItemIndex?.[wid]?.label || wid}
            </span>
          ))}
        </div>
      )}
      {open && (
        <div className="mt-2 space-y-2 border-t border-cat-surface1/60 pt-2">
          {isLlm && node.decisionTrace && (
            <div>
              <div className="text-[9px] uppercase text-cat-overlay mb-1">Decision trace</div>
              <pre className="text-[10px] text-sky-100/90 whitespace-pre-wrap max-h-32 overflow-y-auto bg-sky-950/20 rounded p-2 border border-sky-500/20">
                {JSON.stringify(node.decisionTrace, null, 2)}
              </pre>
            </div>
          )}
          {isLlm && (node.requestMessages?.length ?? 0) > 0 && (
            <div>
              <div className="text-[9px] uppercase text-cat-overlay mb-1">Prompt to model</div>
              <p className="text-[9px] text-cat-overlay mb-1 leading-relaxed">
                Snapshot at the start of this LLM call. After the prior turn&apos;s tools run, their
                output appears here as{' '}
                <span className="font-mono">[tool]</span> messages (scroll to the bottom). The
                separate Tool node between LLM turns shows the full file output too.
              </p>
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
          {isLlm && node.error && (
            <div>
              <div className="text-[9px] uppercase text-rose-400 mb-1">LLM error</div>
              <pre className="text-[10px] text-rose-200/90 whitespace-pre-wrap max-h-40 overflow-y-auto bg-rose-950/30 rounded p-2 border border-rose-500/20">
                {node.error}
              </pre>
            </div>
          )}
          {isLlm && (
            <div>
              <div className="text-[9px] uppercase text-cat-overlay mb-1">Model response</div>
              <pre className="text-[10px] text-cat-subtext whitespace-pre-wrap max-h-48 overflow-y-auto bg-black/30 rounded p-2">
                {node.responseContent?.trim()
                  ? node.responseContent
                  : llmToolCount > 0
                    ? '(No assistant text — tool call(s) only; see below.)'
                    : '(Empty assistant content.)'}
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
  refreshKey = 0,
  liveRefresh = false,
}: TaskFlowPanelProps) {
  const [meta, setMeta] = useState<Omit<TaskFlowResponse, 'nodes'> | null>(null)
  const [nodes, setNodes] = useState<TaskFlowNode[]>([])
  const [error, setError] = useState<string | null>(null)
  const [initialLoading, setInitialLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMoreOlder, setHasMoreOlder] = useState(false)
  const offsetRef = useRef(0)
  const scrollRef = useRef<HTMLDivElement>(null)
  const nodesRef = useRef(nodes)
  nodesRef.current = nodes

  const applyResponse = useCallback((res: TaskFlowResponse, mode: 'replace' | 'append' | 'refresh') => {
    setMeta({
      taskId: res.taskId,
      traces: res.traces,
      count: res.count,
      totalCount: res.totalCount,
      includeFull: res.includeFull,
      workItemIndex: res.workItemIndex,
      totals: res.totals,
      agentWorkItems: res.agentWorkItems,
      suggestedFocusWorkItemId: res.suggestedFocusWorkItemId,
      offset: res.offset,
      limit: res.limit,
      order: res.order,
      hasMoreOlder: res.hasMoreOlder,
    })
    setHasMoreOlder(Boolean(res.hasMoreOlder))
    if (mode === 'append') {
      setNodes((prev) => {
        const seen = new Set(prev.map((n) => n.id))
        const older = (res.nodes || []).filter((n) => !seen.has(n.id))
        return [...prev, ...older]
      })
    } else if (mode === 'refresh') {
      setNodes((prev) => {
        const fresh = res.nodes || []
        const freshIds = new Set(fresh.map((n) => n.id))
        return [...fresh, ...prev.filter((n) => !freshIds.has(n.id))]
      })
    } else {
      setNodes(res.nodes || [])
    }
  }, [])

  const loadPage = useCallback(
    async (offset: number, mode: 'replace' | 'append' | 'refresh', limit = PAGE_SIZE) => {
      const res = await fetchTaskFlow(taskId, {
        limit,
        offset,
        order: 'desc',
        includeFull: true,
      })
      applyResponse(res, mode)
      if (mode === 'append') {
        offsetRef.current = offset + (res.nodes?.length ?? 0)
      } else if (mode === 'replace') {
        offsetRef.current = res.nodes?.length ?? 0
      }
      setHasMoreOlder(Boolean(res.hasMoreOlder))
      return res
    },
    [applyResponse, taskId],
  )

  useEffect(() => {
    if (!active || !taskId) return
    let cancelled = false
    setInitialLoading(true)
    setError(null)
    offsetRef.current = 0
    void loadPage(0, 'replace')
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setInitialLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [active, taskId])

  useEffect(() => {
    if (!active || !taskId || initialLoading) return
    let cancelled = false
    const limit = Math.max(PAGE_SIZE, nodesRef.current.length)
    void loadPage(0, 'refresh', limit).catch(() => {
      if (!cancelled) {
        /* keep existing nodes on soft refresh failure */
      }
    })
    return () => {
      cancelled = true
    }
  }, [refreshKey, active, taskId, initialLoading, loadPage])

  useEffect(() => {
    if (!active || !liveRefresh || !taskId) return
    const interval = window.setInterval(() => {
      const limit = Math.max(PAGE_SIZE, nodesRef.current.length)
      void loadPage(0, 'refresh', limit)
    }, 3500)
    return () => window.clearInterval(interval)
  }, [active, liveRefresh, taskId, loadPage])

  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el || loadingMore || !hasMoreOlder) return
    const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 48
    if (!nearBottom) return
    setLoadingMore(true)
    void loadPage(offsetRef.current, 'append')
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoadingMore(false))
  }, [hasMoreOlder, loadPage, loadingMore])

  if (!active) return null

  const totalsCounts = formatWorkItemCounts(meta?.totals)
  const totalsTiming = formatTimeSplit(meta?.totals)

  return (
    <div className="space-y-2" data-testid="task-flow-panel">
      <p className="text-[10px] text-cat-overlay leading-relaxed">
        Newest LLM/tool steps at the top — scroll down for older events. Updates while this section
        is open during an active step on this card. Expand a node for full prompt / response /
        tool output.
      </p>
      {initialLoading && nodes.length === 0 && (
        <p className="text-[11px] text-cat-subtext">Loading flow…</p>
      )}
      {error && <p className="text-[11px] text-rose-300">{error}</p>}
      {!initialLoading && !error && meta && (
        <>
          <p className="text-[10px] text-cat-overlay font-mono">
            Showing {nodes.length} node{nodes.length === 1 ? '' : 's'}
            {(meta.totalCount ?? 0) > nodes.length ? ` of ${meta.totalCount}` : ''}
            {totalsCounts ? ` · ${totalsCounts}` : ''}
            {totalsTiming ? ` · ${totalsTiming}` : ''}
            {(meta.traces?.length ?? 0) > 0 ? ` · ${meta.traces!.length} step trace(s)` : ''}
          </p>
          <div
            ref={scrollRef}
            onScroll={onScroll}
            className="relative pl-3 max-h-[min(55vh,28rem)] overflow-y-auto space-y-2 before:absolute before:left-1 before:top-2 before:bottom-2 before:w-px before:bg-cat-surface1"
          >
            {nodes.length === 0 ? (
              <p className="text-[11px] text-cat-overlay italic">No LLM/tool events for this card yet.</p>
            ) : (
              nodes.map((node) => (
                <div key={node.id} className="relative">
                  <span className="absolute -left-3 top-3 h-2 w-2 rounded-full bg-cat-overlay/80" />
                  <FlowNode node={node} workItemIndex={meta.workItemIndex} />
                </div>
              ))
            )}
            {loadingMore && (
              <p className="text-[10px] text-cat-subtext text-center py-2">Loading older events…</p>
            )}
            {!hasMoreOlder && nodes.length > 0 && (meta.totalCount ?? 0) > PAGE_SIZE && (
              <p className="text-[10px] text-cat-overlay text-center py-2">Oldest events shown</p>
            )}
          </div>
        </>
      )}
    </div>
  )
}
