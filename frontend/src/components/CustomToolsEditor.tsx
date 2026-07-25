import { useCallback, useEffect, useState } from 'react'
import { fetchCustomTools, fetchToolsCatalog, saveCustomTools } from '../api/client'
import type { CustomToolDef, ToolsCatalogResponse } from '../types'

const AGENT_ROLES = ['Product Owner', 'Developer', 'Code Reviewer', 'QA Tester'] as const

export type CustomToolScope = 'project' | 'global'

interface CustomToolsEditorProps {
  /** Controlled scope from parent, or internal toggle when omitted. */
  scope?: CustomToolScope
  onScopeChange?: (scope: CustomToolScope) => void
  showScopeToggle?: boolean
  onSaved?: (tools: CustomToolDef[]) => void
}

export default function CustomToolsEditor({
  scope: controlledScope,
  onScopeChange,
  showScopeToggle = true,
  onSaved,
}: CustomToolsEditorProps) {
  const [internalScope, setInternalScope] = useState<CustomToolScope>('project')
  const scope = controlledScope ?? internalScope
  const setScope = (s: CustomToolScope) => {
    if (onScopeChange) onScopeChange(s)
    else setInternalScope(s)
  }

  const [tools, setTools] = useState<CustomToolDef[]>([])
  const [catalog, setCatalog] = useState<ToolsCatalogResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [list, cat] = await Promise.all([
        fetchCustomTools(scope),
        fetchToolsCatalog(),
      ])
      setTools(list.tools)
      setCatalog(cat)
      setDirty(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load custom tools')
    }
  }, [scope])

  useEffect(() => {
    void load()
  }, [load])

  const updateTools = (next: CustomToolDef[]) => {
    setTools(next)
    setDirty(true)
    setNotice(null)
  }

  const addQuerySqlPreset = () => {
    const preset = catalog?.presets?.query_sql
    if (!preset) return
    if (tools.some((t) => t.name === preset.name)) return
    updateTools([...tools, { ...preset, scope }])
  }

  const addBlankCustom = () => {
    updateTools([
      ...tools,
      {
        id: `custom_${tools.length + 1}`,
        name: `custom_tool_${tools.length + 1}`,
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
        scope,
      },
    ])
  }

  const patchCustom = (index: number, partial: Partial<CustomToolDef>) => {
    updateTools(tools.map((t, i) => (i === index ? { ...t, ...partial } : t)))
  }

  const removeCustom = (index: number) => {
    updateTools(tools.filter((_, i) => i !== index))
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const result = await saveCustomTools(scope, tools)
      setTools(result.tools)
      setDirty(false)
      setNotice(
        scope === 'global'
          ? 'Saved global tools (available in all projects).'
          : 'Saved project tools.',
      )
      onSaved?.(result.tools)
      const cat = await fetchToolsCatalog()
      setCatalog(cat)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-[11px] font-bold uppercase tracking-wide text-cat-overlay">
            Custom tools
          </h4>
          <p className="text-[10px] text-cat-overlay leading-relaxed mt-0.5">
            Shell, HTTP, or SQL tools the LLM can call. Global tools apply to every project;
            project tools override the same name.
          </p>
        </div>
        {showScopeToggle && (
          <div className="flex gap-1">
            <button
              type="button"
              onClick={() => setScope('project')}
              className={`text-[10px] px-2 py-1 rounded border ${
                scope === 'project'
                  ? 'border-indigo-400 text-indigo-200 bg-indigo-950/40'
                  : 'border-cat-surface1 text-cat-subtext'
              }`}
            >
              This project
            </button>
            <button
              type="button"
              onClick={() => setScope('global')}
              className={`text-[10px] px-2 py-1 rounded border ${
                scope === 'global'
                  ? 'border-violet-400 text-violet-200 bg-violet-950/40'
                  : 'border-cat-surface1 text-cat-subtext'
              }`}
            >
              Global (all projects)
            </button>
          </div>
        )}
      </div>

      {error && <p className="text-[11px] text-rose-400">{error}</p>}
      {notice && <p className="text-[11px] text-emerald-300">{notice}</p>}

      <div className="flex items-center justify-between gap-2">
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
        <button
          type="button"
          disabled={saving || !dirty}
          onClick={() => void handleSave()}
          className="text-[10px] px-2.5 py-1 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/40 disabled:opacity-40"
        >
          {saving ? 'Saving…' : dirty ? 'Save' : 'Saved'}
        </button>
      </div>

      {tools.length === 0 && (
        <p className="text-[10px] text-cat-overlay">
          No {scope} custom tools yet. Add <span className="font-mono">query_sql</span> or a
          shell/HTTP tool, then Save.
        </p>
      )}

      {tools.map((tool, index) => (
        <div
          key={tool.id || tool.name || index}
          className="space-y-1.5 border border-cat-surface1 rounded p-2 bg-cat-base/40"
        >
          <div className="flex gap-2 items-center">
            <span
              className={`text-[9px] uppercase px-1.5 py-0.5 rounded shrink-0 ${
                scope === 'global'
                  ? 'bg-violet-950/60 text-violet-300'
                  : 'bg-indigo-950/60 text-indigo-300'
              }`}
            >
              {scope}
            </span>
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
              value={JSON.stringify(
                tool.sql ?? {
                  connections: { local: 'sqlite:///./data/app.db' },
                  readOnly: true,
                  maxRows: 200,
                },
                null,
                2,
              )}
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
                    http: {
                      ...(tool.http || {}),
                      method: e.target.value,
                      url: tool.http?.url ?? '',
                    },
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
                    http: {
                      ...(tool.http || {}),
                      url: e.target.value,
                      method: tool.http?.method ?? 'POST',
                    },
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
