import { useEffect, useState, type ReactNode } from 'react'
import type { BoardLane, Task, TaskFile } from '../types'
import { formatAcceptanceCriteria, formatTaskText } from '../utils/taskFormat'

function getTaskFilePath(f: TaskFile | string): string {
  return typeof f === 'string' ? f : f.path
}

interface TaskDetailModalProps {
  task: Task | null
  taskLane: BoardLane | null
  sprintRunning?: boolean
  onClose: () => void
  onOpenFile: (path: string) => void
  onUpdate: (
    taskId: string,
    title: string,
    description: string,
    acceptanceCriteria: string[],
  ) => void
  onDelete: (taskId: string) => void
  onClearTranscript?: (taskId: string) => void
  onApprove?: (taskId: string) => void
  onResolveUser?: (taskId: string, answer: string) => void
}

function CollapsibleSection({
  title,
  badge,
  defaultOpen = true,
  children,
}: {
  title: string
  badge?: string | number
  defaultOpen?: boolean
  children: ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-cat-surface1 rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-3 py-2 bg-cat-base/50 hover:bg-cat-base text-left"
      >
        <span className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          {title}
          {badge != null && (
            <span className="ml-2 text-[10px] font-mono text-indigo-300 normal-case">{badge}</span>
          )}
        </span>
        <i className={`fa-solid fa-chevron-${open ? 'up' : 'down'} text-cat-overlay text-[10px]`} />
      </button>
      {open && <div className="p-3">{children}</div>}
    </div>
  )
}

export default function TaskDetailModal({
  task,
  taskLane,
  sprintRunning = false,
  onClose,
  onOpenFile,
  onUpdate,
  onDelete,
  onClearTranscript,
  onApprove,
  onResolveUser,
}: TaskDetailModalProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [acceptanceCriteria, setAcceptanceCriteria] = useState('')
  const [editing, setEditing] = useState(false)
  const [userAnswer, setUserAnswer] = useState('')
  const [showAllTranscript, setShowAllTranscript] = useState(false)

  useEffect(() => {
    if (task) {
      setTitle(formatTaskText(task.title))
      setDescription(formatTaskText(task.description))
      setAcceptanceCriteria(formatAcceptanceCriteria(task.acceptanceCriteria).join('\n'))
      setEditing(false)
      setUserAnswer('')
      setShowAllTranscript(false)
    }
  }, [task])

  if (!task) return null

  const files = task.files ?? []
  const decisions = [...(task.decisions ?? [])].reverse()
  const allTranscript = [...(task.transcript ?? [])].reverse()
  const transcriptCount = allTranscript.length
  const transcriptCollapsedDefault = transcriptCount > 20
  const visibleTranscript = showAllTranscript ? allTranscript : allTranscript.slice(0, 50)
  const acList = formatAcceptanceCriteria(task.acceptanceCriteria)
  const blockedBy = task.blockedBy ?? []

  return (
    <div className="fixed inset-0 bg-black/75 flex items-center justify-center p-4 z-50">
      <div className="bg-cat-surface0 rounded-2xl max-w-2xl w-full border border-cat-surface1 shadow-2xl flex flex-col max-h-[85vh]">
        <div className="sticky top-0 z-10 bg-cat-surface0 rounded-t-2xl border-b border-cat-surface1 px-6 py-4 flex items-center justify-between shrink-0">
          <div className="min-w-0 flex-1 pr-4">
            {editing ? (
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="text-base font-bold text-white bg-cat-base border border-cat-surface1 rounded px-2 py-1 w-full"
              />
            ) : (
              <h3 className="text-base font-bold text-white truncate">{task.title}</h3>
            )}
            <p className="text-[10px] text-indigo-300 font-mono mt-0.5">
              {task.id} · {task.status}
              {task.priority != null && ` · P${task.priority}`}
              {(task.poRoundTrips ?? 0) > 0 && (
                <span className="ml-2 text-amber-400">PO↔Dev ×{task.poRoundTrips}</span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {taskLane === 'Pending Approval' && onApprove && (
              <button
                type="button"
                onClick={() => onApprove(task.id)}
                className="text-xs text-emerald-400 hover:text-emerald-300"
              >
                Approve
              </button>
            )}
            <button
              type="button"
              onClick={() => setEditing((e) => !e)}
              className="text-xs text-indigo-400 hover:text-indigo-300"
            >
              {editing ? 'Cancel Edit' : 'Edit'}
            </button>
            <button type="button" onClick={onClose} className="text-cat-subtext hover:text-white">
              <i className="fa-solid fa-xmark" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3 min-h-0">
          <CollapsibleSection title="Description" defaultOpen>
            {editing ? (
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                className="text-xs text-white bg-cat-base border border-cat-surface1 rounded p-2 min-h-[80px] font-mono w-full max-h-32 overflow-y-auto"
              />
            ) : (
              <p className="text-xs text-cat-subtext max-h-32 overflow-y-auto whitespace-pre-wrap">
                {formatTaskText(task.description)}
              </p>
            )}
          </CollapsibleSection>

          <CollapsibleSection title="Acceptance Criteria" badge={acList.length} defaultOpen>
            {editing ? (
              <textarea
                value={acceptanceCriteria}
                onChange={(e) => setAcceptanceCriteria(e.target.value)}
                placeholder="One criterion per line"
                className="w-full text-xs font-mono bg-cat-base border border-cat-surface1 rounded p-2 min-h-[60px] max-h-32 overflow-y-auto"
              />
            ) : acList.length > 0 ? (
              <ul className="text-[11px] text-cat-subtext list-disc pl-4 space-y-0.5 max-h-32 overflow-y-auto">
                {acList.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            ) : (
              <p className="text-[11px] text-cat-overlay italic">None defined</p>
            )}
          </CollapsibleSection>

          {blockedBy.length > 0 && (
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-cat-subtext mb-1">
                Blocked By
              </h4>
              <p className="text-[11px] font-mono text-orange-300">{blockedBy.join(', ')}</p>
            </div>
          )}

          {task.qaFailure && (
            <div className="bg-rose-950/30 border border-rose-500/30 rounded-lg p-3">
              <h4 className="text-xs font-bold text-rose-300 mb-1">Last QA Failure</h4>
              <p className="text-[11px] text-white max-h-16 overflow-y-auto">{task.qaFailure.reason}</p>
              {task.qaFailure.output && (
                <pre className="text-[10px] text-cat-subtext mt-1 whitespace-pre-wrap font-mono max-h-24 overflow-y-auto">
                  {task.qaFailure.output}
                </pre>
              )}
              <p className="text-[10px] text-cat-overlay mt-1">{task.qaFailure.timestamp}</p>
            </div>
          )}

          {taskLane === 'Needs User' && onResolveUser && (
            <div className="bg-amber-950/20 border border-amber-500/30 rounded-lg p-3 space-y-2">
              {task.userQuestion && (
                <p className="text-[11px] text-amber-200 max-h-24 overflow-y-auto whitespace-pre-wrap">
                  {task.userQuestion}
                </p>
              )}
              <textarea
                value={userAnswer}
                onChange={(e) => setUserAnswer(e.target.value)}
                placeholder="Your answer for the Developer…"
                className="w-full text-xs bg-cat-base border border-cat-surface1 rounded p-2 min-h-[60px]"
              />
              <button
                type="button"
                disabled={!userAnswer.trim()}
                onClick={() => onResolveUser(task.id, userAnswer.trim())}
                className="bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white text-xs py-1.5 px-3 rounded-lg"
              >
                Resolve & Return to Dev
              </button>
            </div>
          )}

          {editing && (
            <div className="flex gap-2 flex-wrap">
              <button
                type="button"
                onClick={() => {
                  onUpdate(
                    task.id,
                    title,
                    description,
                    acceptanceCriteria
                      .split('\n')
                      .map((s) => s.trim())
                      .filter(Boolean),
                  )
                  setEditing(false)
                }}
                className="bg-indigo-600 hover:bg-indigo-500 text-white text-xs py-1.5 px-3 rounded-lg"
              >
                Save
              </button>
              <button
                type="button"
                disabled={sprintRunning}
                title={
                  sprintRunning
                    ? 'Wait for the current sprint step to finish'
                    : 'Delete this task'
                }
                onClick={() => onDelete(task.id)}
                className="bg-rose-950/40 hover:bg-rose-950/60 disabled:opacity-50 text-rose-400 text-xs py-1.5 px-3 rounded-lg border border-rose-500/30"
              >
                Delete Task
              </button>
            </div>
          )}

          <CollapsibleSection title="Associated Files" badge={files.length} defaultOpen={files.length > 0}>
            <div className="overflow-y-auto space-y-1 max-h-24">
              {files.length === 0 ? (
                <p className="text-[11px] text-cat-overlay italic">None yet</p>
              ) : (
                files.map((f, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => {
                      onOpenFile(getTaskFilePath(f))
                      onClose()
                    }}
                    className="w-full text-left text-[11px] font-mono bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 hover:border-indigo-500/50 text-indigo-300"
                  >
                    {getTaskFilePath(f)}
                  </button>
                ))
              )}
            </div>
          </CollapsibleSection>

          <CollapsibleSection title="Agent Decisions" badge={decisions.length} defaultOpen={decisions.length <= 10}>
            <div className="overflow-y-auto space-y-2 max-h-40 pr-1">
              {decisions.length === 0 ? (
                <p className="text-[11px] text-cat-overlay italic">None yet</p>
              ) : (
                decisions.map((d, i) => (
                  <div key={i} className="bg-cat-base border border-cat-surface1 rounded-lg p-2 text-[11px]">
                    <div className="flex justify-between text-[10px] text-cat-overlay mb-1">
                      <span>{d.agent} · {d.type}</span>
                      <span>{d.timestamp}</span>
                    </div>
                    <p className="text-white">{d.summary}</p>
                    {d.detail && (
                      <p className="text-cat-subtext mt-1 whitespace-pre-wrap text-[10px] max-h-20 overflow-y-auto">
                        {d.detail}
                      </p>
                    )}
                  </div>
                ))
              )}
            </div>
          </CollapsibleSection>

          <CollapsibleSection
            title="Transcript"
            badge={transcriptCount}
            defaultOpen={!transcriptCollapsedDefault}
          >
            <div className="flex items-center justify-between mb-2 gap-2">
              {transcriptCount > 50 && !showAllTranscript && (
                <button
                  type="button"
                  onClick={() => setShowAllTranscript(true)}
                  className="text-[10px] text-indigo-400 hover:text-indigo-300"
                >
                  Show all {transcriptCount} entries
                </button>
              )}
              {showAllTranscript && transcriptCount > 50 && (
                <button
                  type="button"
                  onClick={() => setShowAllTranscript(false)}
                  className="text-[10px] text-indigo-400 hover:text-indigo-300"
                >
                  Show last 50 only
                </button>
              )}
              {onClearTranscript && transcriptCount > 0 && (
                <button
                  type="button"
                  onClick={() => onClearTranscript(task.id)}
                  className="text-[10px] text-rose-400 hover:text-rose-300 ml-auto"
                >
                  Clear transcript
                </button>
              )}
            </div>
            <div className="overflow-y-auto max-h-48 space-y-2 pr-1">
              {visibleTranscript.length === 0 ? (
                <p className="text-[11px] text-cat-overlay italic">Empty</p>
              ) : (
                visibleTranscript.map((entry, i) => (
                  <div
                    key={i}
                    className="text-[10px] font-mono text-cat-subtext bg-cat-base border border-cat-surface1 rounded p-2"
                  >
                    <div className="text-cat-overlay mb-0.5">
                      [{entry.timestamp}] {entry.agent ?? entry.role}
                    </div>
                    <p className="whitespace-pre-wrap max-h-24 overflow-y-auto">{entry.content}</p>
                  </div>
                ))
              )}
            </div>
          </CollapsibleSection>
        </div>
      </div>
    </div>
  )
}
