import { useCallback, useEffect, useRef, useState } from 'react'
import {
  cancelSprint,
  fetchPendingApprovals,
  fetchPendingTools,
  fetchState,
  runSprint,
  subscribeEvents,
} from '../api/client'
import type {
  ActivityEvent,
  AgentRunState,
  AppState,
  Board,
  PendingToolApproval,
  SystemLog,
  ToolExecutionEvent,
} from '../types'
import { EMPTY_BOARD, hasSprintWork } from '../types'
import { hydrateActivityFromBoard, mergeActivityEvents } from '../utils/activityFromBoard'

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

function patchBoardFromEvent(data: unknown, prev: AppState): AppState | null {
  if (!data || typeof data !== 'object') return null
  const payload = data as { board?: Board }
  if (!payload.board) return null
  return { ...prev, board: payload.board }
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
  }
}

function mapToolSource(raw: unknown): ToolExecutionEvent['source'] {
  if (raw === 'manual' || raw === 'replay' || raw === 'agent') return raw
  return 'agent'
}

function buildToolEventId(payload: Record<string, unknown>): string {
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

function applyToolEnd(events: ToolExecutionEvent[], payload: Record<string, unknown>): ToolExecutionEvent[] {
  const id = buildToolEventId(payload)
  const success = payload.toolSuccess !== false
  const runCommandStatus =
    payload.runCommandStatus != null ? String(payload.runCommandStatus) : undefined
  const exitCodeRaw = payload.exitCode ?? payload.exit_code
  const exitCode =
    exitCodeRaw != null && exitCodeRaw !== '' ? Number(exitCodeRaw) : undefined
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
    status: success ? 'completed' : 'failed',
    source: mapToolSource(payload.source),
    exitCode: Number.isFinite(exitCode) ? exitCode : undefined,
    runCommandStatus,
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
  }
}

export function useAppState() {
  const [state, setState] = useState<AppState>(defaultState)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activityEvents, setActivityEvents] = useState<ActivityEvent[]>([])
  const [pendingTools, setPendingTools] = useState<import('../types').PendingToolRequest[]>([])
  const [pendingApprovals, setPendingApprovals] = useState<PendingToolApproval[]>([])
  const [activeRun, setActiveRun] = useState<AgentRunState | null>(null)
  const [displayRun, setDisplayRun] = useState<AgentRunState | null>(null)
  const [currentTool, setCurrentTool] = useState<string | null>(null)
  const [toolEvents, setToolEvents] = useState<ToolExecutionEvent[]>([])
  const [toolStartTick, setToolStartTick] = useState(0)
  const liveActivityRef = useRef<ActivityEvent[]>([])
  const boardRef = useRef<Board>(defaultState.board)
  const displayRunTimerRef = useRef<number | null>(null)

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
  }, [state.board])

  const syncActivityFromBoard = useCallback((board: Board) => {
    const hydrated = hydrateActivityFromBoard(board)
    setActivityEvents(mergeActivityEvents(hydrated, liveActivityRef.current))
  }, [])

  const refreshPendingTools = useCallback(async () => {
    try {
      const data = await fetchPendingTools()
      setPendingTools(data.pending ?? [])
    } catch {
      setPendingTools([])
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

  const refresh = useCallback(async () => {
    try {
      const data = await fetchState()
      setState(data)
      syncActivityFromBoard(data.board)
      setActiveRun(data.activeAgentRun ? mapAgentRun(data.activeAgentRun as unknown as Record<string, unknown>) : null)
      setPendingApprovals(data.pendingToolApprovals ?? [])
      setError(null)
      void refreshPendingTools()
      void refreshPendingApprovals()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch state')
    }
  }, [syncActivityFromBoard, refreshPendingTools, refreshPendingApprovals])

  const applyState = useCallback(
    (data: AppState) => {
      setState(data)
      syncActivityFromBoard(data.board)
      setActiveRun(data.activeAgentRun ? mapAgentRun(data.activeAgentRun as unknown as Record<string, unknown>) : null)
      setPendingApprovals(data.pendingToolApprovals ?? [])
      setError(null)
      void refreshPendingTools()
      void refreshPendingApprovals()
    },
    [syncActivityFromBoard, refreshPendingTools, refreshPendingApprovals],
  )

  const appendLog = useCallback((log: SystemLog) => {
    setState((prev) => ({ ...prev, logs: [...prev.logs, log] }))
  }, [])

  const appendActivity = useCallback((event: ActivityEvent) => {
    liveActivityRef.current = [...liveActivityRef.current, event].slice(-200)
    setActivityEvents(
      mergeActivityEvents(hydrateActivityFromBoard(boardRef.current), liveActivityRef.current),
    )
  }, [])

  const clearToolEvents = useCallback(() => {
    setToolEvents([])
  }, [])

  const toolFailureCount = toolEvents.filter((e) => e.status === 'failed').length
  const toolRunningCount = toolEvents.filter((e) => e.status === 'running').length

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    syncActivityFromBoard(state.board)
  }, [state.board, syncActivityFromBoard])

  useEffect(() => {
    const source = subscribeEvents((event) => {
      if (event.type === 'state' && event.data) {
        const data = event.data as AppState
        setState(data)
        syncActivityFromBoard(data.board)
      } else if (event.type === 'log' && event.data) {
        appendLog(event.data as SystemLog)
      } else if (event.type === 'board') {
        setState((prev) => {
          const next = patchBoardFromEvent(event.data, prev) ?? prev
          syncActivityFromBoard(next.board)
          return next
        })
      } else if (event.type === 'files') {
        void refresh()
      } else if (event.type === 'sprint') {
        void refresh()
        setActiveRun(null)
        setCurrentTool(null)
        setDisplayRun(null)
      } else if (event.type === 'activity' && event.data) {
        appendActivity(event.data as ActivityEvent)
      } else if (event.type === 'pending_tool' && event.data) {
        void refreshPendingTools()
      } else if (event.type === 'agent_run' && event.data) {
        const run = mapAgentRun(event.data as Record<string, unknown>)
        if (run.status === 'completed' || run.status === 'failed') {
          setDisplayRun(run)
          setActiveRun(null)
          setCurrentTool(null)
          scheduleDisplayRunClear()
        } else {
          setActiveRun(run)
          setDisplayRun(run)
          setCurrentTool(run.currentTool ?? null)
        }
      } else if (event.type === 'tool_start' && event.data) {
        const payload = event.data as Record<string, unknown>
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
      } else if (event.type === 'tool_approval_required' && event.data) {
        const approval = mapApprovalFromEvent(event.data as Record<string, unknown>)
        setPendingApprovals((prev) =>
          prev.some((p) => p.id === approval.id) ? prev : [...prev, approval],
        )
        void refreshPendingApprovals()
      }
    })

    return () => source.close()
  }, [
    refresh,
    appendLog,
    appendActivity,
    syncActivityFromBoard,
    refreshPendingTools,
    refreshPendingApprovals,
    scheduleDisplayRunClear,
  ])

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
    activeRun: runBarState,
    currentTool,
    toolEvents,
    clearToolEvents,
    toolFailureCount,
    toolRunningCount,
    toolStartTick,
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
