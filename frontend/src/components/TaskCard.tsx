import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import type { BoardLane, Task } from '../types'
import type { TaskRunInfo } from '../utils/taskRunInfo'
import { formatRunStatus } from '../utils/taskRunInfo'
import { deriveTaskFiles, formatTaskText } from '../utils/taskFormat'

interface TaskCardProps {
  task: Task
  fileCount: number
  decisionCount: number
  onClick: () => void
  dragDisabled?: boolean
  lane?: BoardLane
  runInfo?: TaskRunInfo | null
}

export default function TaskCard({
  task,
  fileCount,
  decisionCount,
  onClick,
  dragDisabled = false,
  lane,
  runInfo = null,
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
  const childTaskCount = (task.childTaskIds ?? []).length
  const isSubtask = Boolean(task.parentTaskId)
  const isFeature = task.workType === 'feature' || lane === 'Features'
  const featureParentId = task.featureId
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

  const isActiveRun = runInfo != null && runInfo.taskId === task.id
  const statusLabel = isActiveRun ? formatRunStatus(runInfo) : ''

  return (
    <button
      ref={setNodeRef}
      type="button"
      style={style}
      {...attributes}
      {...listeners}
      onClick={onClick}
      className={`w-full text-left bg-cat-surface0 p-2.5 rounded-lg border transition-all text-xs ${
        isActiveRun
          ? 'border-indigo-400/70 ring-1 ring-indigo-500/40 shadow-md shadow-indigo-950/30'
          : isFeature
            ? 'border-violet-500/30 hover:border-violet-400/50'
            : dragDisabled
              ? 'cursor-not-allowed opacity-70 border-cat-surface1'
              : 'border-cat-surface1 hover:border-indigo-500/50 cursor-grab active:cursor-grabbing'
      } ${isFeature ? 'cursor-pointer' : ''}`}
    >
      {isActiveRun && (
        <div className="mb-2 rounded-md bg-indigo-950/50 border border-indigo-500/40 px-2 py-1.5 space-y-1">
          <div className="flex items-center gap-1.5 text-[10px] text-indigo-200">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse shrink-0" />
            <span className="font-semibold">{runInfo.agent}</span>
            <span className="text-indigo-300/80">·</span>
            <span className="text-indigo-100">{statusLabel}</span>
          </div>
          {runInfo.iteration != null && runInfo.maxIterations != null && (
            <p className="text-[9px] text-cat-subtext font-mono">
              LLM iteration {runInfo.iteration}/{runInfo.maxIterations}
            </p>
          )}
          {runInfo.currentTool && (
            <p className="text-[9px] text-amber-200/90 font-mono truncate" title={runInfo.currentTool}>
              Tool: {runInfo.currentTool}
            </p>
          )}
          {runInfo.lastEvent && (
            <p className="text-[9px] text-cyan-200/80 font-mono truncate" title={runInfo.lastEvent}>
              {runInfo.lastEvent}
            </p>
          )}
          {runInfo.lane && (
            <p className="text-[9px] text-cat-overlay">Lane: {runInfo.lane}</p>
          )}
        </div>
      )}
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
          {task.workType === 'planning' || (task.requiresDev === false && !isFeature) ? (
            <span className="text-[9px] bg-violet-950/50 text-violet-300 px-1 py-0.5 rounded" title="PO-only card">
              PO
            </span>
          ) : null}
          {isFeature && (
            <span className="text-[9px] bg-violet-950/60 text-violet-200 px-1 py-0.5 rounded" title="Feature epic (stationary)">
              Epic
            </span>
          )}
          {featureParentId && (
            <span
              className="text-[9px] bg-violet-950/50 text-violet-300 px-1 py-0.5 rounded font-mono"
              title={`Parent feature ${featureParentId}`}
            >
              ↗{featureParentId.slice(0, 8)}
            </span>
          )}
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
          {childTaskCount > 0 && (
            <span
              className="text-[9px] bg-violet-950/50 text-violet-300 px-1 py-0.5 rounded"
              title="Implementation cards"
            >
              +{childTaskCount}
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
