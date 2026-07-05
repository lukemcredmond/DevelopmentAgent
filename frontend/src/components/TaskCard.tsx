import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import type { Task } from '../types'
import { formatTaskText } from '../utils/taskFormat'

interface TaskCardProps {
  task: Task
  fileCount: number
  decisionCount: number
  onClick: () => void
  dragDisabled?: boolean
}

export default function TaskCard({
  task,
  fileCount,
  decisionCount,
  onClick,
  dragDisabled = false,
}: TaskCardProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: task.id, disabled: dragDisabled })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  const blocked = (task.blockedBy ?? []).length > 0

  return (
    <button
      ref={setNodeRef}
      type="button"
      style={style}
      {...attributes}
      {...listeners}
      onClick={onClick}
      className={`w-full text-left bg-cat-surface0 p-2.5 rounded-lg border border-cat-surface1 hover:border-indigo-500/50 transition-all text-xs ${
        dragDisabled ? 'cursor-not-allowed opacity-70' : 'cursor-grab active:cursor-grabbing'
      }`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[10px] bg-indigo-950 text-indigo-300 px-1.5 py-0.5 rounded font-mono font-bold">
          {task.id}
        </span>
        <div className="flex items-center gap-1">
          {task.priority != null && task.priority < 100 && (
            <span className="text-[9px] bg-amber-950/50 text-amber-300 px-1 py-0.5 rounded" title="Priority">
              P{task.priority}
            </span>
          )}
          {blocked && (
            <span className="text-[9px] bg-orange-950/50 text-orange-300 px-1 py-0.5 rounded" title="Blocked by dependency">
              <i className="fa-solid fa-link" />
            </span>
          )}
          {task.qaFailure && (
            <span className="text-[9px] bg-rose-950/50 text-rose-300 px-1 py-0.5 rounded" title="QA failed">
              <i className="fa-solid fa-xmark" />
            </span>
          )}
          {fileCount > 0 && (
            <span className="text-[9px] bg-slate-800 text-slate-300 px-1 py-0.5 rounded">
              <i className="fa-regular fa-file-code mr-0.5" />
              {fileCount}
            </span>
          )}
          {decisionCount > 0 && (
            <span className="text-[9px] bg-slate-800 text-slate-300 px-1 py-0.5 rounded">
              <i className="fa-solid fa-brain mr-0.5" />
              {decisionCount}
            </span>
          )}
        </div>
      </div>
      <h4 className="font-bold text-white mb-1 leading-tight">{task.title}</h4>
      <p className="text-[11px] text-cat-subtext line-clamp-3">{formatTaskText(task.description)}</p>
    </button>
  )
}
