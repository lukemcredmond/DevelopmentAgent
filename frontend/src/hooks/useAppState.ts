import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  cancelSprint,
  clearLogs as clearLogsApi,
  clearToolHistory,
  fetchBackgroundTerminals,
  fetchPendingApprovals,
  fetchPendingTools,
  fetchState,
  fetchToolHistory,
  runSprint,
  stopBackgroundTerminal,
  subscribeEvents,
} from '../api/client'
import { mergeToolHistory } from '../utils/mergeToolHistory'
import type {
  ActivityEvent,
  AgentRunState,
  AppState,
  BackgroundTerminalSession,
  Board,
  BoardLane,
  CardWorkProgress,
  CommandDiagnostic,
  IndexProgress,
  PendingToolApproval,
  SprintProgress,
  SystemLog,
  Task,
  ToolExecutionEvent,
} from '../types'
import { EMPTY_BOARD, hasSprintWork } from '../types'
import { hydrateActivityFromBoard, mergeActivityEvents, filterActivityAfterClear, activityTimestampNow } from '../utils/activityFromBoard'
import { appendTerminalOutput, capLogs } from '../utils/streamBuffers'

const defaultState: AppState = {
  projectId: '',
  projectName: 'My Local Scrum Project',
  brief: '',
  workspaceDir: './workspace',
  skillsDir: './global_skills',
  board: { ...EMPTY_BOARD },
  files: {},
  logs: [],
  availableSkills: [],
  assignedSkills: { po: [], dev: [], cr: [], qa: [] },
  models: {
    po: 'llama3:8b',
    dev: 'qwen2.5-coder:14b',
    cr: 'qwen2.5-coder:7b',
    qa: 'qwen2.5-coder:7b',
  },
  projectsList: [],
}

function mergeBoardDelta(board: Board, payload: { taskId: string; lane: string; task: Task }): Board {
  const next: Board = { ...board }
  for (const lane of Object.keys(next) as BoardLane[]) {
    next[lane] = (next[lane] ?? []).filter((t: Task) => String(t.id) !== String(payload.taskId))
  }
  const targetLane = payload.lane as BoardLane
  if (!next[targetLane]) {
    next[targetLane] = []
  }
  const laneTasks = next[targetLane] ?? []
  const existingIdx = laneTasks.findIndex((t: Task) => String(t.id) === String(payload.taskId))
  if (existingIdx >= 0) {
    const updated = [...laneTasks]
    updated[existingIdx] = payload.task
    next[targetLane] = updated
  } else {
    next[targetLane] = [...laneTasks, payload.task]
  }
  return next
}

function patchBoardFromEvent(data: unknown, prev: AppState): AppState | null {
  if (!data || typeof data !== 'object') return null
  const payload = data as {
    board?: Board
    delta?: boolean
    task?: Task
    taskId?: string
    lane?: string
  }
  if (payload.delta && payload.task && payload.taskId && payload.lane) {
    return {
      ...prev,
      board: mergeBoardDelta(prev.board, {
        taskId: String(payload.taskId),
        lane: String(payload.lane),
        task: payload.task,
      }),
    }
  }
  if (!payload.board) return null
  return { ...prev, board: payload.board }
}

function mapSprintProgress(data: Record<string, unknown>): SprintProgress {
  const phase = String(data.phase ?? 'po_plan')
  const validPhases = ['po_plan', 'sprint_step', 'done', 'cancelled'] as const
  const phaseVal = validPhases.includes(phase as SprintProgress['phase'])
    ? (phase as SprintProgress['phase'])
    : 'po_plan'
  return {
    phase: phaseVal,
    step: Number(data.step ?? 0),
    maxSteps: Number(data.maxSteps ?? data.max_steps ?? 20),
    agent: String(data.agent ?? ''),
    taskId: String(data.taskId ?? data.task_id ?? ''),
    taskTitle: String(data.taskTitle ?? data.task_title ?? ''),
    lane: String(data.lane ?? ''),
    status: data.status != null ? String(data.status) : undefined,
    intent: data.intent != null ? String(data.intent) : undefined,
    cardProgress: mapCardProgress(data.cardProgress ?? data.card_progress),
  }
}

function mapCardProgress(raw: unknown): CardWorkProgress | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const d = raw as Record<string, unknown>
  const gates = Array.isArray(d.gatesRemaining)
    ? d.gatesRemaining.map((g) => String(g))
    : Array.isArray(d.gates_remaining)
      ? (d.gates_remaining as unknown[]).map((g) => String(g))
      : undefined
  const files = Array.isArray(d.filesThisStep)
    ? d.filesThisStep.map((f) => String(f))
    : Array.isArray(d.files_this_step)
      ? (d.files_this_step as unknown[]).map((f) => String(f))
      : undefined
  return {
    subtasksDone: Number(d.subtasksDone ?? d.subtasks_done ?? 0) || 0,
    subtasksTotal: Number(d.subtasksTotal ?? d.subtasks_total ?? 0) || 0,
    stepsOnCard: Number(d.stepsOnCard ?? d.steps_on_card ?? 0) || 0,
    stuckLoops: Number(d.stuckLoops ?? d.stuck_loops ?? 0) || 0,
    poRoundTrips: Number(d.poRoundTrips ?? d.po_round_trips ?? 0) || 0,
    gatesRemaining: gates,
    filesThisStep: files,
    acCount: Number(d.acCount ?? d.ac_count ?? 0) || 0,
    lane: d.lane != null ? String(d.lane) : undefined,
  }
}

function mapAgentRun(raw: Record<string, unknown>): AgentRunState {
  const recentRaw = raw.recent_tools ?? raw.recentTools
  const recentTools = Array.isArray(recentRaw)
    ? recentRaw.map((t) => {
        const entry = t as Record<string, unknown>
        return {
          toolName: String(entry.toolName ?? entry.tool_name ?? '?'),
          toolSuccess: Boolean(entry.toolSuccess ?? entry.tool_success),
          toolOutput: String(entry.toolOutput ?? entry.tool_output ?? ''),
          durationMs: Number(entry.durationMs ?? entry.duration_ms ?? 0),
          timestamp: String(entry.timestamp ?? ''),
        }
      })
    : []
  return {
    runId: String(raw.run_id ?? raw.runId ?? ''),
    taskId: String(raw.task_id ?? raw.taskId ?? ''),
    agent: String(raw.agent ?? ''),
    status: (raw.status as AgentRunState['status']) ?? 'thinking',
    currentTool: (raw.current_tool ?? raw.currentTool) as string | null | undefined,
    startedAt: String(raw.started_at ?? raw.startedAt ?? ''),
    error: (raw.error as string | null | undefined) ?? null,
    iteration: Number(raw.iteration ?? 0) || undefined,
    maxIterations: Number(raw.max_iterations ?? raw.maxIterations ?? 0) || undefined,
    recentTools,
    intent: raw.intent != null ? String(raw.intent) : null,
    cardProgress: mapCardProgress(raw.cardProgress ?? raw.card_progress) ?? null,
    currentToolDetail:
      raw.currentToolDetail != null || raw.current_tool_detail != null
        ? String(raw.currentToolDetail ?? raw.current_tool_detail)
        : null,
  }
}

function mapToolSource(raw: unknown): ToolExecutionEvent['source'] {
  if (
    raw === 'manual' ||
    raw === 'replay' ||
    raw === 'agent' ||
    raw === 'orchestrator' ||
    raw === 'context_inject' ||
    raw === 'user'
  ) {
    return raw
  }
  return 'agent'
}

function buildToolEventId(payload: Record<string, unknown>): string {
  if (payload.eventId != null && String(payload.eventId).trim()) {
    return String(payload.eventId)
  }
  return `${String(payload.runId ?? payload.run_id ?? 'run')}-${String(payload.toolName ?? '?')}-${String(payload.timestamp ?? Date.now())}`
}

function mapToolStart(payload: Record<string, unknown>): ToolExecutionEvent {
  return {
    id: buildToolEventId(payload),
    runId: String(payload.runId ?? payload.run_id ?? ''),
    taskId: payload.taskId != null ? String(payload.taskId) : undefined,
    agent: String(payload.agent ?? ''),
    toolName: String(payload.toolName ?? '?'),
    toolArgs: (payload.toolArgs ?? payload.tool_args) as Record<string, unknown> | undefined,
    timestamp: String(payload.timestamp ?? new Date().toISOString()),
    status: 'running',
    source: mapToolSource(payload.source),
  }
}

function mapRunCommandFields(
  payload: Record<string, unknown>,
): Pick<ToolExecutionEvent, 'command' | 'diagnostics' | 'diagnosticsCount'> {
  const command = payload.command != null ? String(payload.command) : undefined
  const countRaw = payload.diagnosticsCount ?? payload.diagnostics_count
  const diagnosticsCount =
    countRaw != null && countRaw !== '' && Number.isFinite(Number(countRaw))
      ? Number(countRaw)
      : undefined
  const rawDiagnostics = payload.diagnostics
  let diagnostics: CommandDiagnostic[] | undefined
  if (Array.isArray(rawDiagnostics)) {
    diagnostics = rawDiagnostics.map((item) => {
      const row = item as Record<string, unknown>
      return {
        file: String(row.file ?? '?'),
        line: Number(row.line ?? 0),
        column: Number(row.column ?? 0),
        severity: String(row.severity ?? 'info'),
        message: String(row.message ?? ''),
      }
    })
  }
  return { command, diagnostics, diagnosticsCount }
}

function historyPayloadToEvent(payload: Record<string, unknown>): ToolExecutionEvent {
  const success = payload.toolSuccess !== false
  const statusRaw = payload.status
  const status: ToolExecutionEvent['status'] =
    statusRaw === 'running'
      ? 'running'
      : statusRaw === 'awaiting_approval'
        ? 'awaiting_approval'
        : success
          ? 'completed'
          : 'failed'
  const runCommandStatus =
    payload.runCommandStatus != null ? String(payload.runCommandStatus) : undefined
  const exitCodeRaw = payload.exitCode ?? payload.exit_code
  const exitCode =
    exitCodeRaw != null && exitCodeRaw !== '' ? Number(exitCodeRaw) : undefined
  return {
    id: buildToolEventId(payload),
    runId: String(payload.runId ?? payload.run_id ?? ''),
    taskId: payload.taskId != null ? String(payload.taskId) : undefined,
    agent: String(payload.agent ?? ''),
    toolName: String(payload.toolName ?? '?'),
    toolArgs: (payload.toolArgs ?? payload.tool_args) as Record<string, unknown> | undefined,
    toolSuccess: success,
    toolOutput: String(payload.toolOutput ?? ''),
    durationMs: Number(payload.durationMs ?? payload.duration_ms ?? 0),
    timestamp: String(payload.timestamp ?? new Date().toISOString()),
    status,
    source: mapToolSource(payload.source),
    exitCode: Number.isFinite(exitCode) ? exitCode : undefined,
    runCommandStatus,
    ...mapRunCommandFields(payload),
  }
}

function applyToolEnd(events: ToolExecutionEvent[], payload: Record<string, unknown>): ToolExecutionEvent[] {
  const id = buildToolEventId(payload)
  const success = payload.toolSuccess !== false
  const runCommandStatus =
    payload.runCommandStatus != null ? String(payload.runCommandStatus) : undefined
  const exitCodeRaw = payload.exitCode ?? payload.exit_code
  const exitCode =
    exitCodeRaw != null && exitCodeRaw !== '' ? Number(exitCodeRaw) : undefined
  const statusRaw = payload.status
  const status: ToolExecutionEvent['status'] =
    statusRaw === 'awaiting_approval'
      ? 'awaiting_approval'
      : success
        ? 'completed'
        : 'failed'
  const nextEntry: ToolExecutionEvent = {
    id,
    runId: String(payload.runId ?? payload.run_id ?? ''),
    taskId: payload.taskId != null ? String(payload.taskId) : undefined,
    agent: String(payload.agent ?? ''),
    toolName: String(payload.toolName ?? '?'),
    toolArgs: (payload.toolArgs ?? payload.tool_args) as Record<string, unknown> | undefined,
    toolSuccess: success,
    toolOutput: String(payload.toolOutput ?? ''),
    durationMs: Number(payload.durationMs ?? payload.duration_ms ?? 0),
    timestamp: String(payload.timestamp ?? new Date().toISOString()),
    status,
    source: mapToolSource(payload.source),
    exitCode: Number.isFinite(exitCode) ? exitCode : undefined,
    runCommandStatus,
    ...mapRunCommandFields(payload),
  }
  const idx = events.findIndex((e) => e.id === id)
  if (idx >= 0) {
    const copy = [...events]
    copy[idx] = { ...copy[idx], ...nextEntry }
    return copy
  }
  const runningIdx = [...events]
    .reverse()
    .findIndex(
      (e) =>
        e.status === 'running' &&
        e.toolName === nextEntry.toolName &&
        e.runId === nextEntry.runId,
    )
  if (runningIdx >= 0) {
    const realIdx = events.length - 1 - runningIdx
    const copy = [...events]
    copy[realIdx] = { ...copy[realIdx], ...nextEntry }
    return copy
  }
  return [...events, nextEntry].slice(-200)
}

function mapApprovalFromEvent(data: Record<string, unknown>): PendingToolApproval {
  return {
    id: String(data.id ?? ''),
    runId: String(data.runId ?? data.run_id ?? ''),
    taskId: data.taskId != null ? String(data.taskId) : undefined,
    agent: String(data.agent ?? ''),
    toolName: String(data.toolName ?? data.tool_name ?? ''),
    toolArgs: (data.toolArgs ?? data.tool_args) as Record<string, unknown> | undefined,
    timestamp: String(data.timestamp ?? ''),
    nonBlocking: data.nonBlocking === true,
  }
}

export function useAppState() {
  const [state, setState] = useState<AppState>(defaultState)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activityEvents, setActivityEvents] = useState<ActivityEvent[]>([])
  const [activityWasCleared, setActivityWasCleared] = useState(false)
  const [pendingTools, setPendingTools] = useState<import('../types').PendingToolRequest[]>([])
  const [pendingApprovals, setPendingApprovals] = useState<PendingToolApproval[]>([])
  const [activeRun, setActiveRun] = useState<AgentRunState | null>(null)
  const [displayRun, setDisplayRun] = useState<AgentRunState | null>(null)
  const [currentTool, setCurrentTool] = useState<string | null>(null)
  const [toolEvents, setToolEvents] = useState<ToolExecutionEvent[]>([])
  const [terminalSessions, setTerminalSessions] = useState<BackgroundTerminalSession[]>([])
  const [toolStartTick, setToolStartTick] = useState(0)
  const [sprintProgress, setSprintProgress] = useState<SprintProgress | null>(null)
  const [indexProgress, setIndexProgress] = useState<IndexProgress | null>(null)
  const [planOutline, setPlanOutline] = useState('')
  const [planOutlineStreaming, setPlanOutlineStreaming] = useState(false)
  const liveActivityRef = useRef<ActivityEvent[]>([])
  const activityClearedAtRef = useRef<string | null>(null)
  const lastProjectIdRef = useRef<string>(defaultState.projectId)
  const boardRef = useRef<Board>(defaultState.board)
  const displayRunTimerRef = useRef<number | null>(null)
  const sprintRefreshDebounceRef = useRef<number | null>(null)
  const activitySyncDebounceRef = useRef<number | null>(null)
  const toolHistoryDebounceRef = useRef<number | null>(null)
  const logBatchRef = useRef<SystemLog[]>([])
  const logFlushTimerRef = useRef<number | null>(null)
  const [sseLive, setSseLive] = useState(true)
  const [lastToolEventAt, setLastToolEventAt] = useState<string | null>(null)

  const scheduleDisplayRunClear = useCallback(() => {
    if (displayRunTimerRef.current) {
      window.clearTimeout(displayRunTimerRef.current)
    }
    displayRunTimerRef.current = window.setTimeout(() => {
      setDisplayRun(null)
      displayRunTimerRef.current = null
    }, 3000)
  }, [])

  useEffect(() => {
    boardRef.current = state.board
    if (lastProjectIdRef.current !== state.projectId) {
      lastProjectIdRef.current = state.projectId
      activityClearedAtRef.current = null
      liveActivityRef.current = []
      setActivityWasCleared(false)
    }
  }, [state.board, state.projectId])

  const buildActivityEvents = useCallback((board: Board) => {
    const hydrated = hydrateActivityFromBoard(board)
    const merged = mergeActivityEvents(hydrated, liveActivityRef.current)
    return filterActivityAfterClear(merged, activityClearedAtRef.current)
  }, [])

  const syncActivityFromBoard = useCallback(
    (board: Board) => {
      setActivityEvents(buildActivityEvents(board))
    },
    [buildActivityEvents],
  )

  const debouncedSyncActivityFromBoard = useCallback(
    (board: Board) => {
      if (activitySyncDebounceRef.current) {
        window.clearTimeout(activitySyncDebounceRef.current)
      }
      activitySyncDebounceRef.current = window.setTimeout(() => {
        activitySyncDebounceRef.current = null
        syncActivityFromBoard(board)
      }, 1500)
    },
    [syncActivityFromBoard],
  )

  const refreshPendingTools = useCallback(async () => {
    try {
      const data = await fetchPendingTools()
      setPendingTools(data.pending ?? [])
    } catch {
      setPendingTools([])
    }
  }, [])

  const refreshTerminalSessions = useCallback(async () => {
    try {
      const data = await fetchBackgroundTerminals()
      setTerminalSessions((prev) => {
        const byId = new Map(prev.map((s) => [s.id, s]))
        return (data.sessions ?? []).map((s) => {
          const existing = byId.get(s.id)
          return {
            id: s.id,
            command: s.command,
            output: existing?.output ?? '',
            done: s.done,
            exitCode: s.exitCode,
            startedAt: s.startedAt,
          }
        })
      })
    } catch {
      /* optional during startup */
    }
  }, [])

  const stopTerminalSession = useCallback(async (sessionId: string) => {
    try {
      await stopBackgroundTerminal(sessionId)
      setTerminalSessions((prev) =>
        prev.map((s) => (s.id === sessionId ? { ...s, done: true } : s)),
      )
    } catch {
      /* best effort */
    }
  }, [])

  const refreshPendingApprovals = useCallback(async () => {
    try {
      const data = await fetchPendingApprovals()
      setPendingApprovals(data.pending ?? [])
    } catch {
      setPendingApprovals([])
    }
  }, [])

  const refreshToolHistory = useCallback(async () => {
    try {
      const data = await fetchToolHistory()
      const incoming = (data.events ?? []).map((e) =>
        historyPayloadToEvent(e as Record<string, unknown>),
      )
      incoming.sort((a, b) => String(b.timestamp).localeCompare(String(a.timestamp)))
      setToolEvents((prev) => mergeToolHistory(prev, incoming))
    } catch {
      /* history endpoint optional during startup */
    }
  }, [])

  const debouncedRefreshToolHistory = useCallback(() => {
    if (toolHistoryDebounceRef.current) {
      window.clearTimeout(toolHistoryDebounceRef.current)
    }
    toolHistoryDebounceRef.current = window.setTimeout(() => {
      toolHistoryDebounceRef.current = null
      void refreshToolHistory()
    }, 300)
  }, [refreshToolHistory])

  const clearLogs = useCallback(async () => {
    try {
      await clearLogsApi()
      setState((prev) => ({ ...prev, logs: [] }))
    } catch {
      setState((prev) => ({ ...prev, logs: [] }))
    }
  }, [])

  const clearActivity = useCallback(() => {
    activityClearedAtRef.current = activityTimestampNow()
    liveActivityRef.current = []
    setActivityEvents([])
    setActivityWasCleared(true)
  }, [])

  const refresh = useCallback(async (options?: { includeFiles?: boolean }) => {
    try {
      const includeFiles = options?.includeFiles !== false
      const data = await fetchState({ includeFiles })
      setState((prev) => ({
        ...data,
        files: includeFiles ? data.files : prev.files,
      }))
      if (data.projectPlanOutline != null) {
        setPlanOutline(String(data.projectPlanOutline))
      }
      syncActivityFromBoard(data.board)
      setActiveRun(data.activeAgentRun ? mapAgentRun(data.activeAgentRun as unknown as Record<string, unknown>) : null)
      setPendingApprovals(data.pendingToolApprovals ?? [])
      setError(null)
      void refreshPendingTools()
      void refreshPendingApprovals()
      void refreshToolHistory()
      void refreshTerminalSessions()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch state')
    }
  }, [syncActivityFromBoard, refreshPendingTools, refreshPendingApprovals, refreshToolHistory, refreshTerminalSessions])

  const applyState = useCallback(
    (data: AppState) => {
      setState(data)
      if (data.projectPlanOutline != null) {
        setPlanOutline(String(data.projectPlanOutline))
      }
      syncActivityFromBoard(data.board)
      setActiveRun(data.activeAgentRun ? mapAgentRun(data.activeAgentRun as unknown as Record<string, unknown>) : null)
      setPendingApprovals(data.pendingToolApprovals ?? [])
      setError(null)
      void refreshPendingTools()
      void refreshPendingApprovals()
      void refreshToolHistory()
    },
    [syncActivityFromBoard, refreshPendingTools, refreshPendingApprovals, refreshToolHistory],
  )

  const appendLog = useCallback((log: SystemLog) => {
    logBatchRef.current.push(log)
    if (logFlushTimerRef.current != null) return
    logFlushTimerRef.current = window.setTimeout(() => {
      logFlushTimerRef.current = null
      const batch = logBatchRef.current
      logBatchRef.current = []
      if (batch.length === 0) return
      setState((prev) => {
        let logs = prev.logs
        for (const entry of batch) {
          logs = capLogs(logs, entry)
        }
        return { ...prev, logs }
      })
    }, 100)
  }, [])

  const debouncedRefreshAfterSprint = useCallback(() => {
    if (sprintRefreshDebounceRef.current) {
      window.clearTimeout(sprintRefreshDebounceRef.current)
    }
    sprintRefreshDebounceRef.current = window.setTimeout(() => {
      sprintRefreshDebounceRef.current = null
      void refresh({ includeFiles: false })
    }, 500)
  }, [refresh])

  const appendActivity = useCallback(
    (event: ActivityEvent) => {
      liveActivityRef.current = [...liveActivityRef.current, event].slice(-200)
      setActivityEvents(buildActivityEvents(boardRef.current))
    },
    [buildActivityEvents],
  )

  const clearToolEvents = useCallback(async () => {
    try {
      await clearToolHistory()
    } catch {
      /* clear best-effort */
    }
    setToolEvents([])
  }, [])

  const mergeToolEvent = useCallback((payload: Record<string, unknown>) => {
    setToolEvents((prev) => applyToolEnd(prev, payload))
  }, [])

  const toolFailureCount = useMemo(
    () => toolEvents.filter((e) => e.status === 'failed').length,
    [toolEvents],
  )
  const toolRunningCount = useMemo(
    () => toolEvents.filter((e) => e.status === 'running').length,
    [toolEvents],
  )

  const refreshRef = useRef(refresh)
  refreshRef.current = refresh
  const appendLogRef = useRef(appendLog)
  appendLogRef.current = appendLog
  const appendActivityRef = useRef(appendActivity)
  appendActivityRef.current = appendActivity
  const syncActivityFromBoardRef = useRef(syncActivityFromBoard)
  syncActivityFromBoardRef.current = syncActivityFromBoard
  const debouncedSyncActivityFromBoardRef = useRef(debouncedSyncActivityFromBoard)
  debouncedSyncActivityFromBoardRef.current = debouncedSyncActivityFromBoard
  const refreshPendingToolsRef = useRef(refreshPendingTools)
  refreshPendingToolsRef.current = refreshPendingTools
  const refreshPendingApprovalsRef = useRef(refreshPendingApprovals)
  refreshPendingApprovalsRef.current = refreshPendingApprovals
  const scheduleDisplayRunClearRef = useRef(scheduleDisplayRunClear)
  scheduleDisplayRunClearRef.current = scheduleDisplayRunClear
  const debouncedRefreshAfterSprintRef = useRef(debouncedRefreshAfterSprint)
  debouncedRefreshAfterSprintRef.current = debouncedRefreshAfterSprint
  const debouncedRefreshToolHistoryRef = useRef(debouncedRefreshToolHistory)
  debouncedRefreshToolHistoryRef.current = debouncedRefreshToolHistory

  useEffect(() => {
    void refresh()
    void refreshToolHistory()
    void refreshTerminalSessions()
  }, [refresh, refreshToolHistory, refreshTerminalSessions])

  useEffect(() => {
    const source = subscribeEvents((event) => {
      setSseLive(true)
      if (event.type === 'state' && event.data) {
        const data = event.data as AppState
        setState(data)
        if (data.projectPlanOutline != null) {
          setPlanOutline(String(data.projectPlanOutline))
        }
        syncActivityFromBoardRef.current(data.board)
      } else if (event.type === 'log' && event.data) {
        appendLogRef.current(event.data as SystemLog)
      } else if (event.type === 'board') {
        setState((prev) => {
          const next = patchBoardFromEvent(event.data, prev) ?? prev
          debouncedSyncActivityFromBoardRef.current(next.board)
          return next
        })
      } else if (event.type === 'files') {
        void refreshRef.current({ includeFiles: true })
      } else if (event.type === 'sprint') {
        debouncedRefreshAfterSprintRef.current()
        setActiveRun(null)
        setCurrentTool(null)
        setDisplayRun(null)
      } else if (event.type === 'sprint_progress' && event.data) {
        const progress = mapSprintProgress(event.data as Record<string, unknown>)
        if (progress.phase === 'done' || progress.status === 'done') {
          setSprintProgress(null)
          debouncedRefreshAfterSprintRef.current()
        } else {
          setSprintProgress(progress)
        }
      } else if (event.type === 'index_progress' && event.data) {
        const d = event.data as Record<string, unknown>
        setIndexProgress({
          phase: String(d.phase ?? ''),
          filesDone: Number(d.filesDone ?? 0),
          filesTotal: Number(d.filesTotal ?? 0),
          chunks: Number(d.chunks ?? 0),
          currentFile: d.currentFile != null ? String(d.currentFile) : undefined,
          embedFailures: Number(d.embedFailures ?? 0),
        })
        if (d.phase === 'done' || d.phase === 'error') {
          window.setTimeout(() => setIndexProgress(null), 4000)
        }
      } else if (event.type === 'plan_chunk' && event.data) {
        const d = event.data as Record<string, unknown>
        if (d.phase === 'start') {
          setPlanOutlineStreaming(true)
          setPlanOutline('')
        } else if (d.phase === 'done') {
          setPlanOutlineStreaming(false)
          if (d.outline != null) {
            setPlanOutline(String(d.outline))
          }
        } else if (d.chunk != null) {
          setPlanOutline((prev) => prev + String(d.chunk))
        }
      } else if (event.type === 'activity' && event.data) {
        appendActivityRef.current(event.data as ActivityEvent)
      } else if (event.type === 'pending_tool' && event.data) {
        void refreshPendingToolsRef.current()
      } else if (event.type === 'agent_run' && event.data) {
        const run = mapAgentRun(event.data as Record<string, unknown>)
        if (run.status === 'completed' || run.status === 'failed') {
          setDisplayRun(run)
          setActiveRun(null)
          setCurrentTool(null)
          scheduleDisplayRunClearRef.current()
        } else {
          setActiveRun(run)
          setDisplayRun(run)
          setCurrentTool(run.currentTool ?? null)
        }
      } else if (event.type === 'tool_start' && event.data) {
        const payload = event.data as Record<string, unknown>
        setLastToolEventAt(String(payload.timestamp ?? new Date().toISOString()))
        setToolEvents((prev) => [...prev, mapToolStart(payload)].slice(-200))
        setToolStartTick((t) => t + 1)
        const toolName = payload.toolName != null ? String(payload.toolName) : null
        setCurrentTool(toolName)
        setActiveRun((prev) => {
          const base =
            prev ??
            ({
              runId: '',
              taskId: String(payload.taskId ?? ''),
              agent: String(payload.agent ?? ''),
              status: 'tool_executing',
              currentTool: toolName,
              startedAt: new Date().toISOString(),
              recentTools: [],
            } as AgentRunState)
          return { ...base, status: 'tool_executing', currentTool: toolName }
        })
      } else if (event.type === 'tool_end' && event.data) {
        const payload = event.data as Record<string, unknown>
        setLastToolEventAt(String(payload.timestamp ?? new Date().toISOString()))
        const entry = {
          toolName: String(payload.toolName ?? '?'),
          toolSuccess: payload.toolSuccess !== false,
          toolOutput: String(payload.toolOutput ?? ''),
          durationMs: Number(payload.durationMs ?? 0),
          timestamp: String(payload.timestamp ?? ''),
        }
        const mergeRun = (prev: AgentRunState | null): AgentRunState | null => {
          if (!prev) return prev
          const recentTools = [...(prev.recentTools ?? []), entry].slice(-5)
          return { ...prev, recentTools, currentTool: null, status: 'thinking' }
        }
        setActiveRun((prev) => mergeRun(prev))
        setDisplayRun((prev) => mergeRun(prev))
        setCurrentTool(null)
        setToolEvents((prev) => applyToolEnd(prev, payload))
        debouncedRefreshToolHistoryRef.current()
      } else if (event.type === 'tool_approval_required' && event.data) {
        const approval = mapApprovalFromEvent(event.data as Record<string, unknown>)
        setPendingApprovals((prev) =>
          prev.some((p) => p.id === approval.id) ? prev : [...prev, approval],
        )
        void refreshPendingApprovalsRef.current()
      } else if (event.type === 'terminal_stream' && event.data) {
        const payload = event.data as Record<string, unknown>
        const sessionId = String(payload.sessionId ?? '')
        if (!sessionId) return
        setTerminalSessions((prev) => {
          const idx = prev.findIndex((s) => s.id === sessionId)
          if (payload.started) {
            const command = String(payload.command ?? '')
            if (idx >= 0) {
              return prev.map((s) =>
                s.id === sessionId ? { ...s, command: command || s.command, output: '' } : s,
              )
            }
            return [
              {
                id: sessionId,
                command,
                output: '',
                done: false,
                startedAt: new Date().toISOString(),
              },
              ...prev,
            ].slice(0, 20)
          }
          const chunk = payload.chunk != null ? String(payload.chunk) : ''
          const done = payload.done === true
          const exitCode =
            payload.exitCode != null ? Number(payload.exitCode) : undefined
          if (idx >= 0) {
            return prev.map((s) =>
              s.id === sessionId
                ? {
                    ...s,
                    output: appendTerminalOutput(s.output, chunk),
                    done: done || s.done,
                    exitCode: exitCode ?? s.exitCode,
                  }
                : s,
            )
          }
          return [
            {
              id: sessionId,
              command: '',
              output: appendTerminalOutput('', chunk),
              done,
              exitCode,
              startedAt: new Date().toISOString(),
            },
            ...prev,
          ].slice(0, 20)
        })
      }
    })

    const onError = () => setSseLive(false)
    source.addEventListener('error', onError)

    return () => {
      source.removeEventListener('error', onError)
      source.close()
      if (sprintRefreshDebounceRef.current) {
        window.clearTimeout(sprintRefreshDebounceRef.current)
      }
      if (activitySyncDebounceRef.current) {
        window.clearTimeout(activitySyncDebounceRef.current)
      }
      if (toolHistoryDebounceRef.current) {
        window.clearTimeout(toolHistoryDebounceRef.current)
      }
      if (logFlushTimerRef.current) {
        window.clearTimeout(logFlushTimerRef.current)
        logFlushTimerRef.current = null
      }
    }
  }, [])

  const runBarState = activeRun ?? displayRun

  return {
    state,
    setState,
    loading,
    setLoading,
    error,
    setError,
    refresh,
    applyState,
    appendLog,
    activityEvents,
    appendActivity,
    pendingTools,
    refreshPendingTools,
    pendingApprovals,
    refreshPendingApprovals,
    refreshTerminalSessions,
    stopTerminalSession,
    terminalSessions,
    activeRun: runBarState,
    currentTool,
    toolEvents,
    clearToolEvents,
    mergeToolEvent,
    refreshToolHistory,
    clearLogs,
    clearActivity,
    activityWasCleared,
    sseLive,
    lastToolEventAt,
    toolFailureCount,
    toolRunningCount,
    toolStartTick,
    sprintProgress,
    setSprintProgress,
    indexProgress,
    planOutline,
    setPlanOutline,
    planOutlineStreaming,
  }
}

export function useAutoSprint(
  brief: string,
  ollamaUrl: string,
  board: AppState['board'],
  workflowSettings: AppState['workflowSettings'],
  onState: (s: AppState) => void,
) {
  const [autoSprint, setAutoSprint] = useState(false)
  const [autoSprintPaused, setAutoSprintPaused] = useState(false)
  const [sprintRunning, setSprintRunning] = useState(false)
  const cancelRef = useRef<AbortController | null>(null)
  const backlogLenRef = useRef(board.Backlog?.length ?? 0)

  const startAutoSprint = useCallback(async () => {
    if (!hasSprintWork(board, workflowSettings)) {
      setAutoSprintPaused(true)
      return
    }

    setAutoSprintPaused(false)
    setSprintRunning(true)
    cancelRef.current = new AbortController()

    try {
      const data = await runSprint({
        brief,
        ollama_url: ollamaUrl,
        auto: true,
        max_steps: workflowSettings?.maxSprintSteps ?? 20,
      })
      onState(data)
      if (data.lastSprintSummary?.status === 'idle') {
        setAutoSprintPaused(true)
      }
    } catch {
      /* sprint may be cancelled or endpoint unavailable */
    } finally {
      setSprintRunning(false)
    }
  }, [brief, ollamaUrl, board, workflowSettings, onState])

  const stopAutoSprint = useCallback(async () => {
    setAutoSprint(false)
    setAutoSprintPaused(false)
    cancelRef.current?.abort()
    try {
      await cancelSprint()
    } catch {
      /* ignore */
    }
    setSprintRunning(false)
  }, [])

  useEffect(() => {
    if (!autoSprint || autoSprintPaused) return

    const interval = window.setInterval(() => {
      if (!sprintRunning && hasSprintWork(board, workflowSettings)) {
        void startAutoSprint()
      }
    }, 5000)

    return () => window.clearInterval(interval)
  }, [autoSprint, autoSprintPaused, sprintRunning, board, workflowSettings, startAutoSprint])

  useEffect(() => {
    const currentLen = board.Backlog?.length ?? 0
    if (
      autoSprint &&
      autoSprintPaused &&
      currentLen > backlogLenRef.current &&
      hasSprintWork(board, workflowSettings)
    ) {
      setAutoSprintPaused(false)
      void startAutoSprint()
    }
    backlogLenRef.current = currentLen
  }, [board.Backlog?.length, autoSprint, autoSprintPaused, board, workflowSettings, startAutoSprint])

  return {
    autoSprint,
    setAutoSprint,
    autoSprintPaused,
    sprintRunning,
    startAutoSprint,
    stopAutoSprint,
  }
}
