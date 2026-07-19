import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchToolsCatalog } from '../api/client'
import type { CustomToolDef, ToolsCatalogResponse, WorkflowSettings } from '../types'

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
}

export default function AgentToolsPanel({ settings, onSettingsChange }: AgentToolsPanelProps) {
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

  const customTools = settings.customTools ?? []
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

  const updateCustom = (next: CustomToolDef[]) => {
    onSettingsChange({ customTools: next })
  }

  const addQuerySqlPreset = () => {
    const preset = catalog?.presets?.query_sql
    if (!preset) return
    if (customTools.some((t) => t.name === preset.name)) return
    updateCustom([...customTools, preset])
  }

  const addBlankCustom = () => {
    updateCustom([
      ...customTools,
      {
        id: `custom_${customTools.length + 1}`,
        name: `custom_tool_${customTools.length + 1}`,
        description: 'Describe what this tool does for the LLM.',
        parameters: {
          type: 'object',
          properties: {
            input: { type: 'string' },
          },
          required: ['input'],
        },
        agents: ['Developer'],
        executor: 'shell',
        shell: { command: 'echo {input}' },
      },
    ])
  }

  const patchCustom = (index: number, partial: Partial<CustomToolDef>) => {
    const next = customTools.map((t, i) => (i === index ? { ...t, ...partial } : t))
    updateCustom(next)
  }

  const removeCustom = (index: number) => {
    updateCustom(customTools.filter((_, i) => i !== index))
  }

  return (
    <div className="space-y-3 border-t border-cat-surface1 pt-3 mt-2">
      <div>
        <h4 className="text-[11px] font-bold uppercase tracking-wide text-cat-overlay">
          Agent tools
        </h4>
        <p className="text-[10px] text-cat-overlay leading-relaxed mt-0.5">
          Choose which tools each agent may call. Leave a role on defaults, or tick tools to set an
          allowlist. Custom tools are also sent to the LLM as callable functions.
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
        {[...builtinNames, ...customTools.map((t) => t.name)].map((name) => {
          const checked = effectiveForRole(activeRole).includes(name)
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
            </label>
          )
        })}
      </div>

      <div className="flex items-center justify-between pt-1">
        <h4 className="text-[11px] font-bold uppercase tracking-wide text-cat-overlay">
          Custom tools
        </h4>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={addQuerySqlPreset}
            className="text-[10px] text-indigo-300 hover:underline"
          >
            + query_sql template
          </button>
          <button
            type="button"
            onClick={addBlankCustom}
            className="text-[10px] text-indigo-300 hover:underline"
          >
            + blank
          </button>
        </div>
      </div>

      {customTools.length === 0 && (
        <p className="text-[10px] text-cat-overlay">
          No custom tools yet. Add <span className="font-mono">query_sql</span> or a shell/HTTP tool
          the LLM can call.
        </p>
      )}

      {customTools.map((tool, index) => (
        <div
          key={tool.id || tool.name || index}
          className="space-y-1.5 border border-cat-surface1 rounded p-2 bg-cat-base/40"
        >
          <div className="flex gap-2">
            <input
              className="flex-1 bg-cat-base border border-cat-surface1 rounded px-2 py-0.5 text-[11px] font-mono text-white"
              value={tool.name}
              onChange={(e) => patchCustom(index, { name: e.target.value, id: e.target.value })}
              placeholder="tool_name"
            />
            <select
              className="bg-cat-base border border-cat-surface1 rounded px-1 py-0.5 text-[10px] text-white"
              value={tool.executor}
              onChange={(e) =>
                patchCustom(index, {
                  executor: e.target.value as CustomToolDef['executor'],
                })
              }
            >
              <option value="sql">sql</option>
              <option value="shell">shell</option>
              <option value="http">http</option>
            </select>
            <button
              type="button"
              className="text-[10px] text-rose-400 hover:underline"
              onClick={() => removeCustom(index)}
            >
              Remove
            </button>
          </div>
          <input
            className="w-full bg-cat-base border border-cat-surface1 rounded px-2 py-0.5 text-[11px] text-white"
            value={tool.description}
            onChange={(e) => patchCustom(index, { description: e.target.value })}
            placeholder="Description for the LLM"
          />
          <div className="flex flex-wrap gap-2">
            {AGENT_ROLES.map((role) => {
              const on = (tool.agents || []).includes(role)
              return (
                <label key={role} className="flex items-center gap-1 text-[10px] text-cat-subtext">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => {
                      const agents = new Set(tool.agents || [])
                      if (on) agents.delete(role)
                      else agents.add(role)
                      patchCustom(index, { agents: Array.from(agents) })
                    }}
                  />
                  {role.split(' ')[0]}
                </label>
              )
            })}
          </div>
          {tool.executor === 'sql' && (
            <textarea
              className="w-full h-16 bg-cat-base border border-cat-surface1 rounded px-2 py-1 text-[10px] font-mono text-white"
              value={JSON.stringify(tool.sql ?? { connections: { local: 'sqlite:///./data/app.db' }, readOnly: true, maxRows: 200 }, null, 2)}
              onChange={(e) => {
                try {
                  patchCustom(index, { sql: JSON.parse(e.target.value) })
                } catch {
                  /* ignore while typing */
                }
              }}
              spellCheck={false}
            />
          )}
          {tool.executor === 'shell' && (
            <input
              className="w-full bg-cat-base border border-cat-surface1 rounded px-2 py-0.5 text-[10px] font-mono text-white"
              value={tool.shell?.command ?? ''}
              onChange={(e) => patchCustom(index, { shell: { command: e.target.value } })}
              placeholder="python scripts/run.py --db {db_name} --query {query}"
            />
          )}
          {tool.executor === 'http' && (
            <div className="flex gap-1">
              <select
                className="bg-cat-base border border-cat-surface1 rounded px-1 text-[10px] text-white"
                value={tool.http?.method ?? 'POST'}
                onChange={(e) =>
                  patchCustom(index, {
                    http: { ...(tool.http || {}), method: e.target.value, url: tool.http?.url ?? '' },
                  })
                }
              >
                <option value="POST">POST</option>
                <option value="GET">GET</option>
              </select>
              <input
                className="flex-1 bg-cat-base border border-cat-surface1 rounded px-2 py-0.5 text-[10px] font-mono text-white"
                value={tool.http?.url ?? ''}
                onChange={(e) =>
                  patchCustom(index, {
                    http: { ...(tool.http || {}), url: e.target.value, method: tool.http?.method ?? 'POST' },
                  })
                }
                placeholder="http://localhost:9000/query"
              />
            </div>
          )}
          <details className="text-[10px] text-cat-overlay">
            <summary className="cursor-pointer">Parameters JSON schema</summary>
            <textarea
              className="w-full h-20 mt-1 bg-cat-base border border-cat-surface1 rounded px-2 py-1 font-mono text-white"
              value={JSON.stringify(tool.parameters ?? {}, null, 2)}
              onChange={(e) => {
                try {
                  patchCustom(index, { parameters: JSON.parse(e.target.value) })
                } catch {
                  /* ignore while typing */
                }
              }}
              spellCheck={false}
            />
          </details>
        </div>
      ))}
    </div>
  )
}
