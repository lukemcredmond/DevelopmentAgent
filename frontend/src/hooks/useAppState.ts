import { useCallback, useEffect, useRef, useState } from 'react'
import { cancelSprint, fetchState, runSprint, subscribeEvents } from '../api/client'
import type { AppState, SystemLog } from '../types'
import { EMPTY_BOARD } from '../types'

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

export function useAppState() {
  const [state, setState] = useState<AppState>(defaultState)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

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

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const source = subscribeEvents((event) => {
      if (event.type === 'state' && event.data) {
        setState(event.data as AppState)
      } else if (event.type === 'log' && event.data) {
        appendLog(event.data as SystemLog)
      } else if (event.type === 'board' || event.type === 'files') {
        void refresh()
      } else if (event.type === 'sprint' && event.data) {
        void refresh()
      }
    })

    return () => source.close()
  }, [refresh, appendLog])

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
  }
}

export function useAutoSprint(
  brief: string,
  ollamaUrl: string,
  onState: (s: AppState) => void,
) {
  const [autoSprint, setAutoSprint] = useState(false)
  const [sprintRunning, setSprintRunning] = useState(false)
  const cancelRef = useRef<AbortController | null>(null)

  const startAutoSprint = useCallback(async () => {
    setAutoSprint(true)
    setSprintRunning(true)
    cancelRef.current = new AbortController()

    try {
      const data = await runSprint({
        brief,
        ollama_url: ollamaUrl,
        auto: true,
        max_steps: 20,
      })
      onState(data)
    } catch {
      /* sprint may be cancelled or endpoint unavailable */
    } finally {
      setSprintRunning(false)
    }
  }, [brief, ollamaUrl, onState])

  const stopAutoSprint = useCallback(async () => {
    setAutoSprint(false)
    cancelRef.current?.abort()
    try {
      await cancelSprint()
    } catch {
      /* ignore */
    }
    setSprintRunning(false)
  }, [])

  useEffect(() => {
    if (!autoSprint) return

    const interval = window.setInterval(() => {
      if (!sprintRunning) {
        void startAutoSprint()
      }
    }, 5000)

    return () => window.clearInterval(interval)
  }, [autoSprint, sprintRunning, startAutoSprint])

  return {
    autoSprint,
    setAutoSprint,
    sprintRunning,
    startAutoSprint,
    stopAutoSprint,
  }
}
