import { useCallback, useEffect, useState } from 'react'
import type { LlmDebugEntry } from '../types'
import { clearLlmLogs, fetchLlmLogs } from '../api/client'

interface ModelDebugPanelProps {
  taskIdFilter?: string | null
}

export default function ModelDebugPanel({ taskIdFilter }: ModelDebugPanelProps) {
  const [entries, setEntries] = useState<LlmDebugEntry[]>([])
  const [agentFilter, setAgentFilter] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [clearing, setClearing] = useState(false)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchLlmLogs({
        limit: 200,
        agent: agentFilter || undefined,
        taskId: taskIdFilter || undefined,
      })
      setEntries(data.entries ?? [])
    } catch {
      setEntries([])
    } finally {
      setLoading(false)
    }
  }, [agentFilter, taskIdFilter])

  useEffect(() => {
    void refresh()
    const id = window.setInterval(() => void refresh(), 8000)
    return () => window.clearInterval(id)
  }, [refresh])

  const handleClear = () => {
    setClearing(true)
    void clearLlmLogs()
      .then(() => setEntries([]))
      .finally(() => setClearing(false))
  }

  return (
    <div className="flex flex-col h-full min-h-0 bg-cat-base text-[11px]">
      <div className="shrink-0 border-b border-cat-surface1 px-4 py-2 flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase text-cat-overlay tracking-wide">Model debug</span>
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
        {entries.length > 0 && (
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
      <div className="flex-1 overflow-y-auto p-2 space-y-1 font-mono">
        {entries.length === 0 && (
          <p className="text-cat-overlay p-4 text-center">
            No LLM calls logged yet. Run a sprint step or chat to see Ollama requests here.
          </p>
        )}
        {entries.map((e) => (
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
    </div>
  )
}
