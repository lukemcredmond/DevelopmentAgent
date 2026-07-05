import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  executeTool,
  fetchTaskToolCalls,
  fetchToolRegistry,
  replayTools,
} from '../api/client'
import type {
  AgentId,
  Board,
  Task,
  ToolDefinition,
  ToolExecutionEvent,
  TranscriptToolEntry,
} from '../types'

type ToolFilter = 'all' | 'failed' | 'agent' | 'manual' | 'replay'
type ToolsSubTab = 'log' | 'manual' | 'replay'

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
  onClearLog?: () => void
  board: Board
  selectedTaskId?: string | null
  onRefreshState?: () => void
  preferredSubTab?: ToolsSubTab
  workspaceDir?: string
}

function toolEventBadge(ev: ToolExecutionEvent): { label: string; tone: 'ok' | 'findings' | 'failed' } {
  if (ev.status === 'running') return { label: '…', tone: 'ok' }
  if (ev.runCommandStatus?.startsWith('Findings')) {
    return { label: ev.runCommandStatus, tone: 'findings' }
  }
  if (ev.status === 'failed' || ev.runCommandStatus === 'Failed') {
    return { label: 'FAILED', tone: 'failed' }
  }
  return { label: ev.runCommandStatus ?? 'OK', tone: 'ok' }
}

function readToolsSubTab(): ToolsSubTab {
  try {
    const stored = sessionStorage.getItem(TOOLS_SUBTAB_KEY)
    if (stored === 'log' || stored === 'manual' || stored === 'replay') return stored
  } catch {
    /* ignore */
  }
  return 'manual'
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

export default function ToolsPanel({
  toolEvents,
  onClearLog,
  board,
  selectedTaskId,
  onRefreshState,
  preferredSubTab,
  workspaceDir,
}: ToolsPanelProps) {
  const [subTab, setSubTab] = useState<ToolsSubTab>(readToolsSubTab)
  const [filter, setFilter] = useState<ToolFilter>('all')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const scrollRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)

  const [agentId, setAgentId] = useState<AgentId>('dev')
  const [tools, setTools] = useState<ToolDefinition[]>([])
  const [selectedTool, setSelectedTool] = useState('')
  const [argsJson, setArgsJson] = useState('{}')
  const [taskIdInput, setTaskIdInput] = useState(selectedTaskId ?? '')
  const [running, setRunning] = useState(false)
  const [runnerError, setRunnerError] = useState<string | null>(null)

  const [replayTaskId, setReplayTaskId] = useState(selectedTaskId ?? '')
  const [transcriptEntries, setTranscriptEntries] = useState<TranscriptToolEntry[]>([])
  const [loadingTranscript, setLoadingTranscript] = useState(false)
  const [replaying, setReplaying] = useState(false)

  const boardTasks = useMemo(() => listBoardTasks(board), [board])

  useEffect(() => {
    if (preferredSubTab) setSubTab(preferredSubTab)
  }, [preferredSubTab])

  useEffect(() => {
    try {
      sessionStorage.setItem(TOOLS_SUBTAB_KEY, subTab)
    } catch {
      /* ignore */
    }
  }, [subTab])

  useEffect(() => {
    if (selectedTaskId) {
      setTaskIdInput(selectedTaskId)
      setReplayTaskId(selectedTaskId)
    }
  }, [selectedTaskId])

  useEffect(() => {
    let cancelled = false
    void fetchToolRegistry(agentId)
      .then((data) => {
        if (cancelled) return
        setTools(data.tools ?? [])
        const first = data.tools?.[0]?.name ?? ''
        setSelectedTool((prev) => (data.tools?.some((t) => t.name === prev) ? prev : first))
      })
      .catch(() => {
        if (!cancelled) setTools([])
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
    if (filter === 'failed') return toolEvents.filter((e) => e.status === 'failed')
    if (filter === 'agent') return toolEvents.filter((e) => e.source === 'agent')
    if (filter === 'manual') return toolEvents.filter((e) => e.source === 'manual')
    if (filter === 'replay') return toolEvents.filter((e) => e.source === 'replay')
    return toolEvents
  }, [toolEvents, filter])

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
      await executeTool({
        agent: agentId,
        toolName: selectedTool,
        arguments: args,
        taskId: taskIdInput.trim() || undefined,
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
        {(['log', 'manual', 'replay'] as const).map((tab) => (
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
            {tab === 'log' ? 'Execution Log' : tab === 'manual' ? 'Manual Test' : 'Replay'}
          </button>
        ))}
      </div>

      {subTab === 'log' && (
        <div className="flex flex-col flex-1 min-h-0">
          <div className="bg-cat-mantle/50 border-b border-cat-surface1 px-4 py-2 flex items-center justify-between shrink-0 gap-2">
            <span className="text-[10px] text-cat-overlay uppercase tracking-wide">Filters</span>
            <div className="flex gap-1 flex-wrap justify-end items-center">
              {(['all', 'failed', 'agent', 'manual', 'replay'] as const).map((f) => (
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
                  {f}
                  {f === 'failed' && failureCount > 0 ? ` (${failureCount})` : ''}
                </button>
              ))}
              {onClearLog && toolEvents.length > 0 && (
                <button
                  type="button"
                  onClick={onClearLog}
                  className="text-[10px] text-cat-overlay hover:text-cat-subtext ml-1"
                >
                  Clear
                </button>
              )}
            </div>
          </div>
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2 font-mono text-[11px]"
          >
            {filtered.length === 0 && (
              <p className="text-cat-overlay text-center py-8">
                No tool executions yet. Use the{' '}
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
              </p>
            )}
            {filtered.map((ev) => {
              const isOpen = expanded.has(ev.id)
              const badge = toolEventBadge(ev)
              const failed = badge.tone === 'failed'
              const findings = badge.tone === 'findings'
              const runningEv = ev.status === 'running'
              return (
                <div
                  key={ev.id}
                  className={`rounded border p-2 ${
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
                    <span className="text-cat-overlay text-[10px]">{ev.agent}</span>
                    <span className="text-cat-overlay text-[10px] uppercase">{ev.source}</span>
                    {ev.durationMs != null && ev.durationMs > 0 && (
                      <span className="text-cat-overlay text-[10px]">{ev.durationMs}ms</span>
                    )}
                    <span className="text-cat-overlay text-[10px] ml-auto">{ev.timestamp}</span>
                  </button>
                  {isOpen && (
                    <div className="mt-2 space-y-2 border-t border-cat-surface1/50 pt-2">
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
                    </div>
                  )}
                </div>
              )
            })}
          </div>
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
    </div>
  )
}
