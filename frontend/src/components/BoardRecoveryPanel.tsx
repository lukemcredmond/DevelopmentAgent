import { useEffect, useState } from 'react'
import {
  fetchBoardRecoveryOptions,
  importBoardFromTaskSpecs,
  listBoardSnapshots,
  restoreBoardFromRecovery,
  restoreBoardSnapshot,
} from '../api/client'

interface BoardRecoveryPanelProps {
  projectId: string
  onRestored: (state: import('../types').AppState) => void
}

function formatLanes(counts?: Record<string, number> | null): string {
  if (!counts || !Object.keys(counts).length) return ''
  return Object.entries(counts)
    .filter(([, n]) => n > 0)
    .slice(0, 6)
    .map(([k, n]) => `${k}:${n}`)
    .join(' · ')
}

export default function BoardRecoveryPanel({ projectId, onRestored }: BoardRecoveryPanelProps) {
  const [snapshots, setSnapshots] = useState<
    Array<{ id: string; savedAt?: string; taskCount?: number }>
  >([])
  const [candidates, setCandidates] = useState<
    Array<{
      kind: string
      id: string
      label: string
      taskCount?: number
      laneCounts?: Record<string, number>
    }>
  >([])
  const [orphans, setOrphans] = useState<
    Array<{ kind: string; id: string; label: string; taskCount?: number }>
  >([])
  const [liveCount, setLiveCount] = useState<number | null>(null)
  const [liveLanes, setLiveLanes] = useState<Record<string, number>>({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmOverwrite, setConfirmOverwrite] = useState(false)

  const refresh = () => {
    if (!projectId) return
    void listBoardSnapshots(projectId)
      .then((r) => setSnapshots(r.snapshots ?? []))
      .catch(() => setSnapshots([]))
    void fetchBoardRecoveryOptions(projectId)
      .then((r) => {
        setLiveCount(r.liveTaskCount ?? 0)
        setLiveLanes((r as { liveLaneCounts?: Record<string, number> }).liveLaneCounts ?? {})
        setCandidates(r.candidates ?? [])
        setOrphans(r.orphanProjects ?? [])
      })
      .catch(() => {
        setCandidates([])
        setOrphans([])
        setLiveCount(null)
        setLiveLanes({})
      })
  }

  useEffect(() => {
    refresh()
  }, [projectId])

  if (!projectId) return null

  const guard = (fn: () => void) => {
    if (!confirmOverwrite) {
      setError('Confirm “Overwrite live board” before restoring.')
      return
    }
    fn()
  }

  return (
    <div className="space-y-2 border-t border-cat-surface1 pt-4">
      <h3 className="text-xs font-bold uppercase tracking-wider text-amber-200">
        Board recovery
      </h3>
      <p className="text-[10px] text-cat-overlay leading-relaxed">
        If cards disappeared, restore from an automatic snapshot or a legacy database copy.
        Deleted projects still listed under snapshots can be re-inserted from this panel.
        You can also rebuild cards from <code className="text-cat-subtext">docs/tasks/*-spec.md</code>.
        Skills and model assignments are kept. Live board:{' '}
        <span className="text-cat-subtext font-mono">
          {liveCount == null ? '—' : `${liveCount} card(s)`}
        </span>
        {formatLanes(liveLanes) ? (
          <span className="block text-cat-overlay mt-0.5">{formatLanes(liveLanes)}</span>
        ) : null}
      </p>
      <label
        className="flex items-center gap-2 text-[11px] text-amber-200 cursor-pointer"
        data-testid="board-restore-confirm"
      >
        <input
          type="checkbox"
          checked={confirmOverwrite}
          onChange={(e) => {
            setConfirmOverwrite(e.target.checked)
            setError(null)
          }}
        />
        Overwrite live board (required)
      </label>
      <button
        type="button"
        disabled={busy}
        onClick={() =>
          guard(() => {
            setBusy(true)
            setError(null)
            setMessage(null)
            void importBoardFromTaskSpecs(projectId, true)
              .then((st) => {
                onRestored(st)
                const stats = st.importStats
                setMessage(
                  `Imported ${stats?.importedCount ?? 0} card(s) from docs/tasks` +
                    (stats?.skippedCount ? ` (${stats.skippedCount} skipped)` : ''),
                )
                refresh()
              })
              .catch((err: unknown) => {
                setError(err instanceof Error ? err.message : 'Import failed')
              })
              .finally(() => setBusy(false))
          })
        }
        className="w-full text-left text-[10px] px-2 py-1.5 rounded border border-sky-500/30 text-sky-100 hover:bg-sky-950/40 disabled:opacity-50"
      >
        Rebuild board from docs/tasks
      </button>
      {message && <p className="text-[10px] text-emerald-300">{message}</p>}
      {error && <p className="text-[10px] text-rose-300">{error}</p>}
      {snapshots.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] text-cat-subtext font-semibold">Snapshots</p>
          {snapshots.map((s) => (
            <button
              key={s.id}
              type="button"
              disabled={busy}
              onClick={() =>
                guard(() => {
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
                })
              }
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
              onClick={() =>
                guard(() => {
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
                })
              }
              className="w-full text-left text-[10px] px-2 py-1.5 rounded border border-emerald-500/30 text-emerald-100 hover:bg-emerald-950/40 disabled:opacity-50"
            >
              {c.label}
              {formatLanes(c.laneCounts) ? (
                <span className="block text-cat-overlay">{formatLanes(c.laneCounts)}</span>
              ) : null}
            </button>
          ))}
        </div>
      )}
      {orphans.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] text-cat-subtext font-semibold">Deleted projects (snapshots)</p>
          {orphans.map((o) => (
            <button
              key={`orphan-${o.id}`}
              type="button"
              disabled={busy}
              onClick={() => {
                setBusy(true)
                setError(null)
                setMessage(null)
                void restoreBoardFromRecovery(projectId, { kind: 'orphan_snapshot', id: o.id })
                  .then((st) => {
                    onRestored(st)
                    setMessage(o.label)
                    refresh()
                  })
                  .catch((err: unknown) => {
                    setError(err instanceof Error ? err.message : 'Recovery failed')
                  })
                  .finally(() => setBusy(false))
              }}
              className="w-full text-left text-[10px] px-2 py-1.5 rounded border border-violet-500/30 text-violet-100 hover:bg-violet-950/40 disabled:opacity-50"
            >
              {o.label}
            </button>
          ))}
        </div>
      )}
      {snapshots.length === 0 && candidates.length === 0 && orphans.length === 0 && (
        <p className="text-[10px] text-cat-overlay">
          No richer snapshots or legacy boards found for this project yet.
        </p>
      )}
    </div>
  )
}
