import { useEffect, useState } from 'react'
import { resolvePendingTool } from '../api/client'
import type { PendingToolRequest } from '../types'

const TARGET_TOOLS = [
  'run_command',
  'read_file',
  'write_file',
  'run_test',
  'update_board',
] as const

interface ToolResolutionModalProps {
  pending: PendingToolRequest | null
  onClose: () => void
  onResolved: () => void
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
    setTargetTool('run_command')
    const aliasLower = pending.alias.toLowerCase()
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

  const buildDefaultArgs = (): Record<string, string> => {
    if (targetTool === 'run_command') {
      return { command: command.trim() }
    }
    if (targetTool === 'read_file' || targetTool === 'write_file' || targetTool === 'run_test') {
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
      onResolved()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save mapping')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/75 flex items-center justify-center p-4 z-[60]">
      <div className="bg-cat-surface0 rounded-2xl max-w-lg w-full p-6 border border-amber-500/40 shadow-2xl space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-bold text-amber-200">Unknown Tool Request</h3>
          <button type="button" onClick={onClose} className="text-cat-subtext hover:text-white">
            <i className="fa-solid fa-xmark" />
          </button>
        </div>
        <p className="text-xs text-cat-subtext">
          The agent called <span className="font-mono text-amber-300">{pending.alias}</span> which
          is not registered. Map it to a real action (saved for this project).
        </p>
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
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="text-xs text-cat-subtext hover:text-white px-3 py-1.5"
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
    </div>
  )
}
