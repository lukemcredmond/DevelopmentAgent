import { useCallback, useEffect, useRef, useState } from 'react'
import {
  addManualTask,
  approveTask,
  assignSkills,
  ApiError,
  checkOllamaHealth,
  clearTaskTranscript,
  createProject,
  deleteProject,
  deleteTask,
  exportProject,
  fetchSkills,
  importProject,
  loadProject,
  moveTask,
  planAndRun,
  removeSkill,
  reorderTasks,
  resetWorkspace,
  clearAllTasks,
  resolveToolApproval,
  resolveUserQuestion,
  injectToolEvidence,
  splitTask,
  triggerPlan,
  triggerStep,
  updateConfig,
  updateTask,
  updateWorkflowSettings,
} from './api/client'
import ActivityPanel from './components/ActivityPanel'
import AgentConsole from './components/AgentConsole'
import BriefPanel from './components/BriefPanel'
import ChatPanel, { type ChatUiMessage } from './components/ChatPanel'
import CodeEditor from './components/CodeEditor'
import DiffPanel from './components/DiffPanel'
import FileExplorer from './components/FileExplorer'
import GitPanel from './components/GitPanel'
import KanbanBoard from './components/KanbanBoard'
import ManualTaskModal from './components/ManualTaskModal'
import NewProjectModal from './components/NewProjectModal'
import SearchPanel from './components/SearchPanel'
import Sidebar from './components/Sidebar'
import SkillModal from './components/SkillModal'
import TaskDetailModal from './components/TaskDetailModal'
import TerminalPanel from './components/TerminalPanel'
import ToolResolutionModal from './components/ToolResolutionModal'
import ToolApprovalModal from './components/ToolApprovalModal'
import AgentRunBar from './components/AgentRunBar'
import SprintProgressBar from './components/SprintProgressBar'
import BottomPanelResize, {
  readBottomPanelHeight,
  writeBottomPanelHeight,
} from './components/BottomPanelResize'
import KanbanToggleBar, { readKanbanOpen, writeKanbanOpen } from './components/KanbanToggleBar'
import ToolsPanel from './components/ToolsPanel'
import { useAppState, useAutoSprint } from './hooks/useAppState'
import { useTheme } from './hooks/useTheme'
import type { AgentId, AppState, BoardLane, ChatMessageRecord, PendingToolApproval, PendingToolRequest, Task, WorkflowSettings } from './types'
import { getDisplayLanes } from './types'
import { findTaskOnBoard } from './utils/taskFormat'

type BottomTab = 'console' | 'activity' | 'tools' | 'chat' | 'terminal' | 'search' | 'git'

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
    toolFailureCount,
    toolRunningCount,
    sprintProgress,
    setSprintProgress,
    refreshToolHistory,
    clearLogs,
    clearActivity,
    sseLive,
  } = useAppState()

  const [planRunActive, setPlanRunActive] = useState(false)

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
  const showWorkspace = selectedFile != null || workspaceOpen
  const [showDiff, setShowDiff] = useState(false)
  const [bottomTab, setBottomTab] = useState<BottomTab>('console')
  const [kanbanOpen, setKanbanOpen] = useState(readKanbanOpen)
  const [bottomPanelHeight, setBottomPanelHeight] = useState(readBottomPanelHeight)
  const [panelMaximized, setPanelMaximized] = useState(false)
  const [toolsPreferredSubTab, setToolsPreferredSubTab] = useState<
    'log' | 'manual' | 'replay' | undefined
  >(undefined)
  const workspaceColumnRef = useRef<HTMLDivElement>(null)
  const preMaximizeHeightRef = useRef(bottomPanelHeight)
  const [fileTreeKey, setFileTreeKey] = useState(0)

  const [selectedTask, setSelectedTask] = useState<Task | null>(null)
  const [skillModalAgent, setSkillModalAgent] = useState<AgentId | null>(null)
  const [skillSearch, setSkillSearch] = useState('')
  const [selectedSkillFiles, setSelectedSkillFiles] = useState<string[]>([])
  const [skillModalLoading, setSkillModalLoading] = useState(false)
  const [modalSkills, setModalSkills] = useState(state.availableSkills)
  const [modalSkillsDir, setModalSkillsDir] = useState(skillsDir)

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
    if (lane === 'Needs User' || lane === 'Needs PO') {
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
    await withLoading(async () => {
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
    if (fresh) setSelectedTask(fresh)
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
      applyStateFields(data, setters)
      setLocalFiles(data.files)
      setFileTreeKey((k) => k + 1)
    },
    [applyState],
  )

  const { autoSprint, setAutoSprint, autoSprintPaused, sprintRunning, stopAutoSprint } =
    useAutoSprint(brief, ollamaUrl, state.board, state.workflowSettings, handleState)

  const orchestratedActive = sprintRunning || planRunActive

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
    const key = `allhands-chat-draft-${state.projectId}`
    const saved = sessionStorage.getItem(key)
    if (saved != null) setChatInput(saved)
  }, [state.projectId])

  useEffect(() => {
    const key = `allhands-chat-draft-${state.projectId}`
    sessionStorage.setItem(key, chatInput)
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

  const withLoading = async (fn: () => Promise<void>) => {
    setLoading(true)
    try {
      await fn()
    } finally {
      setLoading(false)
    }
  }

  const openSkillModal = async (agent: AgentId) => {
    setSkillModalAgent(agent)
    setSkillSearch('')
    setSelectedSkillFiles([])
    setSkillModalLoading(true)
    try {
      const data = await fetchSkills()
      setModalSkills(data.skills)
      setModalSkillsDir(data.skillsDir)
    } catch {
      setModalSkills(state.availableSkills)
    } finally {
      setSkillModalLoading(false)
    }
  }

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

  const handleMoveTask = async (taskId: string, fromLane: BoardLane, toLane: BoardLane) => {
    if (orchestratedActive) {
      setActionError('Wait for the current sprint step to finish before moving cards.')
      return
    }
    setActionError(null)
    try {
      const data = await moveTask({ taskId, fromLane, toLane })
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

  useEffect(() => {
    writeBottomPanelHeight(bottomPanelHeight)
  }, [bottomPanelHeight])

  const handleToggleKanban = () => {
    setKanbanOpen((open) => {
      const next = !open
      writeKanbanOpen(next)
      return next
    })
  }

  const handleBottomPanelResize = (height: number) => {
    setPanelMaximized(false)
    setBottomPanelHeight(height)
  }

  const openToolsTab = () => {
    setToolsPreferredSubTab('log')
    setBottomTab('tools')
  }

  const togglePanelMaximize = () => {
    const col = workspaceColumnRef.current
    if (!col) return
    if (panelMaximized) {
      setBottomPanelHeight(preMaximizeHeightRef.current)
      setPanelMaximized(false)
    } else {
      preMaximizeHeightRef.current = bottomPanelHeight
      const maxH = Math.max(220, col.clientHeight * 0.65)
      setBottomPanelHeight(maxH)
      setPanelMaximized(true)
    }
  }

  const bottomTabs: { id: BottomTab; label: string; icon: string; badge?: number }[] = [
    { id: 'console', label: 'Console', icon: 'fa-terminal' },
    {
      id: 'tools',
      label: 'Tools',
      icon: 'fa-wrench',
      badge: toolRunningCount > 0 ? toolRunningCount : toolFailureCount || undefined,
    },
    { id: 'activity', label: 'Activity', icon: 'fa-wave-square', badge: activityEvents.length || undefined },
    { id: 'chat', label: 'Chat', icon: 'fa-comments' },
    { id: 'terminal', label: 'Terminal', icon: 'fa-square-terminal' },
    { id: 'search', label: 'Search', icon: 'fa-magnifying-glass' },
    { id: 'git', label: 'Git', icon: 'fa-code-branch' },
  ]

  return (
    <div className={`flex h-full w-full flex-col lg:flex-row bg-cat-base overflow-hidden ${theme}`}>
      <Sidebar
        state={state}
        ollamaUrl={ollamaUrl}
        brief={brief}
        projectName={projectName}
        workspaceDir={workspaceDir}
        skillsDir={skillsDir}
        poModel={poModel}
        devModel={devModel}
        crModel={crModel}
        qaModel={qaModel}
        loading={loading}
        ollamaOk={ollamaOk}
        autoSprint={autoSprint}
        autoSprintPaused={autoSprintPaused}
        sprintRunning={orchestratedActive}
        isDark={isDark}
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
          void withLoading(async () => handleState(await updateConfig(payload)))
        }
        onOpenNewProject={() => setShowNewProject(true)}
        onOpenSkillModal={(agent) => void openSkillModal(agent)}
        onRemoveSkill={(agent, skill) =>
          void withLoading(async () =>
            handleState(await removeSkill({ agent, skillFile: skill })),
          )
        }
        onPlan={() =>
          void withLoading(async () =>
            handleState(await triggerPlan({ brief, ollama_url: ollamaUrl })),
          )
        }
        onPlanAndRun={() =>
          void withLoading(async () => {
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
          void withLoading(async () => {
            const data = await triggerStep({ brief, ollama_url: ollamaUrl })
            handleState(data)
            const names = Object.keys(data.files)
            if (names.length > 0 && selectedFile && !names.includes(selectedFile)) {
              setSelectedFile(names[names.length - 1] ?? null)
            }
          })
        }
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
        onWorkflowSettingsChange={(partial: Partial<WorkflowSettings>) =>
          void withLoading(async () =>
            handleState(await updateWorkflowSettings(partial)),
          )
        }
      />

      <main className="flex-1 flex flex-col h-full overflow-hidden min-w-0">
        {actionError && (
          <div className="mx-4 mt-2 shrink-0 flex items-center justify-between gap-2 text-[11px] text-rose-200 bg-rose-950/40 border border-rose-500/40 rounded-lg px-3 py-2">
            <span>{actionError}</span>
            <button
              type="button"
              onClick={() => setActionError(null)}
              className="text-rose-300 hover:text-white shrink-0"
              aria-label="Dismiss"
            >
              <i className="fa-solid fa-xmark" />
            </button>
          </div>
        )}

        {actionNotice && (
          <div className="mx-4 mt-2 shrink-0 flex items-center justify-between gap-2 text-[11px] text-emerald-200 bg-emerald-950/40 border border-emerald-500/40 rounded-lg px-3 py-2">
            <span>{actionNotice}</span>
            <button
              type="button"
              onClick={() => setActionNotice(null)}
              className="text-emerald-300 hover:text-white shrink-0"
              aria-label="Dismiss"
            >
              <i className="fa-solid fa-xmark" />
            </button>
          </div>
        )}

        {ollamaOk === false && (
          <div className="mx-4 mt-2 shrink-0 flex items-center gap-2 text-[11px] text-amber-200 bg-amber-950/40 border border-amber-500/40 rounded-lg px-3 py-2">
            <i className="fa-solid fa-triangle-exclamation shrink-0" />
            <span>
              Offline simulation — tools are not running.
              {orchestratedActive ? ' Sprint steps use fallback behavior.' : ' Start Ollama for real agent behavior.'}
            </span>
          </div>
        )}

        {pendingTools.length > 0 && (
          <div className="mx-4 mt-2 shrink-0 flex items-center justify-between gap-2 text-[11px] text-amber-200 bg-amber-950/40 border border-amber-500/40 rounded-lg px-3 py-2">
            <span>
              {pendingTools.length} unknown tool call(s) — map them to run_command or other actions.
            </span>
            <button
              type="button"
              onClick={() => setPendingToolModal(pendingTools[0] ?? null)}
              className="text-amber-300 hover:text-white shrink-0 text-xs underline"
            >
              Resolve
            </button>
          </div>
        )}

        <BriefPanel
          brief={brief}
          onBriefChange={setBrief}
          onOpenManualTask={() => setShowManualTask(true)}
          autonomousMode={state.workflowSettings?.autonomousMode ?? false}
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
            onTaskClick={(task) => setSelectedTask(findTaskOnBoard(state.board, task.id) ?? task)}
            onMoveTask={(taskId, from, to) => void handleMoveTask(taskId, from, to)}
            onReorderBacklog={(taskIds) =>
              void withLoading(async () => handleState(await reorderTasks('Backlog', taskIds)))
            }
          />
        )}

        <div ref={workspaceColumnRef} className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {showWorkspace ? (
            <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-[200px_1fr] overflow-hidden">
              <FileExplorer
                selectedFile={selectedFile}
                onSelectFile={handleOpenFile}
                refreshKey={fileTreeKey}
              />
              <div className="min-h-0 flex flex-col overflow-hidden">
                {showDiff ? (
                  <DiffPanel
                    path={selectedFile}
                    currentContent={localFiles[selectedFile ?? '']}
                  />
                ) : (
                  <CodeEditor
                    files={localFiles}
                    selectedFile={selectedFile}
                    onSelectFile={handleOpenFile}
                    onFilesChange={setLocalFiles}
                    showDiff={showDiff}
                    onToggleDiff={() => setShowDiff((d) => !d)}
                    onCloseWorkspace={handleCloseWorkspace}
                  />
                )}
              </div>
            </div>
          ) : (
            !workspaceBarDismissed && (
              <div className="shrink-0 mx-4 mb-2 flex items-center justify-between gap-3 px-3 py-2 text-[11px] text-cat-subtext bg-cat-surface0/60 border border-cat-surface1 rounded-lg">
                <span>No file open — open from a task card or browse files</span>
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
            )
          )}

          <div className="flex flex-col min-h-0 overflow-hidden flex-1 justify-end">
            <BottomPanelResize
              containerRef={workspaceColumnRef}
              onResize={handleBottomPanelResize}
            />

            <div
              data-bottom-panel
              style={{ height: bottomPanelHeight, maxHeight: '100%' }}
              className="flex flex-col shrink-0 border-t border-cat-surface1 min-h-0"
            >
              <SprintProgressBar progress={sprintProgress} planRunActive={planRunActive} />
              <AgentRunBar
                activeRun={activeRun}
                currentTool={currentTool}
                planRunActive={planRunActive}
                onOpenTools={openToolsTab}
              />
              <div className="flex bg-cat-mantle border-b border-cat-surface1 shrink-0 items-stretch">
                <div className="flex-1 min-w-0 overflow-x-auto">
                  <div className="flex whitespace-nowrap">
                    {bottomTabs.map((tab) => (
                      <button
                        key={tab.id}
                        type="button"
                        onClick={() => setBottomTab(tab.id)}
                        title={tab.label}
                        className={`px-3 md:px-4 py-2 text-[11px] font-semibold uppercase tracking-wider border-r border-cat-surface1 transition-colors shrink-0 ${
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
                    ))}
                  </div>
                </div>
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
                      onTaskClick={(taskId) => {
                        const task = findTaskOnBoard(state.board, taskId)
                        if (task) setSelectedTask(task)
                      }}
                    />
                  </div>
                )}
                {bottomTab === 'tools' && (
                  <div className="absolute inset-0 flex flex-col min-h-0">
                    <ToolsPanel
                      toolEvents={toolEvents}
                      onClearLog={clearToolEvents}
                      onMergeToolEvent={mergeToolEvent}
                      board={state.board}
                      selectedTaskId={selectedTask?.id}
                      onRefreshState={() => void refresh()}
                      preferredSubTab={toolsPreferredSubTab}
                      workspaceDir={state.workspaceDir}
                      onInjectToolEvidence={(taskId, payload) => handleInjectToolEvidence(taskId, payload)}
                    />
                  </div>
                )}
                <ChatPanel
                  hidden={bottomTab !== 'chat'}
                  ollamaUrl={ollamaUrl}
                  filePaths={Object.keys(localFiles)}
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
                  onRefreshState={() => void refresh()}
                  onSplitTask={(taskId) => void handleSplitTask(taskId)}
                  toolEvents={toolEvents}
                />
                <TerminalPanel
                  hidden={bottomTab !== 'terminal'}
                  workspaceDir={state.workspaceDir}
                />
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
        onResolveUser={(taskId, answer) =>
          void withLoading(async () => {
            handleState(await resolveUserQuestion(taskId, answer))
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
        getTaskTitle={(taskId) => findTaskOnBoard(state.board, taskId)?.title}
      />

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
        <div className="fixed inset-0 bg-black/75 flex items-center justify-center p-4 z-50">
          <div className="bg-cat-surface0 rounded-2xl max-w-md w-full p-6 border border-cat-surface1 space-y-3">
            <h3 className="text-base font-bold text-white">Sprint Summary</h3>
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
            <button
              type="button"
              onClick={() => setShowSprintSummary(false)}
              className="w-full bg-indigo-600 text-white text-xs py-2 rounded-lg"
            >
              Close
            </button>
          </div>
        </div>
      )}

      <ToolResolutionModal
        pending={pendingToolModal}
        onClose={() => setPendingToolModal(null)}
        onResolved={() => void refreshPendingTools()}
      />

      <ToolApprovalModal
        pending={approvalModal}
        onClose={() => setApprovalModal(null)}
        onResolved={() => void refreshPendingApprovals()}
        onApprove={async (id, approved) => {
          await resolveToolApproval(id, approved)
        }}
      />
    </div>
  )
}
