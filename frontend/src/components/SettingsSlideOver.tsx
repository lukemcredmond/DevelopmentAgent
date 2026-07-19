import { useState } from 'react'
import type { AgentId, AppState, ConfigPayload, WorkflowSettings } from '../types'
import { AGENT_LABELS, DEFAULT_WORKFLOW_SETTINGS } from '../types'
import GpuModelRecommendations from './GpuModelRecommendations'
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
  onOllamaUrlChange: (v: string) => void
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
  onOpenSkillModal: (agent: AgentId) => void
  onRemoveSkill: (agent: AgentId, skill: string) => void
  onWorkflowSettingsChange: (partial: Partial<WorkflowSettings>) => void
  onExportProject: () => void
  onImportProject: (file: File) => void
  onDeleteProject: () => void
  onOpenMemoryTab?: () => void
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
  onOllamaUrlChange,
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
  onOpenSkillModal,
  onRemoveSkill,
  onWorkflowSettingsChange,
  onExportProject,
  onImportProject,
  onDeleteProject,
  onOpenMemoryTab,
  indexProgress = null,
  skillSuggestionCounts = { po: 0, dev: 0, cr: 0, qa: 0 },
  initialTab = 'project',
}: SettingsSlideOverProps) {
  const [tab, setTab] = useState<SettingsTab>(initialTab)

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
                  <span className="text-[10px] text-cat-subtext block mb-0.5">PROJECT NAME</span>
                  <input
                    type="text"
                    value={projectName}
                    onChange={(e) => onProjectNameChange(e.target.value)}
                    className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-medium focus:outline-none"
                  />
                </label>
                <label className="block text-xs">
                  <span className="text-[10px] text-cat-subtext block mb-0.5">WORKSPACE DIR</span>
                  <input
                    type="text"
                    value={workspaceDir}
                    onChange={(e) => onWorkspaceDirChange(e.target.value)}
                    className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono focus:outline-none"
                  />
                </label>
                <label className="block text-xs">
                  <span className="text-[10px] text-cat-subtext block mb-0.5">GLOBAL SKILLS DIR</span>
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
                    })
                  }
                  className="w-full bg-indigo-600/40 hover:bg-indigo-600/80 border border-indigo-500/30 text-white font-semibold py-2 rounded text-[11px] transition-colors mt-2"
                >
                  Save Custom Configurations
                </button>
              </div>
            </div>
          )}

          {tab === 'models' && (
            <div className="space-y-3 text-xs">
              <label className="block">
                <span className="text-[10px] text-cat-subtext block mb-0.5">OLLAMA URL</span>
                <input
                  type="text"
                  value={ollamaUrl}
                  onChange={(e) => onOllamaUrlChange(e.target.value)}
                  className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono focus:outline-none"
                />
              </label>
              <div className="space-y-2">
                {[
                  { label: 'PO MODEL', value: poModel, onChange: onPoModelChange },
                  { label: 'DEV MODEL', value: devModel, onChange: onDevModelChange },
                  { label: 'CR MODEL', value: crModel, onChange: onCrModelChange },
                  { label: 'QA MODEL', value: qaModel, onChange: onQaModelChange },
                ].map(({ label, value, onChange }) => (
                  <div key={label} className="flex items-center justify-between gap-2">
                    <span className="text-[9px] text-cat-subtext font-bold shrink-0">{label}</span>
                    <input
                      type="text"
                      value={value}
                      onChange={(e) => onChange(e.target.value)}
                      className="bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-right flex-1 focus:outline-none"
                    />
                  </div>
                ))}
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
              <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
                Agent Team & Skills
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
            />
          )}
        </div>
      </div>
    </SlideOver>
  )
}
