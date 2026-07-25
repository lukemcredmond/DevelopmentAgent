import { useCallback, useEffect, useMemo, useState } from 'react'
import { fetchToolRegistry, probeAllTools, probeAllToolsLlm, probeTool, probeToolLlm } from '../api/client'
import {
  AUTO_LLM_ON_PICK_KEY,
  isAutoLlmToolHealthOnPick,
  setAutoLlmToolHealthOnPick,
  toolHealthStorageKey,
} from '../lib/toolHealthLlm'
import type { AgentId, ToolDefinition, ToolProbeResult, ToolProbeStatus } from '../types'

const AGENT_OPTIONS: { id: AgentId; label: string }[] = [
  { id: 'po', label: 'Product Owner' },
  { id: 'dev', label: 'Developer' },
  { id: 'cr', label: 'Code Reviewer' },
  { id: 'qa', label: 'QA Tester' },
]

function statusIcon(status: ToolProbeStatus | 'untested' | 'running'): {
  label: string
  className: string
  title: string
} {
  switch (status) {
    case 'pass':
      return { label: '●', className: 'text-emerald-400', title: 'Passed' }
    case 'fail':
      return { label: '●', className: 'text-rose-400', title: 'Failed' }
    case 'skip':
      return { label: '●', className: 'text-amber-400', title: 'Skipped' }
    case 'running':
      return { label: '…', className: 'text-indigo-300', title: 'Running' }
    default:
      return { label: '○', className: 'text-cat-overlay', title: 'Untested' }
  }
}

function kindBadge(tool: ToolDefinition): string {
  if (tool.kind === 'custom') return tool.scope === 'global' ? 'custom·global' : 'custom'
  if (tool.name.startsWith('mcp_')) return 'mcp'
  return tool.kind || 'builtin'
}

interface ToolHealthPanelProps {
  projectId?: string
}

export default function ToolHealthPanel({ projectId = '' }: ToolHealthPanelProps) {
  const [agentId, setAgentId] = useState<AgentId>('dev')
  const [tools, setTools] = useState<ToolDefinition[]>([])
  const [results, setResults] = useState<Record<string, ToolProbeResult>>({})
  const [running, setRunning] = useState<Record<string, boolean>>({})
  const [batchRunning, setBatchRunning] = useState(false)
  const [batchMode, setBatchMode] = useState<'smoke' | 'llm' | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [registryError, setRegistryError] = useState<string | null>(null)
  const [autoOnPick, setAutoOnPick] = useState(() => isAutoLlmToolHealthOnPick())

  const storageKey = toolHealthStorageKey(projectId, agentId)

  const loadRegistry = useCallback(async () => {
    setRegistryError(null)
    try {
      const data = await fetchToolRegistry(agentId)
      setTools(data.tools ?? [])
    } catch (e) {
      setTools([])
      setRegistryError(e instanceof Error ? e.message : 'Failed to load registry')
    }
  }, [agentId])

  useEffect(() => {
    void loadRegistry()
  }, [loadRegistry])

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(storageKey)
      if (raw) {
        const parsed = JSON.parse(raw) as Record<string, ToolProbeResult>
        setResults(parsed)
      } else {
        setResults({})
      }
    } catch {
      setResults({})
    }
    setExpanded(null)
  }, [storageKey])

  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(results))
    } catch {
      /* ignore quota */
    }
  }, [results, storageKey])

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === AUTO_LLM_ON_PICK_KEY) {
        setAutoOnPick(isAutoLlmToolHealthOnPick())
      }
      if (e.key === storageKey && e.newValue) {
        try {
          setResults(JSON.parse(e.newValue) as Record<string, ToolProbeResult>)
        } catch {
          /* ignore */
        }
      }
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [storageKey])

  const summary = useMemo(() => {
    let pass = 0
    let fail = 0
    let skip = 0
    let untested = 0
    for (const t of tools) {
      const r = results[t.name]
      if (!r) untested += 1
      else if (r.status === 'pass') pass += 1
      else if (r.status === 'fail') fail += 1
      else if (r.status === 'skip') skip += 1
    }
    return { pass, fail, skip, untested, total: tools.length }
  }, [tools, results])

  const runOne = async (toolName: string) => {
    setError(null)
    setRunning((m) => ({ ...m, [toolName]: true }))
    try {
      const { result } = await probeTool({ agent: agentId, toolName })
      setResults((prev) => ({ ...prev, [toolName]: result }))
      setExpanded(toolName)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Probe failed')
    } finally {
      setRunning((m) => {
        const next = { ...m }
        delete next[toolName]
        return next
      })
    }
  }

  const runOneLlm = async (toolName: string) => {
    setError(null)
    setRunning((m) => ({ ...m, [toolName]: true }))
    try {
      const { result } = await probeToolLlm({ agent: agentId, toolName })
      setResults((prev) => ({ ...prev, [toolName]: result }))
      setExpanded(toolName)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'LLM probe failed')
    } finally {
      setRunning((m) => {
        const next = { ...m }
        delete next[toolName]
        return next
      })
    }
  }

  const runAllSafe = async () => {
    setError(null)
    setBatchRunning(true)
    setBatchMode('smoke')
    try {
      const data = await probeAllTools({ agent: agentId, includeDestructive: false })
      const next: Record<string, ToolProbeResult> = { ...results }
      for (const r of data.results) {
        next[r.toolName] = r
      }
      setResults(next)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Probe-all failed')
    } finally {
      setBatchRunning(false)
      setBatchMode(null)
    }
  }

  const runAllLlm = async () => {
    setError(null)
    setBatchRunning(true)
    setBatchMode('llm')
    try {
      const data = await probeAllToolsLlm({ agent: agentId, includeDestructive: false })
      const next: Record<string, ToolProbeResult> = { ...results }
      for (const r of data.results) {
        next[r.toolName] = r
      }
      setResults(next)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'LLM probe-all failed')
    } finally {
      setBatchRunning(false)
      setBatchMode(null)
    }
  }

  const onAutoToggle = (on: boolean) => {
    setAutoOnPick(on)
    setAutoLlmToolHealthOnPick(on)
  }

  return (
    <div className="space-y-3 max-w-4xl">
      <div>
        <h4 className="text-[11px] font-bold uppercase tracking-wide text-cat-overlay">
          Tool Health
        </h4>
        <p className="text-[10px] text-cat-overlay leading-relaxed mt-0.5">
          Smoke-test each tool (no LLM) or ask the agent&apos;s model to call it once. Green = pass,
          red = fail, amber = skipped, gray = untested. Expand for output and model hints.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase text-cat-overlay">Agent</span>
          <select
            value={agentId}
            onChange={(e) => setAgentId(e.target.value as AgentId)}
            className="bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 text-cat-text text-[11px]"
          >
            {AGENT_OPTIONS.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={batchRunning || tools.length === 0}
          onClick={() => void runAllSafe()}
          className="px-3 py-1.5 rounded bg-indigo-600/50 hover:bg-indigo-600/80 border border-indigo-500/40 text-indigo-100 text-[11px] font-semibold disabled:opacity-40"
        >
          {batchRunning && batchMode === 'smoke' ? 'Testing…' : 'Test all safe'}
        </button>
        <button
          type="button"
          disabled={batchRunning || tools.length === 0}
          onClick={() => void runAllLlm()}
          className="px-3 py-1.5 rounded bg-violet-600/40 hover:bg-violet-600/70 border border-violet-500/40 text-violet-100 text-[11px] font-semibold disabled:opacity-40"
        >
          {batchRunning && batchMode === 'llm' ? 'Asking model…' : 'Ask model (all safe)'}
        </button>
        <button
          type="button"
          onClick={() => void loadRegistry()}
          className="text-[10px] text-indigo-300 hover:underline"
        >
          Refresh registry
        </button>
        <span className="text-[10px] text-cat-overlay tabular-nums ml-auto">
          {summary.pass} pass · {summary.fail} fail · {summary.skip} skip · {summary.untested}{' '}
          untested / {summary.total}
        </span>
      </div>

      <label className="flex items-center gap-2 text-[10px] text-cat-subtext cursor-pointer select-none">
        <input
          type="checkbox"
          checked={autoOnPick}
          onChange={(e) => onAutoToggle(e.target.checked)}
          className="rounded border-cat-surface1"
        />
        Auto LLM-test on model pick (Settings → Models)
      </label>

      {error && <p className="text-[11px] text-rose-400">{error}</p>}
      {registryError && <p className="text-[11px] text-amber-300">{registryError}</p>}

      <div className="border border-cat-surface1 rounded-lg overflow-hidden divide-y divide-cat-surface1">
        {tools.length === 0 && (
          <p className="p-3 text-[11px] text-cat-overlay italic">No tools registered for this agent.</p>
        )}
        {tools.map((tool) => {
          const result = results[tool.name]
          const isRunning = Boolean(running[tool.name]) || batchRunning
          const status: ToolProbeStatus | 'untested' | 'running' = isRunning
            ? running[tool.name]
              ? 'running'
              : result?.status || 'untested'
            : result?.status || 'untested'
          const icon = statusIcon(isRunning && running[tool.name] ? 'running' : status)
          const open = expanded === tool.name
          return (
            <div key={tool.name} className="bg-cat-base/30">
              <div className="flex items-center gap-2 px-3 py-2">
                <button
                  type="button"
                  className={`text-base leading-none w-5 ${icon.className}`}
                  title={icon.title}
                  onClick={() => setExpanded(open ? null : tool.name)}
                >
                  {icon.label}
                </button>
                <button
                  type="button"
                  className="flex-1 text-left min-w-0"
                  onClick={() => setExpanded(open ? null : tool.name)}
                >
                  <span className="font-mono text-[11px] text-white">{tool.name}</span>
                  <span className="ml-2 text-[9px] uppercase text-cat-overlay">
                    {kindBadge(tool)}
                  </span>
                </button>
                <button
                  type="button"
                  disabled={isRunning}
                  onClick={() => void runOne(tool.name)}
                  className="shrink-0 text-[10px] px-2.5 py-1 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/40 disabled:opacity-40"
                >
                  {running[tool.name] ? '…' : 'Test'}
                </button>
                <button
                  type="button"
                  disabled={isRunning}
                  onClick={() => void runOneLlm(tool.name)}
                  className="shrink-0 text-[10px] px-2.5 py-1 rounded border border-violet-500/40 text-violet-200 hover:bg-violet-950/40 disabled:opacity-40"
                  title="Ask the agent's model to call this tool"
                >
                  {running[tool.name] ? '…' : 'Ask model'}
                </button>
                <button
                  type="button"
                  className="text-cat-overlay text-[10px] w-5"
                  onClick={() => setExpanded(open ? null : tool.name)}
                  aria-label="Toggle details"
                >
                  {open ? '▾' : '▸'}
                </button>
              </div>
              {open && (
                <div className="px-3 pb-3 space-y-2 border-t border-cat-surface1/60 bg-cat-mantle/40">
                  {result ? (
                    <>
                      <p className="text-[10px] text-cat-overlay pt-2">
                        Status:{' '}
                        <span
                          className={
                            result.status === 'pass'
                              ? 'text-emerald-300'
                              : result.status === 'fail'
                                ? 'text-rose-300'
                                : 'text-amber-300'
                          }
                        >
                          {result.status}
                        </span>
                        {result.mode ? ` · ${result.mode}` : ''}
                        {result.model ? ` · model ${result.model}` : ''}
                        {result.durationMs != null && result.durationMs > 0
                          ? ` · ${result.durationMs}ms`
                          : ''}
                        {result.skipReason ? ` · ${result.skipReason}` : ''}
                      </p>
                      {result.probeArgs && Object.keys(result.probeArgs).length > 0 && (
                        <pre className="text-[10px] font-mono text-cat-subtext bg-cat-base/50 rounded p-2 overflow-x-auto">
                          {JSON.stringify(result.probeArgs, null, 2)}
                        </pre>
                      )}
                      {result.llmContent ? (
                        <pre className="text-[10px] font-mono text-cat-overlay whitespace-pre-wrap max-h-24 overflow-y-auto bg-cat-base/50 rounded p-2">
                          LLM text: {result.llmContent}
                        </pre>
                      ) : null}
                      <pre className="text-[10px] font-mono text-cat-subtext whitespace-pre-wrap max-h-40 overflow-y-auto bg-cat-base/50 rounded p-2">
                        {result.output || '(no output)'}
                      </pre>
                      {(result.hints?.length ?? 0) > 0 && (
                        <div>
                          <p className="text-[10px] font-bold uppercase text-violet-300 mb-1">
                            Hints for the model
                          </p>
                          <ul className="list-disc pl-4 space-y-0.5 text-[10px] text-cat-subtext">
                            {result.hints!.map((h, i) => (
                              <li key={i}>{h}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </>
                  ) : (
                    <p className="text-[10px] text-cat-overlay italic py-2">
                      Not tested yet. Click Test (smoke) or Ask model (LLM).
                    </p>
                  )}
                  {tool.description && (
                    <p className="text-[10px] text-cat-overlay">{tool.description}</p>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
