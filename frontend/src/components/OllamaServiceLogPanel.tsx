import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchOllamaServiceLogs } from '../api/client'

interface OllamaServiceLogPanelProps {
  hidden?: boolean
  onOpenModelTab?: () => void
}

export default function OllamaServiceLogPanel({
  hidden = false,
  onOpenModelTab,
}: OllamaServiceLogPanelProps) {
  const [lines, setLines] = useState<string[]>([])
  const [source, setSource] = useState<string>('')
  const [note, setNote] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [follow, setFollow] = useState(true)
  const [lineCount, setLineCount] = useState(50)
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedRef = useRef(true)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchOllamaServiceLogs(lineCount)
      setLines(data.lines ?? [])
      setSource(data.source ?? '')
      setNote(data.note ?? '')
      setError(data.error ?? null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load logs')
    } finally {
      setLoading(false)
    }
  }, [lineCount])

  useEffect(() => {
    if (hidden) return
    if (!follow) {
      void refresh()
    }
  }, [hidden, follow, refresh])

  useEffect(() => {
    if (hidden || !follow) return
    const es = new EventSource(`/api/ollama/service-logs/stream?lines=${lineCount}`)
    es.onmessage = (ev) => {
      const text = ev.data?.trim()
      if (!text) return
      setLines((prev) => {
        const next = [...prev, text]
        if (next.length > 500) return next.slice(-500)
        return next
      })
    }
    es.addEventListener('meta', (ev) => {
      const parts = String(ev.data || '').split('|')
      setSource(parts[0] || '')
      setNote(parts[1] || '')
    })
    es.onerror = () => {
      setError('Stream disconnected — click Refresh')
    }
    return () => es.close()
  }, [hidden, follow, lineCount])

  useEffect(() => {
    if (hidden || follow) return
    const id = window.setInterval(() => void refresh(), 10_000)
    return () => window.clearInterval(id)
  }, [hidden, follow, refresh])

  useEffect(() => {
    if (!pinnedRef.current || !scrollRef.current) return
    scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [lines])

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    pinnedRef.current = atBottom
  }

  if (hidden) return null

  return (
    <div className="flex flex-col h-full min-h-0 bg-cat-base text-xs">
      <div className="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-cat-surface1 bg-cat-surface0/40 flex-wrap">
        <span className="text-[10px] font-bold uppercase tracking-wider text-cat-subtext">
          Ollama Server
        </span>
        {source && (
          <span className="text-[9px] bg-violet-950/50 text-violet-300 px-1.5 py-0.5 rounded font-mono">
            {source}
          </span>
        )}
        {note && <span className="text-[10px] text-cat-overlay truncate max-w-[200px]">{note}</span>}
        <label className="flex items-center gap-1 text-[10px] text-cat-subtext ml-auto">
          <input
            type="checkbox"
            checked={follow}
            onChange={(e) => setFollow(e.target.checked)}
            className="rounded"
          />
          Follow
        </label>
        <label className="flex items-center gap-1 text-[10px] text-cat-subtext">
          Lines
          <input
            type="number"
            min={10}
            max={500}
            value={lineCount}
            onChange={(e) => setLineCount(Math.min(500, Math.max(10, parseInt(e.target.value, 10) || 50)))}
            className="w-12 bg-cat-base border border-cat-surface1 rounded px-1 text-white"
          />
        </label>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="text-[10px] text-indigo-300 hover:text-white px-2 py-0.5 rounded border border-cat-surface1"
        >
          {loading ? '…' : 'Refresh'}
        </button>
        {onOpenModelTab && (
          <button
            type="button"
            onClick={onOpenModelTab}
            className="text-[10px] text-cat-subtext hover:text-indigo-300 underline"
          >
            App LLM logs (Model tab)
          </button>
        )}
      </div>
      {error && (
        <div className="shrink-0 px-3 py-1 text-[10px] text-amber-300 bg-amber-950/30 border-b border-amber-500/20">
          {error}
        </div>
      )}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 min-h-0 overflow-y-auto font-mono text-[11px] p-2 space-y-0.5"
      >
        {lines.length === 0 ? (
          <p className="text-cat-overlay p-4 text-center">
            No server logs yet. Start Ollama, then refresh or enable Follow.
          </p>
        ) : (
          lines.map((line, i) => (
            <div key={`${i}-${line.slice(0, 24)}`} className="text-cat-text whitespace-pre-wrap break-all">
              {line}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
