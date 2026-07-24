import { useEffect, useState } from 'react'
import {
  fetchBoardRecoveryOptions,
  listBoardSnapshots,
  restoreBoardFromRecovery,
  restoreBoardSnapshot,
} from '../api/client'

interface BoardRecoveryPanelProps {
  projectId: string
  onRestored: (state: import('../types').AppState) => void
}

export default function BoardRecoveryPanel({ projectId, onRestored }: BoardRecoveryPanelProps) {
  const [snapshots, setSnapshots] = useState<
    Array<{ id: string; savedAt?: string; taskCount?: number }>
  >([])
  const [candidates, setCandidates] = useState<
    Array<{ kind: string; id: string; label: string; taskCount?: number }>
  >([])
  const [liveCount, setLiveCount] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = () => {
    if (!projectId) return
    void listBoardSnapshots(projectId)
      .then((r) => setSnapshots(r.snapshots ?? []))
      .catch(() => setSnapshots([]))
    void fetchBoardRecoveryOptions(projectId)
      .then((r) => {
        setLiveCount(r.liveTaskCount ?? 0)
        setCandidates(r.candidates ?? [])
      })
      .catch(() => {
        setCandidates([])
        setLiveCount(null)
      })
  }

  useEffect(() => {
    refresh()
  }, [projectId])

  if (!projectId) return null

  return (
    <div className="space-y-2 border-t border-cat-surface1 pt-4">
      <h3 className="text-xs font-bold uppercase tracking-wider text-amber-200">
        Board recovery
      </h3>
      <p className="text-[10px] text-cat-overlay leading-relaxed">
        If cards disappeared, restore from an automatic snapshot or a legacy database copy.
        Skills and model assignments are kept. Live board:{' '}
        <span className="text-cat-subtext font-mono">
          {liveCount == null ? '—' : `${liveCount} card(s)`}
        </span>
      </p>
      {message && <p className="text-[10px] text-emerald-300">{message}</p>}
      {error && <p className="text-[10px] text-rose-300">{error}</p>}
      {snapshots.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] text-cat-subtext font-semibold">Snapshots</p>
          {snapshots.slice(0, 5).map((s) => (
            <button
              key={s.id}
              type="button"
              disabled={busy}
              onClick={() => {
                setBusy(true)
                setError(null)
                setMessage(null)
                void restoreBoardSnapshot(projectId, s.id)
                  .then((st) => {
                    onRestored(st)
                    setMessage(`Restored snapshot ${s.id}`)
                    refresh()
                  })
                  .catch((err: unknown) => {
                    setError(err instanceof Error ? err.message : 'Restore failed')
                  })
                  .finally(() => setBusy(false))
              }}
              className="w-full text-left text-[10px] px-2 py-1.5 rounded border border-amber-500/30 text-amber-100 hover:bg-amber-950/40 disabled:opacity-50"
            >
              {s.savedAt ?? s.id} · {s.taskCount ?? '?'} cards
            </button>
          ))}
        </div>
      )}
      {candidates.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] text-cat-subtext font-semibold">Richer copies found</p>
          {candidates.map((c) => (
            <button
              key={`${c.kind}-${c.id}`}
              type="button"
              disabled={busy}
              onClick={() => {
                setBusy(true)
                setError(null)
                setMessage(null)
                void restoreBoardFromRecovery(projectId, { kind: c.kind, id: c.id })
                  .then((st) => {
                    onRestored(st)
                    setMessage(c.label)
                    refresh()
                  })
                  .catch((err: unknown) => {
                    setError(err instanceof Error ? err.message : 'Recovery failed')
                  })
                  .finally(() => setBusy(false))
              }}
              className="w-full text-left text-[10px] px-2 py-1.5 rounded border border-emerald-500/30 text-emerald-100 hover:bg-emerald-950/40 disabled:opacity-50"
            >
              {c.label}
            </button>
          ))}
        </div>
      )}
      {snapshots.length === 0 && candidates.length === 0 && (
        <p className="text-[10px] text-cat-overlay">
          No richer snapshots or legacy boards found for this project yet.
        </p>
      )}
    </div>
  )
}
