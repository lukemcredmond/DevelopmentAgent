import { useState } from 'react'
import { searchFiles } from '../api/client'
import type { FileSearchResult } from '../types'

interface SearchPanelProps {
  onOpenFile: (path: string) => void
}

export default function SearchPanel({ onOpenFile }: SearchPanelProps) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<FileSearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)

  const handleSearch = async () => {
    const q = query.trim()
    if (!q) return
    setLoading(true)
    setSearched(true)
    try {
      const data = await searchFiles(q)
      setResults(data)
    } catch {
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-full bg-cat-base overflow-hidden">
      <div className="px-4 py-2 border-b border-cat-surface1 shrink-0">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext mb-2">
          Workspace Search
        </h3>
        <div className="flex gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void handleSearch()}
            placeholder="Search files…"
            className="flex-1 bg-cat-surface0 border border-cat-surface1 rounded px-3 py-1.5 text-xs text-white focus:outline-none focus:border-indigo-500 font-mono"
          />
          <button
            type="button"
            onClick={() => void handleSearch()}
            disabled={loading}
            className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white px-3 py-1.5 rounded text-xs"
          >
            {loading ? (
              <i className="fa-solid fa-spinner animate-spin" />
            ) : (
              <i className="fa-solid fa-magnifying-glass" />
            )}
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {searched && results.length === 0 && !loading && (
          <p className="text-xs text-cat-overlay italic">No results found.</p>
        )}
        {results.map((r, i) => (
          <button
            key={`${r.path}-${r.line}-${i}`}
            type="button"
            onClick={() => onOpenFile(r.path)}
            className="w-full text-left bg-cat-surface0 border border-cat-surface1 rounded-lg p-2.5 hover:border-indigo-500/50 transition-colors"
          >
            <div className="text-[11px] font-mono text-indigo-300">
              {r.path}:{r.line}
            </div>
            <div className="text-[11px] text-cat-subtext mt-1 line-clamp-2">{r.preview}</div>
          </button>
        ))}
      </div>
    </div>
  )
}
