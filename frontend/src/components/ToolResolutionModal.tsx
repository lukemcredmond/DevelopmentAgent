import { useEffect, useState } from 'react'
import {
  dismissAllPendingTools,
  dismissPendingTool,
  resolvePendingTool,
} from '../api/client'
import type { PendingToolRequest } from '../types'
import SlideOver from './SlideOver'

const TARGET_TOOLS = [
  'run_command',
  'read_file',
  'write_file',
  'apply_patch',
  'run_test',
  'update_board',
] as const

/** Real app tools — if the alias matches, this is likely role/mode gating, not an invent. */
const CANONICAL_TOOL_NAMES = new Set([
  'write_file',
  'apply_patch',
  'delete_file',
  'read_file',
  'list_dir',
  'run_test',
  'run_command',
  'update_board',
  'add_backlog_tasks',
  'add_subtasks',
  'grep',
  'glob_file_search',
  'search_code',
  'semantic_search',
  'graph_query',
  'web_search',
  'git_status',
  'git_diff',
  'git_commit',
  'git_init',
])

interface ToolResolutionModalProps {
  pending: PendingToolRequest | null
  onClose: () => void
  onResolved: () => void | Promise<void>
}

export default function ToolResolutionModal({
  pending,
  onClose,
  onResolved,
}: ToolResolutionModalProps) {
  const [targetTool, setTargetTool] = useState('run_command')
  const [command, setCommand] = useState('')
  const [path, setPath] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!pending) return
    setError(null)
    const aliasLower = pending.alias.toLowerCase()
    const matchedTarget = TARGET_TOOLS.find((t) => t === pending.alias || t === aliasLower)
    setTargetTool(matchedTarget ?? 'run_command')
    if (aliasLower.includes('flutter') || aliasLower.includes('dart')) {
      setCommand('flutter analyze')
    } else if (typeof pending.arguments.command === 'string') {
      setCommand(pending.arguments.command)
    } else {
      const first = Object.values(pending.arguments).find((v) => typeof v === 'string')
      setCommand(typeof first === 'string' ? first : pending.alias.replace(/_/g, ' '))
    }
    if (typeof pending.arguments.path === 'string') {
      setPath(pending.arguments.path)
    } else {
      setPath('')
    }
  }, [pending])

  if (!pending) return null

  const isCanonicalAlias = CANONICAL_TOOL_NAMES.has(pending.alias)

  const buildDefaultArgs = (): Record<string, string> => {
    if (targetTool === 'run_command') {
      return { command: command.trim() }
    }
    if (
      targetTool === 'read_file' ||
      targetTool === 'write_file' ||
      targetTool === 'apply_patch' ||
      targetTool === 'run_test'
    ) {
      return { [targetTool === 'run_test' ? 'test_script_path' : 'path']: path.trim() }
    }
    if (targetTool === 'update_board') {
      return {
        task_id: String(pending.taskId ?? pending.arguments.task_id ?? ''),
        target_lane: String(pending.arguments.target_lane ?? 'In Progress'),
      }
    }
    return {}
  }

  const finish = async () => {
    await onResolved()
    onClose()
  }

  const handleSave = async () => {
    setLoading(true)
    setError(null)
    try {
      const defaultArgs = buildDefaultArgs()
      if (targetTool === 'run_command' && !defaultArgs.command) {
        setError('Enter a shell command.')
        return
      }
      await resolvePendingTool(pending.id, {
        targetTool,
        defaultArgs,
        saveMapping: true,
      })
      await finish()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save mapping')
    } finally {
      setLoading(false)
    }
  }

  const handleDismiss = async () => {
    setLoading(true)
    setError(null)
    try {
      await dismissPendingTool(pending.id)
      await finish()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to dismiss')
    } finally {
      setLoading(false)
    }
  }

  const handleStopSprint = async () => {
    setLoading(true)
    setError(null)
    try {
      await dismissAllPendingTools({ cancelSprint: true })
      await finish()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to stop sprint')
    } finally {
      setLoading(false)
    }
  }

  return (
    <SlideOver
      open
      onClose={() => void handleDismiss()}
      side="right"
      title={<span className="text-amber-200">Unknown Tool Request</span>}
      widthClass="w-full max-w-lg"
      zIndexClass="z-[60]"
      footer={
        <div className="flex flex-wrap gap-2 justify-between items-center">
          <button
            type="button"
            disabled={loading}
            onClick={() => void handleStopSprint()}
            className="text-xs text-rose-300 hover:text-rose-200 border border-rose-500/40 px-3 py-1.5 rounded-lg disabled:opacity-50"
          >
            Stop sprint
          </button>
          <div className="flex gap-2">
            <button
              type="button"
              disabled={loading}
              onClick={() => void handleDismiss()}
              className="text-xs text-cat-subtext hover:text-white px-3 py-1.5 disabled:opacity-50"
            >
              Dismiss
            </button>
            <button
              type="button"
              disabled={loading}
              onClick={() => void handleSave()}
              className="bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white text-xs px-4 py-1.5 rounded-lg"
            >
              Save mapping
            </button>
          </div>
        </div>
      }
    >
      <div className="p-4 space-y-4">
        <p className="text-xs text-cat-subtext">
          The agent called <span className="font-mono text-amber-300">{pending.alias}</span> which
          is not registered. Map it to a real action (saved for this project), dismiss it, or stop
          the sprint.
        </p>
        {pending.agentRole && (
          <p className="text-[11px] text-cat-overlay">
            Agent: <span className="font-mono text-cat-subtext">{pending.agentRole}</span>
          </p>
        )}
        {isCanonicalAlias && (
          <p className="text-[11px] text-amber-200/90 bg-amber-950/40 border border-amber-500/30 rounded px-2 py-1.5">
            This is a real app tool but unavailable for this agent or mode (e.g. refinement strips
            write tools). Prefer Dismiss — mapping will not enable it for the wrong role. Wait for
            implementation, or use an agent that has this tool.
          </p>
        )}
        <pre className="text-[10px] font-mono bg-cat-base border border-cat-surface1 rounded p-2 max-h-24 overflow-auto text-cat-subtext">
          {JSON.stringify(pending.arguments, null, 2)}
        </pre>
        <div>
          <label className="text-[10px] uppercase text-cat-overlay font-bold">Map to tool</label>
          <select
            value={targetTool}
            onChange={(e) => setTargetTool(e.target.value)}
            className="w-full mt-1 bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 text-xs text-white"
          >
            {TARGET_TOOLS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        {targetTool === 'run_command' && (
          <div>
            <label className="text-[10px] uppercase text-cat-overlay font-bold">Command</label>
            <input
              type="text"
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              placeholder="flutter analyze"
              className="w-full mt-1 bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 text-xs font-mono text-white"
            />
          </div>
        )}
        {(targetTool === 'read_file' ||
          targetTool === 'write_file' ||
          targetTool === 'apply_patch' ||
          targetTool === 'run_test') && (
          <div>
            <label className="text-[10px] uppercase text-cat-overlay font-bold">Path</label>
            <input
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="lib/main.dart"
              className="w-full mt-1 bg-cat-base border border-cat-surface1 rounded px-2 py-1.5 text-xs font-mono text-white"
            />
          </div>
        )}
        {error && <p className="text-xs text-rose-400">{error}</p>}
      </div>
    </SlideOver>
  )
}
