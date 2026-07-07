import { useCallback, useEffect, useMemo, useState } from 'react'
import type { LlmDebugEntry, ModelTimelineItem, ModelTimelineThread } from '../types'
import { clearLlmLogs, fetchLlmLogs, fetchModelTimeline } from '../api/client'

interface ModelDebugPanelProps {
  taskIdFilter?: string | null
}

type ViewMode = 'list' | 'conversation'

export default function ModelDebugPanel({ taskIdFilter }: ModelDebugPanelProps) {
  const [entries, setEntries] = useState<LlmDebugEntry[]>([])
  const [threads, setThreads] = useState<ModelTimelineThread[]>([])
  const [agentFilter, setAgentFilter] = useState('')
  const [viewMode, setViewMode] = useState<ViewMode>('list')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [expandedThread, setExpandedThread] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [clearing, setClearing] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      if (viewMode === 'conversation') {
        const data = await fetchModelTimeline({
          taskId: taskIdFilter || undefined,
          limit: 150,
        })
        setThreads(data.threads ?? [])
        setEntries([])
      } else {
        const data = await fetchLlmLogs({
          limit: 200,
          agent: agentFilter || undefined,
          taskId: taskIdFilter || undefined,
        })
        setEntries(data.entries ?? [])
        setThreads([])
      }
    } catch {
      setEntries([])
      setThreads([])
    } finally {
      setLoading(false)
    }
  }, [agentFilter, taskIdFilter, viewMode])

  useEffect(() => {
    void refresh()
    let id = window.setInterval(() => void refresh(), document.visibilityState === 'hidden' ? 15000 : 8000)
    const onVisibility = () => {
      window.clearInterval(id)
      id = window.setInterval(() => void refresh(), document.visibilityState === 'hidden' ? 15000 : 8000)
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [refresh])

  const displayEntries = useMemo(
    () => [...entries].reverse(),
    [entries],
  )

  const displayThreads = useMemo(
    () => [...threads],
    [threads],
  )

  const handleClear = () => {
    setClearing(true)
    void clearLlmLogs()
      .then(() => {
        setEntries([])
        setThreads([])
      })
      .finally(() => setClearing(false))
  }

  const renderTimelineItem = (item: ModelTimelineItem, key: string) => {
    if (item.kind === 'tool') {
      return (
        <div
          key={key}
          className="ml-6 border-l-2 border-amber-500/40 pl-3 py-1.5 space-y-1"
        >
          <div className="text-[10px] text-amber-300 font-semibold">
            Tool: {item.toolName}
            {item.status === 'awaiting_approval' && (
              <span className="ml-2 text-rose-300">awaiting approval</span>
            )}
          </div>
          <div className="text-[9px] text-cat-overlay">
            {item.agent} · {item.timestamp}
            {item.durationMs != null ? ` · ${item.durationMs}ms` : ''}
          </div>
          <pre className="text-[10px] text-cat-subtext whitespace-pre-wrap max-h-24 overflow-y-auto bg-black/20 rounded p-2">
            {item.toolOutput || '(no output)'}
          </pre>
        </div>
      )
    }

    const toolCalls = (item.toolCalls ?? []) as Array<{ name?: string; arguments?: unknown }>
    return (
      <div key={key} className="space-y-1">
        <div className="rounded-lg border border-cat-surface1 bg-cat-mantle/40 px-3 py-2">
          <div className="flex flex-wrap gap-2 text-[10px] mb-1">
            <span className="text-indigo-300 font-semibold">{item.agent}</span>
            <span className="text-cat-overlay">{item.timestamp}</span>
            <span className="text-cat-subtext">iter {item.iteration}</span>
            {item.durationMs != null && (
              <span className="text-cat-subtext">{item.durationMs}ms</span>
            )}
            {item.error && <span className="text-rose-400">ERR</span>}
          </div>
          {item.content && (
            <pre className="text-[10px] text-cat-subtext whitespace-pre-wrap max-h-32 overflow-y-auto">
              {item.content}
            </pre>
          )}
          {toolCalls.length > 0 && (
            <div className="mt-2 space-y-1">
              <div className="text-[9px] uppercase text-cat-overlay">Tool calls</div>
              {toolCalls.map((tc, i) => (
                <div
                  key={`${key}-tc-${i}`}
                  className="text-[10px] font-mono text-amber-200/90 bg-black/20 rounded p-1.5"
                >
                  {tc.name}
                  {tc.arguments != null && (
                    <pre className="text-cat-overlay mt-1 whitespace-pre-wrap">
                      {typeof tc.arguments === 'string'
                        ? tc.arguments
                        : JSON.stringify(tc.arguments, null, 2)}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full min-h-0 bg-cat-base text-[11px]">
      <div className="shrink-0 border-b border-cat-surface1 px-4 py-2 flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase text-cat-overlay tracking-wide">Model debug</span>
        <div className="flex rounded border border-cat-surface1 overflow-hidden text-[10px]">
          <button
            type="button"
            onClick={() => setViewMode('list')}
            className={`px-2 py-0.5 ${viewMode === 'list' ? 'bg-indigo-600/40 text-indigo-200' : 'text-cat-overlay'}`}
          >
            List
          </button>
          <button
            type="button"
            onClick={() => setViewMode('conversation')}
            className={`px-2 py-0.5 ${viewMode === 'conversation' ? 'bg-indigo-600/40 text-indigo-200' : 'text-cat-overlay'}`}
          >
            Conversation
          </button>
        </div>
        {viewMode === 'list' && (
          <>
            <span className="text-[10px] text-cat-overlay">Newest first</span>
            <select
              value={agentFilter}
              onChange={(e) => setAgentFilter(e.target.value)}
              className="bg-cat-mantle border border-cat-surface1 rounded px-2 py-0.5 text-[10px]"
            >
              <option value="">All agents</option>
              <option value="Product Owner">PO</option>
              <option value="Developer">Dev</option>
              <option value="Code Reviewer">CR</option>
              <option value="QA Tester">QA</option>
            </select>
          </>
        )}
        {taskIdFilter && (
          <span className="text-[10px] text-indigo-300 font-mono">task: {taskIdFilter}</span>
        )}
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="text-[10px] text-cat-overlay hover:text-white disabled:opacity-50"
        >
          {loading ? 'Loading…' : 'Refresh'}
        </button>
        {(entries.length > 0 || threads.length > 0) && (
          <button
            type="button"
            onClick={handleClear}
            disabled={clearing}
            className="text-[10px] text-cat-overlay hover:text-white ml-auto disabled:opacity-50"
          >
            {clearing ? 'Clearing…' : 'Clear'}
          </button>
        )}
      </div>

      {viewMode === 'list' ? (
        <div className="flex-1 min-h-0 overflow-y-auto p-2 font-mono">
          {displayEntries.length === 0 ? (
            <p className="text-cat-overlay p-4 text-center">
              No LLM calls logged yet. Run a sprint step or chat to see Ollama requests here.
            </p>
          ) : (
            <div className="space-y-1">
              {displayEntries.map((e) => (
                <div
                  key={e.id}
                  className="border border-cat-surface1 rounded bg-cat-mantle/40 overflow-hidden"
                >
                  <button
                    type="button"
                    onClick={() => setExpandedId(expandedId === e.id ? null : e.id)}
                    className="w-full text-left px-3 py-2 hover:bg-cat-surface0/50 flex items-center gap-2 flex-wrap"
                  >
                    <span className="text-indigo-300">{e.agent}</span>
                    <span className="text-cat-overlay">{e.timestamp}</span>
                    <span className="text-cat-subtext">iter {e.iteration}</span>
                    <span className="text-cat-subtext">{e.durationMs}ms</span>
                    {e.error && <span className="text-rose-400">ERR</span>}
                    {e.toolNames?.length > 0 && (
                      <span className="text-amber-300/80">tools: {e.toolNames.join(', ')}</span>
                    )}
                  </button>
                  {expandedId === e.id && (
                    <div className="px-3 pb-3 space-y-2 border-t border-cat-surface1">
                      {e.responseContent && (
                        <div>
                          <div className="text-[9px] uppercase text-cat-overlay mb-1">Response</div>
                          <pre className="whitespace-pre-wrap text-cat-subtext max-h-32 overflow-y-auto">
                            {e.responseContent}
                          </pre>
                        </div>
                      )}
                      <div>
                        <div className="text-[9px] uppercase text-cat-overlay mb-1 flex justify-between">
                          <span>Request messages</span>
                          <button
                            type="button"
                            className="text-indigo-400 hover:text-indigo-300"
                            onClick={() =>
                              void navigator.clipboard.writeText(JSON.stringify(e.requestMessages, null, 2))
                            }
                          >
                            Copy
                          </button>
                        </div>
                        <pre className="whitespace-pre-wrap text-cat-subtext max-h-48 overflow-y-auto text-[10px]">
                          {JSON.stringify(e.requestMessages, null, 2)}
                        </pre>
                      </div>
                      {e.error && (
                        <pre className="text-rose-300 whitespace-pre-wrap">{e.error}</pre>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
          {displayThreads.length === 0 ? (
            <p className="text-cat-overlay text-center py-8">
              No timeline data yet. Run a sprint step to see LLM ↔ tool chains here.
            </p>
          ) : (
            displayThreads.map((thread) => {
              const tid = thread.taskId
              const open = expandedThread === tid || (taskIdFilter != null && taskIdFilter === tid)
              return (
                <div key={tid} className="border border-cat-surface1 rounded-lg overflow-hidden">
                  <button
                    type="button"
                    onClick={() => setExpandedThread(open && !taskIdFilter ? null : tid)}
                    className="w-full text-left px-3 py-2 bg-cat-mantle/30 hover:bg-cat-surface0/30 flex justify-between"
                  >
                    <span className="text-indigo-300 font-mono text-[10px]">
                      Task {tid === 'no-task' ? '(none)' : tid}
                    </span>
                    <span className="text-cat-overlay text-[10px]">{thread.items.length} events</span>
                  </button>
                  {open && (
                    <div className="p-3 space-y-2 border-t border-cat-surface1">
                      {thread.items.map((item, idx) =>
                        renderTimelineItem(item, `${tid}-${item.kind}-${item.id ?? idx}`),
                      )}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
