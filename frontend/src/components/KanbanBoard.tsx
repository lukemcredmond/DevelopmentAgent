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
import { useMemo, useState } from 'react'
import type { Board, BoardLane, Task, WorkflowSettings } from '../types'
import { getDisplayLanes } from '../types'
import { deriveTaskFiles } from '../utils/taskFormat'
import KanbanColumn from './KanbanColumn'

interface KanbanBoardProps {
  board: Board
  projectName: string
  workspaceDir: string
  activeLanes?: BoardLane[]
  workflowSettings?: WorkflowSettings
  sprintRunning?: boolean
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

export default function KanbanBoard({
  board,
  projectName,
  workspaceDir,
  activeLanes,
  workflowSettings,
  sprintRunning = false,
  onTaskClick,
  onMoveTask,
  onReorderBacklog,
  onReorderLane,
}: KanbanBoardProps) {
  const [activeTask, setActiveTask] = useState<Task | null>(null)
  const lanes = useMemo(
    () => getDisplayLanes(activeLanes, workflowSettings),
    [activeLanes, workflowSettings],
  )

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

    let toLane: BoardLane | null = null
    if (lanes.includes(over.id as BoardLane)) {
      toLane = over.id as BoardLane
    } else {
      toLane = findLane(String(over.id))
    }

    if (!toLane || fromLane === toLane) {
      const reorderable = fromLane === 'Backlog' ? onReorderBacklog : fromLane === 'Refinement' ? onReorderLane : undefined
      if (reorderable && (fromLane === 'Backlog' || fromLane === 'Refinement')) {
        const ids = (board[fromLane] ?? []).map((t) => t.id)
        const overIdx = ids.indexOf(String(over.id))
        const activeIdx = ids.indexOf(taskId)
        if (overIdx >= 0 && activeIdx >= 0 && overIdx !== activeIdx) {
          const next = [...ids]
          next.splice(activeIdx, 1)
          next.splice(overIdx, 0, taskId)
          if (fromLane === 'Backlog') onReorderBacklog?.(next)
          else onReorderLane?.(fromLane, next)
        }
      }
      return
    }

    onMoveTask(taskId, fromLane, toLane)
  }

  return (
    <div className="p-4 overflow-y-auto bg-cat-surface0/30 flex flex-col border-b border-cat-surface1 min-h-0 flex-1">
      <div className="flex items-center justify-between mb-3 shrink-0">
        <h2 className="text-sm font-bold uppercase tracking-wider text-cat-subtext flex items-center gap-2">
          <i className="fa-solid fa-table-columns text-indigo-500" />
          Project Board: {projectName}
        </h2>
        <span className="text-[10px] text-cat-subtext font-mono bg-cat-base px-2 py-1 rounded">
          Workspace: {workspaceDir}
        </span>
      </div>

      {sprintRunning && (
        <div className="mb-2 text-[10px] text-amber-300 bg-amber-950/30 border border-amber-500/30 rounded px-2 py-1 shrink-0">
          Sprint step in progress — wait for it to finish before moving or deleting cards
        </div>
      )}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <div
          className="grid gap-3 flex-1 min-h-[160px]"
          style={{
            gridTemplateColumns: `repeat(${Math.min(lanes.length, 4)}, minmax(140px, 1fr))`,
          }}
        >
          {lanes.map((lane) => (
            <KanbanColumn
              key={lane}
              lane={lane}
              tasks={board[lane] ?? []}
              onTaskClick={onTaskClick}
              getTaskFileCount={getTaskFileCount}
              getTaskDecisionCount={getTaskDecisionCount}
              dragDisabled={sprintRunning}
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
  )
}
