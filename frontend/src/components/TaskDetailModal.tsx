import { useEffect, useMemo, useState, type ReactNode } from 'react'
import type {
  AgentRunState,
  BoardLane,
  CommandDiagnostic,
  LastSprintContextSources,
  Task,
  TaskFile,
  TaskFlowSummaryResponse,
  TaskGitCommit,
  TaskTranscriptEntry,
} from '../types'
import { fetchTaskFlowSummary } from '../api/client'
import { formatAcceptanceCriteria, formatTaskText, deriveTaskFiles, sanitizeTaskForUi, formatQaPair, isGenericNeedsUserText } from '../utils/taskFormat'
import { incompleteDevProgressLabels, taskLooksIncompleteOnDone } from '../utils/taskDoneAudit'
import {
  formatDurationMs,
  formatTokensLine,
} from '../utils/agentUsageFormat'
import { formatToolBreakdown, formatWorkItemCounts } from '../utils/flowCounts'
import SlideOver from './SlideOver'
import TaskFlowPanel from './TaskFlowPanel'
import DevPhaseStepper from './DevPhaseStepper'
import DevPhaseGraphPanel from './DevPhaseGraphPanel'
import FieldHistoryButton from './FieldHistoryButton'

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
    specFields?: {
      userStory?: string
      scope?: string
      outOfScope?: string
      testPlan?: string
      actualSummary?: string
    },
  ) => void
  onAcChecklistChange?: (taskId: string, acChecklist: boolean[]) => void
  onDelete: (taskId: string) => void
  onRaiseSemanticMinScore?: () => void
  onReindexCodebase?: () => void
  onClearTranscript?: (taskId: string) => void
  onApprove?: (taskId: string) => void
  onResolveUser?: (taskId: string, answer: string, target: 'dev' | 'refinement' | 'po') => void
  onDiscussWithAgent?: (task: Task, lane: BoardLane | null) => void
  /** Open PO chat pinned to this card with a seeded rewrite prompt for the description. */
  onClarifyWithPo?: (task: Task) => void
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
  /** Workspace-detected analyze/lint command; empty if unknown. */
  defaultInjectCommand?: string
  onRelatedTaskClick?: (taskId: string) => void
  getTaskTitle?: (taskId: string) => string | undefined
  taskExistsOnBoard?: (taskId: string) => boolean
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
  onRunInProgressStep?: (taskId: string) => void | Promise<void>
  onAddFeatureFollowUp?: (feature: Task) => void
  onFocusAdvance?: (taskId: string) => void | Promise<void>
  onFocusReset?: (taskId: string) => void | Promise<void>
  onClearToolFingerprints?: (taskId: string) => void | Promise<void>
  /** True while an agent step is actively running on this card (for suggested focus highlight). */
  isAgentRunningOnTask?: boolean
  /** Live agent run (for Phase Graph stepper). */
  activeRun?: AgentRunState | null
  /** Bumps when tools/LLM events fire (SSE) so Flow can refresh without closing the section. */
  flowActivityKey?: string | number
  /** Last sprint step prompt inject snapshot (Qdrant / file preload / packer). */
  lastSprintContextSources?: LastSprintContextSources | null
}

function formatContextSourcesLine(cs: LastSprintContextSources): string {
  const parts: string[] = []
  if (cs.localSlmProfile) parts.push('profile=local_slm (bounded preload)')
  parts.push(
    cs.semanticUsed
      ? `semantic=${cs.semanticChunkCount ?? 0} chunk(s)`
      : 'semantic=off',
  )
  parts.push(`files=${cs.filePreloadCount ?? 0}`)
  if (cs.graphUsed) parts.push('graph=on')
  const pack = (cs.contextPacker || 'off').toLowerCase()
  if (pack !== 'off') {
    parts.push(`packer=${pack} (${cs.contextPackerChars ?? 0} chars)`)
  } else {
    parts.push('packer=off')
  }
  parts.push(`qdrantIndex=${cs.qdrantIndexChunks ?? 0} chunks`)
  if (cs.agentRole) parts.push(`role=${cs.agentRole}`)
  return parts.join(' · ')
}

function CollapsibleSection({
  title,
  badge,
  defaultOpen = true,
  open: openProp,
  onOpenChange,
  headerExtra,
  children,
}: {
  title: string
  badge?: string | number
  defaultOpen?: boolean
  open?: boolean
  onOpenChange?: (open: boolean) => void
  headerExtra?: ReactNode
  children: ReactNode
}) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen)
  const controlled = openProp !== undefined
  const open = controlled ? Boolean(openProp) : internalOpen
  const setOpen = (next: boolean | ((prev: boolean) => boolean)) => {
    const value = typeof next === 'function' ? next(open) : next
    if (!controlled) setInternalOpen(value)
    onOpenChange?.(value)
  }
  return (
    <div className="border border-cat-surface1 rounded-lg overflow-hidden">
      <div className="w-full flex items-center justify-between px-3 py-2 bg-cat-base/50 hover:bg-cat-base">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="flex-1 flex items-center text-left min-w-0"
        >
          <span className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
            {title}
            {badge != null && (
              <span className="ml-2 text-[10px] font-mono text-indigo-300 normal-case">{badge}</span>
            )}
          </span>
        </button>
        <div className="flex items-center gap-1 shrink-0">
          {headerExtra}
          <button
            type="button"
            onClick={() => setOpen(!open)}
            className="text-cat-overlay text-[10px] px-1"
            aria-label={open ? 'Collapse' : 'Expand'}
          >
            <i className={`fa-solid fa-chevron-${open ? 'up' : 'down'}`} />
          </button>
        </div>
      </div>
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
  onAcChecklistChange,
  onDelete,
  onClearTranscript,
  onApprove,
  onResolveUser,
  onDiscussWithAgent,
  onClarifyWithPo,
  onSplit,
  onInjectToolEvidence,
  defaultInjectCommand = '',
  onRelatedTaskClick,
  getTaskTitle,
  taskExistsOnBoard,
  onDiagnose,
  onRetryStep,
  onViewFileDiff,
  onOpenModelTab,
  onRaiseSemanticMinScore,
  onReindexCodebase,
  maxRefinementRoundTrips,
  requireBacklogRefinement = false,
  onEscapeSubtasks,
  onMoveToInProgress,
  onRunInProgressStep,
  onAddFeatureFollowUp,
  onFocusAdvance,
  onFocusReset,
  onClearToolFingerprints,
  isAgentRunningOnTask = false,
  activeRun = null,
  flowActivityKey = '',
  lastSprintContextSources = null,
}: TaskDetailModalProps) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [acceptanceCriteria, setAcceptanceCriteria] = useState('')
  const [userStory, setUserStory] = useState('')
  const [scope, setScope] = useState('')
  const [outOfScope, setOutOfScope] = useState('')
  const [testPlan, setTestPlan] = useState('')
  const [actualSummary, setActualSummary] = useState('')
  const [editing, setEditing] = useState(false)
  const [userAnswer, setUserAnswer] = useState('')
  const [injectCommand, setInjectCommand] = useState(defaultInjectCommand)
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
  const [runningDevStep, setRunningDevStep] = useState(false)
  const [flowOpen, setFlowOpen] = useState(false)
  const [phaseGraphOpen, setPhaseGraphOpen] = useState(false)
  const [flowSummary, setFlowSummary] = useState<TaskFlowSummaryResponse | null>(null)

  useEffect(() => {
    if (!task) return
    setTitle(formatTaskText(task.title))
    setDescription(formatTaskText(task.description))
    setAcceptanceCriteria(formatAcceptanceCriteria(task.acceptanceCriteria).join('\n'))
    setUserStory(formatTaskText(task.userStory ?? ''))
    setScope(formatTaskText(task.scope ?? ''))
    setOutOfScope(formatTaskText(task.outOfScope ?? ''))
    setTestPlan(formatTaskText(task.testPlan ?? ''))
    setActualSummary(formatTaskText(task.actualSummary ?? ''))
    setEditing(false)
    setShowAllTranscript(false)
    setShowFailuresOnly(false)
    setInjectCommand(defaultInjectCommand)
    setInjectOutput('')
    setInjectNote('')
    setFlowOpen(false)
    setPhaseGraphOpen(false)
    try {
      const draft = sessionStorage.getItem(`needs-user-draft-${task.id}`)
      setUserAnswer(draft ?? '')
    } catch {
      setUserAnswer('')
    }
  }, [task?.id, defaultInjectCommand])

  // Counts-only flow view so each Agent progress row can show LLM/tool effort.
  const flowRefreshKey = `${task?.transcript?.length ?? 0}-${flowActivityKey}`
  useEffect(() => {
    if (!task?.id) {
      setFlowSummary(null)
      return
    }
    let cancelled = false
    void fetchTaskFlowSummary(task.id, { limit: 200 })
      .then((res) => {
        if (!cancelled) setFlowSummary(res)
      })
      .catch(() => {
        if (!cancelled) setFlowSummary(null)
      })
    return () => {
      cancelled = true
    }
  }, [task?.id, flowRefreshKey])

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
  const missingBlockers = blockedBy.filter(
    (id) => taskExistsOnBoard && !taskExistsOnBoard(id),
  )
  const dependencyOutcomes = safeTask.dependencyOutcomes ?? []
  const subtaskIds = safeTask.subtaskIds ?? []
  const relatedTaskIds = safeTask.relatedTaskIds ?? []
  const featureHistory = safeTask.featureHistory ?? []
  const childTaskIds = safeTask.childTaskIds ?? []
  const featureRollup = safeTask.featureRollup
  const rollupChildren = featureRollup?.children ?? []
  const rollupFiles = featureRollup?.files ?? []
  const rollupDecisions = featureRollup?.recentDecisions ?? []
  const parentFeatureId = safeTask.featureId
  const isFeatureEpic = safeTask.workType === 'feature' || taskLane === 'Features'
  const diagnosis = safeTask.lastDiagnosis
  const commandDiagnostics = getCommandDiagnostics(safeTask)
  const needsUserQuestion = (() => {
    const q = safeTask.userQuestion?.trim() || ''
    if (q && !isGenericNeedsUserText(q)) return q
    const derived = deriveNeedsUserReason(safeTask).trim()
    if (derived && !isGenericNeedsUserText(derived)) return derived
    return q || derived || 'What should Developer do next on this card?'
  })()
  const needsUserReason =
    (safeTask.needsUserReason?.trim() && !isGenericNeedsUserText(safeTask.needsUserReason)
      ? safeTask.needsUserReason.trim()
      : '') ||
    (safeTask.lastDiagnosis?.problem?.trim() || '') ||
    'The agents stopped because they cannot proceed without your answer. Round-trips to Product Owner are not the question — see How to move on.'
  const needsUserAction =
    (safeTask.needsUserAction?.trim() && !isGenericNeedsUserText(safeTask.needsUserAction)
      ? safeTask.needsUserAction.trim()
      : '') ||
    'Type a short answer below, then click Send to Developer to resume. Use Send to Product Owner only if you are rewriting the spec.'
  const suggestedTarget = safeTask.needsUserSuggestedTarget || 'dev'
  const priorUserAnswers = safeTask.userResolutions ?? []
  const isDuplicateQuestion = safeTask.needsUserDuplicate === true
  const workLabel = isFeatureEpic
    ? 'Feature epic (stationary)'
    : safeTask.workType === 'planning' || safeTask.requiresDev === false
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

  const doneEvidenceGap =
    taskLane === 'Done' && taskLooksIncompleteOnDone(safeTask)
  const doneGapLabels = doneEvidenceGap ? incompleteDevProgressLabels(safeTask) : []

  return (
    <SlideOver
      open
      onClose={onClose}
      side="right"
      hideHeader
      widthClass="w-full max-w-[min(560px,90vw)]"
      zIndexClass="z-50"
    >
      <div className="flex flex-col h-full min-h-0">
        <div className="sticky top-0 z-10 bg-cat-surface0 border-b border-cat-surface1 px-5 py-3 flex items-center justify-between shrink-0">
          <div className="min-w-0 flex-1 pr-4">
            <div className="flex items-start gap-1 min-w-0">
              {editing ? (
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="text-base font-bold text-white bg-cat-base border border-cat-surface1 rounded px-2 py-1 w-full min-w-0"
                />
              ) : (
                <h3 className="text-base font-bold text-white truncate min-w-0 flex-1">
                  {safeTask.title}
                </h3>
              )}
              <FieldHistoryButton taskId={task.id} field="title" className="mt-1" />
            </div>
            <p className="text-[10px] text-indigo-300 font-mono mt-0.5">
              {task.id} · {taskLane ?? task.status}
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
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg text-cat-subtext hover:text-white hover:bg-cat-surface1"
              aria-label="Close"
            >
              <i className="fa-solid fa-xmark" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3 min-h-0">
          {doneEvidenceGap && (
            <div
              className="rounded-lg border border-amber-500/40 bg-amber-950/25 p-3 space-y-1"
              data-testid="done-evidence-gap-banner"
            >
              <p className="text-xs font-semibold text-amber-200">Done on board — evidence may be incomplete</p>
              <p className="text-[11px] text-amber-100/85">
                Agent progress or acceptance criteria suggest this card was not fully verified. Use{' '}
                <strong>Audit Done</strong> on the kanban toolbar to review all Done cards, or move this
                card back to In Progress.
              </p>
              {doneGapLabels.length > 0 && (
                <p className="text-[10px] text-amber-200/80">
                  Pending: {doneGapLabels.join(', ')}
                </p>
              )}
            </div>
          )}
          {/* action-first: Needs User resolve */}
          {taskLane === 'Needs User' && onResolveUser && (
            <div className="bg-amber-950/20 border border-amber-500/30 rounded-lg p-3 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-xs font-bold text-amber-300">Question for you</h4>
                {isDuplicateQuestion && (
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-rose-950/50 text-rose-300 border border-rose-500/40">
                    Same question again?
                  </span>
                )}
              </div>
              <p className="text-[11px] text-amber-100/90 whitespace-pre-wrap">{needsUserQuestion}</p>
              <div className="text-[10px] space-y-1">
                <p className="text-amber-200 font-semibold">Why we stopped</p>
                <p className="text-amber-100/80 whitespace-pre-wrap">{needsUserReason}</p>
              </div>
              <div className="text-[10px] space-y-1">
                <p className="text-amber-200 font-semibold">How to move on</p>
                <p className="text-amber-100/80 whitespace-pre-wrap">{needsUserAction}</p>
              </div>
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
                placeholder={needsUserQuestion.slice(0, 180) || 'Your answer…'}
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
                ).map(({ target, label, className }) => {
                  const recommended = suggestedTarget === target
                  return (
                  <button
                    key={target}
                    type="button"
                    disabled={!userAnswer.trim()}
                    title={recommended ? 'Recommended for this card' : undefined}
                    onClick={() => {
                      try {
                        sessionStorage.removeItem(`needs-user-draft-${task.id}`)
                      } catch {
                        /* ignore */
                      }
                      onResolveUser(task.id, userAnswer.trim(), target)
                      setUserAnswer('')
                    }}
                    className={`${className} disabled:opacity-50 text-white text-xs py-1.5 px-3 rounded-lg ${
                      recommended ? 'ring-2 ring-white/70 ring-offset-1 ring-offset-amber-950' : 'opacity-90'
                    }`}
                  >
                    {recommended ? `${label} (recommended)` : label}
                  </button>
                  )
                })}
              </div>
            </div>
          )}
          <CollapsibleSection
            title="Description"
            defaultOpen
            headerExtra={<FieldHistoryButton taskId={task.id} field="description" />}
          >
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
            {onClarifyWithPo && (
              <button
                type="button"
                data-testid="clarify-with-po"
                onClick={() => onClarifyWithPo(task)}
                className="mt-2 w-full bg-amber-950/40 hover:bg-amber-950/60 text-amber-100 text-[11px] py-1.5 px-3 rounded-lg border border-amber-500/30"
              >
                Clarify description with PO…
              </button>
            )}
          </CollapsibleSection>

          <CollapsibleSection
            title="Specification (SDD)"
            defaultOpen={editing}
            headerExtra={<FieldHistoryButton taskId={task.id} field="sdd" />}
          >
            {(safeTask.sddInheritedFromFeature?.length ?? 0) > 0 && (
              <p className="text-[10px] text-violet-300/90 mb-2 font-mono">
                Inherited from feature {safeTask.featureId}:{' '}
                {safeTask.sddInheritedFromFeature!.join(', ')}
              </p>
            )}
            <p className="text-[10px] text-cat-overlay mb-2">
              Card fields generate <code className="text-cat-subtext">docs/tasks/…-spec.md</code> for
              agents and review. Working notes stay in the Q&A doc.
            </p>
            {(() => {
              const wt = (safeTask.workType ?? 'implementation').toLowerCase()
              const acN = acList.length
              const missing: string[] = []
              const warnings: string[] = []
              if (!formatTaskText(safeTask.description).trim()) missing.push('description')
              if (!wt) missing.push('workType')
              const minAc = wt === 'planning' || wt === 'spike' ? 0 : wt === 'implementation' ? 2 : 1
              if (acN < minAc && wt !== 'planning' && wt !== 'spike') {
                missing.push(`acceptanceCriteria (need ≥${minAc})`)
              }
              if (wt === 'implementation' && !formatTaskText(safeTask.userStory).trim()) {
                warnings.push('userStory recommended')
              }
              if (wt === 'implementation' && !formatTaskText(safeTask.scope).trim()) {
                warnings.push('scope recommended')
              }
              if (wt === 'implementation' && !formatTaskText(safeTask.testPlan).trim()) {
                warnings.push('testPlan recommended')
              }
              if (missing.length || warnings.length) {
                return (
                  <div className="mb-2 text-[10px] space-y-1">
                    {missing.length > 0 && (
                      <p className="text-amber-200/90 bg-amber-950/30 border border-amber-500/30 rounded px-2 py-1">
                        Spec gaps: {missing.join(', ')}
                      </p>
                    )}
                    {warnings.map((w) => (
                      <p
                        key={w}
                        className="text-cat-overlay bg-cat-base border border-cat-surface1 rounded px-2 py-0.5"
                      >
                        {w}
                      </p>
                    ))}
                  </div>
                )
              }
              return (
                <p className="text-[10px] text-emerald-200/80 mb-2">Spec readiness: OK for Dev</p>
              )
            })()}
            {editing ? (
              <div className="space-y-2">
                <label className="block">
                  <span className="text-[10px] text-cat-overlay">User story</span>
                  <input
                    type="text"
                    value={userStory}
                    onChange={(e) => setUserStory(e.target.value)}
                    placeholder="As a … I want … so that …"
                    className="w-full text-xs bg-cat-base border border-cat-surface1 rounded p-2 mt-0.5"
                  />
                </label>
                <label className="block">
                  <span className="text-[10px] text-cat-overlay">Scope (one bullet per line)</span>
                  <textarea
                    value={scope}
                    onChange={(e) => setScope(e.target.value)}
                    className="w-full text-xs font-mono bg-cat-base border border-cat-surface1 rounded p-2 min-h-[48px] max-h-28 overflow-y-auto mt-0.5"
                  />
                </label>
                <label className="block">
                  <span className="text-[10px] text-cat-overlay">Out of scope</span>
                  <textarea
                    value={outOfScope}
                    onChange={(e) => setOutOfScope(e.target.value)}
                    className="w-full text-xs font-mono bg-cat-base border border-cat-surface1 rounded p-2 min-h-[40px] max-h-24 overflow-y-auto mt-0.5"
                  />
                </label>
                <label className="block">
                  <span className="text-[10px] text-cat-overlay">Test plan</span>
                  <textarea
                    value={testPlan}
                    onChange={(e) => setTestPlan(e.target.value)}
                    placeholder="Commands or manual verify steps"
                    className="w-full text-xs font-mono bg-cat-base border border-cat-surface1 rounded p-2 min-h-[48px] max-h-28 overflow-y-auto mt-0.5"
                  />
                </label>
              </div>
            ) : (
              <div className="text-[11px] text-cat-subtext space-y-2 max-h-40 overflow-y-auto">
                <p>
                  <span className="text-cat-overlay">Story: </span>
                  {formatTaskText(safeTask.userStory) || '—'}
                </p>
                <p className="whitespace-pre-wrap">
                  <span className="text-cat-overlay">Scope: </span>
                  {formatTaskText(safeTask.scope) || '—'}
                </p>
                <p className="whitespace-pre-wrap">
                  <span className="text-cat-overlay">Out of scope: </span>
                  {formatTaskText(safeTask.outOfScope) || '—'}
                </p>
                <p className="whitespace-pre-wrap">
                  <span className="text-cat-overlay">Test plan: </span>
                  {formatTaskText(safeTask.testPlan) || '—'}
                </p>
                {(safeTask.specMarkdownPath || safeTask.id) && (
                  <button
                    type="button"
                    className="text-indigo-300 hover:text-indigo-200 underline text-[10px]"
                    onClick={() =>
                      onOpenFile(
                        safeTask.specMarkdownPath ||
                          `docs/tasks/${safeTask.id}-spec.md`,
                      )
                    }
                  >
                    Open spec markdown
                  </button>
                )}
              </div>
            )}
          </CollapsibleSection>

          {(() => {
            const items = safeTask.agentWorkItems ?? []
            if (!items.length) return null
            const done = items.filter((i) => i.status === 'done').length
            return (
              <CollapsibleSection
                title="Agent progress"
                badge={`${done}/${items.length}`}
                defaultOpen
              >
                <p className="text-[10px] text-cat-overlay mb-2">
                  Derived process checklist from card evidence (reads, writes, verify, lane) — not live
                  “current step” and not QA acceptance criteria. LLM/tool counts come from Flow aggregation.
                </p>
                <ul className="text-[11px] space-y-1.5" data-testid="agent-work-items">
                  {items.map((item) => {
                    const entry = flowSummary?.workItemIndex?.[item.id]
                    const counts = formatWorkItemCounts(entry)
                    const breakdown = formatToolBreakdown(entry)
                    return (
                    <li key={item.id}>
                      <div
                        id={`agent-work-item-${item.id}`}
                        className="flex items-start gap-2 rounded px-1 py-0.5"
                      >
                        <span
                          className={
                            item.status === 'done'
                              ? 'text-emerald-400'
                              : item.status === 'blocked'
                                ? 'text-rose-400'
                                : 'text-cat-overlay'
                          }
                          aria-hidden
                        >
                          {item.status === 'done' ? '☑' : item.status === 'blocked' ? '☒' : '☐'}
                        </span>
                        <span
                          className={
                            item.status === 'done'
                              ? 'text-emerald-200/80 line-through'
                              : item.status === 'blocked'
                                ? 'text-rose-200/90'
                                : 'text-cat-subtext'
                          }
                        >
                          {item.label}
                        </span>
                        {counts ? (
                          <span
                            className="ml-auto shrink-0 text-[9px] text-cat-overlay font-mono"
                            title={breakdown || undefined}
                            data-testid={`work-item-counts-${item.id}`}
                          >
                            {counts}
                          </span>
                        ) : entry?.toolLinked === false ? (
                          <span className="ml-auto shrink-0 text-[9px] text-cat-overlay italic">
                            board state
                          </span>
                        ) : null}
                      </div>
                      {breakdown && (
                        <p className="pl-6 text-[9px] text-cat-overlay font-mono truncate">
                          {breakdown}
                        </p>
                      )}
                    </li>
                    )
                  })}
                </ul>
              </CollapsibleSection>
            )
          })()}

          <CollapsibleSection
            title="Acceptance Criteria (QA)"
            badge={acList.length}
            defaultOpen
            headerExtra={<FieldHistoryButton taskId={task.id} field="acceptanceCriteria" />}
          >
            <p className="text-[10px] text-cat-overlay mb-2">
              For the QA agent / Done gate — check off when verifying the card.
            </p>
            {editing ? (
              <textarea
                value={acceptanceCriteria}
                onChange={(e) => setAcceptanceCriteria(e.target.value)}
                placeholder="One criterion per line"
                className="w-full text-xs font-mono bg-cat-base border border-cat-surface1 rounded p-2 min-h-[60px] max-h-32 overflow-y-auto"
              />
            ) : acList.length > 0 ? (
              <ul
                className="text-[11px] text-cat-subtext space-y-1 max-h-40 overflow-y-auto"
                data-testid="ac-checklist"
              >
                {acList.map((c, i) => {
                  const checks = safeTask.acChecklist ?? []
                  const checked = Boolean(checks[i])
                  return (
                    <li key={i} className="flex items-start gap-2">
                      <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={checked}
                        disabled={!onAcChecklistChange}
                        onChange={() => {
                          if (!onAcChecklistChange) return
                          const next = acList.map((_, j) =>
                            j === i ? !checked : Boolean(checks[j]),
                          )
                          onAcChecklistChange(task.id, next)
                        }}
                      />
                      <span className={checked ? 'text-emerald-200/90 line-through' : ''}>{c}</span>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className="text-[11px] text-cat-overlay italic">None defined</p>
            )}
          </CollapsibleSection>

          <CollapsibleSection
            title="Expected vs actual"
            badge={
              safeTask.acVerification?.length
                ? String(safeTask.acVerification.length)
                : undefined
            }
            defaultOpen
          >
            <div className="space-y-2 text-[11px]">
              <div>
                <p className="text-[10px] text-cat-overlay uppercase mb-0.5">Expected</p>
                <p className="text-cat-subtext whitespace-pre-wrap max-h-24 overflow-y-auto">
                  {formatTaskText(safeTask.expectedSummary) || '—'}
                </p>
              </div>
              <div>
                <p className="text-[10px] text-cat-overlay uppercase mb-0.5">Actual</p>
                {editing ? (
                  <textarea
                    value={actualSummary}
                    onChange={(e) => setActualSummary(e.target.value)}
                    placeholder="Optional override of rolled-up actual result"
                    className="w-full text-xs font-mono bg-cat-base border border-cat-surface1 rounded p-2 min-h-[48px] max-h-28 overflow-y-auto"
                  />
                ) : (
                  <p className="text-cat-subtext whitespace-pre-wrap max-h-24 overflow-y-auto">
                    {formatTaskText(safeTask.actualSummary) || '—'}
                  </p>
                )}
              </div>
              {(safeTask.acVerification?.length ?? 0) > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-[10px] border border-cat-surface1 rounded">
                    <thead>
                      <tr className="text-cat-overlay bg-cat-base">
                        <th className="text-left p-1">Criterion</th>
                        <th className="text-left p-1">Met</th>
                        <th className="text-left p-1">Actual</th>
                      </tr>
                    </thead>
                    <tbody>
                      {safeTask.acVerification!.map((row, i) => (
                        <tr key={i} className="border-t border-cat-surface1">
                          <td className="p-1 text-cat-subtext align-top">{row.criterion}</td>
                          <td className="p-1 align-top">
                            {row.met === true ? 'yes' : row.met === false ? 'no' : '—'}
                          </td>
                          <td className="p-1 text-cat-overlay align-top whitespace-pre-wrap">
                            {row.actual || '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-cat-overlay italic">Add acceptance criteria to track per-row verification.</p>
              )}
            </div>
          </CollapsibleSection>

          {lastSprintContextSources?.taskId === safeTask.id && (
            <div
              className="text-[10px] text-cat-subtext border border-cat-surface1/80 rounded px-2 py-1.5 bg-cat-mantle/40"
              data-testid="task-context-sources"
            >
              <span className="text-cat-overlay uppercase tracking-wide text-[9px]">
                Last step context sources
              </span>
              <p className="mt-0.5 font-mono leading-relaxed">
                {formatContextSourcesLine(lastSprintContextSources)}
              </p>
            </div>
          )}

          <div id="task-flow-section">
            <CollapsibleSection
              title="Flow"
              badge="LLM + tools"
              defaultOpen={false}
              open={flowOpen}
              onOpenChange={setFlowOpen}
            >
              <TaskFlowPanel
                taskId={task.id}
                active={flowOpen || phaseGraphOpen}
                refreshKey={flowRefreshKey}
                liveRefresh={flowOpen || phaseGraphOpen}
              />
            </CollapsibleSection>
          </div>

          <div id="task-phase-graph-section">
            <CollapsibleSection
              title="Phase graph"
              badge="Explore → Patch → Verify"
              defaultOpen={false}
              open={phaseGraphOpen}
              onOpenChange={setPhaseGraphOpen}
            >
              <DevPhaseGraphPanel
                taskId={task.id}
                active={phaseGraphOpen || flowOpen}
                refreshKey={flowRefreshKey}
                liveRefresh={phaseGraphOpen || flowOpen}
                activeRun={
                  activeRun && activeRun.taskId === task.id ? activeRun : null
                }
                lastSnapshot={safeTask.lastStepProgress?.devPhaseGraph ?? null}
                lastLabel={safeTask.lastStepProgress?.devPhase ?? null}
                onSelectNode={(nodeId) => {
                  setFlowOpen(true)
                  window.setTimeout(() => {
                    const el = document.getElementById(`flow-node-${nodeId}`)
                    el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
                  }, 80)
                }}
              />
            </CollapsibleSection>
          </div>

          {safeTask.retrievalFeedback && (
            <div
              className="rounded-lg border border-amber-500/30 bg-amber-950/20 p-2.5 space-y-1.5"
              data-testid="retrieval-feedback-banner"
            >
              <p className="text-[11px] text-amber-200 font-semibold">Semantic context may be noisy</p>
              <p className="text-[10px] text-amber-100/80">
                {safeTask.retrievalFeedback.note ||
                  `Weak hits ${safeTask.retrievalFeedback.weakHits ?? '?'}/${safeTask.retrievalFeedback.totalHits ?? '?'}`}
              </p>
              <div className="flex flex-wrap gap-2">
                {onRaiseSemanticMinScore && (
                  <button
                    type="button"
                    onClick={onRaiseSemanticMinScore}
                    className="text-[10px] px-2 py-0.5 rounded border border-amber-500/40 text-amber-200 hover:bg-amber-950/40"
                  >
                    Raise min score
                  </button>
                )}
                {onReindexCodebase && (
                  <button
                    type="button"
                    onClick={onReindexCodebase}
                    className="text-[10px] px-2 py-0.5 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/40"
                  >
                    Re-index
                  </button>
                )}
              </div>
            </div>
          )}

          {(safeTask.focusMode === 'ac' || safeTask.focusMode === 'subtask') &&
            (safeTask.acceptanceCriteria?.length ?? 0) > 0 && (
              <div className="bg-violet-950/25 border border-violet-500/35 rounded-lg p-3 space-y-2">
                <h4 className="text-xs font-bold text-violet-200">Dev focus slice</h4>
                <p className="text-[11px] text-cat-subtext">
                  {safeTask.focusMode === 'ac' && safeTask.acceptanceCriteria
                    ? `AC ${(safeTask.focusAcIndex ?? 0) + 1}/${safeTask.acceptanceCriteria.length}: ${
                        safeTask.acceptanceCriteria[safeTask.focusAcIndex ?? 0] ?? '—'
                      }`
                    : safeTask.focusSubtaskId
                      ? `Subtask: ${safeTask.focusSubtaskId}`
                      : 'Whole card'}
                  {safeTask.focusStepsRun != null && safeTask.focusStepsRun > 0
                    ? ` · ${safeTask.focusStepsRun} focus step(s) run`
                    : ''}
                </p>
                <div className="flex flex-wrap gap-2">
                  {onFocusAdvance && (
                    <button
                      type="button"
                      disabled={sprintRunning}
                      onClick={() => void onFocusAdvance(safeTask.id)}
                      className="text-[10px] px-2 py-0.5 rounded border border-violet-400/50 text-violet-100 hover:bg-violet-950/50 disabled:opacity-40"
                    >
                      Next focus slice
                    </button>
                  )}
                  {onFocusReset && (
                    <button
                      type="button"
                      disabled={sprintRunning}
                      onClick={() => void onFocusReset(safeTask.id)}
                      className="text-[10px] px-2 py-0.5 rounded border border-cat-surface1 text-cat-subtext hover:bg-cat-surface0 disabled:opacity-40"
                    >
                      Reset focus
                    </button>
                  )}
                  {onClearToolFingerprints && (
                    <button
                      type="button"
                      disabled={sprintRunning}
                      onClick={() => void onClearToolFingerprints(safeTask.id)}
                      className="text-[10px] px-2 py-0.5 rounded border border-amber-500/40 text-amber-100 hover:bg-amber-950/40 disabled:opacity-40"
                    >
                      Clear blocked tool fingerprints
                    </button>
                  )}
                </div>
              </div>
            )}

          {(() => {
            const lsp = safeTask.lastStepProgress
            const cp = lsp?.cardProgress
            const subtasksTotal = cp?.subtasksTotal ?? (safeTask.subtaskIds ?? []).length
            const subtasksDone = cp?.subtasksDone
            const gates = cp?.gatesRemaining
            const filesStep = lsp?.filesThisStep ?? cp?.filesThisStep ?? []
            const acCount = cp?.acCount ?? (safeTask.acceptanceCriteria ?? []).length
            const stuck = safeTask.stuckLoops ?? cp?.stuckLoops ?? 0
            const phaseSnap = lsp?.devPhaseGraph
            const phaseLabel = lsp?.devPhase
            const showStrip =
              subtasksTotal > 0 ||
              (gates && gates.length > 0) ||
              stuck > 0 ||
              Boolean(lsp?.whyCardStayed) ||
              Boolean(lsp?.intent) ||
              filesStep.length > 0 ||
              acCount > 0 ||
              Boolean(phaseSnap?.phase || phaseLabel)
            if (!showStrip) return null
            return (
              <div className="bg-sky-950/20 border border-sky-500/30 rounded-lg p-3 space-y-1.5">
                <h4 className="text-xs font-bold text-sky-200">Work progress</h4>
                {(phaseSnap || phaseLabel) && (
                  <DevPhaseStepper snapshot={phaseSnap} label={phaseLabel} />
                )}
                {lsp?.intent && (
                  <p className="text-[11px] text-violet-200">Last intent: {lsp.intent}</p>
                )}
                {subtasksTotal > 0 && (
                  <p className="text-[11px] text-cat-subtext">
                    Subtasks Done: {subtasksDone != null ? subtasksDone : '—'}/{subtasksTotal}
                  </p>
                )}
                {acCount > 0 && (
                  <p className="text-[11px] text-cat-subtext">
                    Acceptance criteria: {acCount} defined (not auto-scored)
                  </p>
                )}
                {gates && gates.length > 0 && (
                  <p className="text-[11px] text-cat-subtext">
                    Gates remaining: {gates.join(' → ')}
                  </p>
                )}
                {stuck > 0 && (
                  <p className="text-[11px] text-amber-300">
                    Steps without lane move: {stuck}
                  </p>
                )}
                {filesStep.length > 0 && (
                  <p className="text-[11px] text-cat-subtext truncate" title={filesStep.join(', ')}>
                    Files this step: {filesStep.join(', ')}
                  </p>
                )}
                {lsp?.whyCardStayed && (
                  <p className="text-[11px] text-amber-200">
                    Why stayed: {lsp.whyCardStayed}
                  </p>
                )}
                {lsp?.suggestedAction && (
                  <p className="text-[11px] text-cat-subtext">
                    Suggested: {lsp.suggestedAction}
                  </p>
                )}
              </div>
            )
          })()}

          {(() => {
            const usage = safeTask.agentUsage
            const rows = usage
              ? Object.entries(usage).filter(
                  ([, e]) =>
                    e &&
                    ((e.durationMs ?? 0) > 0 ||
                      (e.ollamaMs ?? 0) > 0 ||
                      (e.callCount ?? 0) > 0 ||
                      (e.stepCount ?? 0) > 0),
                )
              : []
            if (!rows.length) return null
            return (
              <div className="bg-violet-950/20 border border-violet-500/30 rounded-lg p-3 space-y-2">
                <h4 className="text-xs font-bold text-violet-200">Agent usage</h4>
                <div className="space-y-1.5">
                  {rows.map(([role, e]) => (
                    <div
                      key={role}
                      className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-[11px]"
                    >
                      <span className="text-white font-medium">{role}</span>
                      <span className="text-cat-subtext tabular-nums">
                        {formatDurationMs(e.durationMs ?? e.ollamaMs)} wall
                        {(e.ollamaMs ?? 0) > 0 ? ` · ${formatDurationMs(e.ollamaMs)} Ollama` : ''}
                        {(e.toolMs ?? 0) > 0 ? ` · ${formatDurationMs(e.toolMs)} tools` : ''}
                        {(e.stepCount ?? 0) > 0 ? ` · ${e.stepCount} steps` : ''}
                        {(e.callCount ?? 0) > 0 ? ` · ${e.callCount} calls` : ''}
                      </span>
                      <span className="w-full text-cat-overlay tabular-nums">
                        Tokens: {formatTokensLine(e)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )
          })()}

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

          {taskLane === 'In Progress' && onRunInProgressStep && (
            <div className="bg-teal-950/20 border border-teal-500/30 rounded-lg p-3 space-y-2">
              <h4 className="text-xs font-bold text-teal-200">Run dev step</h4>
              <p className="text-[10px] text-cat-subtext">
                Run the Developer agent on this card now. Skips Needs PO, Backlog, and Refinement.
              </p>
              <button
                type="button"
                disabled={runningDevStep || sprintRunning}
                onClick={() => {
                  setRunningDevStep(true)
                  void Promise.resolve(onRunInProgressStep(task.id)).finally(() =>
                    setRunningDevStep(false),
                  )
                }}
                className="w-full bg-teal-600/40 hover:bg-teal-600/60 disabled:opacity-50 text-teal-100 text-xs py-2 px-3 rounded-lg border border-teal-500/30"
              >
                {runningDevStep ? 'Running…' : 'Run dev step on this card'}
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

          {(safeTask.lastStepDiagnostics || safeTask.lastStepOutcome) && (
            <CollapsibleSection title="Step diagnostics" defaultOpen>
              <div className="space-y-1 text-[11px] font-mono text-cat-subtext" data-testid="task-step-diagnostics">
                <p className="text-cat-overlay text-[10px] uppercase tracking-wide">LAST_STEP_DIAGNOSTICS</p>
                {safeTask.lastStepDiagnostics?.exitReason && (
                  <p>
                    <span className="text-cat-overlay">exitReason:</span>{' '}
                    {safeTask.lastStepDiagnostics.exitReason}
                  </p>
                )}
                {safeTask.lastStepOutcome?.stopReason &&
                  safeTask.lastStepOutcome.stopReason !== safeTask.lastStepDiagnostics?.exitReason && (
                    <p>
                      <span className="text-cat-overlay">stopReason:</span>{' '}
                      {safeTask.lastStepOutcome.stopReason}
                    </p>
                  )}
                {typeof safeTask.lastStepDiagnostics?.durationMs === 'number' && (
                  <p>
                    <span className="text-cat-overlay">duration:</span>{' '}
                    {Math.round(safeTask.lastStepDiagnostics.durationMs / 1000)}s
                    {typeof safeTask.lastStepDiagnostics.ollamaMsTotal === 'number' &&
                      ` · ollama ${Math.round(safeTask.lastStepDiagnostics.ollamaMsTotal / 1000)}s`}
                    {typeof safeTask.lastStepDiagnostics.toolMsTotal === 'number' &&
                      ` · tools ${Math.round(safeTask.lastStepDiagnostics.toolMsTotal / 1000)}s`}
                  </p>
                )}
                {(safeTask.lastStepDiagnostics?.planRejections != null ||
                  safeTask.lastStepDiagnostics?.textRejections != null) && (
                  <p>
                    <span className="text-cat-overlay">rejects:</span> plan{' '}
                    {safeTask.lastStepDiagnostics.planRejections ?? 0} / text{' '}
                    {safeTask.lastStepDiagnostics.textRejections ?? 0}
                  </p>
                )}
                {(safeTask.lastStepDiagnostics?.toolsUsed?.length ?? 0) > 0 && (
                  <p className="break-all">
                    <span className="text-cat-overlay">toolsUsed:</span>{' '}
                    {safeTask.lastStepDiagnostics?.toolsUsed?.join(', ')}
                  </p>
                )}
                {safeTask.lastStepDiagnostics?.filePath && (
                  <p className="break-all text-indigo-300">
                    <span className="text-cat-overlay">file:</span>{' '}
                    {safeTask.lastStepDiagnostics.filePath}
                  </p>
                )}
                {safeTask.lastStepOutcome?.whyCardStayed && (
                  <p className="text-amber-200/90 whitespace-pre-wrap">
                    {safeTask.lastStepOutcome.whyCardStayed}
                  </p>
                )}
                {safeTask.lastStepDiagnostics?.hint && (
                  <p
                    className="text-amber-200/90 whitespace-pre-wrap"
                    data-testid="task-step-diagnostics-hint"
                  >
                    {safeTask.lastStepDiagnostics.hint}
                  </p>
                )}
              </div>
            </CollapsibleSection>
          )}

          {blockedBy.length > 0 && (
            <div>
              <h4 className="text-xs font-bold uppercase tracking-wider text-cat-subtext mb-1">
                Blocked By
              </h4>
              <p className="text-[11px] font-mono text-orange-300">{blockedBy.join(', ')}</p>
              {missingBlockers.length > 0 && (
                <p className="text-[11px] text-rose-300 mt-1">
                  Missing blocker ID(s): {missingBlockers.join(', ')} — this card may never unblock
                  until you remove or fix these references.
                </p>
              )}
            </div>
          )}

          {dependencyOutcomes.length > 0 && (
            <CollapsibleSection title="Completed dependency outcomes" badge={dependencyOutcomes.length}>
              <ul className="space-y-2 text-[11px] text-cat-subtext">
                {dependencyOutcomes.map((outcome) => (
                  <li
                    key={outcome.taskId}
                    className="border border-cat-surface1 rounded p-2 bg-cat-base/40"
                  >
                    <p className="text-white font-mono text-[10px]">
                      {outcome.taskId} · {outcome.title}
                      {outcome.completedAt ? ` · ${outcome.completedAt}` : ''}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap">{outcome.summary}</p>
                    {outcome.files && outcome.files.length > 0 && (
                      <p className="mt-1 text-indigo-300/90 font-mono text-[10px]">
                        Files: {outcome.files.join(', ')}
                      </p>
                    )}
                    {outcome.refinementNotes && (
                      <p className="mt-1 text-violet-200/90 whitespace-pre-wrap">
                        {outcome.refinementNotes}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </CollapsibleSection>
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

          {parentFeatureId && (
            <CollapsibleSection title="Parent Feature" defaultOpen>
              <button
                type="button"
                onClick={() => onRelatedTaskClick?.(parentFeatureId)}
                className="w-full text-left text-[11px] font-mono bg-violet-950/30 border border-violet-500/30 rounded px-2 py-1.5 hover:border-violet-400/50 text-violet-200"
              >
                {parentFeatureId}
                {getTaskTitle?.(parentFeatureId) && (
                  <span className="text-cat-subtext font-sans ml-2">
                    — {getTaskTitle(parentFeatureId)}
                  </span>
                )}
              </button>
              <p className="text-[10px] text-cat-subtext mt-1">
                Living spec and prior decisions are injected into agent prompts for this card.
              </p>
            </CollapsibleSection>
          )}

          {isFeatureEpic && (
            <div className="flex flex-wrap gap-2">
              {onAddFeatureFollowUp && (
                <button
                  type="button"
                  onClick={() => onAddFeatureFollowUp(task)}
                  className="text-[11px] px-2.5 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-semibold"
                  data-testid="add-feature-follow-up"
                >
                  Add follow-up
                </button>
              )}
            </div>
          )}

          {isFeatureEpic && featureHistory.length > 0 && (
            <CollapsibleSection title="Feature History" badge={featureHistory.length} defaultOpen>
              <div className="space-y-2 max-h-48 overflow-y-auto">
                {[...featureHistory].reverse().map((entry, idx) => (
                  <div
                    key={`${entry.timestamp}-${idx}`}
                    className="text-[11px] bg-cat-base border border-cat-surface1 rounded px-2 py-1.5"
                  >
                    <p className="text-violet-300 font-semibold">
                      [{entry.timestamp}] {entry.requestTitle}
                      {entry.source !== 'user' && (
                        <span className="text-cat-subtext font-normal ml-1">({entry.source})</span>
                      )}
                    </p>
                    {entry.poSummary && (
                      <p className="text-white mt-0.5">{entry.poSummary}</p>
                    )}
                    {entry.childTaskId && (
                      <button
                        type="button"
                        onClick={() => onRelatedTaskClick?.(entry.childTaskId!)}
                        className="text-[10px] text-indigo-300 font-mono mt-1 hover:underline"
                      >
                        Child: {entry.childTaskId}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </CollapsibleSection>
          )}

          {isFeatureEpic && childTaskIds.length > 0 && (
            <CollapsibleSection title="Implementation Cards" badge={childTaskIds.length} defaultOpen>
              <div className="space-y-1">
                {childTaskIds.map((childId) => {
                  const rolled = rollupChildren.find((c) => c.id === childId)
                  const lane = rolled?.lane || rolled?.status
                  const titleText = rolled?.title || getTaskTitle?.(childId)
                  return (
                    <button
                      key={childId}
                      type="button"
                      onClick={() => onRelatedTaskClick?.(childId)}
                      className="w-full text-left text-[11px] bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 hover:border-indigo-500/50 flex items-center justify-between gap-2"
                    >
                      <span className="font-mono text-indigo-300 truncate">
                        {childId}
                        {titleText && (
                          <span className="text-cat-subtext font-sans ml-2">— {titleText}</span>
                        )}
                      </span>
                      {lane && (
                        <span className="shrink-0 text-[9px] px-1.5 py-0.5 rounded bg-indigo-950/50 text-indigo-200">
                          {lane}
                        </span>
                      )}
                    </button>
                  )
                })}
              </div>
              <p className="text-[10px] text-cat-subtext mt-2">
                Children move Backlog → Refinement (if enabled) → In Progress → QA → Done. Enable
                &quot;Require backlog refinement&quot; for smaller testable cards before In Progress.
              </p>
            </CollapsibleSection>
          )}

          {isFeatureEpic && rollupFiles.length > 0 && (
            <CollapsibleSection title="Rolled-up Files" badge={rollupFiles.length} defaultOpen>
              <ul className="space-y-1 max-h-40 overflow-y-auto">
                {rollupFiles.map((path) => (
                  <li key={path} className="text-[11px] font-mono text-indigo-300 truncate" title={path}>
                    {path}
                  </li>
                ))}
              </ul>
              <p className="text-[10px] text-cat-subtext mt-1">
                Union of files from this epic and its implementation cards.
              </p>
            </CollapsibleSection>
          )}

          {isFeatureEpic && rollupDecisions.length > 0 && (
            <CollapsibleSection
              title="Rolled-up Decisions"
              badge={rollupDecisions.length}
              defaultOpen={rollupDecisions.length <= 8}
            >
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {rollupDecisions.map((d, idx) => (
                  <div
                    key={`${d.timestamp}-${d.childTaskId}-${idx}`}
                    className="text-[11px] bg-cat-base border border-cat-surface1 rounded px-2 py-1.5"
                  >
                    <p className="text-cat-subtext">
                      <span className="text-indigo-300 font-semibold">{d.agent || 'Agent'}</span>
                      {d.type && <span className="ml-1">({d.type})</span>}
                      {d.childTitle && (
                        <span className="ml-1 text-violet-300/90">
                          · {d.childTaskId ? `${d.childTitle}` : d.childTitle}
                        </span>
                      )}
                    </p>
                    <p className="text-white mt-0.5">{d.summary}</p>
                    {d.childTaskId && (
                      <button
                        type="button"
                        onClick={() => onRelatedTaskClick?.(d.childTaskId!)}
                        className="text-[10px] text-indigo-300 font-mono mt-0.5 hover:underline"
                      >
                        {d.childTaskId}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </CollapsibleSection>
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
                  Paste analyze or test output for <em>this card</em> (QA gate / continue next step).
                  Workspace-wide results belong in the bottom <strong>Evidence</strong> tab.
                </p>
                <label className="flex flex-col gap-1">
                  <span className="text-[10px] uppercase text-cat-overlay">Command</span>
                  <input
                    type="text"
                    value={injectCommand}
                    onChange={(e) => setInjectCommand(e.target.value)}
                    placeholder={defaultInjectCommand || 'e.g. npm run lint'}
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
                        toolArgs: {
                          command: injectCommand.trim() || defaultInjectCommand || 'analyze',
                        },
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
                    {
                      userStory: userStory.trim(),
                      scope: scope.trim(),
                      outOfScope: outOfScope.trim(),
                      testPlan: testPlan.trim(),
                      actualSummary: actualSummary.trim(),
                    },
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

          {(() => {
            const resolutions = safeTask.userResolutions ?? []
            const qaPath =
              safeTask.qaMarkdownPath ||
              (safeTask.id ? `docs/tasks/${safeTask.id}-qa.md` : '')
            const recentTools = (safeTask.transcript ?? [])
              .filter((e) => e.toolName)
              .slice(-8)
            const pendingQ = formatTaskText(
              safeTask.userQuestion || safeTask.needsUserReason || '',
            )
            const hasNotes =
              resolutions.length > 0 ||
              decisions.length > 0 ||
              recentTools.length > 0 ||
              !!pendingQ
            return (
              <CollapsibleSection
                title="Working notes (Q&A)"
                badge={hasNotes ? 'summary' : undefined}
                defaultOpen={resolutions.length > 0 || !!pendingQ}
              >
                <div className="space-y-3 text-[11px]">
                  <div>
                    <p className="text-[10px] font-bold uppercase text-cat-overlay mb-1">Q&A</p>
                    {resolutions.length === 0 && !pendingQ ? (
                      <p className="text-cat-overlay italic">No user Q&A yet.</p>
                    ) : (
                      <div className="space-y-2 max-h-40 overflow-y-auto pr-1">
                        {resolutions.slice(-8).map((r, i) => {
                          const { question, answer } = formatQaPair(r)
                          return (
                            <div
                              key={i}
                              className="bg-cat-base border border-cat-surface1 rounded p-2"
                            >
                              <p className="text-white font-medium">Q: {question}</p>
                              <p className="text-cat-subtext mt-1">A: {answer}</p>
                            </div>
                          )
                        })}
                        {pendingQ ? (
                          <div className="bg-amber-950/30 border border-amber-500/30 rounded p-2">
                            <p className="text-white font-medium">Q: {pendingQ}</p>
                            <p className="text-amber-200/80 mt-1 italic">A: Awaiting your answer.</p>
                          </div>
                        ) : null}
                      </div>
                    )}
                  </div>
                  <div>
                    <p className="text-[10px] font-bold uppercase text-cat-overlay mb-1">
                      Decisions (summarized)
                    </p>
                    {decisions.length === 0 ? (
                      <p className="text-cat-overlay italic">None yet</p>
                    ) : (
                      <ul className="space-y-1 max-h-28 overflow-y-auto pr-1 text-cat-subtext">
                        {decisions.slice(-12).map((d, i) => (
                          <li key={i}>
                            [{formatTaskText(d.agent)}/{formatTaskText(d.type)}]{' '}
                            {formatTaskText(d.summary)}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  {recentTools.length > 0 && (
                    <div>
                      <p className="text-[10px] font-bold uppercase text-cat-overlay mb-1">
                        Recent actions
                      </p>
                      <ul className="space-y-1 max-h-24 overflow-y-auto pr-1 font-mono text-cat-subtext">
                        {recentTools.map((e, i) => {
                          const args = (e.toolArgs || {}) as Record<string, unknown>
                          const detail = formatTaskText(
                            args.command || args.path || args.question || '',
                          ).slice(0, 80)
                          const status =
                            e.toolSuccess === true
                              ? 'ok'
                              : e.toolSuccess === false
                                ? 'fail'
                                : '?'
                          return (
                            <li key={i}>
                              {e.toolName} ({status})
                              {detail ? `: ${detail}` : ''}
                            </li>
                          )
                        })}
                      </ul>
                    </div>
                  )}
                  {qaPath && (
                    <button
                      type="button"
                      onClick={() => {
                        onOpenFile(qaPath)
                        onClose()
                      }}
                      className="text-[10px] text-indigo-300 hover:text-indigo-200 underline"
                    >
                      Open {qaPath}
                    </button>
                  )}
                </div>
              </CollapsibleSection>
            )
          })()}

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
    </SlideOver>
  )
}
