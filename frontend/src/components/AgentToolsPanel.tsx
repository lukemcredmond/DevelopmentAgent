import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchToolsCatalog } from '../api/client'
import type { ToolsCatalogResponse, WorkflowSettings } from '../types'

const AGENT_ROLES = ['Product Owner', 'Developer', 'Code Reviewer', 'QA Tester'] as const

const CORE_BUILTINS = [
  'read_file',
  'list_dir',
  'write_file',
  'apply_patch',
  'delete_file',
  'run_command',
  'run_test',
  'grep',
  'glob_file_search',
  'search_code',
  'update_board',
  'add_backlog_tasks',
  'add_subtasks',
  'git_status',
  'git_diff',
  'git_commit',
  'semantic_search',
  'graph_query',
  'web_search',
]

interface AgentToolsPanelProps {
  settings: WorkflowSettings
  onSettingsChange: (partial: Partial<WorkflowSettings>) => void
  onOpenCustomTools?: () => void
}

export default function AgentToolsPanel({
  settings,
  onSettingsChange,
  onOpenCustomTools,
}: AgentToolsPanelProps) {
  const [catalog, setCatalog] = useState<ToolsCatalogResponse | null>(null)
  const [activeRole, setActiveRole] = useState<string>('Developer')
  const [error, setError] = useState<string | null>(null)

  const loadCatalog = useCallback(() => {
    fetchToolsCatalog()
      .then(setCatalog)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load tool catalog'))
  }, [])

  useEffect(() => {
    loadCatalog()
  }, [loadCatalog, settings.agentTools, settings.customTools])

  const builtinNames = useMemo(() => {
    const fromApi = catalog?.builtins.map((b) => b.name) ?? []
    return Array.from(new Set([...CORE_BUILTINS, ...fromApi])).sort()
  }, [catalog])

  const customNames = useMemo(() => {
    const fromSettings = (settings.customTools ?? []).map((t) => t.name)
    const fromCatalog = (catalog?.customTools ?? []).map((t) => t.name)
    return Array.from(new Set([...fromSettings, ...fromCatalog])).sort()
  }, [settings.customTools, catalog])

  const agentTools = settings.agentTools ?? {}

  const effectiveForRole = (role: string): string[] => {
    const override = agentTools[role]
    if (Array.isArray(override) && override.length > 0) return override
    return catalog?.agents[role]?.tools ?? []
  }

  const usingOverride = (role: string) => {
    const override = agentTools[role]
    return Array.isArray(override) && override.length > 0
  }

  const toggleTool = (role: string, toolName: string) => {
    const current = new Set(effectiveForRole(role))
    if (current.has(toolName)) current.delete(toolName)
    else current.add(toolName)
    onSettingsChange({
      agentTools: {
        ...agentTools,
        [role]: Array.from(current).sort(),
      },
    })
  }

  const resetRoleToDefault = (role: string) => {
    const next = { ...agentTools }
    delete next[role]
    onSettingsChange({ agentTools: next })
  }

  return (
    <div className="space-y-3 border-t border-cat-surface1 pt-3 mt-2">
      <div>
        <h4 className="text-[11px] font-bold uppercase tracking-wide text-cat-overlay">
          Agent tools
        </h4>
        <p className="text-[10px] text-cat-overlay leading-relaxed mt-0.5">
          Choose which tools each agent may call. Leave a role on defaults, or tick tools to set an
          allowlist. Edit custom tool definitions in the Tools panel.
        </p>
      </div>

      {error && <p className="text-[11px] text-rose-400">{error}</p>}

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.agentToolsAllowWritesInRefinement ?? false}
          onChange={(e) =>
            onSettingsChange({ agentToolsAllowWritesInRefinement: e.target.checked })
          }
        />
        Allow write/run tools during refinement
      </label>

      <div className="flex flex-wrap gap-1">
        {AGENT_ROLES.map((role) => (
          <button
            key={role}
            type="button"
            onClick={() => setActiveRole(role)}
            className={`text-[10px] px-2 py-1 rounded border ${
              activeRole === role
                ? 'border-indigo-400 text-indigo-200 bg-indigo-950/40'
                : 'border-cat-surface1 text-cat-subtext'
            }`}
          >
            {role}
            {usingOverride(role) ? ' *' : ''}
          </button>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <span className="text-[10px] text-cat-overlay">
          {usingOverride(activeRole)
            ? 'Custom allowlist (saved)'
            : 'Using built-in defaults (click a tool to start an allowlist)'}
        </span>
        {usingOverride(activeRole) && (
          <button
            type="button"
            className="text-[10px] text-amber-300 hover:underline"
            onClick={() => resetRoleToDefault(activeRole)}
          >
            Reset to defaults
          </button>
        )}
      </div>

      <div className="max-h-40 overflow-y-auto grid grid-cols-2 gap-x-2 gap-y-0.5 bg-cat-base/50 rounded border border-cat-surface1 p-2">
        {[...builtinNames, ...customNames].map((name) => {
          const checked = effectiveForRole(activeRole).includes(name)
          const isCustom = customNames.includes(name)
          return (
            <label
              key={name}
              className="flex items-center gap-1.5 text-[10px] text-cat-subtext cursor-pointer font-mono"
            >
              <input
                type="checkbox"
                checked={checked}
                onChange={() => toggleTool(activeRole, name)}
              />
              {name}
              {isCustom && <span className="text-[8px] text-violet-400">c</span>}
            </label>
          )
        })}
      </div>

      <div className="rounded border border-cat-surface1 bg-cat-base/30 px-2 py-2 space-y-1">
        <p className="text-[10px] text-cat-overlay">
          Custom tools ({customNames.length} merged project + global) — create or edit under{' '}
          <span className="text-cat-subtext font-medium">Tools → Custom tools</span>.
        </p>
        {onOpenCustomTools && (
          <button
            type="button"
            onClick={onOpenCustomTools}
            className="text-[10px] text-indigo-300 hover:underline"
          >
            Open Tools → Custom tools
          </button>
        )}
      </div>
    </div>
  )
}
