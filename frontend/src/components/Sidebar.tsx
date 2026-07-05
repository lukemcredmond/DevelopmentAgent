import type { AgentId, AppState, ConfigPayload, WorkflowSettings } from '../types'
import { AGENT_LABELS, DEFAULT_WORKFLOW_SETTINGS } from '../types'
import WorkflowPanel from './WorkflowPanel'

interface SidebarProps {
  state: AppState
  ollamaUrl: string
  brief: string
  projectName: string
  workspaceDir: string
  skillsDir: string
  poModel: string
  devModel: string
  crModel: string
  qaModel: string
  loading: boolean
  ollamaOk: boolean | null
  autoSprint: boolean
  autoSprintPaused?: boolean
  sprintRunning: boolean
  isDark: boolean
  onOllamaUrlChange: (v: string) => void
  onBriefChange: (v: string) => void
  onProjectNameChange: (v: string) => void
  onWorkspaceDirChange: (v: string) => void
  onSkillsDirChange: (v: string) => void
  onPoModelChange: (v: string) => void
  onDevModelChange: (v: string) => void
  onCrModelChange: (v: string) => void
  onQaModelChange: (v: string) => void
  onLoadProject: (id: string) => void
  onSaveConfig: (payload: ConfigPayload) => void
  onOpenNewProject: () => void
  onOpenManualTask: () => void
  onOpenSkillModal: (agent: AgentId) => void
  onRemoveSkill: (agent: AgentId, skill: string) => void
  onPlan: () => void
  onPlanAndRun: () => void
  onStep: () => void
  onClearAllTasks: () => void
  onReset: () => void
  onWorkflowSettingsChange: (partial: Partial<WorkflowSettings>) => void
  onToggleTheme: () => void
  onToggleAutoSprint: (enabled: boolean) => void
  onCancelSprint: () => void
  onExportProject: () => void
  onImportProject: (file: File) => void
  onDeleteProject: () => void
}

const skillBadgeClass: Record<AgentId, string> = {
  po: 'bg-indigo-950/40 border border-indigo-500/30 text-indigo-300',
  dev: 'bg-emerald-950/40 border border-emerald-500/30 text-emerald-300',
  cr: 'bg-orange-950/40 border border-orange-500/30 text-orange-300',
  qa: 'bg-purple-950/40 border border-purple-500/30 text-purple-300',
}

export default function Sidebar({
  state,
  ollamaUrl,
  brief,
  projectName,
  workspaceDir,
  skillsDir,
  poModel,
  devModel,
  crModel,
  qaModel,
  loading,
  ollamaOk,
  autoSprint,
  autoSprintPaused = false,
  sprintRunning,
  isDark,
  onOllamaUrlChange,
  onBriefChange,
  onProjectNameChange,
  onWorkspaceDirChange,
  onSkillsDirChange,
  onPoModelChange,
  onDevModelChange,
  onCrModelChange,
  onQaModelChange,
  onLoadProject,
  onSaveConfig,
  onOpenNewProject,
  onOpenManualTask,
  onOpenSkillModal,
  onRemoveSkill,
  onPlan,
  onPlanAndRun,
  onStep,
  onClearAllTasks,
  onReset,
  onWorkflowSettingsChange,
  onToggleTheme,
  onToggleAutoSprint,
  onCancelSprint,
  onExportProject,
  onImportProject,
  onDeleteProject,
}: SidebarProps) {
  const agents: { id: AgentId; model: string }[] = [
    { id: 'po', model: poModel },
    { id: 'dev', model: devModel },
    { id: 'cr', model: crModel },
    { id: 'qa', model: qaModel },
  ]

  const boardEmpty =
    (state.board.Backlog?.length ?? 0) === 0 &&
    (state.board['In Progress']?.length ?? 0) === 0 &&
    (state.board['Needs PO']?.length ?? 0) === 0 &&
    (state.board['Needs User']?.length ?? 0) === 0 &&
    (state.board.QA?.length ?? 0) === 0

  const ws = state.workflowSettings ?? DEFAULT_WORKFLOW_SETTINGS
  const notifications = state.notifications ?? {
    needsPo: 0,
    needsUser: 0,
    pendingApproval: 0,
    qaFailures: 0,
  }

  return (
    <aside className="w-full lg:w-72 xl:w-80 bg-cat-mantle dark:bg-cat-mantle border-b lg:border-b-0 lg:border-r border-cat-surface1 p-4 flex flex-col justify-between overflow-y-auto shrink-0">
      <div className="space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-cat-surface1">
          <div className="flex items-center gap-3">
            <div className="bg-indigo-600 p-2 rounded-xl text-white shadow-lg shadow-indigo-500/20">
              <i className="fa-solid fa-code-merge text-xl" />
            </div>
            <div>
              <h1 className="font-bold text-lg text-white">All Hands</h1>
              <p className="text-xs text-cat-subtext">Multi-Agent Workspace</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span
              className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${
                ollamaOk === null
                  ? 'bg-cat-surface0 text-cat-subtext'
                  : ollamaOk
                    ? 'bg-emerald-950/50 text-emerald-400 border border-emerald-500/30'
                    : 'bg-rose-950/50 text-rose-400 border border-rose-500/30'
              }`}
              title="Ollama health"
            >
              {ollamaOk === null ? 'Ollama…' : ollamaOk ? 'Ollama OK' : 'Ollama Down'}
            </span>
            <button
              type="button"
              onClick={onToggleTheme}
              className="p-1.5 rounded-lg bg-cat-surface0 border border-cat-surface1 text-cat-subtext hover:text-white"
              title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              <i className={`fa-solid ${isDark ? 'fa-sun' : 'fa-moon'}`} />
            </button>
          </div>
        </div>

        <div className="bg-cat-surface0 p-3 rounded-xl border border-cat-surface1 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
              Load Workspace
            </h3>
            <button
              type="button"
              onClick={onOpenNewProject}
              className="text-xs text-indigo-400 hover:text-indigo-300 font-semibold flex items-center gap-1"
            >
              <i className="fa-solid fa-plus text-[10px]" />
              New
            </button>
          </div>
          <select
            value={state.projectId}
            onChange={(e) => onLoadProject(e.target.value)}
            className="w-full bg-cat-base border border-cat-surface1 rounded-lg p-2 text-xs text-white focus:outline-none focus:border-indigo-500"
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
          <div className="flex flex-wrap gap-1.5 pt-1">
            <button
              type="button"
              onClick={onExportProject}
              className="flex-1 min-w-[70px] text-[10px] bg-cat-base border border-cat-surface1 rounded py-1 text-cat-subtext hover:text-white"
            >
              Export
            </button>
            <label className="flex-1 min-w-[70px] text-[10px] bg-cat-base border border-cat-surface1 rounded py-1 text-cat-subtext hover:text-white text-center cursor-pointer">
              Import
              <input
                type="file"
                accept=".zip"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) onImportProject(file)
                  e.target.value = ''
                }}
              />
            </label>
            <button
              type="button"
              onClick={onDeleteProject}
              disabled={state.projectsList.length <= 1}
              className="flex-1 min-w-[70px] text-[10px] bg-rose-950/20 border border-rose-500/20 rounded py-1 text-rose-400 hover:bg-rose-950/40 disabled:opacity-40"
              title="Delete a non-active project from the list"
            >
              Delete
            </button>
          </div>
        </div>

        <div className="bg-cat-surface0 p-3 rounded-xl border border-cat-surface1 space-y-2">
          <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
            Project Config
          </h3>
          <div className="space-y-1.5 text-xs">
            <label className="block">
              <span className="text-[10px] text-cat-subtext block mb-0.5">PROJECT NAME</span>
              <input
                type="text"
                value={projectName}
                onChange={(e) => onProjectNameChange(e.target.value)}
                className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 text-white font-medium focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="text-[10px] text-cat-subtext block mb-0.5">WORKSPACE DIR</span>
              <input
                type="text"
                value={workspaceDir}
                onChange={(e) => onWorkspaceDirChange(e.target.value)}
                className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 text-white font-mono focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="text-[10px] text-cat-subtext block mb-0.5">GLOBAL SKILLS DIR</span>
              <input
                type="text"
                value={skillsDir}
                onChange={(e) => onSkillsDirChange(e.target.value)}
                className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 text-white font-mono focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="text-[10px] text-cat-subtext block mb-0.5">OLLAMA URL</span>
              <input
                type="text"
                value={ollamaUrl}
                onChange={(e) => onOllamaUrlChange(e.target.value)}
                className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 text-white font-mono focus:outline-none"
              />
            </label>

            <div className="pt-2 border-t border-cat-surface1/50 space-y-1.5">
              {[
                { label: 'PO MODEL', value: poModel, onChange: onPoModelChange },
                { label: 'DEV MODEL', value: devModel, onChange: onDevModelChange },
                { label: 'CR MODEL', value: crModel, onChange: onCrModelChange },
                { label: 'QA MODEL', value: qaModel, onChange: onQaModelChange },
              ].map(({ label, value, onChange }) => (
                <div key={label} className="flex items-center justify-between">
                  <span className="text-[9px] text-cat-subtext font-bold">{label}</span>
                  <input
                    type="text"
                    value={value}
                    onChange={(e) => onChange(e.target.value)}
                    className="bg-cat-base border border-cat-surface1 rounded p-0.5 px-1 font-mono text-[10px] text-right w-2/3 focus:outline-none"
                  />
                </div>
              ))}
            </div>

            <button
              type="button"
              onClick={() =>
                onSaveConfig({
                  projectName,
                  workspaceDir,
                  skillsDir,
                  poModel,
                  devModel,
                  crModel,
                  qaModel,
                })
              }
              className="w-full bg-indigo-600/40 hover:bg-indigo-600/80 border border-indigo-500/30 text-white font-semibold py-1 rounded text-[11px] transition-colors mt-2"
            >
              Save Custom Configurations
            </button>
          </div>
        </div>

        <div className="bg-cat-surface0 p-3 rounded-xl border border-cat-surface1 space-y-3">
          <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
            Agent Team & Skills
          </h3>
          <div className="space-y-2">
            {agents.map(({ id, model }) => (
              <div
                key={id}
                className="p-2 bg-cat-base rounded border border-cat-surface1 text-xs"
              >
                <div className="flex items-center justify-between font-bold text-white mb-1">
                  <span>{AGENT_LABELS[id]}</span>
                  <span className="text-[9px] font-mono text-cat-subtext bg-cat-surface0 px-1 py-0.5 rounded">
                    {model}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1 mb-1.5">
                  {(state.assignedSkills[id] ?? []).map((skill) => (
                    <span
                      key={skill}
                      className={`${skillBadgeClass[id]} text-[10px] px-1.5 py-0.5 rounded flex items-center gap-1`}
                    >
                      <span>
                        {skill.split('/').pop()?.replace('.md', '').replace('_', ' ')}
                      </span>
                      <button
                        type="button"
                        onClick={() => onRemoveSkill(id, skill)}
                        className="hover:text-red-400 text-slate-400"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                  {(state.assignedSkills[id] ?? []).length === 0 && (
                    <span className="text-[10px] text-cat-overlay italic">No skills</span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => onOpenSkillModal(id)}
                  className="bg-cat-surface0 hover:bg-cat-surface1 text-cat-subtext py-0.5 px-2 rounded border border-cat-surface1 text-[10px] font-semibold transition-colors"
                >
                  + Add Skill
                </button>
              </div>
            ))}
          </div>
        </div>

        <WorkflowPanel
          settings={ws}
          changelog={state.briefChangelog ?? []}
          notifications={notifications}
          onSettingsChange={onWorkflowSettingsChange}
        />

        <div className="bg-cat-surface0 p-3 rounded-xl border border-cat-surface1 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
              Project Brief
            </h3>
            <button
              type="button"
              onClick={onOpenManualTask}
              className="text-xs text-indigo-400 hover:text-indigo-300 font-semibold flex items-center gap-1"
            >
              <i className="fa-solid fa-square-plus" />
              Add Feature
            </button>
          </div>
          <p className="text-[10px] text-cat-overlay leading-relaxed">
            Describe your project. Plan & Run automates PO → Dev → QA. Developer questions go to
            Needs PO; user decisions go to Needs User.
          </p>
          <textarea
            value={brief}
            onChange={(e) => onBriefChange(e.target.value)}
            className="w-full h-20 bg-cat-base border border-cat-surface1 rounded-lg p-2 text-xs text-white focus:outline-none focus:border-indigo-500 resize-none font-mono"
            placeholder="Describe your project goals, features, and constraints…"
          />
          <div className="space-y-2 pt-1">
            <button
              type="button"
              onClick={onPlanAndRun}
              disabled={loading || !brief.trim()}
              className="w-full bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white font-medium py-2 rounded-lg text-xs transition-colors flex items-center justify-center gap-2"
            >
              {loading ? (
                <i className="fa-solid fa-spinner animate-spin" />
              ) : (
                <i className="fa-solid fa-rocket" />
              )}
              Plan & Run (Brief → PO → Sprint)
            </button>
            <button
              type="button"
              onClick={onPlan}
              disabled={loading || !brief.trim()}
              className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-medium py-2 rounded-lg text-xs transition-colors flex items-center justify-center gap-2"
            >
              <i className="fa-solid fa-layer-group" />
              Send Brief to PO Only
            </button>
            <button
              type="button"
              onClick={onStep}
              disabled={loading || boardEmpty}
              className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-medium py-2 rounded-lg text-xs transition-colors flex items-center justify-center gap-2"
            >
              {loading ? (
                <i className="fa-solid fa-spinner animate-spin" />
              ) : (
                <i className="fa-solid fa-play" />
              )}
              Execute Sprint Step
            </button>
            <div className="flex items-center gap-2">
              <label className="flex items-center gap-2 text-xs text-cat-subtext cursor-pointer">
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
                  className="text-xs text-rose-400 hover:text-rose-300"
                >
                  Cancel run
                </button>
              )}
            </div>
            {sprintRunning && (
              <p className="text-[10px] text-violet-300/90 italic">
                Sprint active — watch Console and the progress bar below the editor.
              </p>
            )}
            {autoSprint && autoSprintPaused && !sprintRunning && (
              <p className="text-[10px] text-amber-400/90 italic">
                Paused — waiting for backlog work
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="pt-4 border-t border-cat-surface1 space-y-2">
        <button
          type="button"
          onClick={onClearAllTasks}
          disabled={sprintRunning}
          title={
            sprintRunning
              ? 'Wait for the current sprint step to finish'
              : 'Remove all Kanban cards; workspace files and brief are kept'
          }
          className="w-full bg-amber-950/20 text-amber-300 hover:bg-amber-950/40 disabled:opacity-50 border border-amber-500/20 py-2 rounded-lg text-xs font-medium transition-colors"
        >
          <i className="fa-solid fa-trash-can mr-1" />
          Clear All Tasks
        </button>
        <button
          type="button"
          onClick={onReset}
          className="w-full bg-rose-950/20 text-rose-400 hover:bg-rose-950/40 border border-rose-500/20 py-2 rounded-lg text-xs font-medium transition-colors"
        >
          <i className="fa-solid fa-arrow-rotate-left mr-1" />
          Reset Workspace State
        </button>
      </div>
    </aside>
  )
}
