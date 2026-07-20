import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  addManualTask,
  approveTask,
  assignSkills,
  ApiError,
  checkOllamaHealth,
  clearChatHistory,
  clearTaskTranscript,
  createProject,
  deleteProject,
  deleteTask,
  diagnoseTask,
  dismissSprintRecovery,
  exportProject,
  fetchFileDiff,
  fetchSkills,
  fetchSkillSuggestions,
  importProject,
  loadProject,
  moveTask,
  planAndRun,
  removeSkill,
  escapeSubtaskLoop,
  reorderTasks,
  resetWorkspace,
  retryAgentStep,
  extendAgentStep,
  claimReadyBacklogCards,
  clearAllTasks,
  escalateNeedsUserToPo,
  resolveToolApproval,
  runInProgressStep,
  resolveUserQuestion,
  injectToolEvidence,
  injectProjectToolEvidence,
  deleteProjectToolEvidence,
  clearProjectToolEvidence,
  splitTask,
  triggerPlanOutline,
  triggerPlanBacklog,
  triggerStep,
  updateConfig,
  updateTask,
  updateWorkflowSettings,
} from './api/client'
import ActivityPanel from './components/ActivityPanel'
import AgentConsole from './components/AgentConsole'
import BriefPanel, { readBriefOpen } from './components/BriefPanel'
import ChatPanel, { type ChatUiMessage } from './components/ChatPanel'
import EditorPanel from './components/EditorPanel'
import EvidencePanel from './components/EvidencePanel'
import GitPanel from './components/GitPanel'
import KanbanBoard from './components/KanbanBoard'
import ManualTaskModal from './components/ManualTaskModal'
import NewProjectModal from './components/NewProjectModal'
import SearchPanel from './components/SearchPanel'
import SettingsSlideOver from './components/SettingsSlideOver'
import Sidebar from './components/Sidebar'
import SkillModal from './components/SkillModal'
import SlideOver from './components/SlideOver'
import StatusStrip, { type StatusItem } from './components/StatusStrip'
import TaskDetailModal from './components/TaskDetailModal'
import TerminalPanel from './components/TerminalPanel'
import ToolResolutionModal from './components/ToolResolutionModal'
import ToolApprovalModal from './components/ToolApprovalModal'
import AgentRunBar from './components/AgentRunBar'
import ModelDebugPanel from './components/ModelDebugPanel'
import OllamaServiceLogPanel from './components/OllamaServiceLogPanel'
import FileDiffModal from './components/FileDiffModal'
import SprintProgressBar from './components/SprintProgressBar'
import BottomPanelResize, {
  BOTTOM_PANEL_MIN,
  readBottomPanelCollapsed,
  readBottomPanelHeight,
  writeBottomPanelCollapsed,
  writeBottomPanelHeight,
} from './components/BottomPanelResize'
import CommandPalette, {
  cardPaletteItems,
  collectBoardTasks,
  type CommandPaletteItem,
} from './components/CommandPalette'
import KanbanToggleBar, { readKanbanOpen, writeKanbanOpen } from './components/KanbanToggleBar'
import ToolsPanel from './components/ToolsPanel'
import MemoryPanel from './components/MemoryPanel'
import { useAppState, useAutoSprint } from './hooks/useAppState'
import { useTheme } from './hooks/useTheme'
import type { AgentId, AppState, BoardLane, BriefCategory, ChatMessageRecord, PendingToolApproval, PendingToolRequest, SkillSuggestion, Task, WorkflowSettings } from './types'
import { countClaimableBacklogTasks, getDisplayLanes } from './types'
import { findTaskOnBoard } from './utils/taskFormat'
import { buildTaskRunInfo } from './utils/taskRunInfo'

type BottomTab =
  | 'console'
  | 'activity'
  | 'tools'
  | 'evidence'
  | 'model'
  | 'ollamaServer'
  | 'editor'
  | 'memory'
  | 'chat'
  | 'terminal'
  | 'search'
  | 'git'

const WORKSPACE_OPEN_KEY = 'allhands-workspace-open'

function readWorkspaceOpen(): boolean {
  try {
    return sessionStorage.getItem(WORKSPACE_OPEN_KEY) === 'true'
  } catch {
    return false
  }
}

function writeWorkspaceOpen(open: boolean): void {
  try {
    if (open) sessionStorage.setItem(WORKSPACE_OPEN_KEY, 'true')
    else sessionStorage.removeItem(WORKSPACE_OPEN_KEY)
  } catch {
    /* ignore */
  }
}

function chatRecordsToUi(messages: ChatMessageRecord[] | undefined): ChatUiMessage[] {
  return (messages ?? []).map((m, i) => ({
    id: `stored-${i}-${m.timestamp ?? i}`,
    role: m.role,
    content: m.content,
    agent: m.agent as AgentId | undefined,
  }))
}

function applyStateFields(
  data: AppState,
  setters: {
    setBrief: (v: string) => void
    setProjectName: (v: string) => void
    setWorkspaceDir: (v: string) => void
    setSkillsDir: (v: string) => void
    setPoModel: (v: string) => void
    setDevModel: (v: string) => void
    setCrModel: (v: string) => void
    setQaModel: (v: string) => void
  },
) {
  setters.setBrief(data.brief ?? '')
  setters.setProjectName(data.projectName)
  setters.setWorkspaceDir(data.workspaceDir)
  setters.setSkillsDir(data.skillsDir)
  setters.setPoModel(data.models?.po ?? 'llama3:8b')
  setters.setDevModel(data.models?.dev ?? 'qwen2.5-coder:14b')
  setters.setCrModel(data.models?.cr ?? 'qwen2.5-coder:7b')
  setters.setQaModel(data.models?.qa ?? 'qwen2.5-coder:7b')
}

export default function App() {
  const { theme, toggleTheme, isDark } = useTheme()
  const {
    state,
    setState,
    loading,
    setLoading,
    applyState,
    refresh,
    activityEvents,
    pendingTools,
    refreshPendingTools,
    pendingApprovals,
    refreshPendingApprovals,
    activeRun,
    currentTool,
    toolEvents,
    clearToolEvents,
    mergeToolEvent,
    terminalSessions,
    stopTerminalSession,
    toolFailureCount,
    toolRunningCount,
    sprintProgress,
    setSprintProgress,
    indexProgress,
    refreshToolHistory,
    clearLogs,
    clearActivity,
    activityWasCleared,
    sseLive,
    lastToolEventAt,
    planOutline,
    setPlanOutline,
    planOutlineStreaming,
  } = useAppState()

  const [planRunActive, setPlanRunActive] = useState(false)
  /** Long agent HTTP (Plan / Step / Plan & Run) — progress chrome only; does not set global loading. */
  const [sprintBusy, setSprintBusy] = useState(false)

  const [ollamaUrl, setOllamaUrl] = useState('http://localhost:11434')
  const [brief, setBrief] = useState('')
  const [projectName, setProjectName] = useState('My Local Scrum Project')
  const [workspaceDir, setWorkspaceDir] = useState('./workspace')
  const [skillsDir, setSkillsDir] = useState('./global_skills')
  const [poModel, setPoModel] = useState('llama3:8b')
  const [devModel, setDevModel] = useState('qwen2.5-coder:14b')
  const [crModel, setCrModel] = useState('qwen2.5-coder:7b')
  const [qaModel, setQaModel] = useState('qwen2.5-coder:7b')

  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [workspaceOpen, setWorkspaceOpen] = useState(readWorkspaceOpen)
  const [workspaceBarDismissed, setWorkspaceBarDismissed] = useState(false)
  const [showDiff, setShowDiff] = useState(false)
  const [bottomTab, setBottomTab] = useState<BottomTab>('console')
  const [memoryCount, setMemoryCount] = useState(0)
  const [kanbanOpen, setKanbanOpen] = useState(readKanbanOpen)
  const [briefOpen, setBriefOpen] = useState(() => (readKanbanOpen() ? false : readBriefOpen()))
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [bottomPanelHeight, setBottomPanelHeight] = useState(readBottomPanelHeight)
  const [panelMaximized, setPanelMaximized] = useState(false)
  const [bottomPanelCollapsed, setBottomPanelCollapsed] = useState(readBottomPanelCollapsed)
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false)
  const [retryingStep, setRetryingStep] = useState(false)
  const [extendingStep, setExtendingStep] = useState(false)
  const [fileDiffModal, setFileDiffModal] = useState<{
    path: string
    previousContent: string
    content: string
  } | null>(null)
  const [toolsPreferredSubTab, setToolsPreferredSubTab] = useState<
    'log' | 'manual' | 'replay' | undefined
  >(undefined)
  const workspaceColumnRef = useRef<HTMLDivElement>(null)
  const preMaximizeHeightRef = useRef(bottomPanelHeight)
  const preCollapseHeightRef = useRef(
    Math.max(BOTTOM_PANEL_MIN, readBottomPanelHeight()),
  )
  const [fileTreeKey, setFileTreeKey] = useState(0)

  const [selectedTask, setSelectedTask] = useState<Task | null>(null)
  const [skillModalAgent, setSkillModalAgent] = useState<AgentId | null>(null)
  const [skillSearch, setSkillSearch] = useState('')
  const [selectedSkillFiles, setSelectedSkillFiles] = useState<string[]>([])
  const [skillModalLoading, setSkillModalLoading] = useState(false)
  const [modalSkills, setModalSkills] = useState(state.availableSkills)
  const [modalSkillsDir, setModalSkillsDir] = useState(skillsDir)
  const [modalBriefCategories, setModalBriefCategories] = useState<BriefCategory[]>([])
  const [modalSuggestions, setModalSuggestions] = useState<SkillSuggestion[]>([])
  const [skillSuggestionCounts, setSkillSuggestionCounts] = useState<Record<AgentId, number>>({
    po: 0,
    dev: 0,
    cr: 0,
    qa: 0,
  })

  const [chatAgent, setChatAgent] = useState<AgentId>('dev')
  const [chatInput, setChatInput] = useState('')
  const [chatMessages, setChatMessages] = useState<ChatUiMessage[]>([])
  const [chatContextFiles, setChatContextFiles] = useState<string[]>([])
  const [chatPinnedTask, setChatPinnedTask] = useState<Task | null>(null)

  const [showNewProject, setShowNewProject] = useState(false)
  const [newProjName, setNewProjName] = useState('')
  const [newProjDir, setNewProjDir] = useState('./workspace_new')

  const [showManualTask, setShowManualTask] = useState(false)
  const [manualTitle, setManualTitle] = useState('')
  const [manualDesc, setManualDesc] = useState('')

  const [showSprintSummary, setShowSprintSummary] = useState(false)
  const [ollamaOk, setOllamaOk] = useState<boolean | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionErrorShowModelLink, setActionErrorShowModelLink] = useState(false)
  const [actionNotice, setActionNotice] = useState<string | null>(null)
  const [pendingToolModal, setPendingToolModal] = useState<PendingToolRequest | null>(null)
  const [approvalModal, setApprovalModal] = useState<PendingToolApproval | null>(null)

  const findTaskLane = (taskId: string): BoardLane | null => {
    const lanes = getDisplayLanes(state.activeLanes, state.workflowSettings)
    for (const lane of lanes) {
      if ((state.board[lane] ?? []).some((t) => t.id === taskId)) return lane
    }
    return null
  }

  const selectedTaskLane = selectedTask ? findTaskLane(selectedTask.id) : null
  const chatPinnedLane = chatPinnedTask ? findTaskLane(chatPinnedTask.id) : null

  const handleDiscussWithAgent = (task: Task, lane: BoardLane | null) => {
    setChatPinnedTask(task)
    setSelectedTask(null)
    setBottomTab('chat')
    if (lane === 'Refinement') {
      const status = task.refinementStatus ?? 'pending'
      setChatAgent(status === 'dev_reviewed' ? 'po' : 'dev')
    } else if (lane === 'Needs User' || lane === 'Needs PO') {
      setChatAgent('po')
    }
  }

  const handleInjectToolEvidence = async (
    taskId: string,
    payload: {
      toolName: string
      toolArgs: Record<string, unknown>
      toolOutput: string
      note?: string
    },
  ) => {
    await withLoading(async () => {
      const data = await injectToolEvidence(taskId, payload)
      handleState(data)
      void refreshToolHistory()
      const updated = Object.values(data.board)
        .flat()
        .find((t) => t.id === taskId)
      if (updated) setSelectedTask(updated)
    })
  }

  const handleSplitTask = async (taskId: string) => {
    await withSprintBusy(async () => {
      setActionError(null)
      try {
        const data = await splitTask(taskId, { ollamaUrl })
        handleState(data)
        const added = data.splitResult?.added ?? 0
        setSelectedTask(null)
        if (added > 0) {
          setActionNotice(
            `Added ${added} subtask${added === 1 ? '' : 's'}; original card moved to Done.`,
          )
        } else {
          setActionError('Split completed but no subtasks were added — try again or refine the card.')
        }
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.detail
            : err instanceof Error
              ? err.message
              : 'Failed to split task.'
        setActionError(message)
      }
    })
  }

  const handleOpenFile = useCallback((path: string) => {
    setSelectedFile(path)
    setWorkspaceOpen(true)
    setWorkspaceBarDismissed(false)
    writeWorkspaceOpen(true)
  }, [])

  const handleCloseWorkspace = useCallback(() => {
    setSelectedFile(null)
    setWorkspaceOpen(false)
    writeWorkspaceOpen(false)
  }, [])

  useEffect(() => {
    if (!selectedTask) return
    const fresh = findTaskOnBoard(state.board, selectedTask.id)
    if (fresh) {
      setSelectedTask((prev) => (prev && prev.id === fresh.id ? { ...prev, ...fresh } : fresh))
    }
  }, [state.board, selectedTask?.id])
  const [localFiles, setLocalFiles] = useState<Record<string, string>>({})

  const setters = {
    setBrief,
    setProjectName,
    setWorkspaceDir,
    setSkillsDir,
    setPoModel,
    setDevModel,
    setCrModel,
    setQaModel,
  }

  const handleState = useCallback(
    (data: AppState) => {
      applyState(data)
      setLocalFiles(data.files)
      setFileTreeKey((k) => k + 1)
    },
    [applyState],
  )

  const { autoSprint, setAutoSprint, autoSprintPaused, sprintRunning, stopAutoSprint } =
    useAutoSprint(brief, ollamaUrl, state.board, state.workflowSettings, handleState)

  const orchestratedActive = sprintRunning || planRunActive || sprintBusy

  const activeTaskRunInfo = useMemo(
    () =>
      buildTaskRunInfo({
        activeRun,
        sprintProgress,
        activeStepDiagnostics: state.activeStepDiagnostics,
        currentTool,
      }),
    [activeRun, sprintProgress, state.activeStepDiagnostics, currentTool],
  )

  useEffect(() => {
    if (!planRunActive) return
    if ((state.board.Backlog?.length ?? 0) > 0) {
      setKanbanOpen(true)
    }
  }, [planRunActive, state.board.Backlog?.length])

  useEffect(() => {
    applyStateFields(state, setters)
    setLocalFiles(state.files)
    setChatMessages(chatRecordsToUi(state.chatMessages))
  }, [state.projectId])

  useEffect(() => {
    setLocalFiles(state.files)
  }, [state.files])

  useEffect(() => {
    if (!workspaceOpen && !selectedFile) return
    const pathCount = state.filePaths?.length ?? 0
    const loadedCount = Object.keys(state.files).length
    if (pathCount > 0 && loadedCount >= pathCount) return
    if (pathCount === 0 && loadedCount > 0) return
    void refresh({ includeFiles: true })
  }, [workspaceOpen, selectedFile, state.filePaths?.length, state.files, refresh])

  useEffect(() => {
    const key = `allhands-chat-draft-${state.projectId}`
    const saved = sessionStorage.getItem(key)
    if (saved != null) setChatInput(saved)
  }, [state.projectId])

  useEffect(() => {
    const key = `allhands-chat-draft-${state.projectId}`
    const timer = window.setTimeout(() => {
      try {
        if (chatInput.trim()) {
          sessionStorage.setItem(key, chatInput)
        } else {
          sessionStorage.removeItem(key)
        }
      } catch {
        /* ignore */
      }
    }, 400)
    return () => window.clearTimeout(timer)
  }, [chatInput, state.projectId])

  useEffect(() => {
    let cancelled = false
    const check = () => {
      checkOllamaHealth(ollamaUrl)
        .then((r) => {
          if (!cancelled) setOllamaOk(r.ok)
        })
        .catch(() => {
          if (!cancelled) setOllamaOk(false)
        })
    }
    check()
    const interval = window.setInterval(check, 30000)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [ollamaUrl])

  const handleClearChat = useCallback(async () => {
    await clearChatHistory()
    setChatMessages([])
  }, [])

  const withLoading = async (fn: () => Promise<void>) => {
    setLoading(true)
    try {
      await fn()
    } finally {
      setLoading(false)
    }
  }

  /** Long sprint/agent HTTP — keeps Settings/board/tabs interactive (no global loading). */
  const withSprintBusy = async (fn: () => Promise<void>) => {
    setSprintBusy(true)
    try {
      await fn()
    } finally {
      setSprintBusy(false)
    }
  }

  const openSkillModal = async (agent: AgentId) => {
    setSkillModalAgent(agent)
    setSkillSearch('')
    setSelectedSkillFiles([])
    setSkillModalLoading(true)
    try {
      const [skillsData, suggData] = await Promise.all([
        fetchSkills(),
        fetchSkillSuggestions(agent),
      ])
      setModalSkills(skillsData.skills)
      setModalSkillsDir(skillsData.skillsDir)
      setModalBriefCategories(suggData.briefCategories ?? [])
      setModalSuggestions(suggData.suggestions ?? [])
    } catch {
      setModalSkills(state.availableSkills)
      setModalBriefCategories([])
      setModalSuggestions([])
    } finally {
      setSkillModalLoading(false)
    }
  }

  const refreshSkillSuggestionCounts = useCallback(async () => {
    if (!brief.trim()) {
      setSkillSuggestionCounts({ po: 0, dev: 0, cr: 0, qa: 0 })
      return
    }
    const agents: AgentId[] = ['po', 'dev', 'cr', 'qa']
    const entries = await Promise.all(
      agents.map(async (id) => {
        try {
          const data = await fetchSkillSuggestions(id, 5)
          const assigned = state.assignedSkills[id] ?? []
          const count = (data.suggestions ?? []).filter(
            (s) => !assigned.includes(s.filename),
          ).length
          return [id, count] as const
        } catch {
          return [id, 0] as const
        }
      }),
    )
    setSkillSuggestionCounts(Object.fromEntries(entries) as Record<AgentId, number>)
  }, [brief, state.assignedSkills])

  useEffect(() => {
    const timer = window.setTimeout(() => void refreshSkillSuggestionCounts(), 400)
    return () => window.clearTimeout(timer)
  }, [refreshSkillSuggestionCounts])

  useEffect(() => {
    if (pendingTools.length > 0 && !pendingToolModal) {
      setPendingToolModal(pendingTools[0] ?? null)
    }
  }, [pendingTools, pendingToolModal])

  useEffect(() => {
    if (pendingApprovals.length > 0 && !approvalModal) {
      setApprovalModal(pendingApprovals[0] ?? null)
    }
  }, [pendingApprovals, approvalModal])

  const handleMoveTask = async (
    taskId: string,
    fromLane: BoardLane,
    toLane: BoardLane,
    skipRefinement?: boolean,
  ) => {
    if (orchestratedActive) {
      setActionError('Wait for the current sprint step to finish before moving cards.')
      return
    }
    setActionError(null)
    try {
      const data = await moveTask({ taskId, fromLane, toLane, skipRefinement })
      handleState(data)
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Failed to move task. The board may be locked — try again in a moment.'
      setActionError(message)
    }
  }

  const claimableBacklogCount = useMemo(
    () => countClaimableBacklogTasks(state.board, state.workflowSettings),
    [state.board, state.workflowSettings],
  )

  useEffect(() => {
    if (!bottomPanelCollapsed) {
      writeBottomPanelHeight(bottomPanelHeight)
    }
  }, [bottomPanelHeight, bottomPanelCollapsed])

  useEffect(() => {
    writeBottomPanelCollapsed(bottomPanelCollapsed)
  }, [bottomPanelCollapsed])

  const expandBottomPanel = useCallback(() => {
    if (!bottomPanelCollapsed) return
    setBottomPanelCollapsed(false)
    setBottomPanelHeight((h) =>
      Math.max(BOTTOM_PANEL_MIN, preCollapseHeightRef.current || h || BOTTOM_PANEL_MIN),
    )
  }, [bottomPanelCollapsed])

  const toggleBottomPanelCollapse = useCallback(() => {
    setBottomPanelCollapsed((collapsed) => {
      if (collapsed) {
        setPanelMaximized(false)
        setBottomPanelHeight(
          Math.max(BOTTOM_PANEL_MIN, preCollapseHeightRef.current || BOTTOM_PANEL_MIN),
        )
        return false
      }
      preCollapseHeightRef.current = Math.max(BOTTOM_PANEL_MIN, bottomPanelHeight)
      setPanelMaximized(false)
      return true
    })
  }, [bottomPanelHeight])

  const handleToggleKanban = () => {
    setKanbanOpen((open) => {
      const next = !open
      writeKanbanOpen(next)
      if (next) {
        setBriefOpen(false)
        if (!bottomPanelCollapsed) {
          setBottomPanelHeight((h) => Math.min(h, 260))
        }
      } else if (!bottomPanelCollapsed) {
        setBottomPanelHeight((h) => Math.max(h, 320))
      }
      return next
    })
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === ',') {
        e.preventDefault()
        setSettingsOpen(true)
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'j') {
        e.preventDefault()
        toggleBottomPanelCollapse()
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setCommandPaletteOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [toggleBottomPanelCollapse])

  const handleBottomPanelResize = (height: number) => {
    setPanelMaximized(false)
    setBottomPanelCollapsed(false)
    setBottomPanelHeight(height)
  }

  const openToolsTab = () => {
    expandBottomPanel()
    setToolsPreferredSubTab('log')
    setBottomTab('tools')
  }

  const openModelTab = () => {
    expandBottomPanel()
    setBottomTab('model')
  }

  const applyStepOutcome = useCallback(
    (data: AppState) => {
      const outcome = data.lastStepOutcome
      const diagnostics = data.lastStepDiagnostics
      const activeDiag = data.activeStepDiagnostics
      const ollamaFallback =
        outcome?.stopReason === 'ollama_fallback' ||
        (outcome?.message ?? '').includes('SIMULATION_FALLBACK') ||
        (outcome?.message ?? '').toLowerCase().includes('simulation fallback') ||
        diagnostics?.exitReason === 'ollama_fallback'

      const cardStayedNote =
        outcome?.laneAfter === 'In Progress' && outcome?.whyCardStayed
          ? `Card stayed In Progress: ${outcome.whyCardStayed}${
              outcome.suggestedAction ? ` ${outcome.suggestedAction}` : ''
            }`
          : null

      if (activeDiag?.filePath) {
        setActionNotice(
          `Live diagnostics: ${activeDiag.filePath} — copy path from Console`,
        )
      } else if (cardStayedNote) {
        setActionError(cardStayedNote)
        setActionErrorShowModelLink(false)
      } else if (diagnostics?.filePath) {
        const diagNote = `Diagnostics saved: ${diagnostics.filePath}${diagnostics.hint ? ` — ${diagnostics.hint}` : ''}`
        if (outcome && (!outcome.ok || outcome.toolFailures > 0)) {
          setActionError(`${outcome.message}\n${diagNote}`)
          setActionErrorShowModelLink(ollamaFallback)
        } else {
          setActionNotice(diagNote)
          setActionErrorShowModelLink(false)
        }
      } else if (outcome && (!outcome.ok || outcome.toolFailures > 0)) {
        setActionError(outcome.message)
        setActionErrorShowModelLink(ollamaFallback)
      } else if (outcome?.laneAfter === 'In Progress' && outcome.agent === 'Developer') {
        setActionNotice(outcome.message)
        setActionErrorShowModelLink(false)
      } else {
        setActionErrorShowModelLink(false)
      }
      if (outcome && (!outcome.ok || outcome.toolFailures > 0) && outcome.toolFailures > 0) {
        openToolsTab()
      }
    },
    [],
  )

  const togglePanelMaximize = () => {
    const col = workspaceColumnRef.current
    if (!col) return
    if (bottomPanelCollapsed) {
      expandBottomPanel()
    }
    if (panelMaximized) {
      setBottomPanelHeight(preMaximizeHeightRef.current)
      setPanelMaximized(false)
    } else {
      preMaximizeHeightRef.current = Math.max(BOTTOM_PANEL_MIN, bottomPanelHeight)
      const maxH = Math.max(220, col.clientHeight * 0.65)
      setBottomPanelHeight(maxH)
      setPanelMaximized(true)
      setBottomPanelCollapsed(false)
    }
  }

  const chatFilePaths = useMemo(() => Object.keys(localFiles), [localFiles])

  const bottomTabs = useMemo(
    (): { id: BottomTab; label: string; icon: string; badge?: number; group?: string }[] => [
      { id: 'console', label: 'Console', icon: 'fa-terminal', group: 'Work' },
      { id: 'tools', label: 'Tools', icon: 'fa-wrench', group: 'Work', badge: toolRunningCount > 0 ? toolRunningCount : toolFailureCount || undefined },
      {
        id: 'evidence',
        label: 'Evidence',
        icon: 'fa-flask',
        group: 'Work',
        badge: (state.projectToolEvidence?.length ?? 0) || undefined,
      },
      { id: 'activity', label: 'Activity', icon: 'fa-wave-square', group: 'Work', badge: activityEvents.length || undefined },
      { id: 'chat', label: 'Chat', icon: 'fa-comments', group: 'Work' },
      { id: 'editor', label: 'Editor', icon: 'fa-file-code', group: 'Code', badge: selectedFile ? 1 : undefined },
      { id: 'terminal', label: 'Terminal', icon: 'fa-square-terminal', group: 'Code' },
      { id: 'search', label: 'Search', icon: 'fa-magnifying-glass', group: 'Code' },
      { id: 'git', label: 'Git', icon: 'fa-code-branch', group: 'Code' },
      { id: 'model', label: 'Model', icon: 'fa-brain', group: 'Debug' },
      { id: 'ollamaServer', label: 'Ollama Server', icon: 'fa-server', group: 'Debug' },
      { id: 'memory', label: 'Memory', icon: 'fa-brain-circuit', group: 'Debug', badge: memoryCount > 0 ? memoryCount : undefined },
    ],
    [toolRunningCount, toolFailureCount, activityEvents.length, memoryCount, selectedFile, state.projectToolEvidence?.length],
  )

  const commandPaletteItems = useMemo((): CommandPaletteItem[] => {
    const tabItems: CommandPaletteItem[] = bottomTabs.map((tab) => ({
      id: `tab:${tab.id}`,
      label: `Open ${tab.label}`,
      hint: tab.group,
      group: 'Tab',
      run: () => {
        expandBottomPanel()
        setBottomTab(tab.id)
      },
    }))
    const nav: CommandPaletteItem[] = [
      {
        id: 'settings',
        label: 'Open Settings',
        hint: 'Ctrl+,',
        group: 'Nav',
        run: () => setSettingsOpen(true),
      },
      {
        id: 'toggle-board',
        label: kanbanOpen ? 'Hide board' : 'Show board',
        group: 'Nav',
        run: () => handleToggleKanban(),
      },
      {
        id: 'toggle-bottom',
        label: bottomPanelCollapsed ? 'Expand bottom panel' : 'Collapse bottom panel',
        hint: 'Ctrl+J',
        group: 'Nav',
        run: () => toggleBottomPanelCollapse(),
      },
      {
        id: 'maximize-bottom',
        label: panelMaximized ? 'Restore bottom panel' : 'Maximize bottom panel',
        group: 'Nav',
        run: () => togglePanelMaximize(),
      },
    ]
    const lanes = getDisplayLanes(state.activeLanes, state.workflowSettings)
    const cards = cardPaletteItems(collectBoardTasks(state.board, lanes), (task) =>
      setSelectedTask(task),
    )
    return [...nav, ...tabItems, ...cards]
  }, [
    bottomTabs,
    expandBottomPanel,
    kanbanOpen,
    bottomPanelCollapsed,
    panelMaximized,
    state.activeLanes,
    state.workflowSettings,
    state.board,
    toggleBottomPanelCollapse,
  ])

  const pendingWorkflowRef = useRef<Partial<WorkflowSettings>>({})
  const workflowSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (workflowSaveTimerRef.current) clearTimeout(workflowSaveTimerRef.current)
    }
  }, [])

  const handleWorkflowSettingsChange = useCallback(
    (partial: Partial<WorkflowSettings>) => {
      setState((prev) => ({
        ...prev,
        workflowSettings: {
          ...(prev.workflowSettings ?? {}),
          ...partial,
        } as WorkflowSettings,
      }))
      pendingWorkflowRef.current = { ...pendingWorkflowRef.current, ...partial }
      if (workflowSaveTimerRef.current) clearTimeout(workflowSaveTimerRef.current)
      workflowSaveTimerRef.current = setTimeout(() => {
        workflowSaveTimerRef.current = null
        const payload = pendingWorkflowRef.current
        pendingWorkflowRef.current = {}
        void updateWorkflowSettings(payload)
          .then((data) => {
            setState((prev) => ({
              ...prev,
              workflowSettings: data.workflowSettings,
              activeLanes: data.activeLanes,
              notifications: data.notifications,
              board: data.board,
            }))
          })
          .catch(() => {
            pendingWorkflowRef.current = { ...payload, ...pendingWorkflowRef.current }
          })
      }, 350)
    },
    [setState],
  )

  const handleEscalateNeedsUserToPo = useCallback(() => {
    if (
      !window.confirm(
        'Move all Needs User cards to Needs PO? Use this when cards are clarification, not true user decisions.',
      )
    ) {
      return
    }
    void withLoading(async () => handleState(await escalateNeedsUserToPo()))
  }, [handleState])

  const handleRefreshState = useCallback(() => {
    void refresh()
  }, [refresh])

  const handleOpenConsoleTab = useCallback(() => {
    expandBottomPanel()
    setBottomTab('console')
  }, [expandBottomPanel])

  const handleOpenMemoryTab = useCallback(() => {
    expandBottomPanel()
    setBottomTab('memory')
  }, [expandBottomPanel])

  const handleActivityTaskClick = useCallback(
    (taskId: string) => {
      const task = findTaskOnBoard(state.board, taskId)
      if (task) setSelectedTask(task)
    },
    [state.board],
  )

  const openTaskFromRunBar = useCallback(
    (taskId: string) => {
      const task = findTaskOnBoard(state.board, taskId)
      if (task) {
        setSelectedTask(task)
        setActionError(null)
        return
      }
      setActionError(
        `Card ${taskId} is not on the board (moved, deleted, or stale sprint session).`,
      )
      setActionErrorShowModelLink(false)
    },
    [state.board],
  )

  const handleRefreshToolHistory = useCallback(() => {
    void refreshToolHistory()
  }, [refreshToolHistory])

  const statusItems = useMemo((): StatusItem[] => {
    const items: StatusItem[] = []
    if (actionError) {
      items.push({
        id: 'error',
        tone: 'error',
        summary: actionError.split('\n')[0] ?? actionError,
        detail: actionError.includes('\n') ? (
          <span className="whitespace-pre-wrap">{actionError}</span>
        ) : undefined,
        actions: (
          <>
            {actionErrorShowModelLink && (
              <button type="button" onClick={openModelTab} className="underline text-xs">
                Model tab
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                setActionError(null)
                setActionErrorShowModelLink(false)
              }}
              aria-label="Dismiss"
            >
              <i className="fa-solid fa-xmark" />
            </button>
          </>
        ),
      })
    }
    if (actionNotice) {
      items.push({
        id: 'notice',
        tone: 'notice',
        summary: actionNotice,
        actions: (
          <button type="button" onClick={() => setActionNotice(null)} aria-label="Dismiss">
            <i className="fa-solid fa-xmark" />
          </button>
        ),
      })
    }
    const outcome = state.lastStepOutcome
    if (outcome?.stopReason === 'max_iterations' && outcome.taskId) {
      const prog = outcome.stepProgress
      const iters = prog
        ? `${prog.iterationsUsed}/${prog.iterationsMax}`
        : ''
      items.push({
        id: 'max-iter',
        tone: 'warning',
        summary: `Hit LLM iteration limit${iters ? ` (${iters})` : ''} on ${outcome.taskId}`,
        detail: prog ? (
          <span>
            Used: {(prog.toolsUsed || []).join(', ') || 'none'}
            {prog.stuckLoop
              ? ' · repeated tool fails'
              : ' · not stuck in a loop'}
            . Extend continues with prior context; Reset starts fresh.
          </span>
        ) : (
          <span>{outcome.message}</span>
        ),
        actions: (
          <>
            <button
              type="button"
              disabled={extendingStep}
              onClick={() => {
                setExtendingStep(true)
                void extendAgentStep({
                  taskId: outcome.taskId,
                  agentId: 'dev',
                  action: 'extend',
                  extraIterations: 4,
                  ollamaUrl,
                })
                  .then((r) => {
                    if (r.state) {
                      handleState(r.state)
                      applyStepOutcome(r.state)
                    }
                  })
                  .finally(() => setExtendingStep(false))
              }}
              className="px-2 py-0.5 rounded bg-emerald-700/50 text-[10px] font-semibold"
            >
              Extend +4
            </button>
            <button
              type="button"
              disabled={extendingStep}
              onClick={() => {
                setExtendingStep(true)
                void extendAgentStep({
                  taskId: outcome.taskId,
                  agentId: 'dev',
                  action: 'reset',
                  ollamaUrl,
                })
                  .then((r) => {
                    if (r.state) {
                      handleState(r.state)
                      applyStepOutcome(r.state)
                    }
                  })
                  .finally(() => setExtendingStep(false))
              }}
              className="underline text-xs"
            >
              Reset
            </button>
          </>
        ),
      })
    }
    if (state.recovery?.interrupted) {
      items.push({
        id: 'recovery',
        tone: 'warning',
        summary: `Session interrupted — ${state.recovery.taskTitle} (${state.recovery.taskId})`,
        detail: (
          <span>
            Lane {state.recovery.lane}
            {state.recovery.agent ? ` · ${state.recovery.agent}` : ''}
            {state.recovery.lastEvent ? ` · Last: ${state.recovery.lastEvent}` : ''}
          </span>
        ),
        actions: (
          <>
            <button
              type="button"
              onClick={() => {
                if (orchestratedActive) {
                  setActionError('Wait for the current sprint step to finish before resuming.')
                  return
                }
                void withSprintBusy(async () => {
                  setActionError(null)
                  try {
                    const data = await runInProgressStep({
                      brief,
                      ollama_url: ollamaUrl,
                      taskId: state.recovery?.taskId,
                    })
                    handleState(data)
                    applyStepOutcome(data)
                  } catch (err) {
                    const message =
                      err instanceof ApiError
                        ? err.detail
                        : err instanceof Error
                          ? err.message
                          : 'Failed to resume in-progress step.'
                    setActionError(message)
                  }
                })
              }}
              className="px-2 py-0.5 rounded bg-amber-600/50 text-[10px] font-semibold"
            >
              Resume
            </button>
            <button
              type="button"
              onClick={() =>
                void withLoading(async () => handleState(await dismissSprintRecovery()))
              }
              className="underline text-xs"
            >
              Dismiss
            </button>
          </>
        ),
      })
    }
    if (ollamaOk === false) {
      items.push({
        id: 'ollama',
        tone: 'warning',
        summary: 'Ollama offline — using simulation fallback',
        actions: (
          <button type="button" onClick={openModelTab} className="underline text-xs">
            Model tab
          </button>
        ),
      })
    }
    if (pendingTools.length > 0) {
      items.push({
        id: 'pending-tools',
        tone: 'warning',
        summary: `${pendingTools.length} unknown tool call(s)`,
        actions: (
          <button
            type="button"
            onClick={() => setPendingToolModal(pendingTools[0] ?? null)}
            className="underline text-xs"
          >
            Resolve
          </button>
        ),
      })
    }
    return items
  }, [
    actionError,
    actionErrorShowModelLink,
    actionNotice,
    state.recovery,
    state.lastStepOutcome,
    extendingStep,
    ollamaOk,
    pendingTools,
    orchestratedActive,
    brief,
    ollamaUrl,
    handleState,
    applyStepOutcome,
    openModelTab,
  ])

  return (
    <div className={`flex h-full w-full flex-col lg:flex-row bg-cat-base overflow-hidden ${theme}`}>
      <Sidebar
        state={state}
        brief={brief}
        ollamaOk={ollamaOk}
        autoSprint={autoSprint}
        autoSprintPaused={autoSprintPaused}
        sprintRunning={orchestratedActive}
        isDark={isDark}
        onOpenSettings={() => setSettingsOpen(true)}
        onLoadProject={(id) =>
          void withLoading(async () => handleState(await loadProject(id)))
        }
        onOpenNewProject={() => setShowNewProject(true)}
        onPlan={() =>
          void withSprintBusy(async () =>
            handleState(await triggerPlanOutline({ brief, ollama_url: ollamaUrl })),
          )
        }
        onGenerateBacklog={() =>
          void withSprintBusy(async () =>
            handleState(
              await triggerPlanBacklog({
                brief,
                ollama_url: ollamaUrl,
                outline: planOutline,
              }),
            ),
          )
        }
        planOutlineReady={planOutline.trim().length > 0}
        onPlanAndRun={() =>
          void withSprintBusy(async () => {
            expandBottomPanel()
            setPlanRunActive(true)
            setSprintProgress(null)
            setBottomTab('console')
            if (bottomPanelHeight < 180) {
              setBottomPanelHeight(220)
            }
            try {
              const data = await planAndRun({
                brief,
                ollama_url: ollamaUrl,
                max_steps: state.workflowSettings?.maxSprintSteps ?? 20,
              })
              handleState(data)
              if (data.lastSprintSummary) setShowSprintSummary(true)
            } finally {
              setPlanRunActive(false)
            }
          })
        }
        onStep={() =>
          void withSprintBusy(async () => {
            const data = await triggerStep({ brief, ollama_url: ollamaUrl })
            handleState(data)
            applyStepOutcome(data)
            const names = Object.keys(data.files)
            if (names.length > 0 && selectedFile && !names.includes(selectedFile)) {
              setSelectedFile(names[names.length - 1] ?? null)
            }
          })
        }
        onRunInProgress={() => {
          if (orchestratedActive) {
            setActionError('Wait for the current sprint step to finish before running in progress.')
            return
          }
          void withSprintBusy(async () => {
            setActionError(null)
            try {
              const data = await runInProgressStep({ brief, ollama_url: ollamaUrl })
              handleState(data)
              applyStepOutcome(data)
              const names = Object.keys(data.files)
              if (names.length > 0 && selectedFile && !names.includes(selectedFile)) {
                setSelectedFile(names[names.length - 1] ?? null)
              }
            } catch (err) {
              const message =
                err instanceof ApiError
                  ? err.detail
                  : err instanceof Error
                    ? err.message
                    : 'Failed to run in-progress step.'
              setActionError(message)
            }
          })
        }}
        inProgressCount={state.board['In Progress']?.length ?? 0}
        onClaimReadyCards={() =>
          void withLoading(async () => {
            if (orchestratedActive) {
              setActionError('Wait for the current sprint step to finish before claiming cards.')
              return
            }
            setActionError(null)
            try {
              const data = await claimReadyBacklogCards(5)
              handleState(data)
            } catch (err) {
              const message =
                err instanceof ApiError
                  ? err.detail
                  : err instanceof Error
                    ? err.message
                    : 'Failed to claim ready cards.'
              setActionError(message)
            }
          })
        }
        claimableBacklogCount={claimableBacklogCount}
        onEscalateNeedsUserToPo={handleEscalateNeedsUserToPo}
        onClearAllTasks={() => {
          if (
            !window.confirm(
              'Remove all Kanban cards? Workspace files and the project brief will be kept.',
            )
          ) {
            return
          }
          void withLoading(async () => {
            if (orchestratedActive) await stopAutoSprint()
            handleState(await clearAllTasks())
            setSelectedTask(null)
          })
        }}
        onReset={() =>
          void withLoading(async () => {
            handleState(await resetWorkspace())
            setSelectedFile('package.json')
          })
        }
        onToggleTheme={toggleTheme}
        onToggleAutoSprint={setAutoSprint}
        onCancelSprint={() => void stopAutoSprint()}
      />

      <SettingsSlideOver
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        state={state}
        ollamaUrl={ollamaUrl}
        projectName={projectName}
        workspaceDir={workspaceDir}
        skillsDir={skillsDir}
        poModel={poModel}
        devModel={devModel}
        crModel={crModel}
        qaModel={qaModel}
        indexProgress={indexProgress}
        skillSuggestionCounts={skillSuggestionCounts}
        onOllamaUrlChange={setOllamaUrl}
        onProjectNameChange={setProjectName}
        onWorkspaceDirChange={setWorkspaceDir}
        onSkillsDirChange={setSkillsDir}
        onPoModelChange={setPoModel}
        onDevModelChange={setDevModel}
        onCrModelChange={setCrModel}
        onQaModelChange={setQaModel}
        onLoadProject={(id) =>
          void withLoading(async () => handleState(await loadProject(id)))
        }
        onSaveConfig={(payload) =>
          void withLoading(async () => {
            const data = await updateConfig(payload)
            handleState(data)
            applyStateFields(data, setters)
          })
        }
        onOpenNewProject={() => setShowNewProject(true)}
        onOpenSkillModal={(agent) => void openSkillModal(agent)}
        onRemoveSkill={(agent, skill) =>
          void withLoading(async () =>
            handleState(await removeSkill({ agent, skillFile: skill })),
          )
        }
        onWorkflowSettingsChange={handleWorkflowSettingsChange}
        onExportProject={() => exportProject(state.projectId)}
        onImportProject={(file) =>
          void withLoading(async () => handleState(await importProject(file)))
        }
        onDeleteProject={() => {
          const other = state.projectsList.find((p) => p.id !== state.projectId)
          if (!other) return
          if (!window.confirm(`Delete project "${other.name}"?`)) return
          void withLoading(async () => {
            await deleteProject(other.id)
            handleState(await loadProject(state.projectId))
          })
        }}
        onOpenMemoryTab={handleOpenMemoryTab}
      />


      <main className="flex-1 flex flex-col h-full overflow-hidden min-w-0">
        <StatusStrip items={statusItems} />

        <BriefPanel
          brief={brief}
          onBriefChange={setBrief}
          open={briefOpen}
          onOpenChange={setBriefOpen}
          onOpenManualTask={() => setShowManualTask(true)}
          autonomousMode={state.workflowSettings?.autonomousMode ?? false}
          planOutline={planOutline}
          onPlanOutlineChange={setPlanOutline}
          planOutlineStreaming={planOutlineStreaming}
          onGenerateBacklog={() =>
            void withLoading(async () =>
              handleState(
                await triggerPlanBacklog({
                  brief,
                  ollama_url: ollamaUrl,
                  outline: planOutline,
                }),
              ),
            )
          }
          generateBacklogDisabled={orchestratedActive || !brief.trim()}
        />

        <KanbanToggleBar
          board={state.board}
          projectName={state.projectName}
          open={kanbanOpen}
          onToggle={handleToggleKanban}
          activeLanes={state.activeLanes}
          workflowSettings={state.workflowSettings}
        />
        {kanbanOpen && (
          <KanbanBoard
            board={state.board}
            projectName={state.projectName}
            workspaceDir={state.workspaceDir}
            activeLanes={state.activeLanes}
            workflowSettings={state.workflowSettings}
            sprintRunning={orchestratedActive}
            activeRunInfo={activeTaskRunInfo}
            onTaskClick={(task) => setSelectedTask(findTaskOnBoard(state.board, task.id) ?? task)}
            onMoveTask={(taskId, from, to) => void handleMoveTask(taskId, from, to)}
            onReorderBacklog={(taskIds) =>
              void withLoading(async () => handleState(await reorderTasks('Backlog', taskIds)))
            }
            onReorderLane={(lane, taskIds) =>
              void withLoading(async () => handleState(await reorderTasks(lane, taskIds)))
            }
          />
        )}

        <div ref={workspaceColumnRef} className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {!workspaceBarDismissed && (
            <div className="shrink-0 mx-4 mb-2 flex items-center justify-between gap-3 px-3 py-2 text-[11px] text-cat-subtext bg-cat-surface0/60 border border-cat-surface1 rounded-lg">
              <span>
                {selectedFile
                  ? `File ready: ${selectedFile} — open the Editor tab below`
                  : 'No file open — open from a task card or browse files, then use the Editor tab'}
              </span>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  type="button"
                  onClick={() => {
                    setWorkspaceOpen(true)
                    writeWorkspaceOpen(true)
                    setWorkspaceBarDismissed(false)
                  }}
                  className="px-2.5 py-1 rounded bg-indigo-600/40 hover:bg-indigo-600/60 text-indigo-200 text-[10px] font-semibold"
                >
                  Browse files
                </button>
                <button
                  type="button"
                  onClick={() => setWorkspaceBarDismissed(true)}
                  className="text-cat-overlay hover:text-white px-1"
                  aria-label="Dismiss"
                >
                  <i className="fa-solid fa-xmark" />
                </button>
              </div>
            </div>
          )}

          <div className="flex flex-col min-h-0 overflow-hidden flex-1 justify-end">
            <BottomPanelResize
              containerRef={workspaceColumnRef}
              onResize={handleBottomPanelResize}
              disabled={bottomPanelCollapsed}
            />

            <div
              data-bottom-panel
              style={{
                height: bottomPanelCollapsed ? undefined : bottomPanelHeight,
                maxHeight: '100%',
              }}
              className={`flex flex-col shrink-0 border-t border-cat-surface1 min-h-0 ${
                bottomPanelCollapsed ? 'h-auto' : ''
              }`}
            >
              {!bottomPanelCollapsed && (
                <>
              <SprintProgressBar
                progress={sprintProgress}
                planRunActive={planRunActive}
                sprintRunning={sprintRunning}
                currentTool={currentTool}
                onOpenTask={openTaskFromRunBar}
              />
              <AgentRunBar
                activeRun={activeRun}
                currentTool={currentTool}
                planRunActive={planRunActive}
                onOpenTools={openToolsTab}
                onOpenTask={openTaskFromRunBar}
                retrying={retryingStep}
                lastStepOutcome={state.lastStepOutcome}
                lastStepDiagnostics={state.lastStepDiagnostics}
                extending={extendingStep}
                onExtend={(extra) => {
                  const taskId =
                    state.lastStepOutcome?.taskId || activeRun?.taskId
                  if (!taskId) return
                  setExtendingStep(true)
                  void extendAgentStep({
                    taskId,
                    agentId: 'dev',
                    action: 'extend',
                    extraIterations: extra,
                    ollamaUrl,
                  })
                    .then((r) => {
                      if (r.state) {
                        handleState(r.state)
                        applyStepOutcome(r.state)
                      }
                    })
                    .catch((err) => {
                      setActionError(
                        err instanceof ApiError
                          ? err.detail
                          : err instanceof Error
                            ? err.message
                            : 'Extend step failed',
                      )
                    })
                    .finally(() => setExtendingStep(false))
                }}
                onResetStep={() => {
                  const taskId =
                    state.lastStepOutcome?.taskId || activeRun?.taskId
                  if (!taskId) return
                  setExtendingStep(true)
                  void extendAgentStep({
                    taskId,
                    agentId: 'dev',
                    action: 'reset',
                    ollamaUrl,
                  })
                    .then((r) => {
                      if (r.state) {
                        handleState(r.state)
                        applyStepOutcome(r.state)
                      }
                    })
                    .catch((err) => {
                      setActionError(
                        err instanceof ApiError
                          ? err.detail
                          : err instanceof Error
                            ? err.message
                            : 'Reset step failed',
                      )
                    })
                    .finally(() => setExtendingStep(false))
                }}
                onRetry={
                  activeRun?.taskId
                    ? (mode) => {
                        setRetryingStep(true)
                        const agentId =
                          activeRun.agent === 'Product Owner'
                            ? 'po'
                            : activeRun.agent === 'Code Reviewer'
                              ? 'cr'
                              : activeRun.agent === 'QA Tester'
                                ? 'qa'
                                : 'dev'
                        void retryAgentStep({
                          taskId: activeRun.taskId,
                          agentId,
                          mode,
                          ollamaUrl,
                        })
                          .then((r) => {
                            if (r.state) handleState(r.state)
                          })
                          .finally(() => setRetryingStep(false))
                      }
                    : undefined
                }
              />
                </>
              )}
              <div className="flex bg-cat-mantle border-b border-cat-surface1 shrink-0 items-stretch">
                <div className="flex-1 min-w-0 overflow-x-auto">
                  <div className="flex whitespace-nowrap items-stretch">
                    {bottomTabs.map((tab, idx) => {
                      const prevGroup = idx > 0 ? bottomTabs[idx - 1]?.group : undefined
                      const showDivider = tab.group && prevGroup && tab.group !== prevGroup
                      return (
                        <div key={tab.id} className="flex items-stretch shrink-0">
                          {showDivider && (
                            <div
                              className="w-px bg-cat-surface1/80 mx-0.5 self-stretch"
                              title={tab.group}
                              aria-hidden
                            />
                          )}
                          <button
                            type="button"
                            onClick={() => {
                              expandBottomPanel()
                              setBottomTab(tab.id)
                            }}
                            title={tab.group ? `${tab.group}: ${tab.label}` : tab.label}
                            className={`px-3 md:px-4 py-2 text-[11px] font-semibold uppercase tracking-wider border-r border-cat-surface1 transition-colors ${
                              bottomTab === tab.id
                                ? 'bg-cat-base text-indigo-400'
                                : 'text-cat-subtext hover:text-white hover:bg-cat-surface0'
                            }`}
                          >
                            <i className={`fa-solid ${tab.icon} md:mr-1.5`} />
                            <span className="hidden md:inline">{tab.label}</span>
                            {tab.badge != null && tab.badge > 0 && (
                              <span className="ml-1.5 text-[9px] bg-indigo-950 text-indigo-300 px-1.5 py-0.5 rounded-full">
                                {tab.badge > 99 ? '99+' : tab.badge}
                              </span>
                            )}
                          </button>
                        </div>
                      )
                    })}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={toggleBottomPanelCollapse}
                  title={
                    bottomPanelCollapsed
                      ? 'Expand panel (Ctrl+J)'
                      : 'Collapse panel (Ctrl+J)'
                  }
                  className={`shrink-0 px-3 py-2 border-l border-cat-surface1 text-cat-subtext hover:text-white hover:bg-cat-surface0 transition-colors ${
                    bottomPanelCollapsed ? 'text-indigo-400' : ''
                  }`}
                >
                  <i
                    className={`fa-solid ${
                      bottomPanelCollapsed ? 'fa-chevron-up' : 'fa-chevron-down'
                    } text-xs`}
                  />
                </button>
                <button
                  type="button"
                  onClick={togglePanelMaximize}
                  title={panelMaximized ? 'Restore panel size' : 'Maximize panel'}
                  className={`shrink-0 px-3 py-2 border-l border-cat-surface1 text-cat-subtext hover:text-white hover:bg-cat-surface0 transition-colors ${
                    panelMaximized ? 'text-indigo-400' : ''
                  }`}
                >
                  <i className="fa-solid fa-up-right-and-down-left-from-center text-xs" />
                </button>
              </div>
              {!bottomPanelCollapsed && (
              <div className="flex-1 min-h-0 flex flex-col relative overflow-hidden">
                {bottomTab === 'console' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <AgentConsole
                      logs={state.logs}
                      onClear={() => void clearLogs()}
                      sseLive={sseLive}
                    />
                  </div>
                )}
                {bottomTab === 'activity' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <ActivityPanel
                      events={activityEvents}
                      onClear={clearActivity}
                      wasCleared={activityWasCleared}
                      onTaskClick={handleActivityTaskClick}
                    />
                  </div>
                )}
                {bottomTab === 'model' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <ModelDebugPanel taskIdFilter={selectedTask?.id ?? null} />
                  </div>
                )}
                {bottomTab === 'ollamaServer' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <OllamaServiceLogPanel hidden={false} onOpenModelTab={openModelTab} />
                  </div>
                )}
                {bottomTab === 'editor' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <EditorPanel
                      hidden={false}
                      selectedFile={selectedFile}
                      localFiles={localFiles}
                      fileTreeKey={fileTreeKey}
                      showDiff={showDiff}
                      onSelectFile={handleOpenFile}
                      onFilesChange={setLocalFiles}
                      onToggleDiff={() => setShowDiff((d) => !d)}
                      onCloseWorkspace={handleCloseWorkspace}
                    />
                  </div>
                )}
                {bottomTab === 'memory' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <MemoryPanel ollamaUrl={ollamaUrl} onCountChange={setMemoryCount} />
                  </div>
                )}
                {bottomTab === 'tools' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <ToolsPanel
                      toolEvents={toolEvents}
                      terminalSessions={terminalSessions}
                      onStopTerminal={stopTerminalSession}
                      onClearLog={clearToolEvents}
                      onMergeToolEvent={mergeToolEvent}
                      board={state.board}
                      selectedTaskId={selectedTask?.id}
                      onRefreshState={handleRefreshState}
                      onRefreshToolHistory={handleRefreshToolHistory}
                      sseLive={sseLive}
                      lastToolEventAt={lastToolEventAt}
                      brief={brief}
                      preferredSubTab={toolsPreferredSubTab}
                      workspaceDir={state.workspaceDir}
                      sprintRunning={orchestratedActive}
                      onOpenConsole={handleOpenConsoleTab}
                      onInjectToolEvidence={(taskId, payload) => handleInjectToolEvidence(taskId, payload)}
                    />
                  </div>
                )}
                {bottomTab === 'evidence' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <EvidencePanel
                      entries={state.projectToolEvidence ?? []}
                      defaultCommand={state.recommendedLintCommand ?? ''}
                      onInject={async (payload) => {
                        handleState(await injectProjectToolEvidence(payload))
                        void refreshToolHistory()
                      }}
                      onDelete={async (entryId) => {
                        handleState(await deleteProjectToolEvidence(entryId))
                      }}
                      onClearAll={async () => {
                        handleState(await clearProjectToolEvidence())
                      }}
                    />
                  </div>
                )}
                {bottomTab === 'chat' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <ChatPanel
                      ollamaUrl={ollamaUrl}
                      filePaths={chatFilePaths}
                      agent={chatAgent}
                      onAgentChange={setChatAgent}
                      input={chatInput}
                      onInputChange={setChatInput}
                      messages={chatMessages}
                      onMessagesChange={setChatMessages}
                      contextFiles={chatContextFiles}
                      onContextFilesChange={setChatContextFiles}
                      pinnedTask={chatPinnedTask}
                      pinnedLane={chatPinnedLane}
                      onClearPinnedTask={() => setChatPinnedTask(null)}
                      onRefreshState={handleRefreshState}
                      onSplitTask={(taskId) => void handleSplitTask(taskId)}
                      toolEvents={toolEvents}
                      onClearChat={handleClearChat}
                    />
                  </div>
                )}
                {bottomTab === 'terminal' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <TerminalPanel workspaceDir={state.workspaceDir} />
                  </div>
                )}
                {bottomTab === 'search' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <SearchPanel onOpenFile={handleOpenFile} />
                  </div>
                )}
                {bottomTab === 'git' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <GitPanel />
                  </div>
                )}
              </div>
              )}
            </div>
          </div>
        </div>
      </main>

      <TaskDetailModal
        task={selectedTask}
        taskLane={selectedTaskLane}
        sprintRunning={orchestratedActive}
        onClose={() => setSelectedTask(null)}
        onOpenFile={handleOpenFile}
        onUpdate={(taskId, title, description, acceptanceCriteria) =>
          void withLoading(async () => {
            handleState(
              await updateTask(taskId, { title, description, acceptanceCriteria }),
            )
            setSelectedTask(null)
          })
        }
        onApprove={(taskId) =>
          void withLoading(async () => {
            handleState(await approveTask(taskId))
            setSelectedTask(null)
          })
        }
        onResolveUser={(taskId, answer, target) =>
          void withLoading(async () => {
            handleState(await resolveUserQuestion(taskId, answer, target))
            setSelectedTask(null)
          })
        }
        onClearTranscript={(taskId) =>
          void withLoading(async () => {
            const data = await clearTaskTranscript(taskId)
            handleState(data)
            const updated = Object.values(data.board)
              .flat()
              .find((t) => t.id === taskId)
            if (updated) setSelectedTask(updated)
          })
        }
        onDelete={(taskId) =>
          void withLoading(async () => {
            if (orchestratedActive) {
              setActionError('Wait for the current sprint step to finish before deleting cards.')
              return
            }
            setActionError(null)
            try {
              handleState(await deleteTask(taskId))
              setSelectedTask(null)
            } catch (err) {
              const message =
                err instanceof ApiError
                  ? err.detail
                  : err instanceof Error
                    ? err.message
                    : 'Failed to delete task.'
              setActionError(message)
            }
          })
        }
        onRelatedTaskClick={(taskId) => {
          const related = findTaskOnBoard(state.board, taskId)
          if (related) setSelectedTask(related)
        }}
        onDiscussWithAgent={(task, lane) => handleDiscussWithAgent(task, lane)}
        onSplit={(taskId) => handleSplitTask(taskId)}
        onInjectToolEvidence={(taskId, payload) => handleInjectToolEvidence(taskId, payload)}
        defaultInjectCommand={state.recommendedLintCommand ?? ''}
        getTaskTitle={(taskId) => findTaskOnBoard(state.board, taskId)?.title}
        taskExistsOnBoard={(taskId) => !!findTaskOnBoard(state.board, taskId)}
        ollamaUrl={ollamaUrl}
        onDiagnose={(taskId) =>
          void withSprintBusy(async () => {
            const data = await diagnoseTask(taskId, ollamaUrl)
            if (data.state) handleState(data.state)
            const updated = Object.values(data.state?.board ?? {})
              .flat()
              .find((t) => t.id === taskId)
            if (updated) {
              setSelectedTask(updated)
            } else if (data.diagnosis) {
              setSelectedTask((prev) =>
                prev?.id === taskId ? { ...prev, lastDiagnosis: data.diagnosis } : prev,
              )
            }
          })
        }
        onRetryStep={(taskId, mode) =>
          void withSprintBusy(async () => {
            const lane = selectedTaskLane
            const agentId =
              lane === 'Refinement'
                ? selectedTask?.refinementStatus === 'dev_reviewed'
                  ? 'po'
                  : 'dev'
                : lane === 'Needs PO' || lane === 'Backlog'
                  ? 'po'
                  : lane === 'QA'
                    ? 'qa'
                    : lane === 'Code Review'
                      ? 'cr'
                      : 'dev'
            const data = await retryAgentStep({ taskId, agentId, mode, ollamaUrl })
            if (data.state) handleState(data.state)
            const updated = Object.values(data.state?.board ?? {})
              .flat()
              .find((t) => t.id === taskId)
            if (updated) setSelectedTask(updated)
          })
        }
        onOpenModelTab={() => setBottomTab('model')}
        maxRefinementRoundTrips={state.workflowSettings?.maxRefinementRoundTrips ?? 3}
        requireBacklogRefinement={state.workflowSettings?.requireBacklogRefinement ?? false}
        onMoveToInProgress={(taskId, fromLane, skipRefinement) =>
          void handleMoveTask(taskId, fromLane, 'In Progress', skipRefinement)
        }
        onRunInProgressStep={(taskId) => {
          if (orchestratedActive) {
            setActionError('Wait for the current sprint step to finish before running dev on this card.')
            return
          }
          void withSprintBusy(async () => {
            setActionError(null)
            try {
              const data = await runInProgressStep({ brief, ollama_url: ollamaUrl, taskId })
              handleState(data)
              applyStepOutcome(data)
              const updated = Object.values(data.board)
                .flat()
                .find((t) => t.id === taskId)
              if (updated) setSelectedTask(updated)
            } catch (err) {
              const message =
                err instanceof ApiError
                  ? err.detail
                  : err instanceof Error
                    ? err.message
                    : 'Failed to run dev step on this card.'
              setActionError(message)
            }
          })
        }}
        onEscapeSubtasks={(taskId) =>
          void withLoading(async () => {
            handleState(await escapeSubtaskLoop(taskId, 'needs_po'))
            setSelectedTask(null)
          })
        }
        onViewFileDiff={(path) =>
          void fetchFileDiff(path).then((d) =>
            setFileDiffModal({
              path,
              previousContent: d.oldValue ?? '',
              content: d.newValue ?? '',
            }),
          )
        }
      />

      {fileDiffModal && (
        <FileDiffModal
          path={fileDiffModal.path}
          previousContent={fileDiffModal.previousContent}
          content={fileDiffModal.content}
          onClose={() => setFileDiffModal(null)}
        />
      )}

      <SkillModal
        agent={skillModalAgent}
        skills={modalSkills}
        assignedSkills={
          skillModalAgent ? (state.assignedSkills[skillModalAgent] ?? []) : []
        }
        skillsDir={modalSkillsDir}
        loading={skillModalLoading}
        search={skillSearch}
        selectedFiles={selectedSkillFiles}
        assigning={loading}
        briefCategories={modalBriefCategories}
        suggestions={modalSuggestions}
        onSearchChange={setSkillSearch}
        onToggleFile={(filename) =>
          setSelectedSkillFiles((prev) =>
            prev.includes(filename)
              ? prev.filter((f) => f !== filename)
              : [...prev, filename],
          )
        }
        onAssign={() =>
          skillModalAgent &&
          selectedSkillFiles.length > 0 &&
          void withLoading(async () => {
            const data = await assignSkills({
              agent: skillModalAgent,
              skillFiles: selectedSkillFiles,
            })
            handleState(data)
            setSelectedSkillFiles([])
            void refreshSkillSuggestionCounts()
          })
        }
        onClose={() => setSkillModalAgent(null)}
      />

      <NewProjectModal
        open={showNewProject}
        name={newProjName}
        dir={newProjDir}
        loading={loading}
        onNameChange={setNewProjName}
        onDirChange={setNewProjDir}
        onSubmit={() =>
          void withLoading(async () => {
            const data = await createProject({
              projectName: newProjName,
              workspaceDir: newProjDir,
            })
            handleState(data)
            setShowNewProject(false)
            setNewProjName('')
          })
        }
        onClose={() => setShowNewProject(false)}
      />

      <ManualTaskModal
        open={showManualTask}
        title={manualTitle}
        description={manualDesc}
        loading={loading}
        onTitleChange={setManualTitle}
        onDescriptionChange={setManualDesc}
        onSubmit={() =>
          void withLoading(async () => {
            const data = await addManualTask({
              title: manualTitle,
              description: manualDesc,
              ollama_url: ollamaUrl,
            })
            handleState(data)
            setBrief(data.brief ?? brief)
            setShowManualTask(false)
            setManualTitle('')
            setManualDesc('')
          })
        }
        onClose={() => setShowManualTask(false)}
      />

      {showSprintSummary && state.lastSprintSummary && (
        <SlideOver
          open
          onClose={() => setShowSprintSummary(false)}
          side="right"
          title="Sprint Summary"
          widthClass="w-full max-w-md"
          footer={
            <button
              type="button"
              onClick={() => setShowSprintSummary(false)}
              className="w-full bg-indigo-600 text-white text-xs py-2 rounded-lg"
            >
              Close
            </button>
          }
        >
          <div className="p-4 space-y-3">
            <p className="text-xs text-cat-subtext">
              Steps run: {state.lastSprintSummary.stepsRun}
            </p>
            <p className="text-xs text-cat-subtext">
              Completed: {state.lastSprintSummary.completed.join(', ') || 'none'}
            </p>
            <p className="text-xs text-cat-subtext">
              QA failed: {state.lastSprintSummary.qaFailed.join(', ') || 'none'}
            </p>
            <p className="text-xs text-cat-subtext">
              Blocked: {state.lastSprintSummary.blocked.join(', ') || 'none'}
            </p>
          </div>
        </SlideOver>
      )}

      <CommandPalette
        open={commandPaletteOpen}
        onClose={() => setCommandPaletteOpen(false)}
        items={commandPaletteItems}
      />

      <ToolResolutionModal
        pending={pendingToolModal}
        onClose={() => setPendingToolModal(null)}
        recommendedLintCommand={state.recommendedLintCommand}
        commandAllowlist={state.workflowSettings?.commandAllowlist ?? []}
        onResolved={async () => {
          await refreshPendingTools()
          setPendingToolModal(null)
        }}
      />

      <ToolApprovalModal
        pending={approvalModal}
        onClose={() => setApprovalModal(null)}
        onResolved={() => void refreshPendingApprovals()}
        onApprove={async (id, approved) => {
          const data = await resolveToolApproval(id, approved)
          applyState(data)
          void refreshToolHistory()
        }}
      />
    </div>
  )
}
