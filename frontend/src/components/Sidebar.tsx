import { memo } from 'react'
import type { AppState } from '../types'

interface SidebarProps {
  state: AppState
  brief: string
  loading: boolean
  ollamaOk: boolean | null
  autoSprint: boolean
  autoSprintPaused?: boolean
  sprintRunning: boolean
  isDark: boolean
  onOpenSettings: () => void
  onLoadProject: (id: string) => void
  onOpenNewProject: () => void
  onPlan: () => void
  onGenerateBacklog?: () => void
  planOutlineReady?: boolean
  onPlanAndRun: () => void
  onStep: () => void
  onRunInProgress?: () => void
  inProgressCount?: number
  onClaimReadyCards?: () => void
  claimableBacklogCount?: number
  onEscalateNeedsUserToPo?: () => void
  onClearAllTasks: () => void
  onReset: () => void
  onToggleTheme: () => void
  onToggleAutoSprint: (enabled: boolean) => void
  onCancelSprint: () => void
}

export default memo(function Sidebar({
  state,
  brief,
  loading,
  ollamaOk,
  autoSprint,
  autoSprintPaused = false,
  sprintRunning,
  isDark,
  onOpenSettings,
  onLoadProject,
  onOpenNewProject,
  onPlan,
  onGenerateBacklog,
  planOutlineReady = false,
  onPlanAndRun,
  onStep,
  onRunInProgress,
  inProgressCount = 0,
  onClaimReadyCards,
  claimableBacklogCount = 0,
  onEscalateNeedsUserToPo,
  onClearAllTasks,
  onReset,
  onToggleTheme,
  onToggleAutoSprint,
  onCancelSprint,
}: SidebarProps) {
  const boardEmpty =
    (state.board.Backlog?.length ?? 0) === 0 &&
    (state.board['In Progress']?.length ?? 0) === 0 &&
    (state.board['Needs PO']?.length ?? 0) === 0 &&
    (state.board['Needs User']?.length ?? 0) === 0 &&
    (state.board.QA?.length ?? 0) === 0

  const notifications = state.notifications ?? {
    needsPo: 0,
    needsUser: 0,
    pendingApproval: 0,
    qaFailures: 0,
  }

  return (
    <aside className="w-full lg:w-52 xl:w-56 bg-cat-mantle dark:bg-cat-mantle border-b lg:border-b-0 lg:border-r border-cat-surface1 p-3 flex flex-col justify-between overflow-y-auto shrink-0">
      <div className="space-y-3">
        <div className="flex items-center justify-between pb-2 border-b border-cat-surface1 gap-1">
          <div className="flex items-center gap-2 min-w-0">
            <div className="bg-indigo-600 p-1.5 rounded-lg text-white shadow-lg shadow-indigo-500/20 shrink-0">
              <i className="fa-solid fa-code-merge text-sm" />
            </div>
            <div className="min-w-0">
              <h1 className="font-bold text-sm text-white truncate">All Hands</h1>
              <p className="text-[10px] text-cat-subtext truncate">Multi-Agent</p>
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0">
            <span
              className={`text-[9px] px-1.5 py-0.5 rounded-full font-semibold ${
                ollamaOk === null
                  ? 'bg-cat-surface0 text-cat-subtext'
                  : ollamaOk
                    ? 'bg-emerald-950/50 text-emerald-400 border border-emerald-500/30'
                    : 'bg-rose-950/50 text-rose-400 border border-rose-500/30'
              }`}
              title="Ollama health"
            >
              {ollamaOk === null ? '…' : ollamaOk ? 'OK' : 'Down'}
            </span>
            <button
              type="button"
              onClick={onToggleTheme}
              className="p-1.5 rounded-lg bg-cat-surface0 border border-cat-surface1 text-cat-subtext hover:text-white"
              title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              <i className={`fa-solid ${isDark ? 'fa-sun' : 'fa-moon'} text-xs`} />
            </button>
          </div>
        </div>

        <button
          type="button"
          onClick={onOpenSettings}
          className="w-full bg-cat-surface0 hover:bg-cat-surface1 border border-cat-surface1 text-white font-semibold py-2 rounded-lg text-xs transition-colors flex items-center justify-center gap-2"
          title="Settings (Ctrl+,)"
        >
          <i className="fa-solid fa-gear text-indigo-400" />
          Settings
        </button>

        <div className="bg-cat-surface0 p-2.5 rounded-xl border border-cat-surface1 space-y-2">
          <div className="flex items-center justify-between">
            <h3 className="text-[10px] font-bold uppercase tracking-wider text-cat-subtext">
              Project
            </h3>
            <button
              type="button"
              onClick={onOpenNewProject}
              className="text-[10px] text-indigo-400 hover:text-indigo-300 font-semibold"
            >
              + New
            </button>
          </div>
          <select
            value={state.projectId}
            onChange={(e) => onLoadProject(e.target.value)}
            className="w-full bg-cat-base border border-cat-surface1 rounded-lg p-1.5 text-[11px] text-white focus:outline-none focus:border-indigo-500"
          >
            {state.projectsList.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
            {state.projectsList.length === 0 && (
              <option value="default-proj">Default Project Workspace</option>
            )}
          </select>
        </div>

        <div className="bg-cat-surface0 p-2.5 rounded-xl border border-cat-surface1 space-y-2">
          <h3 className="text-[10px] font-bold uppercase tracking-wider text-cat-subtext">Sprint</h3>
          <p className="text-[9px] text-cat-overlay leading-relaxed">
            Plan → Features → sprint. Use Settings for models and workflow.
          </p>
          <div className="space-y-1.5">
            <button
              type="button"
              onClick={onPlan}
              disabled={loading || !brief.trim()}
              className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-medium py-1.5 rounded-lg text-[11px] transition-colors flex items-center justify-center gap-1.5"
            >
              <i className="fa-solid fa-map" />
              Plan outline
            </button>
            {onGenerateBacklog && (
              <button
                type="button"
                onClick={onGenerateBacklog}
                disabled={loading || !brief.trim() || !planOutlineReady}
                className="w-full bg-violet-700 hover:bg-violet-600 disabled:opacity-50 text-white font-medium py-1.5 rounded-lg text-[11px] transition-colors flex items-center justify-center gap-1.5"
              >
                <i className="fa-solid fa-layer-group" />
                Generate Features
              </button>
            )}
            <button
              type="button"
              onClick={onPlanAndRun}
              disabled={loading || !brief.trim()}
              className="w-full bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white font-medium py-1.5 rounded-lg text-[11px] transition-colors flex items-center justify-center gap-1.5"
            >
              {loading ? (
                <i className="fa-solid fa-spinner animate-spin" />
              ) : (
                <i className="fa-solid fa-rocket" />
              )}
              Plan & Run
            </button>
            <button
              type="button"
              onClick={onStep}
              disabled={loading || boardEmpty}
              className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium py-1.5 rounded-lg text-[11px] transition-colors flex items-center justify-center gap-1.5"
            >
              {loading ? (
                <i className="fa-solid fa-spinner animate-spin" />
              ) : (
                <i className="fa-solid fa-play" />
              )}
              Execute Step
            </button>
            {onRunInProgress && inProgressCount > 0 && (
              <button
                type="button"
                onClick={onRunInProgress}
                disabled={loading || sprintRunning}
                className="w-full bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white font-medium py-1.5 rounded-lg text-[11px] transition-colors flex items-center justify-center gap-1.5"
              >
                <i className="fa-solid fa-forward" />
                Run In Progress ({inProgressCount})
              </button>
            )}
            {onClaimReadyCards && claimableBacklogCount > 0 && (
              <button
                type="button"
                onClick={onClaimReadyCards}
                disabled={loading || sprintRunning}
                className="w-full bg-teal-700 hover:bg-teal-600 disabled:opacity-50 text-white font-medium py-1.5 rounded-lg text-[11px] transition-colors flex items-center justify-center gap-1.5"
              >
                <i className="fa-solid fa-hand-pointer" />
                Claim ({claimableBacklogCount})
              </button>
            )}
            <div className="flex items-center gap-2 pt-0.5">
              <label className="flex items-center gap-1.5 text-[11px] text-cat-subtext cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoSprint}
                  onChange={(e) => onToggleAutoSprint(e.target.checked)}
                  className="rounded border-cat-surface1"
                />
                Auto Sprint
              </label>
              {sprintRunning && (
                <button
                  type="button"
                  onClick={onCancelSprint}
                  className="text-[10px] text-rose-400 hover:text-rose-300"
                >
                  Cancel
                </button>
              )}
            </div>
            {sprintRunning && (
              <p className="text-[9px] text-violet-300/90 italic">Sprint active</p>
            )}
            {autoSprint && autoSprintPaused && !sprintRunning && (
              <p className="text-[9px] text-amber-400/90 italic">Paused — waiting for backlog</p>
            )}
            {(notifications.needsUser ?? 0) > 0 && onEscalateNeedsUserToPo && (
              <button
                type="button"
                onClick={onEscalateNeedsUserToPo}
                disabled={loading || sprintRunning}
                className="w-full bg-amber-950/30 hover:bg-amber-950/50 disabled:opacity-50 text-amber-200 text-[11px] py-1.5 px-2 rounded-lg border border-amber-500/30"
              >
                Send {notifications.needsUser} Needs User → PO
              </button>
            )}
          </div>
        </div>
      </div>

      <div className="pt-3 border-t border-cat-surface1 space-y-1.5">
        <button
          type="button"
          onClick={onClearAllTasks}
          disabled={sprintRunning}
          title={
            sprintRunning
              ? 'Wait for the current sprint step to finish'
              : 'Remove all Kanban cards; workspace files and brief are kept'
          }
          className="w-full bg-amber-950/20 text-amber-300 hover:bg-amber-950/40 disabled:opacity-50 border border-amber-500/20 py-1.5 rounded-lg text-[11px] font-medium transition-colors"
        >
          Clear Tasks
        </button>
        <button
          type="button"
          onClick={onReset}
          className="w-full bg-rose-950/20 text-rose-400 hover:bg-rose-950/40 border border-rose-500/20 py-1.5 rounded-lg text-[11px] font-medium transition-colors"
        >
          Reset Workspace
        </button>
      </div>
    </aside>
  )
})
