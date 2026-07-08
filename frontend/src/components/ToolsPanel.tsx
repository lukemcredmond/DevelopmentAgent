import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import VirtualScrollList from './VirtualScrollList'
import {
  executeTool,
  fetchStackCatalog,
  fetchTaskToolCalls,
  fetchToolRegistry,
  replayTools,
} from '../api/client'
import type {
  AgentId,
  Board,
  BriefCategory,
  StackCatalogEntry,
  Task,
  ToolDefinition,
  ToolExecutionEvent,
  TranscriptToolEntry,
} from '../types'

type ToolFilter = 'all' | 'work' | 'this_task' | 'failed' | 'agent' | 'manual' | 'replay' | 'orchestrator' | 'context_inject'
type ToolsSubTab = 'log' | 'manual' | 'replay' | 'reference'

const WORK_SOURCES = new Set(['agent', 'orchestrator', 'manual', 'user'])

interface ManualRunResult {
  toolName: string
  success: boolean
  output: string
  durationMs?: number
  timestamp: string
}

const TOOLS_SUBTAB_KEY = 'allhands-tools-subtab'
const SCROLL_THRESHOLD_PX = 48

const AGENT_OPTIONS: { id: AgentId; label: string }[] = [
  { id: 'po', label: 'Product Owner' },
  { id: 'dev', label: 'Developer' },
  { id: 'cr', label: 'Code Reviewer' },
  { id: 'qa', label: 'QA Tester' },
]

interface ToolsPanelProps {
  toolEvents: ToolExecutionEvent[]
  terminalSessions?: import('../types').BackgroundTerminalSession[]
  onStopTerminal?: (sessionId: string) => void | Promise<void>
  onClearLog?: () => void | Promise<void>
  onMergeToolEvent?: (payload: Record<string, unknown>) => void
  board: Board
  selectedTaskId?: string | null
  onRefreshState?: () => void
  onRefreshToolHistory?: () => void
  sseLive?: boolean
  lastToolEventAt?: string | null
  brief?: string
  preferredSubTab?: ToolsSubTab
  workspaceDir?: string
  sprintRunning?: boolean
  onOpenConsole?: () => void
  onInjectToolEvidence?: (
    taskId: string,
    payload: {
      toolName: string
      toolArgs: Record<string, unknown>
      toolOutput: string
      note?: string
    },
  ) => void | Promise<void>
}

function toolEventBadge(ev: ToolExecutionEvent): { label: string; tone: 'ok' | 'findings' | 'failed' } {
  if (ev.status === 'awaiting_approval') return { label: 'Awaiting approval', tone: 'findings' }
  if (ev.status === 'running') return { label: '…', tone: 'ok' }
  if (ev.toolName === 'context_inject') return { label: 'Context', tone: 'ok' }
  if (ev.source === 'user') return { label: 'User inject', tone: 'ok' }
  if (ev.source === 'orchestrator') {
    if (
      ev.runCommandStatus?.startsWith('Findings') ||
      ev.runCommandStatus?.startsWith('Tests failed')
    ) {
      return { label: ev.runCommandStatus, tone: 'findings' }
    }
    if (ev.status === 'failed' || ev.toolSuccess === false) {
      return { label: 'Auto QA FAIL', tone: 'failed' }
    }
    return { label: ev.runCommandStatus ?? 'Auto QA', tone: 'ok' }
  }
  if (ev.runCommandStatus?.startsWith('Findings') || ev.runCommandStatus?.startsWith('Tests failed')) {
    return { label: ev.runCommandStatus, tone: 'findings' }
  }
  if (ev.status === 'failed' || ev.runCommandStatus === 'Failed') {
    return { label: 'FAILED', tone: 'failed' }
  }
  return { label: ev.runCommandStatus ?? 'OK', tone: 'ok' }
}

function runCommandSummaryLine(ev: ToolExecutionEvent): string | null {
  if (ev.toolName !== 'run_command' || ev.status === 'running') return null
  if (ev.diagnosticsCount != null && ev.diagnosticsCount > 0) {
    return `${ev.diagnosticsCount} finding${ev.diagnosticsCount === 1 ? '' : 's'} — fix file:line entries below.`
  }
  if (
    ev.runCommandStatus?.startsWith('Findings') ||
    ev.runCommandStatus?.startsWith('Tests failed')
  ) {
    return 'Executed with findings — command ran; see output for issues.'
  }
  if (ev.status === 'failed' || ev.runCommandStatus === 'Failed' || ev.toolSuccess === false) {
    return 'Execution failed — command did not run successfully.'
  }
  return null
}

function sourceDisplayLabel(source: ToolExecutionEvent['source']): string {
  if (source === 'orchestrator') return 'Auto QA'
  if (source === 'context_inject') return 'Context'
  if (source === 'user') return 'User'
  return source
}

function readToolsSubTab(): ToolsSubTab {
  try {
    const stored = sessionStorage.getItem(TOOLS_SUBTAB_KEY)
    if (stored === 'log' || stored === 'manual' || stored === 'replay' || stored === 'reference') return stored
  } catch {
    /* ignore */
  }
  return 'log'
}

function defaultArgsForTool(tool: ToolDefinition | undefined): string {
  if (!tool?.parameters?.properties) return '{}'
  const props = tool.parameters.properties as Record<string, { type?: string }>
  const required = (tool.parameters.required as string[]) ?? []
  const obj: Record<string, string> = {}
  for (const key of Object.keys(props)) {
    obj[key] = required.includes(key) ? '' : ''
  }
  if (tool.name === 'read_file' && 'path' in obj) {
    obj.path = 'lib/main.dart'
  }
  if (Object.keys(obj).length === 0) return '{}'
  return JSON.stringify(
    Object.fromEntries(
      Object.entries(obj).map(([k, _]) => [k, props[k]?.type === 'string' ? '' : null]),
    ),
    null,
    2,
  )
}

function findTaskLane(board: Board, taskId: string): string {
  for (const lane of Object.keys(board)) {
    const tasks = board[lane as keyof Board]
    if (Array.isArray(tasks) && tasks.some((t) => t.id === taskId)) return lane
  }
  return '?'
}

function listBoardTasks(board: Board): Task[] {
  const lanes = ['Backlog', 'In Progress', 'Needs PO', 'Needs User', 'Code Review', 'QA', 'Done']
  const tasks: Task[] = []
  for (const lane of lanes) {
    const laneTasks = board[lane as keyof Board]
    if (Array.isArray(laneTasks)) {
      tasks.push(...(laneTasks as Task[]))
    }
  }
  return tasks
}

function formatLastToolAgo(timestamp: string | null | undefined): string {
  if (!timestamp) return 'never'
  const parsed = Date.parse(timestamp.replace(' ', 'T'))
  if (Number.isNaN(parsed)) return timestamp
  const sec = Math.max(0, Math.floor((Date.now() - parsed) / 1000))
  if (sec < 60) return `${sec}s ago`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  return `${Math.floor(min / 60)}h ago`
}

export default function ToolsPanel({
  toolEvents,
  terminalSessions = [],
  onStopTerminal,
  onClearLog,
  onMergeToolEvent,
  board,
  selectedTaskId,
  onRefreshState,
  onRefreshToolHistory,
  sseLive = true,
  lastToolEventAt = null,
  brief = '',
  preferredSubTab,
  workspaceDir,
  sprintRunning = false,
  onOpenConsole,
  onInjectToolEvidence,
}: ToolsPanelProps) {
  const [subTab, setSubTab] = useState<ToolsSubTab>(readToolsSubTab)
  const [filter, setFilter] = useState<ToolFilter>('all')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)
  const [stackCatalog, setStackCatalog] = useState<StackCatalogEntry[]>([])
  const [catalogCategories, setCatalogCategories] = useState<BriefCategory[]>([])
  const [catalogLoading, setCatalogLoading] = useState(false)
  const [expandedStacks, setExpandedStacks] = useState<Set<string>>(new Set())

  const [agentId, setAgentId] = useState<AgentId>('dev')
  const [tools, setTools] = useState<ToolDefinition[]>([])
  const [selectedTool, setSelectedTool] = useState('')
  const [argsJson, setArgsJson] = useState('{}')
  const [taskIdInput, setTaskIdInput] = useState(selectedTaskId ?? '')
  const [running, setRunning] = useState(false)
  const [runnerError, setRunnerError] = useState<string | null>(null)
  const [lastManualResult, setLastManualResult] = useState<ManualRunResult | null>(null)

  const [replayTaskId, setReplayTaskId] = useState(selectedTaskId ?? '')
  const [transcriptEntries, setTranscriptEntries] = useState<TranscriptToolEntry[]>([])
  const [loadingTranscript, setLoadingTranscript] = useState(false)
  const [replaying, setReplaying] = useState(false)
  const [clearingLog, setClearingLog] = useState(false)

  const boardTasks = useMemo(() => listBoardTasks(board), [board])

  useEffect(() => {
    if (preferredSubTab) setSubTab(preferredSubTab)
  }, [preferredSubTab])

  useEffect(() => {
    if (subTab === 'log') {
      onRefreshToolHistory?.()
    }
  }, [subTab, onRefreshToolHistory])

  useEffect(() => {
    if (subTab !== 'log' && !sprintRunning) return
    if (!onRefreshToolHistory) return
    const interval = window.setInterval(() => {
      onRefreshToolHistory()
    }, 15000)
    return () => window.clearInterval(interval)
  }, [subTab, sprintRunning, onRefreshToolHistory])

  useEffect(() => {
    if (subTab !== 'reference') return
    setCatalogLoading(true)
    void fetchStackCatalog(true)
      .then((data) => {
        setStackCatalog(data.stacks ?? [])
        setCatalogCategories(data.briefCategories ?? [])
      })
      .catch(() => {
        setStackCatalog([])
        setCatalogCategories([])
      })
      .finally(() => setCatalogLoading(false))
  }, [subTab, brief])

  useEffect(() => {
    try {
      sessionStorage.setItem(TOOLS_SUBTAB_KEY, subTab)
    } catch {
      /* ignore */
    }
  }, [subTab])

  const [registryError, setRegistryError] = useState<string | null>(null)

  useEffect(() => {
    if (selectedTaskId) {
      setTaskIdInput(selectedTaskId)
      setReplayTaskId(selectedTaskId)
    }
  }, [selectedTaskId])

  useEffect(() => {
    let cancelled = false
    setRegistryError(null)
    void fetchToolRegistry(agentId)
      .then((data) => {
        if (cancelled) return
        const loaded = data.tools ?? []
        setTools(loaded)
        if (loaded.length === 0) {
          setRegistryError('No tools returned — restart the backend or check Workflow settings.')
        }
        const first = loaded[0]?.name ?? ''
        setSelectedTool((prev) => (loaded.some((t) => t.name === prev) ? prev : first))
      })
      .catch((err) => {
        if (!cancelled) {
          setTools([])
          setRegistryError(
            err instanceof Error ? err.message : 'Failed to load tool registry from /api/tools/registry',
          )
        }
      })
    return () => {
      cancelled = true
    }
  }, [agentId])

  useEffect(() => {
    const tool = tools.find((t) => t.name === selectedTool)
    setArgsJson(defaultArgsForTool(tool))
  }, [selectedTool, tools])

  const loadTranscript = useCallback(async (taskId: string) => {
    if (!taskId.trim()) {
      setTranscriptEntries([])
      return
    }
    setLoadingTranscript(true)
    setRunnerError(null)
    try {
      const data = await fetchTaskToolCalls(taskId.trim())
      setTranscriptEntries(data.entries ?? [])
    } catch (e) {
      setTranscriptEntries([])
      setRunnerError(e instanceof Error ? e.message : 'Failed to load transcript')
    } finally {
      setLoadingTranscript(false)
    }
  }, [])

  useEffect(() => {
    if (subTab === 'replay' && replayTaskId) {
      void loadTranscript(replayTaskId)
    }
  }, [subTab, replayTaskId, loadTranscript])

  const filtered = useMemo(() => {
    if (filter === 'this_task' && selectedTaskId) {
      return toolEvents.filter((e) => e.taskId === selectedTaskId)
    }
    if (filter === 'failed') {
      return toolEvents.filter((e) => {
        if (
          e.runCommandStatus?.startsWith('Findings') ||
          e.runCommandStatus?.startsWith('Tests failed')
        ) {
          return false
        }
        return e.status === 'failed' || e.toolSuccess === false
      })
    }
    if (filter === 'agent') return toolEvents.filter((e) => e.source === 'agent')
    if (filter === 'work') return toolEvents.filter((e) => WORK_SOURCES.has(e.source ?? ''))
    if (filter === 'manual') return toolEvents.filter((e) => e.source === 'manual')
    if (filter === 'replay') return toolEvents.filter((e) => e.source === 'replay')
    if (filter === 'orchestrator') return toolEvents.filter((e) => e.source === 'orchestrator')
    if (filter === 'context_inject') return toolEvents.filter((e) => e.source === 'context_inject')
    return toolEvents
  }, [toolEvents, filter, selectedTaskId])

  const failureCount = useMemo(
    () => toolEvents.filter((e) => e.status === 'failed').length,
    [toolEvents],
  )

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    stickToBottomRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_THRESHOLD_PX
  }

  useEffect(() => {
    if (subTab !== 'log') return
    const el = scrollRef.current
    if (!el || !stickToBottomRef.current) return
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [filtered.length, toolEvents.length, subTab])

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleRunManual = async () => {
    setRunnerError(null)
    let args: Record<string, unknown>
    try {
      args = JSON.parse(argsJson) as Record<string, unknown>
    } catch {
      setRunnerError('Invalid JSON in arguments')
      return
    }
    if (!selectedTool) {
      setRunnerError('Select a tool')
      return
    }
    setRunning(true)
    try {
      const { result } = await executeTool({
        agent: agentId,
        toolName: selectedTool,
        arguments: args,
        taskId: taskIdInput.trim() || undefined,
      })
      onMergeToolEvent?.({
        runId: result.runId ?? 'manual',
        taskId: (result.taskId ?? taskIdInput.trim()) || 'system',
        agent: result.agent,
        toolName: result.toolName,
        toolArgs: result.toolArgs,
        toolSuccess: result.toolSuccess,
        toolOutput: result.toolOutput,
        durationMs: result.durationMs,
        timestamp: result.timestamp,
        source: result.source ?? 'manual',
      })
      setLastManualResult({
        toolName: result.toolName,
        success: result.toolSuccess !== false,
        output: result.toolOutput ?? '',
        durationMs: result.durationMs,
        timestamp: result.timestamp ?? new Date().toISOString(),
      })
      onRefreshState?.()
    } catch (e) {
      setRunnerError(e instanceof Error ? e.message : 'Tool execution failed')
    } finally {
      setRunning(false)
    }
  }

  const handleReplay = async (indices?: number[], failedOnly = false) => {
    if (!replayTaskId.trim()) {
      setRunnerError('Select a task to replay')
      return
    }
    setReplaying(true)
    setRunnerError(null)
    try {
      await replayTools({
        taskId: replayTaskId.trim(),
        entryIndices: indices,
        failedOnly,
      })
      onRefreshState?.()
      void loadTranscript(replayTaskId.trim())
    } catch (e) {
      setRunnerError(e instanceof Error ? e.message : 'Replay failed')
    } finally {
      setReplaying(false)
    }
  }

  const selectedToolDef = tools.find((t) => t.name === selectedTool)

  return (
    <div className="flex flex-col h-full min-h-0 overflow-hidden bg-[#0f0f15]">
      <div className="bg-cat-mantle border-b border-cat-surface1 px-4 py-2 flex items-center gap-2 shrink-0">
        {(['log', 'manual', 'replay', 'reference'] as const).map((tab) => (
          <button
            key={tab}
            type="button"
            onClick={() => setSubTab(tab)}
            className={`text-[10px] px-3 py-1 rounded uppercase tracking-wide font-semibold ${
              subTab === tab
                ? 'bg-indigo-600/40 text-indigo-200'
                : 'text-cat-overlay hover:text-cat-subtext'
            }`}
          >
            {tab === 'log'
              ? `Execution Log${toolEvents.length > 0 ? ` (${toolEvents.length})` : ''}`
              : tab === 'manual'
                ? 'Manual Test'
                : tab === 'reference'
                  ? 'Stack Reference'
                  : 'Replay'}
          </button>
        ))}
      </div>

      {subTab === 'log' && (
        <div className="flex flex-col flex-1 min-h-0">
          <div className="bg-cat-mantle/50 border-b border-cat-surface1 px-4 py-2 flex items-center justify-between shrink-0 gap-2">
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-cat-overlay uppercase tracking-wide">Filters</span>
              <span className="text-[9px] text-cat-overlay/80 normal-case">Newest first</span>
            </div>
            <div className="flex gap-1 flex-wrap justify-end items-center">
              {(
                [
                  'all',
                  ...(selectedTaskId ? (['this_task'] as const) : []),
                  'work',
                  'failed',
                  'agent',
                  'orchestrator',
                  'context_inject',
                  'manual',
                  'replay',
                ] as const
              ).map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setFilter(f)}
                  className={`text-[10px] px-2 py-0.5 rounded uppercase tracking-wide ${
                    filter === f
                      ? 'bg-indigo-600/40 text-indigo-200'
                      : 'text-cat-overlay hover:text-cat-subtext'
                  }`}
                >
                  {f === 'orchestrator'
                    ? 'Auto QA'
                    : f === 'context_inject'
                      ? 'Context'
                      : f === 'work'
                        ? 'Work'
                        : f === 'this_task'
                          ? 'This task'
                          : f}
                  {f === 'failed' && failureCount > 0 ? ` (${failureCount})` : ''}
                </button>
              ))}
              {onClearLog && toolEvents.length > 0 && (
                <button
                  type="button"
                  disabled={clearingLog}
                  onClick={() => {
                    setClearingLog(true)
                    void Promise.resolve(onClearLog()).finally(() => setClearingLog(false))
                  }}
                  className="text-[10px] text-cat-overlay hover:text-cat-subtext ml-1 disabled:opacity-50"
                >
                  {clearingLog ? 'Clearing…' : 'Clear'}
                </button>
              )}
              {onRefreshToolHistory && (
                <button
                  type="button"
                  onClick={() => onRefreshToolHistory()}
                  className="text-[10px] text-indigo-300 hover:text-indigo-200 ml-1"
                >
                  Refresh
                </button>
              )}
            </div>
          </div>
          <p className="shrink-0 text-[9px] text-cat-overlay/90 px-4 py-1 border-b border-cat-surface1/40">
            Commands mentioned in agent text are not tool runs — check Auto QA filter or the task
            transcript.
          </p>
          <p className="shrink-0 text-[9px] px-4 py-1 border-b border-cat-surface1/40 flex flex-wrap gap-x-3 gap-y-0.5">
            <span className={sseLive ? 'text-emerald-300/90' : 'text-rose-300/90'}>
              SSE {sseLive ? 'live' : 'disconnected'}
            </span>
            <span className="text-cat-overlay">Events: {toolEvents.length}</span>
            <span className="text-cat-overlay">Last tool: {formatLastToolAgo(lastToolEventAt)}</span>
            {!sseLive && toolEvents.length === 0 && onRefreshToolHistory && (
              <button
                type="button"
                onClick={() => onRefreshToolHistory()}
                className="text-indigo-300 hover:text-indigo-200 underline"
              >
                Reload history
              </button>
            )}
          </p>
          <VirtualScrollList
            className="flex-1 min-h-0 p-3 font-mono text-[11px]"
            items={filtered}
            estimateRowHeight={96}
            getKey={(ev) => ev.id}
            onScroll={handleScroll}
            newestFirst
            empty={
              <p className="text-cat-overlay text-center py-8">
                {sprintRunning && toolEvents.length === 0 && (
                  <span className="block mb-3 text-amber-200/90">
                    Sprint is active but no tools logged yet — the agent may still be thinking, or
                    check{' '}
                    {onOpenConsole ? (
                      <button
                        type="button"
                        onClick={onOpenConsole}
                        className="text-indigo-400 hover:text-indigo-300 underline"
                      >
                        Console
                      </button>
                    ) : (
                      'Console'
                    )}{' '}
                    for system logs.
                  </span>
                )}
                {!sseLive && toolEvents.length === 0 && (
                  <span className="block mb-3 text-rose-300/90">
                    Live event stream disconnected — click Refresh or reload the page.
                  </span>
                )}
                {filter === 'this_task' && selectedTaskId ? (
                  <>
                    No tool runs for this task in the log. Open the task transcript for command
                    output mentioned in agent text, or try the{' '}
                    <button
                      type="button"
                      onClick={() => setFilter('all')}
                      className="text-indigo-400 hover:text-indigo-300 underline"
                    >
                      All
                    </button>{' '}
                    filter.
                  </>
                ) : filter === 'work' || filter === 'agent' ? (
                  <>
                    No agent tool calls yet this session — Context preload events are hidden by default.
                    Use the{' '}
                    <button
                      type="button"
                      onClick={() => setFilter('context_inject')}
                      className="text-indigo-400 hover:text-indigo-300 underline"
                    >
                      Context
                    </button>{' '}
                    filter to see file pre-load activity, or run tools from{' '}
                    <button
                      type="button"
                      onClick={() => setSubTab('manual')}
                      className="text-indigo-400 hover:text-indigo-300 underline"
                    >
                      Manual Test
                    </button>
                    .
                  </>
                ) : (
                  <>
                    No tool executions yet. Agent tools appear here during sprints; history loads from
                    task transcripts. Use the{' '}
                    <button
                      type="button"
                      onClick={() => setSubTab('manual')}
                      className="text-indigo-400 hover:text-indigo-300 underline"
                    >
                      Manual Test
                    </button>{' '}
                    or{' '}
                    <button
                      type="button"
                      onClick={() => setSubTab('replay')}
                      className="text-indigo-400 hover:text-indigo-300 underline"
                    >
                      Replay
                    </button>{' '}
                    tabs to test tools without a sprint.
                  </>
                )}
              </p>
            }
            renderRow={(ev) => {
              const isOpen = expanded.has(ev.id)
              const badge = toolEventBadge(ev)
              const failed = badge.tone === 'failed'
              const findings = badge.tone === 'findings'
              const runningEv = ev.status === 'running'
              return (
                <div
                  className={`rounded border p-2 mb-2 ${
                    failed
                      ? 'border-rose-500/40 bg-rose-950/20'
                      : findings
                        ? 'border-amber-500/40 bg-amber-950/20'
                        : runningEv
                          ? 'border-indigo-500/40 bg-indigo-950/20'
                          : 'border-cat-surface1 bg-cat-mantle/40'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => toggleExpand(ev.id)}
                    className="w-full text-left flex items-center gap-2 flex-wrap"
                  >
                    {runningEv && (
                      <span className="inline-block w-2 h-2 rounded-full bg-indigo-400 animate-pulse shrink-0" />
                    )}
                    {!runningEv && (
                      <span
                        className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded shrink-0 ${
                          failed
                            ? 'bg-rose-900/60 text-rose-200'
                            : findings
                              ? 'bg-amber-900/60 text-amber-200'
                              : 'bg-emerald-900/60 text-emerald-200'
                        }`}
                      >
                        {badge.label}
                      </span>
                    )}
                    <span className="text-indigo-300 font-bold">{ev.toolName}</span>
                    {ev.toolName === 'run_command' &&
                      ev.diagnosticsCount != null &&
                      ev.diagnosticsCount > 0 && (
                        <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded shrink-0 bg-amber-900/60 text-amber-200">
                          {ev.diagnosticsCount} finding{ev.diagnosticsCount === 1 ? '' : 's'}
                        </span>
                      )}
                    <span className="text-cat-overlay text-[10px]">{ev.agent}</span>
                    <span className="text-cat-overlay text-[10px] uppercase">{sourceDisplayLabel(ev.source)}</span>
                    {ev.durationMs != null && ev.durationMs > 0 && (
                      <span className="text-cat-overlay text-[10px]">{ev.durationMs}ms</span>
                    )}
                    <span className="text-cat-overlay text-[10px] ml-auto">{ev.timestamp}</span>
                  </button>
                  {isOpen && (
                    <div className="mt-2 space-y-2 border-t border-cat-surface1/50 pt-2">
                      {runCommandSummaryLine(ev) && (
                        <p
                          className={`text-[10px] px-2 py-1 rounded ${
                            findings
                              ? 'bg-amber-950/40 text-amber-200'
                              : failed
                                ? 'bg-rose-950/40 text-rose-200'
                                : 'bg-cat-surface1 text-cat-subtext'
                          }`}
                        >
                          {runCommandSummaryLine(ev)}
                        </p>
                      )}
                      {ev.taskId && (
                        <p className="text-cat-overlay text-[10px]">Task: {ev.taskId}</p>
                      )}
                      {ev.toolArgs && Object.keys(ev.toolArgs).length > 0 && (
                        <div>
                          <p className="text-[9px] uppercase text-cat-overlay mb-1">Arguments</p>
                          <pre className="text-[10px] text-cat-subtext whitespace-pre-wrap break-all bg-black/20 p-2 rounded max-h-32 overflow-y-auto">
                            {JSON.stringify(ev.toolArgs, null, 2)}
                          </pre>
                        </div>
                      )}
                      {ev.diagnostics != null && ev.diagnostics.length > 0 && (
                        <div>
                          <p className="text-[9px] uppercase text-cat-overlay mb-1">
                            Diagnostics ({ev.diagnostics.length})
                          </p>
                          <ul className="text-[10px] text-cat-subtext space-y-1 max-h-40 overflow-y-auto bg-black/20 p-2 rounded">
                            {ev.diagnostics.map((diag, index) => (
                              <li key={`${diag.file}:${diag.line}:${index}`} className="break-all">
                                <span className="text-amber-200">{diag.file}:{diag.line}</span>{' '}
                                <span className="uppercase text-[9px] text-cat-overlay">
                                  {diag.severity}
                                </span>{' '}
                                {diag.message}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <div>
                        <p className="text-[9px] uppercase text-cat-overlay mb-1">Output</p>
                        <pre
                          className={`text-[10px] whitespace-pre-wrap break-all bg-black/20 p-2 rounded max-h-48 overflow-y-auto ${
                            failed ? 'text-rose-100' : 'text-cat-subtext'
                          }`}
                        >
                          {runningEv ? '(running…)' : ev.toolOutput || '(no output)'}
                        </pre>
                      </div>
                      {onInjectToolEvidence && ev.taskId && ev.toolOutput && !runningEv && (
                        <button
                          type="button"
                          onClick={() =>
                            void onInjectToolEvidence(ev.taskId!, {
                              toolName: ev.toolName,
                              toolArgs: ev.toolArgs ?? {},
                              toolOutput: ev.toolOutput ?? '',
                              note: 'Promoted from Tools Execution Log',
                            })
                          }
                          className="text-[10px] text-indigo-300 hover:text-indigo-200 underline"
                        >
                          Use as task evidence
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )
            }}
          />
          {terminalSessions.length > 0 && (
            <div className="mt-4 border-t border-cat-surface1 pt-3">
              <p className="text-[10px] uppercase text-cat-overlay mb-2">Background terminals</p>
              <div className="space-y-2">
                {terminalSessions.map((session) => (
                  <div
                    key={session.id}
                    className="rounded border border-cat-surface1 bg-black/20 p-2"
                  >
                    <div className="flex items-center gap-2 mb-1">
                      <span
                        className={`text-[9px] uppercase px-1.5 py-0.5 rounded ${
                          session.done
                            ? session.exitCode === 0
                              ? 'bg-emerald-950/50 text-emerald-300'
                              : 'bg-rose-950/50 text-rose-300'
                            : 'bg-indigo-950/50 text-indigo-300 animate-pulse'
                        }`}
                      >
                        {session.done ? `exit ${session.exitCode ?? '?'}` : 'running'}
                      </span>
                      <span className="text-[10px] font-mono text-cat-subtext truncate flex-1">
                        {session.command || session.id}
                      </span>
                      {!session.done && onStopTerminal && (
                        <button
                          type="button"
                          onClick={() => void onStopTerminal(session.id)}
                          className="text-[10px] text-rose-300 hover:text-rose-200 shrink-0"
                        >
                          Stop
                        </button>
                      )}
                    </div>
                    <pre className="text-[10px] text-cat-subtext whitespace-pre-wrap break-all max-h-36 overflow-y-auto font-mono">
                      {session.output || (session.done ? '(no output)' : '…')}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {subTab === 'manual' && (
        <div className="flex-1 min-h-0 overflow-y-auto p-4 text-[11px]">
          {workspaceDir && (
            <p className="text-[10px] text-cat-overlay mb-3 font-mono">
              Workspace root: <span className="text-cat-subtext">{workspaceDir}</span>
              {' — '}use paths relative to this folder (e.g.{' '}
              <code className="text-indigo-300">lib/main.dart</code>)
            </p>
          )}
          {runnerError && (
            <p className="text-rose-300 mb-3 text-[10px] font-mono">{runnerError}</p>
          )}
          {registryError && (
            <p className="text-amber-300 mb-3 text-[10px]">{registryError}</p>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-w-3xl">
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase text-cat-overlay">Agent</span>
              <select
                value={agentId}
                onChange={(e) => setAgentId(e.target.value as AgentId)}
                className="bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 text-cat-text"
              >
                {AGENT_OPTIONS.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[10px] uppercase text-cat-overlay">Tool</span>
              <select
                value={selectedTool}
                onChange={(e) => setSelectedTool(e.target.value)}
                className="bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 text-cat-text"
              >
                {tools.map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 md:col-span-2">
              <span className="text-[10px] uppercase text-cat-overlay">Task ID (optional)</span>
              <input
                value={taskIdInput}
                onChange={(e) => setTaskIdInput(e.target.value)}
                placeholder="Associate files / transcript with task"
                className="bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 text-cat-text font-mono"
              />
            </label>
            <label className="flex flex-col gap-1 md:col-span-2">
              <span className="text-[10px] uppercase text-cat-overlay">
                Arguments (JSON)
                {selectedToolDef?.description && (
                  <span className="normal-case text-cat-overlay ml-2">
                    — {selectedToolDef.description}
                  </span>
                )}
              </span>
              <textarea
                value={argsJson}
                onChange={(e) => setArgsJson(e.target.value)}
                rows={8}
                className="bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 text-cat-text font-mono text-[10px] min-h-[120px]"
              />
            </label>
            <div className="md:col-span-2">
              <button
                type="button"
                disabled={running}
                onClick={() => void handleRunManual()}
                className="px-5 py-2 rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs font-bold"
              >
                {running ? 'Running…' : 'Run tool'}
              </button>
            </div>
            {lastManualResult && (
              <div className="md:col-span-2 border border-cat-surface1 rounded-lg overflow-hidden">
                <div className="flex items-center justify-between gap-2 px-3 py-2 bg-cat-base/50 border-b border-cat-surface1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[10px] uppercase text-cat-overlay">Result</span>
                    <span
                      className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded ${
                        lastManualResult.success
                          ? 'bg-emerald-900/60 text-emerald-200'
                          : 'bg-rose-900/60 text-rose-200'
                      }`}
                    >
                      {lastManualResult.success ? 'OK' : 'Failed'}
                    </span>
                    <span className="text-indigo-300 font-bold text-[11px]">{lastManualResult.toolName}</span>
                    {lastManualResult.durationMs != null && lastManualResult.durationMs > 0 && (
                      <span className="text-cat-overlay text-[10px]">{lastManualResult.durationMs}ms</span>
                    )}
                    <span className="text-cat-overlay text-[10px]">{lastManualResult.timestamp}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => void navigator.clipboard.writeText(lastManualResult.output)}
                    className="text-[10px] text-indigo-400 hover:text-indigo-300 shrink-0"
                  >
                    Copy output
                  </button>
                </div>
                <pre className="p-3 text-[10px] text-cat-subtext whitespace-pre-wrap max-h-64 overflow-y-auto font-mono">
                  {lastManualResult.output || '(empty output)'}
                </pre>
              </div>
            )}
          </div>
        </div>
      )}

      {subTab === 'replay' && (
        <div className="flex-1 min-h-0 overflow-y-auto p-4 text-[11px]">
          {runnerError && (
            <p className="text-rose-300 mb-3 text-[10px] font-mono">{runnerError}</p>
          )}
          <div className="space-y-3 max-w-3xl">
            <div className="flex flex-wrap gap-2 items-end">
              <label className="flex flex-col gap-1 flex-1 min-w-[200px]">
                <span className="text-[10px] uppercase text-cat-overlay">Task</span>
                <select
                  value={replayTaskId}
                  onChange={(e) => setReplayTaskId(e.target.value)}
                  className="bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 text-cat-text"
                >
                  <option value="">Select task…</option>
                  {boardTasks.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.title || t.id} ({findTaskLane(board, t.id)})
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={replaying || !replayTaskId}
                onClick={() => void handleReplay(undefined, true)}
                className="px-3 py-1.5 rounded bg-rose-900/60 hover:bg-rose-800/60 disabled:opacity-50 text-rose-100 text-xs"
              >
                Replay all failed
              </button>
              <button
                type="button"
                disabled={replaying || !replayTaskId}
                onClick={() => void handleReplay()}
                className="px-3 py-1.5 rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-xs"
              >
                Replay all tools
              </button>
            </div>

            {loadingTranscript && (
              <p className="text-cat-overlay text-[10px]">Loading transcript…</p>
            )}
            {!loadingTranscript && transcriptEntries.length === 0 && replayTaskId && (
              <p className="text-cat-overlay text-[10px]">
                No tool calls in this task transcript.
              </p>
            )}
            <div className="space-y-1">
              {transcriptEntries.map((entry) => (
                <div
                  key={entry.index}
                  className="flex items-center gap-2 p-2 rounded border border-cat-surface1 bg-cat-mantle/30 font-mono text-[10px]"
                >
                  <span
                    className={
                      entry.toolSuccess === false ? 'text-rose-400' : 'text-emerald-400'
                    }
                  >
                    {entry.toolSuccess === false ? '✗' : '✓'}
                  </span>
                  <span className="text-indigo-300">{entry.toolName}</span>
                  <span className="text-cat-overlay truncate flex-1">
                    {JSON.stringify(entry.toolArgs ?? {}).slice(0, 80)}
                  </span>
                  <button
                    type="button"
                    disabled={replaying}
                    onClick={() => void handleReplay([entry.index])}
                    className="text-indigo-400 hover:text-indigo-300 shrink-0"
                  >
                    Replay
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {subTab === 'reference' && (
        <div className="flex-1 min-h-0 overflow-y-auto p-4 text-[11px] space-y-3">
          <p className="text-[10px] text-cat-overlay">
            Supported tools and example commands by stack. Agents use generic file/shell tools —
            adapt commands to your workspace.
          </p>
          {catalogCategories.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              <span className="text-[10px] text-cat-overlay">From brief:</span>
              {catalogCategories.map((cat) => (
                <span
                  key={cat.id}
                  className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-950/50 border border-indigo-500/30 text-indigo-200"
                >
                  {cat.label}
                </span>
              ))}
            </div>
          )}
          {catalogLoading && (
            <p className="text-cat-overlay text-center py-8">Loading stack catalog…</p>
          )}
          {!catalogLoading &&
            stackCatalog.map((stack) => {
              const open = expandedStacks.has(stack.id)
              return (
                <div
                  key={stack.id}
                  className={`border rounded-lg overflow-hidden ${
                    stack.matched ? 'border-indigo-500/40 bg-indigo-950/20' : 'border-cat-surface1'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() =>
                      setExpandedStacks((prev) => {
                        const next = new Set(prev)
                        if (next.has(stack.id)) next.delete(stack.id)
                        else next.add(stack.id)
                        return next
                      })
                    }
                    className="w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-cat-surface0/30"
                  >
                    <span className="font-semibold text-indigo-300">{stack.label}</span>
                    {stack.matched && (
                      <span className="text-[9px] text-indigo-200/80">matches brief</span>
                    )}
                  </button>
                  {open && (
                    <div className="px-3 pb-3 space-y-2 border-t border-cat-surface1/50">
                      <p className="text-cat-subtext text-[10px]">{stack.description}</p>
                      <p className="text-cat-overlay text-[10px]">{stack.notes}</p>
                      {stack.recommendedSkills.length > 0 && (
                        <div>
                          <div className="text-[9px] uppercase text-cat-overlay mb-1">Skills</div>
                          <div className="flex flex-wrap gap-1">
                            {stack.recommendedSkills.map((s) => (
                              <span
                                key={s}
                                className="text-[10px] font-mono text-emerald-300/90"
                              >
                                {s}
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                      <div>
                        <div className="text-[9px] uppercase text-cat-overlay mb-1">
                          Example commands
                        </div>
                        <ul className="space-y-1">
                          {stack.exampleCommands.map((cmd) => (
                            <li key={cmd} className="flex items-center gap-2">
                              <code className="text-[10px] text-cat-subtext flex-1">{cmd}</code>
                              <button
                                type="button"
                                onClick={() => void navigator.clipboard.writeText(cmd)}
                                className="text-[9px] text-indigo-400 hover:text-indigo-300"
                              >
                                Copy
                              </button>
                            </li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <div className="text-[9px] uppercase text-cat-overlay mb-1">
                          Agent tools
                        </div>
                        {Object.entries(stack.tools).map(([agentKey, toolNames]) => (
                          <div key={agentKey} className="text-[10px] text-cat-subtext mb-1">
                            <span className="text-indigo-300">{agentKey}</span>:{' '}
                            {toolNames.join(', ')}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
        </div>
      )}
    </div>
  )
}
