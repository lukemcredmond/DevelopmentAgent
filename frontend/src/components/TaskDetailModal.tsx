import { useEffect, useMemo, useState, type ReactNode } from 'react'
import type { BoardLane, CommandDiagnostic, Task, TaskFile, TaskGitCommit, TaskTranscriptEntry } from '../types'
import { formatAcceptanceCriteria, formatTaskText, deriveTaskFiles, sanitizeTaskForUi } from '../utils/taskFormat'

function getCommandDiagnostics(task: Task): CommandDiagnostic[] {
  if (task.lastCommandDiagnostics?.length) {
    return task.lastCommandDiagnostics
  }
  const transcript = task.transcript ?? []
  for (let i = transcript.length - 1; i >= 0; i--) {
    const entry = transcript[i]
    if (entry.toolName !== 'run_command') continue
    const output = entry.toolOutput ?? entry.content ?? ''
    const command = String((entry.toolArgs as Record<string, unknown> | undefined)?.command ?? '')
    const findings: CommandDiagnostic[] = []
    const bulletPattern =
      /^\s*(error|warning|info)\s+[•-]\s+(.+?)\s+[•-]\s+(.+?):(\d+):(\d+)\s*$/gim
    let match: RegExpExecArray | null
    while ((match = bulletPattern.exec(output)) !== null) {
      findings.push({
        severity: match[1].toLowerCase(),
        message: match[2].trim(),
        file: match[3].replace(/\\/g, '/'),
        line: Number(match[4]),
        column: Number(match[5]),
      })
    }
    if (findings.length > 0 || output.includes('## Problems')) {
      const problemsBlock = output.split('## Problems')[1]?.split('## Output')[0] ?? ''
      const linePattern = /^-\s+(.+?):(\d+):(\d+)\s+(\S+)\s+(.+)$/gm
      while ((match = linePattern.exec(problemsBlock)) !== null) {
        findings.push({
          file: match[1].replace(/\\/g, '/'),
          line: Number(match[2]),
          column: Number(match[3]),
          severity: match[4].toLowerCase(),
          message: match[5].trim(),
        })
      }
    }
    if (findings.length > 0) return findings
    if (command && output) break
  }
  return []
}

function getTaskFilePath(f: TaskFile | string): string {
  return typeof f === 'string' ? f : f.path
}

function fileActionBadgeClass(action?: string): string {
  switch (action) {
    case 'written':
      return 'bg-emerald-950/60 text-emerald-300'
    case 'read':
      return 'bg-slate-800 text-slate-300'
    case 'context':
      return 'bg-violet-950/60 text-violet-300'
    case 'tested':
      return 'bg-amber-950/60 text-amber-300'
    default:
      return 'bg-cat-surface1 text-cat-subtext'
  }
}

function isTranscriptFailure(entry: TaskTranscriptEntry): boolean {
  if (entry.toolSuccess === false) return true
  if (entry.role === 'tool') {
    const content = entry.content ?? ''
    if (content.includes('✗') || /\bFAILED\b/i.test(content)) return true
  }
  return false
}

function deriveNeedsUserReason(task: Task): string {
  if (task.userQuestion?.trim()) return task.userQuestion.trim()
  const decisions = task.decisions ?? []
  for (let i = decisions.length - 1; i >= 0; i--) {
    const d = decisions[i]
    if (['stuck_loop', 'po_limit', 'dev_escalation'].includes(d.type)) {
      return d.detail?.trim() ? `${d.summary}\n${d.detail}` : d.summary
    }
    if (/no progress|clarify|needs user/i.test(d.summary)) {
      return d.summary
    }
  }
  const transcript = task.transcript ?? []
  for (let i = transcript.length - 1; i >= 0; i--) {
    const entry = transcript[i]
    const content = entry.content ?? ''
    if (
      (entry.role === 'system' || entry.agent === 'System') &&
      /no progress|clarify|needs user|stuck loop|could not agree/i.test(content)
    ) {
      return content
    }
  }
  return 'Action required — the agent could not proceed without your input.'
}

function buildCommitUrl(remoteUrl: string, hash: string): string | null {
  let url = remoteUrl.trim()
  if (url.startsWith('git@')) {
    const match = /^git@([^:]+):(.+?)(?:\.git)?$/.exec(url)
    if (match) {
      url = `https://${match[1]}/${match[2].replace(/\.git$/, '')}`
    }
  }
  url = url.replace(/\.git$/, '')
  if (
    url.includes('github.com') ||
    url.includes('gitlab.com') ||
    url.includes('dev.azure.com') ||
    url.includes('visualstudio.com')
  ) {
    return `${url}/commit/${hash}`
  }
  return null
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
  onResolveUser?: (taskId: string, answer: string, target: 'dev' | 'refinement' | 'po') => void
  onDiscussWithAgent?: (task: Task, lane: BoardLane | null) => void
  onSplit?: (taskId: string) => void | Promise<void>
  onInjectToolEvidence?: (
    taskId: string,
    payload: {
      toolName: string
      toolArgs: Record<string, unknown>
      toolOutput: string
      note?: string
    },
  ) => void | Promise<void>
  onRelatedTaskClick?: (taskId: string) => void
  getTaskTitle?: (taskId: string) => string | undefined
  ollamaUrl?: string
  onDiagnose?: (taskId: string) => void | Promise<void>
  onRetryStep?: (taskId: string, mode: 'same' | 'optimized' | 'fix_and_verify') => void | Promise<void>
  onViewFileDiff?: (path: string) => void | Promise<void>
  onOpenModelTab?: () => void
  maxRefinementRoundTrips?: number
  requireBacklogRefinement?: boolean
  onEscapeSubtasks?: (taskId: string) => void | Promise<void>
  onMoveToInProgress?: (
    taskId: string,
    fromLane: BoardLane,
    skipRefinement?: boolean,
  ) => void | Promise<void>
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

function GitCommitSection({ commit }: { commit: TaskGitCommit }) {
  const shortHash = commit.hash.slice(0, 8)
  const link = commit.remoteUrl ? buildCommitUrl(commit.remoteUrl, commit.hash) : null

  const copyHash = () => {
    void navigator.clipboard.writeText(commit.hash)
  }

  return (
    <CollapsibleSection title="Git Commit" defaultOpen>
      <div className="space-y-1 text-[11px]">
        {link ? (
          <a
            href={link}
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono text-indigo-300 hover:text-indigo-200 underline"
          >
            {shortHash}
          </a>
        ) : (
          <button
            type="button"
            onClick={copyHash}
            className="font-mono text-indigo-300 hover:text-indigo-200"
            title="Copy full hash"
          >
            {shortHash}
          </button>
        )}
        {commit.message && (
          <p className="text-cat-subtext whitespace-pre-wrap">{commit.message}</p>
        )}
        {commit.timestamp && (
          <p className="text-[10px] text-cat-overlay">{commit.timestamp}</p>
        )}
      </div>
    </CollapsibleSection>
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
  onDiscussWithAgent,
  onSplit,
  onInjectToolEvidence,
  onRelatedTaskClick,
  getTaskTitle,
  onDiagnose,
  onRetryStep,
  onViewFileDiff,
  onOpenModelTab,
  maxRefinementRoundTrips,
  requireBacklogRefinement = false,
  onEscapeSubtasks,
  onMoveToInProgress,
}: TaskDetailModalProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [acceptanceCriteria, setAcceptanceCriteria] = useState('')
  const [editing, setEditing] = useState(false)
  const [userAnswer, setUserAnswer] = useState('')
  const [injectCommand, setInjectCommand] = useState('flutter analyze')
  const [injectOutput, setInjectOutput] = useState('')
  const [injectNote, setInjectNote] = useState('')
  const [injecting, setInjecting] = useState(false)
  const [showAllTranscript, setShowAllTranscript] = useState(false)
  const [showFailuresOnly, setShowFailuresOnly] = useState(false)
  const [splitting, setSplitting] = useState(false)
  const [diagnosing, setDiagnosing] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [showPriorAnswers, setShowPriorAnswers] = useState(false)
  const [skipRemainingRefinement, setSkipRemainingRefinement] = useState(false)
  const [movingToProgress, setMovingToProgress] = useState(false)

  useEffect(() => {
    if (!task) return
    setTitle(formatTaskText(task.title))
    setDescription(formatTaskText(task.description))
    setAcceptanceCriteria(formatAcceptanceCriteria(task.acceptanceCriteria).join('\n'))
    setEditing(false)
    setShowAllTranscript(false)
    setShowFailuresOnly(false)
    try {
      const draft = sessionStorage.getItem(`needs-user-draft-${task.id}`)
      setUserAnswer(draft ?? '')
    } catch {
      setUserAnswer('')
    }
  }, [task?.id])

  useEffect(() => {
    if (!task?.id) return
    const timer = window.setTimeout(() => {
      try {
        if (userAnswer.trim()) {
          sessionStorage.setItem(`needs-user-draft-${task.id}`, userAnswer)
        } else {
          sessionStorage.removeItem(`needs-user-draft-${task.id}`)
        }
      } catch {
        /* ignore */
      }
    }, 400)
    return () => window.clearTimeout(timer)
  }, [task?.id, userAnswer])

  const safeTask = useMemo(() => (task ? sanitizeTaskForUi(task) : null), [task])

  if (!task || !safeTask) return null
  const files = deriveTaskFiles(safeTask)
  const filesFromTranscriptOnly = (safeTask.files ?? []).length === 0 && files.length > 0
  const decisions = [...(safeTask.decisions ?? [])].reverse()
  const allTranscript = [...(safeTask.transcript ?? [])].reverse()
  const transcriptFailureCount = allTranscript.filter(isTranscriptFailure).length
  const decisionFailureCount = decisions.filter((d) => d.type === 'tool_fail').length
  const totalFailureCount = transcriptFailureCount + decisionFailureCount
  const transcriptCount = allTranscript.length
  const transcriptCollapsedDefault = transcriptCount > 20 && totalFailureCount === 0
  const filteredTranscript = showFailuresOnly
    ? allTranscript.filter(isTranscriptFailure)
    : allTranscript
  const visibleTranscript = showAllTranscript ? filteredTranscript : filteredTranscript.slice(0, 50)
  const acList = formatAcceptanceCriteria(safeTask.acceptanceCriteria)
  const blockedBy = safeTask.blockedBy ?? []
  const subtaskIds = safeTask.subtaskIds ?? []
  const relatedTaskIds = safeTask.relatedTaskIds ?? []
  const diagnosis = safeTask.lastDiagnosis
  const commandDiagnostics = getCommandDiagnostics(safeTask)
  const stuckLoopCount = (safeTask.decisions ?? []).filter((d) => d.type === 'stuck_loop').length
  const lastFailedTool = [...(safeTask.transcript ?? [])]
    .reverse()
    .find((e) => e.toolSuccess === false || (e.role === 'tool' && /FAILED|✗/i.test(e.content ?? '')))
  const needsUserReason =
    safeTask.needsUserReason?.trim() ||
    deriveNeedsUserReason(safeTask).split('\n')[0] ||
    'The agent could not proceed without your input.'
  const needsUserAction =
    safeTask.needsUserAction?.trim() ||
    safeTask.userQuestion?.trim() ||
    'Describe the missing information or decision needed to continue.'
  const priorUserAnswers = safeTask.userResolutions ?? []
  const isDuplicateQuestion = safeTask.needsUserDuplicate === true
  const workLabel =
    safeTask.workType === 'planning' || safeTask.requiresDev === false
      ? 'PO only'
      : safeTask.requiresQa === false
        ? 'Dev (no QA)'
        : 'Dev + QA'
  const canMoveToInProgress =
    Boolean(onMoveToInProgress) &&
    taskLane != null &&
    taskLane !== 'In Progress' &&
    taskLane !== 'Done' &&
    (taskLane === 'Backlog' || taskLane === 'Refinement')
  const showSkipRefinementOption =
    requireBacklogRefinement &&
    (taskLane === 'Refinement' || safeTask.refinementComplete === false)

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
              <h3 className="text-base font-bold text-white truncate">{safeTask.title}</h3>
            )}
            <p className="text-[10px] text-indigo-300 font-mono mt-0.5">
              {task.id} · {task.status}
              {task.priority != null && ` · P${task.priority}`}
              {(task.poRoundTrips ?? 0) > 0 && (
                <span className="ml-2 text-amber-400">PO↔Dev ×{task.poRoundTrips}</span>
              )}
              <span className="ml-2 text-violet-300/90">{workLabel}</span>
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

          {canMoveToInProgress && onMoveToInProgress && taskLane && (
            <div className="bg-emerald-950/20 border border-emerald-500/30 rounded-lg p-3 space-y-2">
              <h4 className="text-xs font-bold text-emerald-200">Run implementation now</h4>
              <p className="text-[10px] text-cat-subtext">
                Move this card to In Progress so the next sprint step runs dev work before more
                refinement.
              </p>
              {showSkipRefinementOption && (
                <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
                  <input
                    type="checkbox"
                    checked={skipRemainingRefinement}
                    onChange={(e) => setSkipRemainingRefinement(e.target.checked)}
                    className="rounded border-cat-surface1"
                  />
                  Skip remaining refinement
                </label>
              )}
              <button
                type="button"
                disabled={movingToProgress || sprintRunning}
                onClick={() => {
                  setMovingToProgress(true)
                  void Promise.resolve(
                    onMoveToInProgress(task.id, taskLane, skipRemainingRefinement || undefined),
                  ).finally(() => setMovingToProgress(false))
                }}
                className="w-full bg-emerald-600/40 hover:bg-emerald-600/60 disabled:opacity-50 text-emerald-100 text-xs py-2 px-3 rounded-lg border border-emerald-500/30"
              >
                {movingToProgress ? 'Moving…' : 'Move to In Progress'}
              </button>
            </div>
          )}

          {diagnosis && (
            <CollapsibleSection title="Diagnosis" defaultOpen>
              <p className="text-[11px] text-white mb-2">{diagnosis.summary}</p>
              <p className="text-[11px] text-rose-200/90 mb-1">
                <strong className="font-normal text-cat-subtext">Problem: </strong>
                {diagnosis.problem}
              </p>
              <p className="text-[11px] text-cat-subtext mb-1">
                Root cause: <span className="text-amber-200">{diagnosis.rootCause}</span>
              </p>
              <p className="text-[11px] text-emerald-200/90 mb-2">{diagnosis.recommendedAction}</p>
              {diagnosis.evidence?.length > 0 && (
                <ul className="text-[10px] text-cat-overlay list-disc pl-4 space-y-0.5 mb-2">
                  {diagnosis.evidence.map((ev, i) => (
                    <li key={i}>{ev}</li>
                  ))}
                </ul>
              )}
              {onOpenModelTab && (
                <button
                  type="button"
                  onClick={onOpenModelTab}
                  className="text-[10px] text-indigo-400 hover:text-indigo-300"
                >
                  View diagnosis prompt in Model tab →
                </button>
              )}
            </CollapsibleSection>
          )}

          {taskLane !== 'Done' && (onDiagnose || onRetryStep) && (
            <div className="flex flex-wrap gap-2">
              {onDiagnose && (
                <button
                  type="button"
                  disabled={diagnosing || sprintRunning}
                  onClick={() => {
                    setDiagnosing(true)
                    void Promise.resolve(onDiagnose(task.id)).finally(() => setDiagnosing(false))
                  }}
                  className="text-xs px-3 py-1.5 rounded border border-amber-500/40 text-amber-200 hover:bg-amber-950/30 disabled:opacity-50"
                >
                  {diagnosing ? 'Diagnosing…' : 'Diagnose card'}
                </button>
              )}
              {onRetryStep && (
                <>
                  <button
                    type="button"
                    disabled={retrying || sprintRunning}
                    onClick={() => {
                      setRetrying(true)
                      void Promise.resolve(onRetryStep(task.id, 'same')).finally(() =>
                        setRetrying(false),
                      )
                    }}
                    className="text-xs px-3 py-1.5 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/30 disabled:opacity-50"
                  >
                    Retry step
                  </button>
                  <button
                    type="button"
                    disabled={retrying || sprintRunning}
                    onClick={() => {
                      setRetrying(true)
                      void Promise.resolve(onRetryStep(task.id, 'optimized')).finally(() =>
                        setRetrying(false),
                      )
                    }}
                    className="text-xs px-3 py-1.5 rounded border border-violet-500/40 text-violet-200 hover:bg-violet-950/30 disabled:opacity-50"
                  >
                    Retry (optimized)
                  </button>
                  <button
                    type="button"
                    disabled={retrying || sprintRunning}
                    onClick={() => {
                      setRetrying(true)
                      void Promise.resolve(onRetryStep(task.id, 'fix_and_verify')).finally(() =>
                        setRetrying(false),
                      )
                    }}
                    className="text-xs px-3 py-1.5 rounded border border-emerald-500/40 text-emerald-200 hover:bg-emerald-950/30 disabled:opacity-50"
                  >
                    Fix &amp; verify
                  </button>
                </>
              )}
            </div>
          )}

          <CollapsibleSection title="Associated Files" badge={files.length} defaultOpen>
            <div className="overflow-y-auto space-y-1 max-h-48">
              {files.length === 0 ? (
                <p className="text-[11px] text-cat-overlay italic">
                  No files yet — files appear after the agent reads or edits workspace files during
                  a sprint.
                </p>
              ) : (
                files.map((f, i) => (
                  <div key={`${f.path}-${i}`} className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => {
                        onOpenFile(getTaskFilePath(f))
                        onClose()
                      }}
                      className="flex-1 text-left text-[11px] font-mono bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 hover:border-indigo-500/50 text-indigo-300 flex items-center justify-between gap-2"
                    >
                      <span className="truncate">{getTaskFilePath(f)}</span>
                      {f.action && (
                        <span
                          className={`shrink-0 text-[9px] uppercase px-1.5 py-0.5 rounded ${fileActionBadgeClass(f.action)}`}
                        >
                          {f.action}
                        </span>
                      )}
                    </button>
                    {onViewFileDiff && (
                      <button
                        type="button"
                        title="View diff"
                        onClick={() => void onViewFileDiff(getTaskFilePath(f))}
                        className="shrink-0 text-[10px] px-2 py-1 rounded border border-cat-surface1 text-cat-overlay hover:text-white"
                      >
                        Diff
                      </button>
                    )}
                  </div>
                ))
              )}
            </div>
            {filesFromTranscriptOnly && files.length > 0 && (
              <p className="text-[10px] text-cat-overlay italic mt-2">Derived from tool transcript</p>
            )}
          </CollapsibleSection>

          {commandDiagnostics.length > 0 && (
            <CollapsibleSection
              title="Command diagnostics"
              badge={commandDiagnostics.length}
              defaultOpen
            >
              <div className="overflow-x-auto">
                <table className="w-full text-[10px] font-mono border-collapse">
                  <thead>
                    <tr className="text-cat-overlay text-left border-b border-cat-surface1">
                      <th className="py-1 pr-2">File</th>
                      <th className="py-1 pr-2">Line</th>
                      <th className="py-1 pr-2">Severity</th>
                      <th className="py-1">Message</th>
                    </tr>
                  </thead>
                  <tbody>
                    {commandDiagnostics.map((diag, index) => (
                      <tr key={`${diag.file}:${diag.line}:${index}`} className="border-b border-cat-surface1/40">
                        <td className="py-1 pr-2 text-indigo-300 whitespace-nowrap">{diag.file}</td>
                        <td className="py-1 pr-2 text-cat-subtext">{diag.line}</td>
                        <td className="py-1 pr-2 uppercase text-amber-200">{diag.severity}</td>
                        <td className="py-1 text-cat-subtext break-all">{diag.message}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CollapsibleSection>
          )}

          {blockedBy.length > 0 && (
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-cat-subtext mb-1">
                Blocked By
              </h4>
              <p className="text-[11px] font-mono text-orange-300">{blockedBy.join(', ')}</p>
            </div>
          )}

          {(subtaskIds.length > 0 || safeTask.parentTaskId) && (
            <div className="bg-sky-950/30 border border-sky-500/30 rounded-lg p-3">
              <h4 className="text-xs font-bold text-sky-300 mb-1">Todo hierarchy</h4>
              {safeTask.parentTaskId && (
                <p className="text-[11px] text-white mb-2">
                  Parent:{' '}
                  <button
                    type="button"
                    onClick={() => onRelatedTaskClick?.(safeTask.parentTaskId!)}
                    className="font-mono text-sky-300 hover:underline"
                  >
                    {safeTask.parentTaskId}
                    {getTaskTitle?.(safeTask.parentTaskId) ? ` — ${getTaskTitle(safeTask.parentTaskId)}` : ''}
                  </button>
                </p>
              )}
              {subtaskIds.length > 0 && (
                <ul className="text-[11px] text-white space-y-1">
                  {subtaskIds.map((sid) => (
                    <li key={sid}>
                      <button
                        type="button"
                        onClick={() => onRelatedTaskClick?.(sid)}
                        className="font-mono text-sky-300 hover:underline text-left"
                      >
                        {sid}
                        {getTaskTitle?.(sid) ? ` — ${getTaskTitle(sid)}` : ''}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {(safeTask.subtaskSpawnCount ?? 0) > 0 && (
                <p className="text-[10px] text-cat-subtext mt-2">
                  Subtask rounds: {safeTask.subtaskSpawnCount}
                </p>
              )}
              {onEscapeSubtasks &&
                ((safeTask.subtaskSpawnCount ?? 0) >= 3 || subtaskIds.length > 0) && (
                  <button
                    type="button"
                    onClick={() => void onEscapeSubtasks(task.id)}
                    className="mt-2 text-[10px] px-2 py-1 rounded border border-amber-500/40 text-amber-300 hover:bg-amber-950/40"
                  >
                    Escape subtask loop → Needs PO
                  </button>
                )}
            </div>
          )}

          {relatedTaskIds.length > 0 && (
            <CollapsibleSection title="Related Features" badge={relatedTaskIds.length} defaultOpen>
              <div className="space-y-1">
                {relatedTaskIds.map((relatedId) => (
                  <button
                    key={relatedId}
                    type="button"
                    onClick={() => onRelatedTaskClick?.(relatedId)}
                    className="w-full text-left text-[11px] font-mono bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 hover:border-indigo-500/50 text-indigo-300"
                  >
                    {relatedId}
                    {getTaskTitle?.(relatedId) && (
                      <span className="text-cat-subtext font-sans ml-2">
                        — {getTaskTitle(relatedId)}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </CollapsibleSection>
          )}

          {safeTask.gitCommit?.hash && <GitCommitSection commit={safeTask.gitCommit} />}

          {(taskLane === 'Refinement' ||
            safeTask.refinementStatus ||
            (safeTask.refinementQuestions?.length ?? 0) > 0 ||
            safeTask.refinementNotes) && (
            <div className="bg-violet-950/30 border border-violet-500/30 rounded-lg p-3">
              <h4 className="text-xs font-bold text-violet-300 mb-1">Refinement</h4>
              {safeTask.refinementStatus && (
                <p className="text-[11px] text-white">
                  Status: {safeTask.refinementStatus.replace('_', ' ')}
                  {safeTask.refinementComplete ? ' · ready for dev' : ''}
                </p>
              )}
              {(safeTask.refinementRoundTrips ?? 0) > 0 && (
                <p className="text-[10px] text-cat-subtext mt-1">
                  Round {safeTask.refinementRoundTrips}
                  {maxRefinementRoundTrips != null ? ` / ${maxRefinementRoundTrips}` : ''}
                </p>
              )}
              {(safeTask.refinementQuestions?.length ?? 0) > 0 && (
                <div className="mt-2">
                  <p className="text-[10px] text-violet-200 font-semibold">Developer questions</p>
                  <ul className="text-[11px] text-white list-disc pl-4 mt-1">
                    {safeTask.refinementQuestions!.map((q) => (
                      <li key={q}>{q}</li>
                    ))}
                  </ul>
                </div>
              )}
              {safeTask.spikeReport && (
                <div className="mt-2">
                  <p className="text-[10px] text-cyan-200 font-semibold">Spike report</p>
                  <p className="text-[11px] text-cat-subtext mt-1 whitespace-pre-wrap">
                    {safeTask.spikeReport}
                  </p>
                </div>
              )}
              {safeTask.refinementNotes && (
                <p className="text-[11px] text-cat-subtext mt-2 whitespace-pre-wrap">
                  {safeTask.refinementNotes}
                </p>
              )}
            </div>
          )}

          {safeTask.qaFailure && (
            <div className="bg-rose-950/30 border border-rose-500/30 rounded-lg p-3">
              <h4 className="text-xs font-bold text-rose-300 mb-1">Last QA Failure</h4>
              <p className="text-[11px] text-white max-h-16 overflow-y-auto">{safeTask.qaFailure.reason}</p>
              {safeTask.qaFailure.output && (
                <pre className="text-[10px] text-cat-subtext mt-1 whitespace-pre-wrap font-mono max-h-24 overflow-y-auto">
                  {safeTask.qaFailure.output}
                </pre>
              )}
              <p className="text-[10px] text-cat-overlay mt-1">{safeTask.qaFailure.timestamp}</p>
            </div>
          )}

          {safeTask.qaEvidence && (
            <div
              className={`rounded-lg p-3 border ${
                safeTask.qaEvidence.passed
                  ? 'bg-emerald-950/20 border-emerald-500/30'
                  : 'bg-amber-950/20 border-amber-500/30'
              }`}
            >
              <h4 className="text-xs font-bold text-cat-subtext mb-1">QA test evidence</h4>
              <p className="text-[11px] text-white">
                {safeTask.qaEvidence.playbookRun
                  ? `Playbook: ${safeTask.qaEvidence.passed ? 'passed' : 'failed or incomplete'}`
                  : 'No automated playbook detected for this project'}
              </p>
              {safeTask.qaEvidence.commands.length > 0 && (
                <ul className="text-[10px] text-cat-subtext mt-1 list-disc pl-4">
                  {safeTask.qaEvidence.commands.map((cmd) => (
                    <li key={cmd}>{cmd}</li>
                  ))}
                </ul>
              )}
              {!safeTask.qaEvidence.passed && safeTask.qaEvidence.playbookRun && (
                <p className="text-[10px] text-amber-300 mt-1">Tests must pass before Done.</p>
              )}
              {safeTask.qaEvidence.userOverride && (
                <p className="text-[10px] text-emerald-300 mt-1">User-provided evidence accepted.</p>
              )}
            </div>
          )}

          {onInjectToolEvidence &&
            (taskLane === 'In Progress' ||
              taskLane === 'QA' ||
              (safeTask.qaEvidence && !safeTask.qaEvidence.passed)) && (
              <div className="bg-indigo-950/20 border border-indigo-500/30 rounded-lg p-3 space-y-2">
                <h4 className="text-xs font-bold text-indigo-200">Provide command output</h4>
                <p className="text-[10px] text-cat-subtext">
                  Paste analyze or test output so the agent can continue on the next sprint step.
                </p>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] uppercase text-cat-overlay">Command</span>
                  <input
                    type="text"
                    value={injectCommand}
                    onChange={(e) => setInjectCommand(e.target.value)}
                    className="bg-cat-base border border-cat-surface1 rounded px-2 py-1 text-[11px] text-white"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] uppercase text-cat-overlay">Output</span>
                  <textarea
                    value={injectOutput}
                    onChange={(e) => setInjectOutput(e.target.value)}
                    rows={5}
                    placeholder="Analyzing project…&#10;warning • …&#10;error • …"
                    className="bg-cat-base border border-cat-surface1 rounded px-2 py-1 text-[11px] text-white font-mono"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] uppercase text-cat-overlay">Note (optional)</span>
                  <input
                    type="text"
                    value={injectNote}
                    onChange={(e) => setInjectNote(e.target.value)}
                    className="bg-cat-base border border-cat-surface1 rounded px-2 py-1 text-[11px] text-white"
                  />
                </label>
                <button
                  type="button"
                  disabled={injecting || !injectOutput.trim()}
                  onClick={() => {
                    setInjecting(true)
                    void Promise.resolve(
                      onInjectToolEvidence(task.id, {
                        toolName: 'run_command',
                        toolArgs: { command: injectCommand.trim() || 'flutter analyze' },
                        toolOutput: injectOutput.trim(),
                        note: injectNote.trim() || undefined,
                      }),
                    ).finally(() => setInjecting(false))
                  }}
                  className="w-full bg-indigo-600/40 hover:bg-indigo-600/60 disabled:opacity-50 text-indigo-100 text-xs py-2 px-3 rounded-lg border border-indigo-500/30"
                >
                  {injecting ? 'Injecting…' : 'Inject & continue'}
                </button>
              </div>
            )}

          {onSplit && taskLane !== 'Done' && (
            <div className="space-y-1">
              <p className="text-[10px] text-cat-overlay leading-relaxed">
                Splits this card into subtasks on the backlog (same as the PO agent{' '}
                <span className="font-mono text-violet-300">add_backlog_tasks</span> tool).
              </p>
              <button
              type="button"
              disabled={sprintRunning || splitting}
              title={
                sprintRunning
                  ? 'Wait for the current sprint step to finish'
                  : 'Split this card into smaller backlog tasks via the Product Owner'
              }
              onClick={() => {
                setSplitting(true)
                void Promise.resolve(onSplit(task.id)).finally(() => setSplitting(false))
              }}
              className="w-full bg-violet-950/40 hover:bg-violet-950/60 disabled:opacity-50 text-violet-200 text-xs py-2 px-3 rounded-lg border border-violet-500/30"
            >
              {splitting ? 'Splitting…' : 'Split into subtasks'}
            </button>
            </div>
          )}

          {onDiscussWithAgent && (
            <button
              type="button"
              onClick={() => onDiscussWithAgent(task, taskLane)}
              className="w-full bg-indigo-950/40 hover:bg-indigo-950/60 text-indigo-200 text-xs py-2 px-3 rounded-lg border border-indigo-500/30"
            >
              Discuss with agent…
              {(taskLane === 'Needs User' || taskLane === 'Needs PO') && (
                <span className="text-indigo-400/80"> (opens PO chat — can split into subtasks)</span>
              )}
            </button>
          )}

          {taskLane === 'Needs User' && onResolveUser && (
            <div className="bg-amber-950/20 border border-amber-500/30 rounded-lg p-3 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-xs font-bold text-amber-300">Why this needs you</h4>
                {isDuplicateQuestion && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-rose-950/50 text-rose-300 border border-rose-500/40">
                    Same question again?
                  </span>
                )}
              </div>
              <p className="text-[11px] text-amber-100/90 whitespace-pre-wrap">{needsUserReason}</p>
              <div className="text-[10px] space-y-1">
                <p className="text-amber-200 font-semibold">What to provide</p>
                <p className="text-amber-100/80 whitespace-pre-wrap">{needsUserAction}</p>
              </div>
              {stuckLoopCount > 0 && (
                <p className="text-[10px] text-amber-300/80">
                  Stuck loop rounds: {stuckLoopCount}
                </p>
              )}
              {lastFailedTool && (
                <p className="text-[10px] text-rose-300/90">
                  Last failed tool: {lastFailedTool.toolName ?? 'unknown'}
                </p>
              )}
              {priorUserAnswers.length > 0 && (
                <div className="text-[10px] space-y-1">
                  <button
                    type="button"
                    onClick={() => setShowPriorAnswers((o) => !o)}
                    className="text-amber-200 font-semibold hover:text-amber-100"
                  >
                    {showPriorAnswers ? 'Hide' : 'Show'} prior answers ({priorUserAnswers.length})
                  </button>
                  {showPriorAnswers && (
                    <ul className="space-y-2 max-h-32 overflow-y-auto">
                      {[...priorUserAnswers].reverse().map((res, i) => (
                        <li
                          key={`${res.timestamp}-${i}`}
                          className="text-amber-100/80 border border-amber-500/20 rounded p-1.5"
                        >
                          <p className="text-amber-200/90 font-semibold">Q: {res.question}</p>
                          <p className="whitespace-pre-wrap">A: {res.answer}</p>
                          <p className="text-amber-400/70 text-[9px]">
                            → {res.targetLane} · {res.timestamp}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              {commandDiagnostics.length > 0 && (
                <div className="text-[10px] space-y-1">
                  <p className="text-amber-200 font-semibold">Top lint issues</p>
                  <ul className="space-y-0.5 max-h-24 overflow-y-auto">
                    {commandDiagnostics.slice(0, 5).map((d, i) => (
                      <li key={i} className="text-amber-100/70 font-mono">
                        {d.severity} · {d.file}:{d.line} — {d.message}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <textarea
                value={userAnswer}
                onChange={(e) => setUserAnswer(e.target.value)}
                placeholder="Your answer for the Developer…"
                className="w-full text-xs bg-cat-base border border-cat-surface1 rounded p-2 min-h-[60px]"
              />
              <div className="flex flex-wrap gap-2 pt-1">
                {(
                  [
                    { target: 'dev' as const, label: 'Send to Developer', className: 'bg-amber-600 hover:bg-amber-500' },
                    ...(requireBacklogRefinement ||
                    safeTask.refinementStatus ||
                    safeTask.refinementRoundTrips
                      ? [
                          {
                            target: 'refinement' as const,
                            label: 'Send to Refinement',
                            className: 'bg-violet-700 hover:bg-violet-600',
                          },
                        ]
                      : []),
                    {
                      target: 'po' as const,
                      label: 'Send to Product Owner',
                      className: 'bg-indigo-700 hover:bg-indigo-600',
                    },
                  ] as const
                ).map(({ target, label, className }) => (
                  <button
                    key={target}
                    type="button"
                    disabled={!userAnswer.trim()}
                    onClick={() => {
                      try {
                        sessionStorage.removeItem(`needs-user-draft-${task.id}`)
                      } catch {
                        /* ignore */
                      }
                      onResolveUser(task.id, userAnswer.trim(), target)
                      setUserAnswer('')
                    }}
                    className={`${className} disabled:opacity-50 text-white text-xs py-1.5 px-3 rounded-lg`}
                  >
                    {label}
                  </button>
                ))}
              </div>
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

          <CollapsibleSection title="Agent Decisions" badge={decisions.length} defaultOpen={decisions.length <= 10}>
            <div className="overflow-y-auto space-y-2 max-h-40 pr-1">
              {decisions.length === 0 ? (
                <p className="text-[11px] text-cat-overlay italic">None yet</p>
              ) : (
                decisions.map((d, i) => (
                  <div
                    key={i}
                    className={`bg-cat-base border rounded-lg p-2 text-[11px] ${
                      d.type === 'tool_fail'
                        ? 'border-rose-500/50 bg-rose-950/20'
                        : 'border-cat-surface1'
                    }`}
                  >
                    <div className="flex justify-between text-[10px] text-cat-overlay mb-1 gap-2">
                      <span className="flex items-center gap-2">
                        {d.type === 'tool_fail' && (
                          <span className="text-[9px] font-bold uppercase px-1 py-0.5 rounded bg-rose-900/60 text-rose-200">
                            FAILED
                          </span>
                        )}
                        <span>{d.agent} · {d.type}</span>
                      </span>
                      <span>{d.timestamp}</span>
                    </div>
                    <p className={d.type === 'tool_fail' ? 'text-rose-100' : 'text-white'}>
                      {d.summary}
                    </p>
                    {d.detail && (
                      <p
                        className={`mt-1 whitespace-pre-wrap text-[10px] max-h-20 overflow-y-auto ${
                          d.type === 'tool_fail' ? 'text-rose-200/90' : 'text-cat-subtext'
                        }`}
                      >
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
            badge={
              totalFailureCount > 0
                ? `${transcriptCount} · ${totalFailureCount} failed`
                : transcriptCount
            }
            defaultOpen={!transcriptCollapsedDefault}
          >
            {totalFailureCount > 0 && (
              <div className="mb-2 p-2 rounded-lg border border-rose-500/40 bg-rose-950/25 text-[10px] text-rose-100">
                <span className="font-bold uppercase text-rose-300 mr-2">
                  {totalFailureCount} tool failure{totalFailureCount === 1 ? '' : 's'}
                </span>
                Red entries below show failed read/write/run commands. Check Agent Decisions for
                summaries.
              </div>
            )}
            <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
              {transcriptFailureCount > 0 && (
                <button
                  type="button"
                  onClick={() => setShowFailuresOnly((v) => !v)}
                  className={`text-[10px] px-2 py-0.5 rounded border ${
                    showFailuresOnly
                      ? 'border-rose-500/50 text-rose-300 bg-rose-950/30'
                      : 'border-cat-surface1 text-indigo-400 hover:text-indigo-300'
                  }`}
                >
                  {showFailuresOnly ? 'Show all' : `Failures only (${transcriptFailureCount})`}
                </button>
              )}
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
                <p className="text-[11px] text-cat-overlay italic">
                  {showFailuresOnly ? 'No failed tool entries in transcript' : 'Empty'}
                </p>
              ) : (
                visibleTranscript.map((entry, i) => {
                  const failed = isTranscriptFailure(entry)
                  return (
                  <div
                    key={i}
                    className={`text-[10px] font-mono bg-cat-base border rounded p-2 ${
                      failed
                        ? 'border-rose-500/60 bg-rose-950/25 ring-1 ring-rose-500/20'
                        : entry.role === 'tool' && entry.toolSuccess === true
                          ? 'border-emerald-500/40'
                          : 'border-cat-surface1'
                    }`}
                  >
                    <div className="text-cat-overlay mb-0.5 flex items-center gap-2 flex-wrap">
                      <span>
                        [{entry.timestamp}] {entry.agent ?? entry.role}
                      </span>
                      {failed && (
                        <span className="text-[9px] font-bold uppercase px-1 py-0.5 rounded bg-rose-900/60 text-rose-200">
                          FAILED
                        </span>
                      )}
                      {entry.role === 'tool' && entry.toolSuccess != null && !failed && (
                        <span className="text-emerald-400 font-bold">OK</span>
                      )}
                      {entry.toolName && (
                        <span className={failed ? 'text-rose-300' : 'text-indigo-300'}>
                          {entry.toolName}
                        </span>
                      )}
                    </div>
                    <p
                      className={`whitespace-pre-wrap max-h-24 overflow-y-auto ${
                        failed ? 'text-rose-100' : 'text-cat-subtext'
                      }`}
                    >
                      {entry.content}
                    </p>
                    {entry.toolOutput && failed && (
                      <pre className="mt-1 text-[9px] text-rose-200/90 whitespace-pre-wrap max-h-20 overflow-y-auto border-t border-rose-500/30 pt-1">
                        {entry.toolOutput}
                      </pre>
                    )}
                    {entry.toolArgs && Object.keys(entry.toolArgs).length > 0 && (
                      <pre className="mt-1 text-[9px] text-cat-overlay overflow-x-auto">
                        {JSON.stringify(entry.toolArgs, null, 2)}
                      </pre>
                    )}
                  </div>
                  )
                })
              )}
            </div>
          </CollapsibleSection>
        </div>
      </div>
    </div>
  )
}
