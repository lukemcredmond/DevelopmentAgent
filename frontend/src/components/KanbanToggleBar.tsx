import { useMemo } from 'react'
import type { Board, BoardLane, WorkflowSettings } from '../types'
import { getDisplayLanes } from '../types'

const KANBAN_STORAGE_KEY = 'allhands-kanban-open'

interface KanbanToggleBarProps {
  board: Board
  projectName: string
  open: boolean
  onToggle: () => void
  activeLanes?: BoardLane[]
  workflowSettings?: WorkflowSettings
}

export function readKanbanOpen(): boolean {
  try {
    const stored = localStorage.getItem(KANBAN_STORAGE_KEY)
    if (stored === 'false') return false
    if (stored === 'true') return true
  } catch {
    /* ignore */
  }
  return true
}

export function writeKanbanOpen(open: boolean): void {
  try {
    localStorage.setItem(KANBAN_STORAGE_KEY, String(open))
  } catch {
    /* ignore */
  }
}

export default function KanbanToggleBar({
  board,
  projectName,
  open,
  onToggle,
  activeLanes,
  workflowSettings,
}: KanbanToggleBarProps) {
  const lanes = useMemo(
    () => getDisplayLanes(activeLanes, workflowSettings),
    [activeLanes, workflowSettings],
  )

  const summaries = useMemo(
    () =>
      lanes
        .map((lane) => {
          const count = (board[lane] ?? []).length
          if (count === 0) return null
          return { lane, count }
        })
        .filter(Boolean) as { lane: string; count: number }[],
    [board, lanes],
  )

  return (
    <div className="shrink-0 flex items-center gap-3 px-4 py-1.5 bg-cat-mantle border-b border-cat-surface1">
      <button
        type="button"
        onClick={onToggle}
        className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-cat-subtext hover:text-white transition-colors shrink-0"
        aria-expanded={open}
      >
        <i className={`fa-solid fa-chevron-${open ? 'down' : 'right'} text-[10px] text-indigo-400`} />
        <i className="fa-solid fa-table-columns text-indigo-500" />
        {open ? 'Hide Board' : 'Show Board'}
      </button>
      <span className="text-[10px] text-cat-overlay truncate hidden sm:inline">{projectName}</span>
      {!open && summaries.length > 0 && (
        <div className="flex flex-wrap gap-1.5 min-w-0 flex-1">
          {summaries.map(({ lane, count }) => (
            <span
              key={lane}
              className="text-[10px] font-mono bg-cat-base border border-cat-surface1 text-cat-subtext px-2 py-0.5 rounded"
            >
              {lane} {count}
            </span>
          ))}
        </div>
      )}
      {!open && summaries.length === 0 && (
        <span className="text-[10px] text-cat-overlay italic">Board empty</span>
      )}
    </div>
  )
}
