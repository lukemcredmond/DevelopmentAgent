import { useDroppable } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable'
import type { BoardLane, Task } from '../types'
import TaskCard from './TaskCard'

interface KanbanColumnProps {
  lane: BoardLane
  tasks: Task[]
  onTaskClick: (task: Task) => void
  getTaskFileCount: (task: Task) => number
  getTaskDecisionCount: (task: Task) => number
}

export default function KanbanColumn({
  lane,
  tasks,
  onTaskClick,
  getTaskFileCount,
  getTaskDecisionCount,
}: KanbanColumnProps) {
  const { setNodeRef, isOver } = useDroppable({ id: lane })

  return (
    <div
      ref={setNodeRef}
      className={`bg-cat-base p-2.5 rounded-xl border flex flex-col min-h-[160px] transition-colors ${
        isOver ? 'border-indigo-500/60' : 'border-cat-surface1'
      }`}
    >
      <div className="flex items-center justify-between pb-1.5 border-b border-cat-surface1 mb-2.5">
        <span className="text-xs font-bold text-cat-text uppercase tracking-wider">
          {lane}
        </span>
        <span className="bg-cat-surface0 text-cat-subtext text-[10px] font-mono px-2 py-0.5 rounded-full">
          {tasks.length}
        </span>
      </div>
      <SortableContext items={tasks.map((t) => t.id)} strategy={verticalListSortingStrategy}>
        <div className="space-y-2 overflow-y-auto flex-1">
          {tasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              fileCount={getTaskFileCount(task)}
              decisionCount={getTaskDecisionCount(task)}
              onClick={() => onTaskClick(task)}
            />
          ))}
        </div>
      </SortableContext>
    </div>
  )
}
