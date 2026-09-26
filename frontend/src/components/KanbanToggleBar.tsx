import { useMemo } from 'react'
import type { Board, BoardLane, WorkflowSettings } from '../types'
import { getDisplayLanes } from '../types'

const KANBAN_STORAGE_KEY = 'allhands-kanban-open'
export const BOARD_VIEW_STORAGE_KEY = 'allhands-board-view-mode'

export type BoardViewMode = 'kanban' | 'tree'

interface KanbanToggleBarProps {
  board: Board
  projectName: string
  open: boolean
  onToggle: () => void
  boardViewMode?: BoardViewMode
  onBoardViewModeChange?: (mode: BoardViewMode) => void
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

export function readBoardViewMode(): BoardViewMode {
  try {
    const stored = localStorage.getItem(BOARD_VIEW_STORAGE_KEY)
    if (stored === 'tree') return 'tree'
  } catch {
    /* ignore */
  }
  return 'kanban'
}

export function writeBoardViewMode(mode: BoardViewMode): void {
  try {
    localStorage.setItem(BOARD_VIEW_STORAGE_KEY, mode)
  } catch {
    /* ignore */
  }
}

export default function KanbanToggleBar({
  board,
  projectName,
  open,
  onToggle,
  boardViewMode = 'kanban',
  onBoardViewModeChange,
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
      {open && onBoardViewModeChange && (
        <div className="flex items-center gap-1 ml-2 shrink-0">
          <span className="text-[9px] uppercase text-cat-overlay">View</span>
          {(['kanban', 'tree'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => onBoardViewModeChange(mode)}
              className={`text-[10px] px-2 py-0.5 rounded capitalize ${
                boardViewMode === mode
                  ? 'bg-indigo-600/40 text-indigo-100 font-semibold'
                  : 'text-cat-subtext hover:text-white'
              }`}
            >
              {mode}
            </button>
          ))}
        </div>
      )}
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
