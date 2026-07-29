import { useCallback, useEffect, useState } from 'react'
import type { AgentId, AppState, ConfigPayload, WorkflowSettings } from '../types'
import { AGENT_LABELS, DEFAULT_WORKFLOW_SETTINGS } from '../types'
import { isAutoLlmToolHealthOnPick, runAndPersistLlmProbeAll } from '../lib/toolHealthLlm'
import BoardRecoveryPanel from './BoardRecoveryPanel'
import GpuModelRecommendations from './GpuModelRecommendations'
import InstalledModelsPanel from './InstalledModelsPanel'
import { SettingHint } from './SettingHint'
import SlideOver from './SlideOver'
import WorkflowPanel from './WorkflowPanel'

export type SettingsTab = 'project' | 'models' | 'agents' | 'workflow'

interface SettingsSlideOverProps {
  open: boolean
  onClose: () => void
  state: AppState
  ollamaUrl: string
  projectName: string
  workspaceDir: string
  skillsDir: string
  poModel: string
  devModel: string
  crModel: string
  qaModel: string
  poBackupModel: string
  devBackupModel: string
  crBackupModel: string
  qaBackupModel: string
  onOllamaUrlChange: (v: string) => void
  onProjectNameChange: (v: string) => void
  onWorkspaceDirChange: (v: string) => void
  onSkillsDirChange: (v: string) => void
  onPoModelChange: (v: string) => void
  onDevModelChange: (v: string) => void
  onCrModelChange: (v: string) => void
  onQaModelChange: (v: string) => void
  onPoBackupModelChange: (v: string) => void
  onDevBackupModelChange: (v: string) => void
  onCrBackupModelChange: (v: string) => void
  onQaBackupModelChange: (v: string) => void
  onLoadProject: (id: string) => void
  onSaveConfig: (payload: ConfigPayload) => void
  onOpenNewProject: () => void
  onOpenSkillModal: (agent: AgentId) => void
  onRemoveSkill: (agent: AgentId, skill: string) => void
  onWorkflowSettingsChange: (partial: Partial<WorkflowSettings>) => void
  onExportProject: () => void
  onImportProject: (file: File) => void
  onDeleteProject: () => void
  onOpenMemoryTab?: () => void
  onOpenCustomTools?: () => void
  onBoardRestored?: (state: AppState) => void
  indexProgress?: import('../types').IndexProgress | null
  skillSuggestionCounts?: Record<AgentId, number>
  initialTab?: SettingsTab
}

const skillBadgeClass: Record<AgentId, string> = {
  po: 'bg-indigo-950/40 border border-indigo-500/30 text-indigo-300',
  dev: 'bg-emerald-950/40 border border-emerald-500/30 text-emerald-300',
  cr: 'bg-orange-950/40 border border-orange-500/30 text-orange-300',
  qa: 'bg-purple-950/40 border border-purple-500/30 text-purple-300',
}

const TABS: { id: SettingsTab; label: string; icon: string }[] = [
  { id: 'project', label: 'Project', icon: 'fa-folder' },
  { id: 'models', label: 'Models', icon: 'fa-microchip' },
  { id: 'agents', label: 'Agents', icon: 'fa-users' },
  { id: 'workflow', label: 'Workflow', icon: 'fa-sliders' },
]

export default function SettingsSlideOver({
  open,
  onClose,
  state,
  ollamaUrl,
  projectName,
  workspaceDir,
  skillsDir,
  poModel,
  devModel,
  crModel,
  qaModel,
  poBackupModel,
  devBackupModel,
  crBackupModel,
  qaBackupModel,
  onOllamaUrlChange,
  onProjectNameChange,
  onWorkspaceDirChange,
  onSkillsDirChange,
  onPoModelChange,
  onDevModelChange,
  onCrModelChange,
  onQaModelChange,
  onPoBackupModelChange,
  onDevBackupModelChange,
  onCrBackupModelChange,
  onQaBackupModelChange,
  onLoadProject,
  onSaveConfig,
  onOpenNewProject,
  onOpenSkillModal,
  onRemoveSkill,
  onWorkflowSettingsChange,
  onExportProject,
  onImportProject,
  onDeleteProject,
  onOpenMemoryTab,
  onOpenCustomTools,
  onBoardRestored,
  indexProgress = null,
  skillSuggestionCounts = { po: 0, dev: 0, cr: 0, qa: 0 },
  initialTab = 'project',
}: SettingsSlideOverProps) {
  const [tab, setTab] = useState<SettingsTab>(initialTab)
  const [modelFocus, setModelFocus] = useState<'PO' | 'DEV' | 'CR' | 'QA'>('DEV')
  const [llmHealthStatus, setLlmHealthStatus] = useState<string | null>(null)
  const [apiTokenInput, setApiTokenInput] = useState('')

  useEffect(() => {
    if (!open) return
    try {
      setApiTokenInput(localStorage.getItem('allhandsApiToken') || '')
    } catch {
      setApiTokenInput('')
    }
  }, [open])

  // Re-hydrate role model fields from server state when Settings opens / project changes.
  useEffect(() => {
    if (!open) return
    if (state.models?.po) onPoModelChange(state.models.po)
    if (state.models?.dev) onDevModelChange(state.models.dev)
    if (state.models?.cr) onCrModelChange(state.models.cr)
    if (state.models?.qa) onQaModelChange(state.models.qa)
    if (state.backupModels?.po !== undefined) onPoBackupModelChange(state.backupModels.po)
    if (state.backupModels?.dev !== undefined) onDevBackupModelChange(state.backupModels.dev)
    if (state.backupModels?.cr !== undefined) onCrBackupModelChange(state.backupModels.cr)
    if (state.backupModels?.qa !== undefined) onQaBackupModelChange(state.backupModels.qa)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sync once when open/projectId/models change
  }, [
    open,
    state.projectId,
    state.models?.po,
    state.models?.dev,
    state.models?.cr,
    state.models?.qa,
    state.backupModels?.po,
    state.backupModels?.dev,
    state.backupModels?.cr,
    state.backupModels?.qa,
  ])

  const assignModel = useCallback(
    (role: AgentId, name: string) => {
      if (role === 'po') onPoModelChange(name)
      else if (role === 'dev') onDevModelChange(name)
      else if (role === 'cr') onCrModelChange(name)
      else onQaModelChange(name)

      if (!isAutoLlmToolHealthOnPick()) return
      const projectId = state.projectId || ''
      setLlmHealthStatus(`LLM tool check running for ${role.toUpperCase()} (${name})…`)
      void runAndPersistLlmProbeAll(role, name, projectId)
        .then(({ summary }) => {
          setLlmHealthStatus(
            `LLM tool check (${role.toUpperCase()}): ${summary.pass} pass, ${summary.fail} fail, ${summary.skip} skip — see Tools → Health`,
          )
        })
        .catch((err: unknown) => {
          setLlmHealthStatus(
            `LLM tool check failed: ${err instanceof Error ? err.message : String(err)}`,
          )
        })
    },
    [onPoModelChange, onDevModelChange, onCrModelChange, onQaModelChange, state.projectId],
  )

  const agents: { id: AgentId; model: string }[] = [
    { id: 'po', model: poModel },
    { id: 'dev', model: devModel },
    { id: 'cr', model: crModel },
    { id: 'qa', model: qaModel },
  ]

  const ws = state.workflowSettings ?? DEFAULT_WORKFLOW_SETTINGS
  const notifications = state.notifications ?? {
    needsPo: 0,
    needsUser: 0,
    pendingApproval: 0,
    qaFailures: 0,
  }

  return (
    <SlideOver
      open={open}
      onClose={onClose}
      side="left"
      title={
        <span className="flex items-center gap-2">
          <i className="fa-solid fa-gear text-indigo-400" />
          Settings
        </span>
      }
      widthClass="w-full max-w-[min(720px,92vw)]"
      zIndexClass="z-50"
    >
      <div className="flex h-full min-h-0">
        <nav className="w-36 shrink-0 border-r border-cat-surface1 bg-cat-mantle/40 p-2 space-y-0.5">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`w-full text-left text-[11px] font-semibold px-2.5 py-2 rounded-lg flex items-center gap-2 transition-colors ${
                tab === t.id
                  ? 'bg-indigo-600/30 text-indigo-200'
                  : 'text-cat-subtext hover:text-white hover:bg-cat-surface1'
              }`}
            >
              <i className={`fa-solid ${t.icon} w-3.5 text-center opacity-80`} />
              {t.label}
            </button>
          ))}
        </nav>
        <div className="flex-1 min-w-0 overflow-y-auto p-4 space-y-4">
          {tab === 'project' && (
            <div className="space-y-4">
              <div className="space-y-3">
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
                <div className="flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    onClick={onExportProject}
                    className="flex-1 min-w-[70px] text-[10px] bg-cat-base border border-cat-surface1 rounded py-1.5 text-cat-subtext hover:text-white"
                  >
                    Export
                  </button>
                  <label className="flex-1 min-w-[70px] text-[10px] bg-cat-base border border-cat-surface1 rounded py-1.5 text-cat-subtext hover:text-white text-center cursor-pointer">
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
                    className="flex-1 min-w-[70px] text-[10px] bg-rose-950/20 border border-rose-500/20 rounded py-1.5 text-rose-400 hover:bg-rose-950/40 disabled:opacity-40"
                  >
                    Delete
                  </button>
                </div>
              </div>
              <div className="space-y-2 border-t border-cat-surface1 pt-4">
                <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
                  Paths
                </h3>
                <label className="block text-xs">
                  <span className="text-[10px] text-cat-subtext mb-0.5 inline-flex items-center">
                    PROJECT NAME
                    <SettingHint hint="Display name for this project in the sidebar and exports." />
                  </span>
                  <input
                    type="text"
                    value={projectName}
                    onChange={(e) => onProjectNameChange(e.target.value)}
                    className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-medium focus:outline-none"
                  />
                </label>
                <label className="block text-xs">
                  <span className="text-[10px] text-cat-subtext mb-0.5 inline-flex items-center">
                    WORKSPACE DIR
                    <SettingHint hint="Folder on your PC where the agent reads and writes project files." />
                  </span>
                  <input
                    type="text"
                    value={workspaceDir}
                    onChange={(e) => onWorkspaceDirChange(e.target.value)}
                    className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono focus:outline-none"
                  />
                </label>
                <label className="block text-xs">
                  <span className="text-[10px] text-cat-subtext mb-0.5 inline-flex items-center">
                    GLOBAL SKILLS DIR
                    <SettingHint hint="Folder of shared skill files agents can use across projects." />
                  </span>
                  <input
                    type="text"
                    value={skillsDir}
                    onChange={(e) => onSkillsDirChange(e.target.value)}
                    className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono focus:outline-none"
                  />
                </label>
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
                      poBackupModel,
                      devBackupModel,
                      crBackupModel,
                      qaBackupModel,
                    })
                  }
                  className="w-full bg-indigo-600/40 hover:bg-indigo-600/80 border border-indigo-500/30 text-white font-semibold py-2 rounded text-[11px] transition-colors mt-2"
                >
                  Save Custom Configurations
                </button>
              </div>
              <BoardRecoveryPanel
                projectId={state.projectId}
                onRestored={(st) => {
                  onBoardRestored?.(st)
                }}
              />
            </div>
          )}

          {tab === 'models' && (
            <div className="space-y-3 text-xs">
              <label className="block">
                <span className="text-[10px] text-cat-subtext mb-0.5 inline-flex items-center">
                  OLLAMA URL
                  <SettingHint hint="Address of your local Ollama server that runs the AI models (usually http://localhost:11434)." />
                </span>
                <input
                  type="text"
                  value={ollamaUrl}
                  onChange={(e) => onOllamaUrlChange(e.target.value)}
                  className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono focus:outline-none"
                />
              </label>
              <div className="space-y-1.5" data-testid="settings-api-token">
                <label className="block">
                  <span className="text-[10px] text-cat-subtext mb-0.5 inline-flex items-center">
                    LOCALHOST API TOKEN
                    <SettingHint hint="Password the browser sends to the backend when you set ALLHANDS_API_TOKEN on the server. Leave blank if the server has no token." />
                  </span>
                  <input
                    type="password"
                    autoComplete="off"
                    value={apiTokenInput}
                    onChange={(e) => setApiTokenInput(e.target.value)}
                    onBlur={() => {
                      const v = apiTokenInput.trim()
                      try {
                        if (v) localStorage.setItem('allhandsApiToken', v)
                        else localStorage.removeItem('allhandsApiToken')
                      } catch {
                        /* ignore */
                      }
                    }}
                    placeholder="Only if ALLHANDS_API_TOKEN is set on the server"
                    className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono focus:outline-none"
                  />
                </label>
                <p className="text-[10px] text-cat-overlay leading-relaxed">
                  Stored in this browser only (not sent as a Workflow setting). Leave blank when the
                  backend has no token. Or set <span className="font-mono">VITE_ALLHANDS_API_TOKEN</span>.
                </p>
                <button
                  type="button"
                  onClick={() => {
                    setApiTokenInput('')
                    try {
                      localStorage.removeItem('allhandsApiToken')
                    } catch {
                      /* ignore */
                    }
                  }}
                  className="text-[10px] text-cat-overlay hover:text-rose-300"
                >
                  Clear stored token
                </button>
              </div>
              <div className="space-y-3">
                {(
                  [
                    {
                      label: 'PO',
                      value: poModel,
                      onChange: onPoModelChange,
                      backup: poBackupModel,
                      onBackup: onPoBackupModelChange,
                      focus: 'PO' as const,
                    },
                    {
                      label: 'DEV',
                      value: devModel,
                      onChange: onDevModelChange,
                      backup: devBackupModel,
                      onBackup: onDevBackupModelChange,
                      focus: 'DEV' as const,
                    },
                    {
                      label: 'CR',
                      value: crModel,
                      onChange: onCrModelChange,
                      backup: crBackupModel,
                      onBackup: onCrBackupModelChange,
                      focus: 'CR' as const,
                    },
                    {
                      label: 'QA',
                      value: qaModel,
                      onChange: onQaModelChange,
                      backup: qaBackupModel,
                      onBackup: onQaBackupModelChange,
                      focus: 'QA' as const,
                    },
                  ] as const
                ).map(({ label, value, onChange, backup, onBackup, focus }) => (
                  <div key={label} className="space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[9px] text-cat-subtext font-bold shrink-0 inline-flex items-center">
                        {label} MODEL
                        <SettingHint
                          hint={`Primary Ollama model for the ${label} agent. Use a name from “ollama list”.`}
                        />
                      </span>
                      <input
                        type="text"
                        value={value}
                        onFocus={() => setModelFocus(focus)}
                        onChange={(e) => onChange(e.target.value)}
                        className="bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white text-right flex-1 focus:outline-none"
                      />
                    </div>
                    <div className="flex items-center justify-between gap-2 pl-0.5">
                      <span className="text-[9px] text-cat-overlay shrink-0 inline-flex items-center">
                        Backup (stuck)
                        <SettingHint hint="Optional second model used for a few steps when this agent gets stuck looping." />
                      </span>
                      <input
                        type="text"
                        value={backup}
                        onFocus={() => setModelFocus(focus)}
                        onChange={(e) => onBackup(e.target.value)}
                        placeholder="optional"
                        className="bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white text-right flex-1 focus:outline-none"
                      />
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-[10px] text-cat-overlay leading-relaxed">
                Backup models run for the next few stuck steps when that agent loops on plan/text
                with no progress, then revert to primary. Skipped for lint/tool blockers. Leave blank
                to disable per role.
              </p>
              <InstalledModelsPanel
                ollamaUrl={ollamaUrl}
                focusedRole={modelFocus}
                onPickModel={(name) => {
                  const role =
                    modelFocus === 'PO'
                      ? 'po'
                      : modelFocus === 'DEV'
                        ? 'dev'
                        : modelFocus === 'CR'
                          ? 'cr'
                          : 'qa'
                  assignModel(role, name)
                }}
              />
              {llmHealthStatus && (
                <p className="text-[10px] text-violet-300 leading-relaxed">{llmHealthStatus}</p>
              )}
              <label className="block text-[11px] text-cat-subtext">
                <span className="text-[10px] text-cat-overlay block mb-0.5">
                  Ollama keep_alive (keeps weights loaded between sprint steps)
                </span>
                <input
                  type="text"
                  value={state.workflowSettings?.ollamaKeepAlive ?? '30m'}
                  onChange={(e) => onWorkflowSettingsChange({ ollamaKeepAlive: e.target.value })}
                  className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white focus:outline-none"
                  placeholder="30m"
                />
              </label>
              <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
                Use <code className="text-cat-subtext">30m</code> or <code className="text-cat-subtext">-1</code>{' '}
                to avoid cold-reloading the model every iteration.
              </p>
              <div className="border border-cat-surface1 rounded-lg p-2 space-y-2">
                <p className="text-[10px] font-bold uppercase tracking-wider text-cat-subtext">
                  Advanced context (LLM speed)
                </p>
                <p className="text-[10px] text-cat-overlay leading-relaxed">
                  Raise tool-output chars if tools look truncated; lower prune % to keep more history.
                  Same controls live under Workflow.
                </p>
                <label className="block text-[11px] text-cat-subtext">
                  <span className="text-[10px] text-cat-overlay block mb-0.5">
                    Max tool output chars (to LLM)
                  </span>
                  <input
                    type="number"
                    min={1000}
                    max={50000}
                    value={state.workflowSettings?.maxToolOutputCharsForLlm ?? 6000}
                    onChange={(e) =>
                      onWorkflowSettingsChange({
                        maxToolOutputCharsForLlm: Number(e.target.value) || 6000,
                      })
                    }
                    className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white focus:outline-none"
                  />
                </label>
                <label className="block text-[11px] text-cat-subtext">
                  <span className="text-[10px] text-cat-overlay block mb-0.5">
                    Message prune threshold (% of num_ctx)
                  </span>
                  <input
                    type="number"
                    min={30}
                    max={90}
                    value={state.workflowSettings?.messagePruneThresholdPct ?? 60}
                    onChange={(e) =>
                      onWorkflowSettingsChange({
                        messagePruneThresholdPct: Number(e.target.value) || 60,
                      })
                    }
                    className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white focus:outline-none"
                  />
                </label>
              </div>
              <GpuModelRecommendations
                ollamaUrl={ollamaUrl}
                poModel={poModel}
                devModel={devModel}
                crModel={crModel}
                qaModel={qaModel}
                onPoModelChange={onPoModelChange}
                onDevModelChange={onDevModelChange}
                onCrModelChange={onCrModelChange}
                onQaModelChange={onQaModelChange}
                onPickModelForRole={(role, model) => assignModel(role, model)}
              />
              <p className="text-[10px] text-cat-overlay leading-relaxed">
                Model changes apply after{' '}
                <strong className="text-cat-subtext">Save Custom Configurations</strong>.
              </p>
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
                    poBackupModel,
                    devBackupModel,
                    crBackupModel,
                    qaBackupModel,
                  })
                }
                className="w-full bg-indigo-600/40 hover:bg-indigo-600/80 border border-indigo-500/30 text-white font-semibold py-2 rounded text-[11px] transition-colors"
              >
                Save Custom Configurations
              </button>
            </div>
          )}

          {tab === 'agents' && (
            <div className="space-y-3">
              <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext inline-flex items-center gap-1">
                Agent Team & Skills
                <SettingHint hint="Each role (PO, Dev, Reviewer, QA) can have skill files that teach it how to work on your project." />
              </h3>
              {agents.map(({ id, model }) => (
                <div
                  key={id}
                  className="p-3 bg-cat-base rounded-lg border border-cat-surface1 text-xs"
                >
                  <div className="flex items-center justify-between font-bold text-white mb-1.5">
                    <span>{AGENT_LABELS[id]}</span>
                    <span className="text-[9px] font-mono text-cat-subtext bg-cat-surface0 px-1.5 py-0.5 rounded">
                      {model}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-1 mb-2">
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
                    className="bg-cat-surface0 hover:bg-cat-surface1 text-cat-subtext py-1 px-2 rounded border border-cat-surface1 text-[10px] font-semibold transition-colors inline-flex items-center gap-1"
                  >
                    + Add Skill
                    {(skillSuggestionCounts[id] ?? 0) > 0 && (
                      <span className="text-[9px] bg-indigo-600/60 text-white px-1 py-0.5 rounded">
                        {skillSuggestionCounts[id]} suggested
                      </span>
                    )}
                  </button>
                </div>
              ))}
            </div>
          )}

          {tab === 'workflow' && (
            <WorkflowPanel
              settings={ws}
              changelog={state.briefChangelog ?? []}
              notifications={notifications}
              onSettingsChange={onWorkflowSettingsChange}
              ollamaUrl={ollamaUrl}
              indexProgress={indexProgress}
              onOpenMemoryTab={onOpenMemoryTab}
              onOpenCustomTools={onOpenCustomTools}
              discordBotStatus={state.discordBotStatus ?? null}
            />
          )}
        </div>
      </div>
    </SlideOver>
  )
}
