import { useCallback, useEffect, useState } from 'react'
import { fetchAgentPromptDefaults, restoreAgentPrompts } from '../api/client'
import type { AgentPromptDefaults, AgentPromptRoleConfig, WorkflowSettings } from '../types'

const ROLES = [
  'Product Owner',
  'Developer',
  'Code Reviewer',
  'QA Tester',
] as const

type AgentRole = (typeof ROLES)[number]

interface AgentPromptsPanelProps {
  settings: WorkflowSettings
  onSettingsChange: (partial: Partial<WorkflowSettings>) => void
}

function roleConfig(
  settings: WorkflowSettings,
  role: AgentRole,
): AgentPromptRoleConfig {
  return settings.agentPrompts?.[role] ?? { system: null, stepInstructions: null }
}

function isCustom(cfg: AgentPromptRoleConfig, field: 'system' | 'stepInstructions'): boolean {
  const v = cfg[field]
  return typeof v === 'string' && v.trim().length > 0
}

export default function AgentPromptsPanel({
  settings,
  onSettingsChange,
}: AgentPromptsPanelProps) {
  const [defaults, setDefaults] = useState<AgentPromptDefaults | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [busyRole, setBusyRole] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void fetchAgentPromptDefaults()
      .then((data) => {
        if (!cancelled) setDefaults(data.agentPromptDefaults)
      })
      .catch(() => {
        if (!cancelled) setLoadError('Could not load default prompts.')
      })
    return () => {
      cancelled = true
    }
  }, [])

  const displayValue = useCallback(
    (role: AgentRole, field: 'system' | 'stepInstructions'): string => {
      const cfg = roleConfig(settings, role)
      const override = cfg[field]
      if (typeof override === 'string' && override.trim()) return override
      return defaults?.[role]?.[field] ?? ''
    },
    [defaults, settings],
  )

  const patchRole = (role: AgentRole, patch: Partial<AgentPromptRoleConfig>) => {
    const next = { ...(settings.agentPrompts ?? {}) }
    next[role] = { ...roleConfig(settings, role), ...patch }
    onSettingsChange({ agentPrompts: next })
  }

  const restore = async (role: AgentRole | null) => {
    setBusyRole(role ?? '__all__')
    try {
      const state = await restoreAgentPrompts(role ?? undefined)
      if (state.workflowSettings) {
        onSettingsChange(state.workflowSettings)
      }
    } finally {
      setBusyRole(null)
    }
  }

  return (
    <div className="space-y-3 border-t border-cat-surface1 pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-[10px] font-bold uppercase tracking-wider text-indigo-200">
          Agent prompts
        </p>
        <button
          type="button"
          disabled={busyRole === '__all__'}
          onClick={() => void restore(null)}
          className="text-[10px] px-2 py-0.5 rounded border border-cat-surface1 text-cat-subtext hover:border-indigo-500/40"
        >
          Restore all defaults
        </button>
      </div>
      <p className="text-[10px] text-cat-overlay leading-relaxed">
        Replace system and sprint-step instructions per role. Edits save with other workflow settings.
        Step templates support placeholders such as{' '}
        <span className="font-mono">{`{lint_hint}`}</span>,{' '}
        <span className="font-mono">{`{target_lane}`}</span>,{' '}
        <span className="font-mono">{`{ac_block}`}</span>.
      </p>
      {loadError && <p className="text-[10px] text-rose-400">{loadError}</p>}

      {ROLES.map((role) => {
        const cfg = roleConfig(settings, role)
        const customSystem = isCustom(cfg, 'system')
        const customStep = isCustom(cfg, 'stepInstructions')
        return (
          <details key={role} className="rounded border border-cat-surface1/80 bg-cat-base/40 p-2">
            <summary className="cursor-pointer text-[11px] text-cat-subtext flex flex-wrap gap-2 items-center">
              <span className="font-semibold text-white">{role}</span>
              {customSystem && (
                <span className="text-[9px] px-1 rounded border border-amber-500/40 text-amber-200">
                  custom system
                </span>
              )}
              {customStep && (
                <span className="text-[9px] px-1 rounded border border-amber-500/40 text-amber-200">
                  custom step
                </span>
              )}
              {!customSystem && !customStep && (
                <span className="text-[9px] text-cat-overlay">using defaults</span>
              )}
            </summary>
            <div className="mt-2 space-y-2">
              <label className="block text-[10px] text-cat-overlay">
                System prompt
                <textarea
                  rows={5}
                  value={displayValue(role, 'system')}
                  onChange={(e) => patchRole(role, { system: e.target.value })}
                  className="mt-1 w-full bg-cat-base border border-cat-surface1 rounded p-2 text-[10px] text-white font-mono"
                />
              </label>
              {(role === 'Developer' || role === 'Code Reviewer' || role === 'QA Tester') && (
                <label className="block text-[10px] text-cat-overlay">
                  Sprint step instructions
                  <textarea
                    rows={6}
                    value={displayValue(role, 'stepInstructions')}
                    onChange={(e) => patchRole(role, { stepInstructions: e.target.value })}
                    className="mt-1 w-full bg-cat-base border border-cat-surface1 rounded p-2 text-[10px] text-white font-mono"
                  />
                </label>
              )}
              <button
                type="button"
                disabled={busyRole === role}
                onClick={() => void restore(role)}
                className="text-[10px] px-2 py-0.5 rounded border border-cat-surface1 text-cat-subtext hover:border-indigo-500/40"
              >
                Restore {role} defaults
              </button>
            </div>
          </details>
        )
      })}
    </div>
  )
}
