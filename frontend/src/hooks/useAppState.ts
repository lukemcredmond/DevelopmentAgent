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
  return {
    runId: String(raw.run_id ?? raw.runId ?? ''),
    taskId: String(raw.task_id ?? raw.taskId ?? ''),
    agent: String(raw.agent ?? ''),
    status: (raw.status as AgentRunState['status']) ?? 'thinking',
    currentTool: (raw.current_tool ?? raw.currentTool) as string | null | undefined,
    startedAt: String(raw.started_at ?? raw.startedAt ?? ''),
    error: (raw.error as string | null | undefined) ?? null,
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
  const [currentTool, setCurrentTool] = useState<string | null>(null)
  const liveActivityRef = useRef<ActivityEvent[]>([])
  const boardRef = useRef<Board>(defaultState.board)

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
      } else if (event.type === 'activity' && event.data) {
        appendActivity(event.data as ActivityEvent)
      } else if (event.type === 'pending_tool' && event.data) {
        void refreshPendingTools()
      } else if (event.type === 'agent_run' && event.data) {
        const run = mapAgentRun(event.data as Record<string, unknown>)
        if (run.status === 'completed' || run.status === 'failed' || run.status === 'idle') {
          setActiveRun(null)
          setCurrentTool(null)
        } else {
          setActiveRun(run)
          setCurrentTool(run.currentTool ?? null)
        }
      } else if (event.type === 'tool_start' && event.data) {
        const payload = event.data as { toolName?: string; agent?: string; taskId?: string }
        setCurrentTool(payload.toolName ?? null)
        setActiveRun((prev) =>
          prev ?? {
            runId: '',
            taskId: String(payload.taskId ?? ''),
            agent: String(payload.agent ?? ''),
            status: 'tool_executing',
            currentTool: payload.toolName ?? null,
            startedAt: new Date().toISOString(),
          },
        )
      } else if (event.type === 'tool_end') {
        setCurrentTool(null)
      } else if (event.type === 'tool_approval_required' && event.data) {
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
  ])

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
    activeRun,
    currentTool,
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
