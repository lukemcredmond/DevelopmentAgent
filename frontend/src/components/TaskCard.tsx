import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import type { BoardLane, Task } from '../types'
import { deriveTaskFiles, formatTaskText } from '../utils/taskFormat'

interface TaskCardProps {
  task: Task
  fileCount: number
  decisionCount: number
  onClick: () => void
  dragDisabled?: boolean
  lane?: BoardLane
}

export default function TaskCard({
  task,
  fileCount,
  decisionCount,
  onClick,
  dragDisabled = false,
  lane,
}: TaskCardProps) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: task.id, disabled: dragDisabled })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  const blocked = (task.blockedBy ?? []).length > 0
  const subtaskCount = (task.subtaskIds ?? []).length
  const isSubtask = Boolean(task.parentTaskId)
  const relatedCount = (task.relatedTaskIds ?? []).length
  const hasCommit = Boolean(task.gitCommit?.hash)
  const isDone = task.status === 'Done'
  const needsUser = lane === 'Needs User' || task.status === 'Needs User'
  const duplicateQuestion = task.needsUserDuplicate === true
  const filePaths = deriveTaskFiles(task).map((f) => f.path).slice(0, 2)

  function truncatePath(path: string): string {
    if (path.length <= 28) return path
    const parts = path.split('/')
    if (parts.length <= 1) return `…${path.slice(-24)}`
    return `…/${parts.slice(-2).join('/')}`
  }

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
          {task.qaEvidence && !task.qaEvidence.passed && task.qaEvidence.playbookRun && (
            <span className="text-[9px] bg-amber-950/50 text-amber-300 px-1 py-0.5 rounded" title="Tests failed or not run">
              No tests
            </span>
          )}
          {task.workType === 'spike' && (
            <span className="text-[9px] bg-cyan-950/50 text-cyan-300 px-1 py-0.5 rounded" title="Spike exploration">
              Spike
            </span>
          )}
          {task.needsSpike && task.workType !== 'spike' && (
            <span className="text-[9px] bg-cyan-950/50 text-cyan-300 px-1 py-0.5 rounded" title="Awaiting spike">
              Spike pending
            </span>
          )}
          {task.workType === 'planning' || task.requiresDev === false ? (
            <span className="text-[9px] bg-violet-950/50 text-violet-300 px-1 py-0.5 rounded" title="PO-only card">
              PO
            </span>
          ) : null}
          {task.requiresQa === false && task.requiresDev !== false && (
            <span className="text-[9px] bg-slate-800 text-slate-300 px-1 py-0.5 rounded" title="Skips QA">
              No QA
            </span>
          )}
          {needsUser && (
            <span
              className="text-[9px] bg-amber-950/50 text-amber-300 px-1 py-0.5 rounded"
              title="Needs your input"
            >
              <i className="fa-solid fa-circle-question" />
            </span>
          )}
          {duplicateQuestion && (
            <span
              className="text-[9px] bg-rose-950/50 text-rose-300 px-1 py-0.5 rounded"
              title="Agent tried to ask the same question again"
            >
              repeat?
            </span>
          )}
          {subtaskCount > 0 && (
            <span
              className="text-[9px] bg-sky-950/50 text-sky-300 px-1 py-0.5 rounded"
              title="Child todos"
            >
              ↳{subtaskCount}
            </span>
          )}
          {isSubtask && (
            <span
              className="text-[9px] bg-sky-950/50 text-sky-300 px-1 py-0.5 rounded"
              title="Subtask of parent"
            >
              sub
            </span>
          )}
          {relatedCount > 0 && (
            <span
              className="text-[9px] bg-violet-950/50 text-violet-300 px-1 py-0.5 rounded"
              title="Related features"
            >
              ↔{relatedCount}
            </span>
          )}
          {isDone && hasCommit && (
            <span
              className="text-[9px] bg-emerald-950/50 text-emerald-300 px-1 py-0.5 rounded font-mono"
              title={`Commit ${task.gitCommit?.hash?.slice(0, 8)}`}
            >
              <i className="fa-solid fa-code-commit mr-0.5" />
              {task.gitCommit?.hash?.slice(0, 7)}
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
      {needsUser && (
        <p className="text-[10px] text-amber-200/90 line-clamp-2 mb-1">
          {task.needsUserAction?.trim() || task.userQuestion?.trim() || 'Action required — open for details'}
        </p>
      )}
      <p className="text-[11px] text-cat-subtext line-clamp-3">{formatTaskText(task.description)}</p>
      {filePaths.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5">
          {filePaths.map((path) => (
            <span
              key={path}
              className="text-[9px] font-mono bg-slate-800/80 text-slate-300 px-1.5 py-0.5 rounded truncate max-w-full"
              title={path}
            >
              {truncatePath(path)}
            </span>
          ))}
        </div>
      )}
    </button>
  )
}
