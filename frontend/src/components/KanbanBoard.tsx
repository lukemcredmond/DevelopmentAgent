import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCorners,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import { memo, useCallback, useEffect, useMemo, useState } from 'react'
import type { Board, BoardLane, Task, WorkflowSettings } from '../types'
import type { TaskRunInfo } from '../utils/taskRunInfo'
import { getDisplayLanes } from '../types'
import { deriveTaskFiles, formatTaskText } from '../utils/taskFormat'
import KanbanColumn from './KanbanColumn'

const BOARD_FILTER_KEY = 'allhands-board-filter'

function readBoardFilter(): string {
  try {
    return sessionStorage.getItem(BOARD_FILTER_KEY) ?? ''
  } catch {
    return ''
  }
}

function writeBoardFilter(q: string): void {
  try {
    if (q) sessionStorage.setItem(BOARD_FILTER_KEY, q)
    else sessionStorage.removeItem(BOARD_FILTER_KEY)
  } catch {
    /* ignore */
  }
}

function taskMatchesFilter(task: Task, query: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  const id = String(task.id ?? '').toLowerCase()
  const title = formatTaskText(task.title).toLowerCase()
  const desc = formatTaskText(task.description).split('\n')[0]?.toLowerCase() ?? ''
  return id.includes(q) || title.includes(q) || desc.includes(q)
}

interface KanbanBoardProps {
  board: Board
  projectName: string
  workspaceDir: string
  activeLanes?: BoardLane[]
  workflowSettings?: WorkflowSettings
  sprintRunning?: boolean
  activeRunInfo?: TaskRunInfo | null
  onTaskClick: (task: Task) => void
  onMoveTask: (taskId: string, fromLane: BoardLane, toLane: BoardLane) => void
  onReorderBacklog?: (taskIds: string[]) => void
  onReorderLane?: (lane: BoardLane, taskIds: string[]) => void
}

function getTaskFileCount(task: Task): number {
  return deriveTaskFiles(task).length
}

function getTaskDecisionCount(task: Task): number {
  return (task.decisions ?? []).length
}

export default memo(function KanbanBoard({
  board,
  projectName,
  workspaceDir,
  activeLanes,
  workflowSettings,
  sprintRunning = false,
  activeRunInfo = null,
  onTaskClick,
  onMoveTask,
  onReorderBacklog,
  onReorderLane,
}: KanbanBoardProps) {
  const [activeTask, setActiveTask] = useState<Task | null>(null)
  const [filterQuery, setFilterQuery] = useState(readBoardFilter)
  const lanes = useMemo(
    () => getDisplayLanes(activeLanes, workflowSettings),
    [activeLanes, workflowSettings],
  )

  useEffect(() => {
    writeBoardFilter(filterQuery)
  }, [filterQuery])

  const filteredBoard = useMemo(() => {
    const q = filterQuery.trim()
    if (!q) return board
    const next: Board = { ...board }
    for (const lane of lanes) {
      next[lane] = (board[lane] ?? []).filter((t) => taskMatchesFilter(t, q))
    }
    return next
  }, [board, filterQuery, lanes])

  const matchCount = useMemo(() => {
    if (!filterQuery.trim()) return null
    return lanes.reduce((sum, lane) => sum + (filteredBoard[lane]?.length ?? 0), 0)
  }, [filterQuery, filteredBoard, lanes])

  const clearFilter = useCallback(() => setFilterQuery(''), [])

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
  )

  const findLane = (taskId: string): BoardLane | null => {
    for (const lane of lanes) {
      if ((board[lane] ?? []).some((t) => t.id === taskId)) return lane
    }
    return null
  }

  const handleDragStart = (event: DragStartEvent) => {
    if (sprintRunning) return
    const taskId = String(event.active.id)
    for (const lane of lanes) {
      const task = (board[lane] ?? []).find((t) => t.id === taskId)
      if (task) {
        setActiveTask(task)
        break
      }
    }
  }

  const handleDragEnd = (event: DragEndEvent) => {
    if (sprintRunning) {
      setActiveTask(null)
      return
    }
    setActiveTask(null)
    const { active, over } = event
    if (!over) return

    const taskId = String(active.id)
    const fromLane = findLane(taskId)
    if (!fromLane) return
    if (fromLane === 'Features') return

    let toLane: BoardLane | null = null
    if (lanes.includes(over.id as BoardLane)) {
      toLane = over.id as BoardLane
    } else {
      toLane = findLane(String(over.id))
    }

    if (!toLane || fromLane === toLane) {
      const reorderable = fromLane === 'Backlog' ? onReorderBacklog : fromLane === 'Refinement' ? onReorderLane : undefined
      if (reorderable && (fromLane === 'Backlog' || fromLane === 'Refinement')) {
        // Reorder within the filtered view, then merge back into full lane order.
        const visibleIds = (filteredBoard[fromLane] ?? []).map((t) => t.id)
        const overIdx = visibleIds.indexOf(String(over.id))
        const activeIdx = visibleIds.indexOf(taskId)
        if (overIdx >= 0 && activeIdx >= 0 && overIdx !== activeIdx) {
          const reorderedVisible = [...visibleIds]
          reorderedVisible.splice(activeIdx, 1)
          reorderedVisible.splice(overIdx, 0, taskId)
          const fullIds = (board[fromLane] ?? []).map((t) => t.id)
          const visibleSet = new Set(visibleIds)
          let vi = 0
          const next = fullIds.map((id) => {
            if (!visibleSet.has(id)) return id
            const nid = reorderedVisible[vi++]
            return nid ?? id
          })
          if (fromLane === 'Backlog') onReorderBacklog?.(next)
          else onReorderLane?.(fromLane, next)
        }
      }
      return
    }

    if (toLane === 'Features') return

    onMoveTask(taskId, fromLane, toLane)
  }

  return (
    <div
      className="p-4 overflow-hidden bg-cat-surface0/30 flex flex-col border-b border-cat-surface1 min-h-0 flex-1 h-full"
      onKeyDown={(e) => {
        if (e.key === 'Escape' && filterQuery) {
          e.stopPropagation()
          clearFilter()
        }
      }}
    >
      <div className="flex items-center justify-between gap-3 mb-3 shrink-0 flex-wrap">
        <h2 className="text-sm font-bold uppercase tracking-wider text-cat-subtext flex items-center gap-2">
          <i className="fa-solid fa-table-columns text-indigo-500" />
          Project Board: {projectName}
        </h2>
        <div className="flex items-center gap-2 flex-1 justify-end min-w-[12rem]">
          <div className="relative flex items-center max-w-xs w-full">
            <i className="fa-solid fa-magnifying-glass absolute left-2 text-[10px] text-cat-overlay pointer-events-none" />
            <input
              type="search"
              value={filterQuery}
              onChange={(e) => setFilterQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  e.preventDefault()
                  clearFilter()
                }
              }}
              placeholder="Filter by title or id…"
              className="w-full bg-cat-base border border-cat-surface1 rounded-lg pl-7 pr-7 py-1 text-[11px] text-white placeholder:text-cat-overlay focus:outline-none focus:border-indigo-500"
              aria-label="Filter board cards"
            />
            {filterQuery && (
              <button
                type="button"
                onClick={clearFilter}
                className="absolute right-1.5 text-cat-overlay hover:text-white p-0.5"
                title="Clear filter (Esc)"
                aria-label="Clear filter"
              >
                <i className="fa-solid fa-xmark text-[10px]" />
              </button>
            )}
          </div>
          {matchCount != null && (
            <span className="text-[10px] text-indigo-300 font-mono bg-indigo-950/40 border border-indigo-500/30 px-2 py-0.5 rounded shrink-0">
              {matchCount} match{matchCount === 1 ? '' : 'es'}
            </span>
          )}
          <span className="text-[10px] text-cat-subtext font-mono bg-cat-base px-2 py-1 rounded hidden lg:inline truncate max-w-[14rem]">
            Workspace: {workspaceDir}
          </span>
        </div>
      </div>

      {sprintRunning && (
        <div className="mb-2 text-[10px] text-amber-300 bg-amber-950/30 border border-amber-500/30 rounded px-2 py-1 shrink-0">
          Sprint step in progress — wait for it to finish before moving or deleting cards
        </div>
      )}

      <div className="flex-1 min-h-0 flex flex-col">
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          <div
            className="grid gap-3 flex-1 min-h-0 overflow-x-auto"
            style={{
              gridTemplateColumns: `repeat(${Math.min(lanes.length, 4)}, minmax(140px, 1fr))`,
            }}
          >
            {lanes.map((lane) => (
              <KanbanColumn
                key={lane}
                lane={lane}
                tasks={filteredBoard[lane] ?? []}
                onTaskClick={onTaskClick}
                getTaskFileCount={getTaskFileCount}
                getTaskDecisionCount={getTaskDecisionCount}
                dragDisabled={sprintRunning || lane === 'Features'}
                activeRunInfo={activeRunInfo}
              />
            ))}
          </div>

          <DragOverlay>
            {activeTask ? (
              <div className="bg-cat-surface0 p-2.5 rounded-lg border border-indigo-500/50 text-xs shadow-xl opacity-90">
                <span className="text-[10px] bg-indigo-950 text-indigo-300 px-1.5 py-0.5 rounded font-mono font-bold">
                  {activeTask.id}
                </span>
                <h4 className="font-bold text-white mt-1">{activeTask.title}</h4>
              </div>
            ) : null}
          </DragOverlay>
        </DndContext>
      </div>
    </div>
  )
})
