import { useDroppable } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable'
import type { BoardLane, Task } from '../types'
import type { TaskRunInfo } from '../utils/taskRunInfo'
import TaskCard from './TaskCard'

interface KanbanColumnProps {
  lane: BoardLane
  tasks: Task[]
  onTaskClick: (task: Task) => void
  getTaskFileCount: (task: Task) => number
  getTaskDecisionCount: (task: Task) => number
  dragDisabled?: boolean
  activeRunInfo?: TaskRunInfo | null
}

export default function KanbanColumn({
  lane,
  tasks,
  onTaskClick,
  getTaskFileCount,
  getTaskDecisionCount,
  dragDisabled = false,
  activeRunInfo = null,
}: KanbanColumnProps) {
  const { setNodeRef, isOver } = useDroppable({ id: lane })
  const highlightNeedsUser = lane === 'Needs User' && tasks.length > 0
  const isFeaturesLane = lane === 'Features'

  return (
    <div
      ref={setNodeRef}
      className={`bg-cat-base p-2.5 rounded-xl border flex flex-col h-full min-h-0 transition-colors ${
        isOver
          ? 'border-indigo-500/60'
          : highlightNeedsUser
            ? 'border-amber-500/50 bg-amber-950/10'
            : isFeaturesLane
              ? 'border-violet-500/40 bg-violet-950/10'
              : 'border-cat-surface1'
      }`}
    >
      <div className="flex items-center justify-between pb-1.5 border-b border-cat-surface1 mb-2.5 shrink-0">
        <span
          className={`text-xs font-bold uppercase tracking-wider ${
            isFeaturesLane ? 'text-violet-300' : 'text-cat-text'
          }`}
        >
          {isFeaturesLane ? 'Features (Epics)' : lane}
        </span>
        <span className="bg-cat-surface0 text-cat-subtext text-[10px] font-mono px-2 py-0.5 rounded-full">
          {tasks.length}
        </span>
      </div>
      <SortableContext items={tasks.map((t) => t.id)} strategy={verticalListSortingStrategy}>
        <div className="space-y-2 overflow-y-auto flex-1 min-h-0">
          {tasks.map((task, i) => (
            <TaskCard
              key={`${lane}-${task.id}-${i}`}
              task={task}
              fileCount={getTaskFileCount(task)}
              decisionCount={getTaskDecisionCount(task)}
              onClick={() => onTaskClick(task)}
              dragDisabled={dragDisabled}
              lane={lane}
              runInfo={activeRunInfo?.taskId === task.id ? activeRunInfo : null}
            />
          ))}
        </div>
      </SortableContext>
    </div>
  )
}
