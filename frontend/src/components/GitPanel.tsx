import { useEffect, useState } from 'react'
import { fetchGitStatus } from '../api/client'
import type { GitStatusResponse } from '../types'

export default function GitPanel() {
  const [status, setStatus] = useState<GitStatusResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = () => {
    setLoading(true)
    fetchGitStatus()
      .then((data) => {
        setStatus(data)
        setError(null)
      })
      .catch(() => {
        setError('Git status API unavailable')
        setStatus(null)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    refresh()
  }, [])

  const statusColor = (s: string) => {
    if (s.includes('M') || s.includes('modified')) return 'text-amber-400'
    if (s.includes('A') || s.includes('added')) return 'text-emerald-400'
    if (s.includes('D') || s.includes('deleted')) return 'text-rose-400'
    if (s.includes('?') || s.includes('untracked')) return 'text-cat-subtext'
    return 'text-cat-text'
  }

  return (
    <div className="flex flex-col h-full bg-cat-base overflow-hidden">
      <div className="px-4 py-2 border-b border-cat-surface1 flex items-center justify-between shrink-0">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Git Status
        </h3>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="text-[10px] text-indigo-400 hover:text-indigo-300"
        >
          <i className={`fa-solid fa-rotate ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {loading && (
          <p className="text-xs text-cat-overlay">
            <i className="fa-solid fa-spinner animate-spin mr-1" />
            Loading…
          </p>
        )}
        {error && !loading && (
          <p className="text-xs text-cat-overlay italic">{error}</p>
        )}
        {status && !loading && (
          <>
            {status.branch && (
              <div className="mb-3 text-xs">
                <span className="text-cat-subtext">Branch: </span>
                <span className="font-mono text-indigo-300">{status.branch}</span>
              </div>
            )}
            {status.clean && (
              <p className="text-xs text-emerald-400">
                <i className="fa-solid fa-check mr-1" />
                Working tree clean
              </p>
            )}
            <div className="space-y-1 font-mono text-[11px]">
              {status.entries.map((entry) => (
                <div key={entry.path} className="flex gap-2">
                  <span className={`shrink-0 w-4 ${statusColor(entry.status)}`}>
                    {entry.status.slice(0, 2)}
                  </span>
                  <span className="text-cat-subtext truncate">{entry.path}</span>
                </div>
              ))}
            </div>
            {status.entries.length === 0 && !status.clean && (
              <p className="text-xs text-cat-overlay italic">No changes detected.</p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
