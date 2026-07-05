import { useCallback, useEffect, useRef, useState } from 'react'
import { cancelSprint, fetchState, runSprint, subscribeEvents } from '../api/client'
import type { ActivityEvent, AppState, Board, SystemLog } from '../types'
import { EMPTY_BOARD, hasSprintWork } from '../types'

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

const MAX_ACTIVITY = 200

function patchBoardFromEvent(data: unknown, prev: AppState): AppState | null {
  if (!data || typeof data !== 'object') return null
  const payload = data as { board?: Board }
  if (!payload.board) return null
  return { ...prev, board: payload.board }
}

export function useAppState() {
  const [state, setState] = useState<AppState>(defaultState)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activityEvents, setActivityEvents] = useState<ActivityEvent[]>([])

  const refresh = useCallback(async () => {
    try {
      const data = await fetchState()
      setState(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch state')
    }
  }, [])

  const applyState = useCallback((data: AppState) => {
    setState(data)
    setError(null)
  }, [])

  const appendLog = useCallback((log: SystemLog) => {
    setState((prev) => ({ ...prev, logs: [...prev.logs, log] }))
  }, [])

  const appendActivity = useCallback((event: ActivityEvent) => {
    setActivityEvents((prev) => [...prev.slice(-(MAX_ACTIVITY - 1)), event])
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const source = subscribeEvents((event) => {
      if (event.type === 'state' && event.data) {
        setState(event.data as AppState)
      } else if (event.type === 'log' && event.data) {
        appendLog(event.data as SystemLog)
      } else if (event.type === 'board') {
        setState((prev) => patchBoardFromEvent(event.data, prev) ?? prev)
      } else if (event.type === 'files') {
        void refresh()
      } else if (event.type === 'sprint') {
        void refresh()
      } else if (event.type === 'activity' && event.data) {
        appendActivity(event.data as ActivityEvent)
      }
    })

    return () => source.close()
  }, [refresh, appendLog, appendActivity])

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
